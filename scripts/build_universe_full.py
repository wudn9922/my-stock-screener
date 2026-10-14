"""
全市場 universe：台股上市＋上櫃普通股、美股各交易所普通股與 ADR 的選股指標，寫入 docs/report/universe.json
（scope: "full"；指標與 main.py 的流動性名單同一套 report_metrics.build_universe，只是名單擴大、RS 百分位在擴大後的
名單內計算），並附加 sector / industry / marketCap 欄。

  python -u scripts/build_universe_full.py [--markets TW,US] [--budget 2400] [--cache-dir .cache/universe-full]

由 .github/workflows/universe-full.yml 在每日報告成功後執行；日 K 存在 Actions cache（不提交進 repo），
格式見 scripts/universe_cache.py。資料來源與解析見 scripts/universe_sources.py。

每次執行
1. 當日行情：台股 證交所 STOCK_DAY_ALL ＋ 櫃買 tpex_mainboard_daily_close_quotes（失敗時改用 Yahoo 批次報價）；
   美股 Yahoo v7 quote 批次（每批 QUOTE_BATCH 檔）。
2. 名單：台股 t187ap03_L ＋ mopsfin_t187ap03_O（失敗時用 LIST_MAX_AGE_DAYS 天內的上次名單，再不行由當日行情推出）；
   美股 NasdaqTrader nasdaqlisted／otherlisted（失敗時改用 Nasdaq screener，再不行用上次名單）；
   Nasdaq screener 另提供 sector / industry / 市值（失敗時用 PROFILE_MAX_AGE_DAYS 天內的上次資料）。
   名單數量低於 MIN_LIST（上市 800、上櫃 500、美股 3000）視為來源異常：不採用，改走退路；
   沒有可用名單的市場本次不寫入（universe.json 保留該市場原本的內容），並以錯誤結束。
3. 完整快取的股票直接接上當日 K；缺口、價格調整（除權、減資、分割）與還沒有完整歷史的股票逐檔向 Yahoo chart
   請求（REQUEST_DELAY 間隔、429 退避、總時間 --budget 秒），沒做完的下次繼續。
4. 只輸出有完整歷史、近 5 個交易日有 K、近 20 根不是全部零成交的股票；某市場名單中有完整歷史的比例低於
   PUBLISH_COVERAGE（冷啟動尚未補齊）時不寫入該市場（保留原內容，選股頁仍顯示 main.py 的流動性名單）。
5. 逐市場寫入 universe.json：只替換這次建好的市場，其他市場原樣保留；內容沒變時不改檔。
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

import report_metrics  # noqa: E402
import universe_cache as uc  # noqa: E402
import universe_sources as src  # noqa: E402

TWSE_COMPANIES_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_COMPANIES_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TWSE_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAILY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"

BENCHMARKS = report_metrics.MARKET_BENCHMARKS
MIN_LIST = {"TWSE": 800, "TPEx": 500, "US": 3000}
PUBLISH_COVERAGE = 0.9
LIST_MAX_AGE_DAYS = 14
PROFILE_MAX_AGE_DAYS = 45
DEFAULT_BUDGET_SECONDS = 2400
REQUEST_DELAY = 0.3
QUOTE_BATCH = 200
QUOTE_DELAY = 0.5
VERIFY_PER_RUN = 400
FULL_RANGE = "2y"
RECENT_RANGE = "3mo"
STALE_TRADING_DAYS = 5
RATE_LIMIT_WAITS = (30, 60, 120)
MAX_CONSECUTIVE_ERRORS = 25
PRUNE_UNLISTED_DAYS = 30
FULL_FIELDS = list(report_metrics.UNIVERSE_FIELDS) + list(report_metrics.UNIVERSE_PROFILE_FIELDS)
BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


# =========================================================================
# 網路
# =========================================================================
class HttpError(RuntimeError):
    def __init__(self, status, url):
        super().__init__(f"HTTP {status}（{url.split('?')[0]}）")
        self.status = status


class RateLimited(HttpError):
    pass


class Network:
    """curl_cffi（模擬 Chrome；Yahoo 與 api.nasdaq.com 需要）；沒有安裝時退回 requests。"""

    def __init__(self):
        try:
            from curl_cffi import requests as curl_requests

            self.session = curl_requests.Session(impersonate="chrome")
        except ImportError:
            import requests

            self.session = requests.Session()
            self.session.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            )

        self.crumb = None

    def get(self, url, params=None, headers=None, timeout=30):
        response = self.session.get(url, params=params, headers=headers, timeout=timeout)

        if response.status_code == 429:
            raise RateLimited(429, url)

        if response.status_code != 200:
            raise HttpError(response.status_code, url)

        return response

    def get_json(self, url, params=None, headers=None, timeout=30):
        response = self.get(url, params, headers, timeout)

        try:
            return response.json()
        except ValueError as exc:
            raise src.SourceFormatError(
                f"{url.split('?')[0]}：回應不是 JSON（前 120 字：{response.text[:120]!r}）"
            ) from exc

    def get_text(self, url, params=None, headers=None, timeout=30):
        return self.get(url, params, headers, timeout).text

    # ---- Yahoo
    def connect_yahoo(self):
        self.session.get("https://fc.yahoo.com/", timeout=20)
        response = self.session.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=20)
        crumb = (response.text or "").strip()

        if response.status_code == 429:
            raise RateLimited(429, "getcrumb")

        if response.status_code != 200 or not crumb or "<" in crumb:
            raise HttpError(response.status_code, "getcrumb")

        self.crumb = crumb

    def chart(self, symbol, range_):
        params = {"range": range_, "interval": "1d", "events": "split", "includePrePost": "false"}

        if self.crumb:
            params["crumb"] = self.crumb

        return self.get_json(YAHOO_CHART_URL.format(symbol=symbol), params, timeout=30)

    def quote(self, symbols):
        params = {
            "symbols": ",".join(symbols),
            "fields": "symbol,quoteType,marketState,regularMarketTime,regularMarketOpen,regularMarketDayHigh,"
                      "regularMarketDayLow,regularMarketPrice,regularMarketVolume,regularMarketPreviousClose,"
                      "sharesOutstanding,marketCap,exchangeTimezoneName",
        }

        if self.crumb:
            params["crumb"] = self.crumb

        return self.get_json(YAHOO_QUOTE_URL, params, timeout=30)


def with_retries(action, label, log, attempts=3, sleep=time.sleep, waits=(3, 10)):
    """名單、行情等單一請求：失敗重試；全部失敗回傳 (None, 最後的錯誤訊息)。"""
    error = None

    for attempt in range(attempts):
        try:
            return action(), None
        except Exception as exc:  # noqa: BLE001 - every source failure is logged and handled by a fallback
            error = f"{type(exc).__name__}: {exc}"
            log(f"⚠️ {label} 失敗（{attempt + 1}/{attempts}）：{error}")

            if attempt + 1 < attempts:
                sleep(waits[min(attempt, len(waits) - 1)])

    return None, error


def market_of(symbol):
    return report_metrics.market_of(symbol)


# =========================================================================
# 名單
# =========================================================================
def _age_days(fetched_at, today):
    try:
        return (datetime.fromisoformat(today).date() - datetime.fromisoformat(fetched_at[:10]).date()).days
    except (TypeError, ValueError):
        return 10 ** 6


def cached_part(lists_cache, key, today, max_age):
    part = (lists_cache or {}).get(key)

    if not isinstance(part, dict) or not isinstance(part.get("items"), dict) or not part["items"]:
        return None

    if _age_days(part.get("fetchedAt"), today) > max_age:
        return None

    return part


def load_tw_exchange(exchange, fetch, lists_cache, snapshot, today, log, problems):
    """
    單一交易所（TWSE / TPEx）的公司名單：官方公司基本資料 → 上次名單（LIST_MAX_AGE_DAYS 天內）→ 當日行情推出。
    回傳 (items, source)；都不可用時 ({}, None)。
    """
    url = TWSE_COMPANIES_URL if exchange == "TWSE" else TPEX_COMPANIES_URL
    minimum = MIN_LIST[exchange]
    unknown = set()
    payload, error = fetch(url)

    if payload is not None:
        try:
            items = src.parse_tw_companies(payload, exchange, unknown)
        except src.SourceFormatError as exc:
            items, error = {}, str(exc)

        if unknown:
            log(f"⚠️ {exchange} 未知產業別代碼：{', '.join(sorted(unknown))}（產業別留空，請補 TW_INDUSTRY_NAMES）")

        if len(items) >= minimum:
            return items, url.rsplit("/", 1)[-1]

        if payload is not None and not error:
            error = f"只有 {len(items)} 檔（門檻 {minimum}）"

    problems.append(f"{exchange} 公司基本資料不可用：{error}")
    cached = cached_part(lists_cache, exchange, today, LIST_MAX_AGE_DAYS)

    if cached is not None:
        log(f"↩️ {exchange} 改用 {cached.get('fetchedAt')} 的名單（{len(cached['items'])} 檔）")
        return cached["items"], f"cache {cached.get('fetchedAt')}"

    derived = src.tw_companies_from_quotes(snapshot, exchange)

    if len(derived) >= minimum:
        log(f"↩️ {exchange} 改由當日行情推出名單（{len(derived)} 檔，沒有產業別與股數）")
        return derived, "daily quotes"

    return {}, None


def load_us_list(fetch_text, fetch_json, lists_cache, today, log, problems):
    """NasdaqTrader → Nasdaq screener → 上次名單。回傳 (items, source, excluded counts)。"""
    excluded = {}
    nasdaq, error_a = fetch_text(NASDAQ_LISTED_URL)
    other, error_b = fetch_text(OTHER_LISTED_URL)

    if nasdaq is not None and other is not None:
        try:
            items = src.parse_us_listed(nasdaq, other, excluded)
        except src.SourceFormatError as exc:
            items, error_a = {}, str(exc)

        if len(items) >= MIN_LIST["US"]:
            return items, "nasdaqtrader", excluded

        error_a = error_a or f"只有 {len(items)} 檔（門檻 {MIN_LIST['US']}）"

    problems.append(f"NasdaqTrader 名單不可用：{error_a or error_b}")
    screener = fetch_json(NASDAQ_SCREENER_URL)

    if screener:
        items = {}
        excluded = {}

        for symbol, row in screener.items():
            reason = src.us_exclusion_reason(row.get("name") or "")

            if reason:
                excluded[reason] = excluded.get(reason, 0) + 1
                continue

            items[symbol] = {"name": src.clean_us_name(row.get("name") or symbol), "exchange": None}

        if len(items) >= MIN_LIST["US"]:
            log(f"↩️ 美股名單改用 Nasdaq screener（{len(items)} 檔；無 ETF／測試旗標，只靠名稱規則排除）")
            return items, "nasdaq screener", excluded

    cached = cached_part(lists_cache, "US", today, LIST_MAX_AGE_DAYS)

    if cached is not None:
        log(f"↩️ 美股改用 {cached.get('fetchedAt')} 的名單（{len(cached['items'])} 檔）")
        return cached["items"], f"cache {cached.get('fetchedAt')}", {}

    return {}, None, excluded


# =========================================================================
# 主流程
# =========================================================================
class Run:
    def __init__(self, args, net=None, now=None, clock=time.monotonic, sleep=time.sleep, log=print):
        self.args = args
        self.net = net
        self.now = now or datetime.now(timezone.utc)
        self.today = self.now.date().isoformat()
        self.clock = clock
        self.sleep = sleep
        self.log = log
        self.deadline = clock() + args.budget
        self.cache_dir = args.cache_dir
        self.state = uc.load_state(self.cache_dir)
        self.lists_cache = uc.read_json(os.path.join(self.cache_dir, "lists.json"), {}) or {}
        self.problems = []
        self.warnings = []
        self.markets = [market for market in ("TW", "US") if market in args.markets]
        self.listing = {}
        self.list_info = {}
        self.snapshots = {}
        self.calendars = {}
        self.bench = {}
        self.us_profiles = {}
        self.us_profiles_date = None
        self.quote_extra = {}
        self.pending = {}
        self.stats = Counter()
        self.yahoo_ready = False
        self.chart_stopped = None
        self.agreement = []

    # ---- helpers
    def time_left(self):
        return self.deadline - self.clock()

    def fetch_json(self, url, params=None, headers=None, label=None):
        return with_retries(lambda: self.net.get_json(url, params, headers, timeout=60), label or url, self.log,
                            sleep=self.sleep)

    def fetch_text(self, url, label=None):
        return with_retries(lambda: self.net.get_text(url, timeout=60), label or url, self.log, sleep=self.sleep)

    def entry(self, symbol):
        return uc.entry_of(self.state, symbol)

    def save(self):
        uc.save_state(self.cache_dir, self.state)
        uc.write_json(os.path.join(self.cache_dir, "lists.json"), self.lists_cache)

    # ---- 1. 當日行情（台股官方）
    def load_tw_snapshot(self):
        bars = {}
        dates = []
        sources = []

        for url, parse, label in (
            (TWSE_DAY_ALL_URL, src.parse_twse_day_all, "證交所 STOCK_DAY_ALL"),
            (TPEX_DAILY_URL, src.parse_tpex_daily_quotes, "櫃買 daily_close_quotes"),
        ):
            payload, error = self.fetch_json(url, label=label)

            if payload is None:
                self.warnings.append(f"{label} 取得失敗：{error}")
                continue

            try:
                snapshot = parse(payload)
            except src.SourceFormatError as exc:
                self.warnings.append(str(exc))
                self.log(f"⚠️ {exc}")
                continue

            if not uc.session_done(snapshot["date"], "TW", self.now):
                self.log(f"⏳ {label} {snapshot['date']} 尚未收盤，略過")
                continue

            bars.update(snapshot["bars"])
            dates.append(snapshot["date"])
            sources.append(label)
            self.log(f"📥 {label}：{len(snapshot['bars'])} 檔，資料日 {snapshot['date']}")

        self.snapshots["TW"] = {"bars": bars, "date": max(dates) if dates else None, "sources": sources}

    # ---- 2. 名單
    def load_lists(self):
        if "TW" in self.markets:
            fetch = lambda url: self.fetch_json(url, label=url.rsplit("/", 1)[-1])  # noqa: E731
            listing = {}
            info = {}

            for exchange in ("TWSE", "TPEx"):
                items, source = load_tw_exchange(
                    exchange, fetch, self.lists_cache, self.snapshots.get("TW"), self.today, self.log, self.warnings
                )
                info[exchange] = {"count": len(items), "source": source}

                if source and not source.startswith("cache") and source != "daily quotes":
                    self.lists_cache[exchange] = {"fetchedAt": self.today, "source": source, "items": items}

                listing.update(items)

            if not info["TWSE"]["count"]:
                self.problems.append("台股上市名單完全不可用（公司基本資料、近期名單與當日行情都沒有）：本次不更新台股")
                listing = {}

            self.listing["TW"] = listing
            self.list_info["TW"] = info
            self.log(f"📋 台股名單：上市 {info['TWSE']['count']:,}（{info['TWSE']['source']}）、"
                     f"上櫃 {info['TPEx']['count']:,}（{info['TPEx']['source']}）")

            for exchange in ("TWSE", "TPEx"):
                items = [item for item in listing.values() if item.get("exchange") == exchange]
                with_industry = sum(1 for item in items if item.get("industry"))
                with_shares = sum(1 for item in items if item.get("shares"))
                info[exchange].update({"industry": with_industry, "shares": with_shares})
                self.log(f"   {exchange}：有產業別 {with_industry:,}、有已發行股數 {with_shares:,} / {len(items):,}")

                if items and with_shares < len(items) * 0.8:
                    self.warnings.append(
                        f"{exchange} 公司基本資料只有 {with_shares}/{len(items)} 檔取得已發行股數（欄位名稱可能改了），市值多為 null"
                    )

        if "US" in self.markets:
            screener = self.load_us_profiles() or {}
            items, source, excluded = load_us_list(
                lambda url: self.fetch_text(url, label=url.rsplit("/", 1)[-1]),
                lambda url: screener, self.lists_cache, self.today, self.log, self.warnings,
            )

            if source in ("nasdaqtrader", "nasdaq screener"):
                self.lists_cache["US"] = {"fetchedAt": self.today, "source": source, "items": items}

            if not items:
                self.problems.append("美股名單完全不可用（NasdaqTrader、Nasdaq screener 與近期名單都沒有）：本次不更新美股")

            self.listing["US"] = items
            exchanges = Counter(item.get("exchange") or "?" for item in items.values())
            self.list_info["US"] = {"count": len(items), "source": source, "excluded": excluded,
                                    "exchanges": dict(exchanges)}
            self.log(f"📋 美股名單：{len(items):,} 檔（{source}；交易所 {dict(exchanges)}；排除 {excluded}）")

        for market in self.markets:
            for symbol in self.listing.get(market, {}):
                self.entry(symbol)["seen"] = self.today

    def load_us_profiles(self):
        """Nasdaq screener → {symbol: row}；同時更新 sector / industry / 股數的快取。"""
        payload, error = self.fetch_json(
            NASDAQ_SCREENER_URL,
            {"tableonly": "true", "download": "true", "limit": "0"},
            BROWSER_HEADERS,
            label="Nasdaq screener",
        )
        rows = {}

        if payload is not None:
            try:
                rows = src.parse_nasdaq_screener(payload)
            except src.SourceFormatError as exc:
                error = str(exc)

        if len(rows) >= MIN_LIST["US"]:
            profiles = {
                symbol: {
                    "sector": row["sector"],
                    "industry": row["industry"],
                    "shares": src.implied_shares(row["marketCap"], row["lastSale"]),
                    "cap": row["marketCap"],
                }
                for symbol, row in rows.items()
            }
            self.lists_cache["USProfiles"] = {"fetchedAt": self.today, "items": profiles}
            self.us_profiles = profiles
            self.us_profiles_date = self.today
            self.log(f"📥 Nasdaq screener：{len(rows)} 檔（sector／industry／市值）")
            return rows

        self.warnings.append(f"Nasdaq screener 不可用（{error or f'只有 {len(rows)} 檔'}）")
        cached = cached_part(self.lists_cache, "USProfiles", self.today, PROFILE_MAX_AGE_DAYS)

        if cached is not None:
            self.us_profiles = cached["items"]
            self.us_profiles_date = cached.get("fetchedAt")
            self.log(f"↩️ 美股產業與市值改用 {self.us_profiles_date} 的資料")
        else:
            self.log("⚠️ 美股產業與市值本次留空")

        return None

    # ---- 3. Yahoo：基準與報價
    def connect_yahoo(self):
        for number, wait in enumerate((0,) + RATE_LIMIT_WAITS):
            if wait:
                self.sleep(wait)

            try:
                self.net.connect_yahoo()
                self.yahoo_ready = True
                return True
            except Exception as exc:  # noqa: BLE001
                self.log(f"⚠️ Yahoo 連線失敗（{number + 1}）：{type(exc).__name__}: {exc}")

            if self.time_left() < 300:
                break

        self.warnings.append("Yahoo 無法連線：本次不抓日 K 歷史與美股報價")
        return False

    def load_benchmarks(self):
        for market in self.markets:
            symbol = BENCHMARKS[market]
            path = uc.bars_path(self.cache_dir, symbol)
            frame = uc.read_bars(path)

            if self.yahoo_ready:
                try:
                    rows, _, _ = src.parse_yahoo_chart(self.net.chart(symbol, FULL_RANGE), symbol)
                    fresh = uc.drop_unfinished(uc.frame_from_rows(rows), market, self.now)

                    if len(fresh) >= report_metrics.BENCH_MIN_BARS:
                        frame = uc.write_bars(path, fresh, keep=600)
                except Exception as exc:  # noqa: BLE001
                    self.warnings.append(f"基準 {symbol} 下載失敗：{type(exc).__name__}: {exc}")

                self.sleep(REQUEST_DELAY)

            self.bench[market] = {"symbol": symbol, "close": frame["Close"] if not frame.empty else None}
            self.calendars[market] = uc.TradingCalendar(
                [day.strftime("%Y-%m-%d") for day in frame.index] if not frame.empty else []
            )

            if frame.empty:
                self.warnings.append(f"基準 {symbol} 沒有資料：相對強弱與 RS 百分位為 null，交易日以平日推算")

    def quote_bars(self, symbols, label):
        bars = {}
        failures = 0

        for start in range(0, len(symbols), QUOTE_BATCH):
            if self.time_left() < 60:
                self.warnings.append(f"{label}：時間不足，報價未取完")
                break

            batch = symbols[start:start + QUOTE_BATCH]

            try:
                bars.update(src.parse_yahoo_quotes(self.net.quote(batch), market_of))
                failures = 0
            except Exception as exc:  # noqa: BLE001
                failures += 1
                self.log(f"⚠️ {label} 第 {start // QUOTE_BATCH + 1} 批失敗：{type(exc).__name__}: {exc}")

                if failures >= 3:
                    self.warnings.append(f"{label} 連續失敗，停止取報價")
                    break

                self.sleep(RATE_LIMIT_WAITS[0] if isinstance(exc, RateLimited) else 3)

            self.sleep(QUOTE_DELAY)

        return bars

    def load_quote_snapshots(self):
        if not self.yahoo_ready:
            return

        if "US" in self.markets and self.listing.get("US"):
            bars = self.quote_bars(sorted(self.listing["US"]), "Yahoo 美股報價")
            self.quote_extra = {symbol: bar for symbol, bar in bars.items()}
            usable = {
                symbol: bar for symbol, bar in bars.items()
                if bar.get("quoteType") in (None, "EQUITY") and uc.session_done(bar["date"], "US", self.now)
            }
            dates = Counter(bar["date"] for bar in usable.values())
            as_of = dates.most_common(1)[0][0] if dates else None
            self.snapshots["US"] = {
                "bars": {symbol: bar for symbol, bar in usable.items() if bar["date"] == as_of},
                "date": as_of,
                "sources": ["Yahoo quote"],
            }
            self.log(f"📥 Yahoo 美股報價：{len(usable)} 檔可用，資料日 {as_of}")

        tw = self.snapshots.get("TW") or {}

        if "TW" in self.markets and self.listing.get("TW"):
            missing = sorted(symbol for symbol in self.listing["TW"] if symbol not in tw.get("bars", {}))

            if len(missing) > 50:
                bars = self.quote_bars(missing, "Yahoo 台股報價（官方行情的退路）")
                usable = {s: b for s, b in bars.items() if uc.session_done(b["date"], "TW", self.now)}

                if usable:
                    merged = dict(tw.get("bars") or {})
                    merged.update(usable)
                    dates = Counter(bar["date"] for bar in merged.values())
                    self.snapshots["TW"] = {
                        "bars": merged,
                        "date": dates.most_common(1)[0][0],
                        "sources": (tw.get("sources") or []) + ["Yahoo quote"],
                    }

    # ---- 4. 套用當日行情、排出逐檔請求
    def plan(self):
        tasks = {"adjusted": [], "recent": [], "full": [], "verify": []}

        for market in self.markets:
            snapshot = self.snapshots.get(market) or {}
            calendar = self.calendars.get(market) or uc.TradingCalendar()

            if snapshot.get("date"):
                calendar.add(snapshot["date"])

            latest = snapshot.get("date")
            listing = self.listing.get(market) or {}
            order = sorted(listing)

            if market == "US":
                order.sort(key=lambda symbol: -((self.us_profiles.get(symbol) or {}).get("cap") or 0))

            for symbol in order:
                entry = self.entry(symbol)

                if uc.is_complete(entry) and not os.path.exists(uc.bars_path(self.cache_dir, symbol)):
                    entry.pop("full", None)  # 快取檔不見了：重建完整歷史

                if not uc.is_complete(entry):
                    if uc.can_retry(entry, self.today):
                        tasks["full"].append(symbol)
                    if symbol in (snapshot.get("bars") or {}):
                        self.pending[symbol] = snapshot["bars"][symbol]
                    continue

                bar = (snapshot.get("bars") or {}).get(symbol)

                if bar is not None:
                    action, detail = uc.snapshot_action(entry, bar, calendar, market)

                    if action == "append":
                        path = uc.bars_path(self.cache_dir, symbol)
                        frame = uc.append_row(uc.read_bars(path), detail)
                        uc.record_frame(self.state, symbol, uc.write_bars(path, frame), self.today)
                        self.stats[f"{market}.appended"] += 1
                    elif action == "adjusted":
                        self.log(f"🔁 {symbol}：{detail} → 整檔重抓")
                        self.pending[symbol] = bar
                        uc.mark_suspect(entry, self.today)
                        tasks["adjusted"].append(symbol)
                        self.stats[f"{market}.adjusted"] += 1
                        continue
                    elif action == "gap":
                        self.pending[symbol] = bar
                        if uc.can_retry(entry, self.today):
                            tasks["recent"].append(symbol)
                        self.stats[f"{market}.gap"] += 1
                        continue
                elif latest and entry.get("last", "") < latest and uc.can_retry(entry, self.today):
                    tasks["recent"].append(symbol)
                    self.stats[f"{market}.noSnapshot"] += 1
                    continue

                if uc.suspect(entry, self.today):
                    tasks["recent"].append(symbol)
                elif uc.verify_due(entry, self.today):
                    tasks["verify"].append(symbol)

        tasks["verify"].sort(key=lambda symbol: self.entry(symbol).get("verified") or "")
        tasks["verify"] = tasks["verify"][:VERIFY_PER_RUN]
        queue = (
            [(symbol, "full", "adjusted") for symbol in tasks["adjusted"]]
            + [(symbol, "recent", "recent") for symbol in tasks["recent"]]
            + [(symbol, "full", "new") for symbol in tasks["full"]]
            + [(symbol, "recent", "verify") for symbol in tasks["verify"]]
        )
        self.log(
            f"🧮 逐檔請求：調整 {len(tasks['adjusted'])}、補近期 {len(tasks['recent'])}、"
            f"建完整歷史 {len(tasks['full'])}、輪流驗證 {len(tasks['verify'])}"
        )
        self.save()
        return queue

    # ---- 5. 逐檔請求
    def fetch_chart(self, symbol, range_):
        """→ DataFrame；429 依 RATE_LIMIT_WAITS 退避重試，仍失敗丟 RateLimited。"""
        for wait in (0,) + RATE_LIMIT_WAITS:
            if wait:
                if self.time_left() < wait + 30:
                    break
                self.log(f"⏳ Yahoo 限流，等待 {wait} 秒")
                self.sleep(wait)

            try:
                rows, _, _ = src.parse_yahoo_chart(self.net.chart(symbol, range_), symbol)
                market = market_of(symbol)
                return uc.drop_unfinished(uc.frame_from_rows(rows), market, self.now)
            except RateLimited:
                continue

        raise RateLimited(429, symbol)

    def apply_pending(self, symbol, frame):
        """逐檔抓完後，若官方／報價的當日 K 比 Yahoo 新就接上去。"""
        bar = self.pending.pop(symbol, None)

        if bar is None or frame.empty:
            return frame

        market = market_of(symbol)

        if bar.get("close") and bar["date"] in frame.index.strftime("%Y-%m-%d"):
            # 同一天 Yahoo 與官方／報價都有：記錄收盤與成交量的一致程度（日誌中的口徑檢查）
            yahoo = frame.loc[bar["date"]]
            self.agreement.append((
                market,
                abs(bar["close"] / float(yahoo["Close"]) - 1),
                (bar["volume"] / float(yahoo["Volume"])) if float(yahoo["Volume"]) > 0 else None,
            ))

        summary = uc.summarize(frame)
        action, detail = uc.snapshot_action(
            summary, bar, self.calendars.get(market) or uc.TradingCalendar(), market, check_reference=False
        )

        if action == "append":
            return uc.append_row(frame, detail)

        return frame

    def execute(self, queue):
        errors_in_row = 0
        done = 0

        for index, (symbol, kind, reason) in enumerate(queue):
            if self.time_left() <= 0:
                self.chart_stopped = f"時間預算用完，尚餘 {len(queue) - index} 檔下次繼續"
                break

            path = uc.bars_path(self.cache_dir, symbol)
            old = uc.read_bars(path)

            try:
                frame = self.fetch_chart(symbol, FULL_RANGE if kind == "full" else RECENT_RANGE)
                errors_in_row = 0
            except RateLimited:
                self.chart_stopped = f"Yahoo 持續限流（HTTP 429），尚餘 {len(queue) - index} 檔下次繼續"
                break
            except Exception as exc:  # noqa: BLE001 - one bad symbol never stops the run
                errors_in_row += 1
                uc.record_failure(self.state, symbol, self.today, f"{type(exc).__name__}: {exc}")
                self.stats["chart.failed"] += 1

                if errors_in_row >= MAX_CONSECUTIVE_ERRORS:
                    self.chart_stopped = f"連續 {errors_in_row} 檔失敗（最後：{exc}），停止逐檔請求"
                    break

                self.sleep(REQUEST_DELAY)
                continue

            self.sleep(REQUEST_DELAY)

            if frame.empty:
                uc.record_failure(self.state, symbol, self.today, "Yahoo 沒有日 K")
                self.stats["chart.empty"] += 1
                continue

            if kind == "recent" and old.empty:
                # 快取檔不見或損壞：近期資料不夠，整檔重抓
                try:
                    frame = self.fetch_chart(symbol, FULL_RANGE)
                    kind = "full"
                except Exception as exc:  # noqa: BLE001
                    uc.record_failure(self.state, symbol, self.today, f"{type(exc).__name__}: {exc}")
                    if isinstance(exc, RateLimited):
                        self.chart_stopped = "Yahoo 持續限流（HTTP 429）"
                        break
                    continue
                self.sleep(REQUEST_DELAY)
            elif kind == "recent":
                check = uc.compare_overlap(old, frame)

                if check is not None and not check["ok"]:
                    self.log(f"🔁 {symbol}：{check['date']} 收盤差 {check['worst']:.2%} → 整檔重抓")
                    uc.mark_suspect(self.entry(symbol), self.today)
                    self.stats["chart.adjusted"] += 1

                    try:
                        frame = self.fetch_chart(symbol, FULL_RANGE)
                    except Exception as exc:  # noqa: BLE001
                        uc.record_failure(self.state, symbol, self.today, f"{type(exc).__name__}: {exc}")
                        if isinstance(exc, RateLimited):
                            self.chart_stopped = "Yahoo 持續限流（HTTP 429）"
                            break
                        continue

                    self.sleep(REQUEST_DELAY)
                    kind = "full"
                elif check is None and uc.summarize(old)["last"] < uc.summarize(frame)["first"]:
                    # 缺口比近期資料還長：近期資料接不上，整檔重抓
                    try:
                        frame = self.fetch_chart(symbol, FULL_RANGE)
                        kind = "full"
                    except Exception as exc:  # noqa: BLE001
                        uc.record_failure(self.state, symbol, self.today, f"{type(exc).__name__}: {exc}")
                        continue
                    self.sleep(REQUEST_DELAY)

            merged = uc.merge_full(old, frame) if kind == "full" else uc.merge_incremental(old, frame)
            merged = self.apply_pending(symbol, merged)
            written = uc.write_bars(path, merged)
            uc.record_frame(self.state, symbol, written, self.today, full=kind == "full", verified=True)
            self.stats[f"chart.{reason}"] += 1
            done += 1

            if done % 200 == 0:
                self.save()
                self.log(f"… 已完成 {done}/{len(queue)} 檔（剩 {self.time_left():.0f} 秒）")

        if self.chart_stopped:
            self.warnings.append(self.chart_stopped)

        self.save()

    # ---- 6. 輸出
    def profile(self, market, symbol, close):
        if market == "TW":
            item = self.listing["TW"].get(symbol) or {}
            shares = item.get("shares")
            return [None, item.get("industry"), int(round(shares * close)) if shares and close else None]

        profile = self.us_profiles.get(symbol) or {}
        shares = profile.get("shares") or (self.quote_extra.get(symbol) or {}).get("shares")
        cap = int(round(shares * close)) if shares and close else None
        return [profile.get("sector"), profile.get("industry"), cap]

    def build_market(self, market):
        listing = self.listing.get(market) or {}

        if not listing:
            return None

        entries = {symbol: self.state["symbols"].get(symbol) or {} for symbol in listing}
        complete = [symbol for symbol, entry in entries.items() if uc.is_complete(entry)]
        coverage = len(complete) / len(listing)

        if coverage < PUBLISH_COVERAGE:
            self.warnings.append(
                f"{market} 冷啟動中：完整歷史 {len(complete):,}/{len(listing):,} 檔（{coverage:.0%}，"
                f"門檻 {PUBLISH_COVERAGE:.0%}），本次不寫入，保留原本的名單"
            )
            return None

        as_of = Counter(entries[symbol]["last"] for symbol in complete).most_common(1)[0][0]
        calendar = self.calendars.get(market) or uc.TradingCalendar()
        cutoff = calendar.nth_last(as_of, STALE_TRADING_DAYS)
        reasons = Counter()
        eligible = []

        for symbol in sorted(listing):
            reason = uc.eligibility(entries[symbol], cutoff)

            if reason:
                reasons[reason] += 1
            else:
                eligible.append(symbol)

        names = {symbol: (item.get("name") or "") for symbol, item in listing.items()}
        universe = report_metrics.build_universe(
            eligible, market, uc.bars_dir(self.cache_dir), self.bench.get(market),
            name_fn=lambda symbol: names.get(symbol, ""),
        )
        records = universe["records"]
        reasons["other"] += len(eligible) - len(records)
        rows = [
            report_metrics.record_to_row(record) + self.profile(market, record["symbol"], record["close"])
            for record in records
        ]
        info = self.list_info.get(market) or {}
        label, note = describe(market, info, len(listing), len(rows), reasons, self.us_profiles_date)
        self.log(f"📊 {market} 全市場：名單 {len(listing):,}、輸出 {len(rows):,}、排除 {dict(reasons)}")

        return {
            "benchmark": universe["benchmark"],
            "asOf": universe["asOf"],
            "universe": label,
            "scope": "full",
            "note": note,
            "counts": {
                "listed": len(listing),
                "rows": len(rows),
                "stale": reasons["stale"],
                "noVolume": reasons["noVolume"],
                "missing": reasons["missing"],
                "other": reasons["other"],
            },
            "rows": rows,
        }

    def prune(self):
        listed = set()

        for market in self.markets:
            listed.update(self.listing.get(market) or {})

        if not listed:
            return

        cutoff = (self.now - timedelta(days=PRUNE_UNLISTED_DAYS)).date().isoformat()

        for symbol, entry in list(self.state["symbols"].items()):
            if symbol in listed or market_of(symbol) not in self.markets:
                continue

            if (entry.get("seen") or "") < cutoff:
                path = uc.bars_path(self.cache_dir, symbol)

                if os.path.exists(path):
                    os.remove(path)

                del self.state["symbols"][symbol]
                self.stats["pruned"] += 1


def agreement_summary(samples):
    """官方行情／Yahoo 報價與 Yahoo 日 K 同一天的收盤差與成交量比（中位數），供 Actions 日誌確認口徑一致。"""
    result = {}

    for market in ("TW", "US"):
        rows = [sample for sample in samples if sample[0] == market]

        if not rows:
            continue

        closes = sorted(sample[1] for sample in rows)
        volumes = sorted(sample[2] for sample in rows if sample[2] is not None)
        result[market] = {
            "samples": len(rows),
            "closeOff": sum(1 for value in closes if value > uc.PRICE_TOLERANCE),
            "volumeRatioMedian": round(volumes[len(volumes) // 2], 3) if volumes else None,
        }

    return result


def describe(market, info, listed, rows, reasons, profiles_date):
    """市場說明（顯示在選股頁）：範圍與檔數、排除了哪些。"""
    excluded = []

    if reasons.get("stale"):
        excluded.append(f"近 {STALE_TRADING_DAYS} 個交易日無 K 線（停牌、暫停交易等）{reasons['stale']:,} 檔")
    if reasons.get("noVolume"):
        excluded.append(f"近 20 日零成交 {reasons['noVolume']:,} 檔")
    if reasons.get("missing"):
        excluded.append(f"歷史日 K 尚未取得 {reasons['missing']:,} 檔")
    if reasons.get("other"):
        excluded.append(f"指標無法計算 {reasons['other']:,} 檔")

    tail = f"；排除{'、'.join(excluded)}" if excluded else ""

    if market == "TW":
        twse = (info.get("TWSE") or {}).get("count") or 0
        tpex = (info.get("TPEx") or {}).get("count") or 0
        label = (
            f"台股上市＋上櫃普通股（上市 {twse:,}、上櫃 {tpex:,} 檔）" if tpex
            else f"台股上市普通股（{twse:,} 檔；上櫃名單暫缺）"
        )
        note = (
            f"名單 {listed:,} 檔、列出 {rows:,} 檔{tail}。不含 ETF、TDR、特別股、權證與興櫃。"
            "產業別：證交所／櫃買中心公司基本資料；市值＝已發行普通股數×收盤價。"
        )
        return label, note

    label = f"美股 NYSE／NASDAQ／NYSE American 等交易所普通股與 ADR（{listed:,} 檔）"
    profile = "產業分類與市值：Nasdaq 股票篩選器（英文分類）"

    if profiles_date:
        profile += f"，{profiles_date} 更新"
    else:
        profile += "，本次無資料"

    note = (
        f"名單 {listed:,} 檔、列出 {rows:,} 檔{tail}。不含 ETF、基金、權證、權利、單位、特別股、債券與 SPAC。"
        f"{profile}。"
    )
    return label, note


def write_markets(report_dir, built, today):
    """逐市場寫入：只替換 built 中的市場，其他市場保留 universe.json 原本的內容；內容沒變不改檔。"""
    existing = report_metrics.read_universe_json(report_dir)
    existing_fields = report_metrics.fields_of(existing) if existing else list(report_metrics.UNIVERSE_FIELDS)
    parts = {}

    for market in report_metrics.MARKET_BENCHMARKS:
        if market in built:
            parts[market] = (built[market], FULL_FIELDS)
        elif existing and isinstance((existing.get("markets") or {}).get(market), dict):
            parts[market] = (existing["markets"][market], existing_fields)

    report_date = (existing or {}).get("reportDate") or max(
        (entry.get("asOf") or "" for entry, _ in parts.values()), default=None
    ) or today
    document = report_metrics.combine_universe_markets(parts, report_date=report_date)

    if existing:
        same = {key: value for key, value in existing.items() if key != "generatedAt"}
        new = {key: value for key, value in report_metrics._clean_json(document).items() if key != "generatedAt"}

        if json.dumps(same, sort_keys=True, ensure_ascii=False) == json.dumps(new, sort_keys=True, ensure_ascii=False):
            print("universe.json 內容沒有變動，不改檔")
            return None

    return report_metrics.write_universe_json(document, report_dir)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--markets", default="TW,US", help="TW,US（預設兩者）")
    parser.add_argument("--budget", type=float,
                        default=float(os.environ.get("UNIVERSE_BUDGET_SECONDS") or DEFAULT_BUDGET_SECONDS),
                        help="網路請求的總時間預算（秒）")
    parser.add_argument("--cache-dir", default=os.environ.get("UNIVERSE_CACHE_DIR") or os.path.join(ROOT, ".cache", "universe-full"))
    parser.add_argument("--report-dir", default=os.environ.get("ATLAS_REPORT_DIR") or os.path.join(ROOT, "docs", "report"))
    parser.add_argument("--offline", action="store_true", help="不連網路，只用快取（lists.json、bars/）重建輸出")
    args = parser.parse_args(argv)
    args.markets = [part.strip().upper() for part in args.markets.split(",") if part.strip()]
    return args


def run(args, net=None, now=None, clock=time.monotonic, sleep=time.sleep, log=print):
    """→ (exit code, Run)。"""
    job = Run(args, net=net, now=now, clock=clock, sleep=sleep, log=log)
    os.makedirs(uc.bars_dir(job.cache_dir), exist_ok=True)

    if args.offline:
        for market in job.markets:
            key_items = {}
            if market == "TW":
                for exchange in ("TWSE", "TPEx"):
                    part = (job.lists_cache.get(exchange) or {}).get("items") or {}
                    key_items.update(part)
                job.list_info["TW"] = {
                    exchange: {"count": len((job.lists_cache.get(exchange) or {}).get("items") or {}), "source": "cache"}
                    for exchange in ("TWSE", "TPEx")
                }
            else:
                key_items = (job.lists_cache.get("US") or {}).get("items") or {}
                job.list_info["US"] = {"count": len(key_items), "source": "cache"}
                cached = job.lists_cache.get("USProfiles") or {}
                job.us_profiles = cached.get("items") or {}
                job.us_profiles_date = cached.get("fetchedAt")
            job.listing[market] = key_items
            frame = uc.read_bars(uc.bars_path(job.cache_dir, BENCHMARKS[market]))
            job.bench[market] = {"symbol": BENCHMARKS[market], "close": frame["Close"] if not frame.empty else None}
            job.calendars[market] = uc.TradingCalendar([day.strftime("%Y-%m-%d") for day in frame.index])
    else:
        net = job.net = net or Network()

        if "TW" in job.markets:
            job.load_tw_snapshot()

        job.load_lists()
        job.connect_yahoo()
        job.load_benchmarks()
        job.load_quote_snapshots()
        queue = job.plan()

        if job.yahoo_ready:
            job.execute(queue)
        else:
            job.save()

        job.prune()
        job.save()

    built = {}

    for market in job.markets:
        try:
            entry = job.build_market(market)
        except Exception as exc:  # noqa: BLE001 - one market failing keeps the other
            job.problems.append(f"{market} 輸出失敗：{type(exc).__name__}: {exc}")
            continue

        if entry is not None:
            built[market] = entry

    if built:
        write_markets(args.report_dir, built, job.today)

    summary = {
        "built": {market: entry["counts"] for market, entry in built.items()},
        "sameDayCheck": agreement_summary(job.agreement),
        "lists": job.list_info,
        "stats": dict(job.stats),
        "warnings": job.warnings,
        "problems": job.problems,
    }
    log(json.dumps(summary, ensure_ascii=False, default=str))

    for warning in job.warnings:
        log(f"::warning::{warning}")

    for problem in job.problems:
        log(f"::error::{problem}")

    return (1 if job.problems else 0), job


def main(argv=None):
    code, _ = run(parse_args(argv))
    return code


if __name__ == "__main__":
    sys.exit(main())
