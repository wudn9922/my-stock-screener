"""
Builds docs/report/rates.json and docs/report/rates-history.json: US Treasury curve, spreads, real yields,
breakevens, the Fed target range and five long-Treasury ETFs (TLT, EDV, GOVZ, ZROZ, TMF).

  python scripts/build_rates.py
  Env: RATES_OUT_DIR (default docs/report), FRED_API_KEY (optional, fallback only)

Sources, per series: FRED fredgraph.csv (no key) -> FRED API (only when FRED_API_KEY is set) -> the
values already in the previous rates-history.json (reported in `missing`). Messages only ever name
the series and the HTTP status: URLs and exception texts are never printed because the API URL
carries api_key. ETF prices are Yahoo Finance adjusted closes (yfinance, imported lazily); when the
download fails the previous ETF blocks are kept.

Nothing is written (old files kept, exit 1) when DGS2/DGS10/DGS30 are not fresh, fewer than 8 of the
11 tenors are fresh, or the latest DGS10 is more than 10 days old. build_payloads() is a pure function
so the whole payload can be tested offline (tests/test_rates.py).

Units: yields %, spreads bp (int), returns / volatility / drawdowns %, durations years, convexity
years² (ΔP/P ≈ −D·Δy + ½·C·Δy², Δy decimal; an iShares-published 3.6 is 360 here).
"""
import csv
import io
import json
import math
import os
import sys
import time
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rates_analytics as ra  # noqa: E402
from rates_config import ETFS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT_DIR = os.path.join(ROOT, "docs", "report")
RATES_FILE = "rates.json"
HISTORY_FILE = "rates-history.json"
VERSION = 1

TENOR_IDS = ["DGS1MO", "DGS3MO", "DGS6MO", "DGS1", "DGS2", "DGS3", "DGS5", "DGS7", "DGS10", "DGS20", "DGS30"]
TENORS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
TENOR_YEARS = [0.0833, 0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30]
REAL_IDS = ["DFII5", "DFII7", "DFII10", "DFII20", "DFII30"]
REAL_TENORS = ["5Y", "7Y", "10Y", "20Y", "30Y"]
INFLATION_IDS = ["T5YIE", "T10YIE", "T5YIFR"]
POLICY_IDS = ["DFEDTARU", "DFEDTARL"]
FRED_IDS = TENOR_IDS + REAL_IDS + INFLATION_IDS + POLICY_IDS
HISTORY_IDS = TENOR_IDS + REAL_IDS + INFLATION_IDS + ["DFEDTARU"]
CORE_IDS = ("DGS2", "DGS10", "DGS30")
FRESH = ("csv", "api")
MIN_FRESH_TENORS = 8
MAX_STALE_DAYS = 10
HISTORY_START = "2003-01-01"
DAILY_POINTS = 260
# (key, long, short): spreads are long - short in bp; T10Y2Y / T10Y3M are not fetched.
SPREADS = [
    ("10y2y", "DGS10", "DGS2"),
    ("10y3m", "DGS10", "DGS3MO"),
    ("30y5y", "DGS30", "DGS5"),
    ("30y10y", "DGS30", "DGS10"),
    ("2y3m", "DGS2", "DGS3MO"),
]
# (key, FRED id) for published breakevens; 20Y / 30Y are derived as DGS - DFII.
INFLATION = [("5Y", "T5YIE"), ("10Y", "T10YIE"), ("5y5y", "T5YIFR")]
DERIVED_INFLATION = [("20Y", "DGS20", "DFII20"), ("30Y", "DGS30", "DFII30")]
# One-day |return| above this is a bad point unless the next days confirm the new level.
BAD_POINT_LIMIT = {1: 0.25, 3: 0.60}
# Weekly ETF index value may come from a close up to this many days before the week's sample date.
ETF_WEEKLY_LOOKBACK_DAYS = 7

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
HTTP_TIMEOUT = (10, 30)
FETCH_ATTEMPTS = 2
REQUEST_PAUSE = 0.3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
SOURCE_LABELS = {"csv": "FRED fredgraph.csv", "api": "FRED API"}
ETF_SOURCE = "Yahoo Finance adjusted close"

NOTES = {
    "rates": "殖利率來自 FRED（美國財政部 H.15 每日固定期限殖利率，約美東 15:30 取價）；免金鑰 CSV 優先，失敗才用 FRED API，"
             "都失敗沿用上次檔案的數值並列在 missing。10Y−2Y 等利差由各期限相減，不另抓 T10Y2Y。",
    "curves": "快照日＝目標日當天或之前最近一個 10Y 有值的日子；個別期限缺值最多往回找 5 天，找不到為 null。",
    "spreads": "利差單位 bp。倒掛＝利差小於 0；相隔 10 個交易日內的倒掛視為同一段。invertedDays 為目前這段倒掛的交易日數；"
               "pct5y 為目前值在近 5 年的百分位。倒掛反映市場預期未來降息，不是衰退時間表。",
    "inflation": "5Y、10Y 為 FRED 的平衡通膨率（T5YIE、T10YIE），5y5y 為 5 年後 5 年遠期（T5YIFR）；"
                 "20Y、30Y 為名目減 TIPS 實質殖利率的推算值（derived）。",
    "etf": "ETF 報酬以 Yahoo Finance 還原收盤價（Adj Close，含配息再投入）計算；1 年報酬不年化，3 年以上為年化（CAGR）；"
           "波動度為日對數報酬標準差 ×√252；資料起點晚於區間起點 7 天以上時該區間為 null。",
    "sensitivity": "實證存續期間 durEmp = −100 × beta，beta 為 ETF 日（或週）報酬對 20 年／30 年殖利率變動（百分點）的迴歸斜率，"
                   "只用兩者都有資料的日子（債市休市、股市開市的日子會併入下一段）。H.15 約 15:30 取價、ETF 16:00 收盤，"
                   "日資料的 beta 會偏小，建議以週資料（3 年）為主。TMF 的 durEmp 已含 3 倍槓桿。",
    "model": "模型存續期間與凸性以今日 DGS20／DGS30 線性內插、半年複利計算的等面額債券梯形為代理（TLT 與 TMF＝20–30 年平價附息債；"
             "EDV＝20–30 年零息債；ZROZ、GOVZ＝25–30 年零息債）。凸性單位為「年²」（ΔP/P ≈ −D·Δy + ½·C·Δy²，Δy 用小數）；"
             "iShares 網站公布的凸性（例如 3.6）要 ×100 才是這裡的單位。TMF 的模型值是未槓桿的標的，情境計算時再乘 3。",
    "static": "名稱、指數、成立日、費用率、公布存續期間等靜態資料為手動輸入（staticAsOf），全部尚未核對（verified=false），請以發行商網站為準。",
    "tmf": "TMF 為每日重設的 3 倍槓桿商品：長期報酬不等於 3 倍 TLT，存續期間情境只適用單日的瞬間變動。",
    "disclaimer": "僅供研究與教育用途，不構成投資建議。",
}


class InsufficientData(Exception):
    pass


class FetchError(Exception):
    """A sanitized fetch failure: the message names only the series and a status / exception type."""


def round_or_none(value, digits):
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits) + 0.0


# --------------------------------------------------------------------------- FRED


def parse_fred_csv(text, series_id):
    """[(date, value)] from fredgraph.csv; header 'DATE' or 'observation_date'; '.' / empty are missing."""
    text = (text or "").lstrip("﻿")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError("empty csv")
    header = [cell.strip() for cell in rows[0]]
    if len(header) < 2 or header[0].lower() not in ("date", "observation_date") or header[1] != series_id:
        raise ValueError("unexpected csv header")
    points = {}
    for row in rows[1:]:
        if len(row) < 2:
            continue
        point = _observation(row[0], row[1])
        if point:
            points[point[0]] = point[1]
    if not points:
        raise ValueError("no observations")
    return sorted(points.items())


def parse_fred_api(payload):
    """[(date, value)] from a FRED API series/observations JSON body."""
    observations = payload.get("observations") if isinstance(payload, dict) else None
    if not isinstance(observations, list):
        raise ValueError("no observations field")
    points = {}
    for item in observations:
        if isinstance(item, dict):
            point = _observation(item.get("date"), item.get("value"))
            if point:
                points[point[0]] = point[1]
    if not points:
        raise ValueError("no observations")
    return sorted(points.items())


def _observation(day, raw):
    day = (day or "").strip()
    raw = (raw or "").strip()
    try:
        parsed = date.fromisoformat(day)
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or len(day) != 10:
        return None
    return parsed.isoformat(), value


def default_http_get():
    import logging

    import requests

    # urllib3 warnings can quote the request URL (and with it api_key)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    def http_get(url, params, timeout):
        response = session.get(url, params=params, timeout=timeout)
        return response.status_code, response.text

    return http_get


def make_fetchers(http_get, api_key, sleep=time.sleep):
    """[(origin, fetch(series_id) -> points)] in the order they are tried. fetch raises FetchError."""

    def attempt(series_id, label, call, parse):
        problem = None
        for number in range(1, FETCH_ATTEMPTS + 1):
            sleep(REQUEST_PAUSE if number == 1 else 3 * number)
            try:
                status, body = call()
            except Exception as exc:  # noqa: BLE001 - requests puts the URL (with api_key) in the message
                problem = type(exc).__name__
                print(f"{series_id}: {label} attempt {number} failed ({problem})")
                continue
            if status == 200:
                try:
                    return parse(body)
                except Exception as exc:  # noqa: BLE001 - malformed body; never echo it
                    problem = f"unparseable response ({type(exc).__name__})"
                    print(f"{series_id}: {label} {problem}")
                    break
            problem = f"HTTP {status}"
            print(f"{series_id}: {label} attempt {number} failed ({problem})")
            if status not in (429,) and status < 500:
                break
        raise FetchError(f"{series_id}: {label} {problem}")

    def fetch_csv(series_id):
        params = {"id": series_id, "cosd": HISTORY_START}
        return attempt(
            series_id, "fredgraph.csv", lambda: http_get(FRED_CSV_URL, params, HTTP_TIMEOUT),
            lambda body: parse_fred_csv(body, series_id),
        )

    def fetch_api(series_id):
        params = {"series_id": series_id, "api_key": api_key, "file_type": "json", "observation_start": HISTORY_START}
        return attempt(
            series_id, "FRED API", lambda: http_get(FRED_API_URL, params, HTTP_TIMEOUT),
            lambda body: parse_fred_api(json.loads(body)),
        )

    fetchers = [("csv", fetch_csv)]
    if api_key:
        fetchers.append(("api", fetch_api))
    return fetchers


def load_series(ids, fetchers, previous):
    """({id: points}, {id: "csv"|"api"|"previous"|None}). Every series falls back on its own."""
    series, origin = {}, {}
    for series_id in ids:
        origin[series_id] = None
        for name, fetch in fetchers:
            try:
                series[series_id] = fetch(series_id)
            except FetchError:
                continue
            origin[series_id] = name
            break
        if origin[series_id] is None and previous.get(series_id):
            series[series_id] = list(previous[series_id])
            origin[series_id] = "previous"
            print(f"{series_id}: using values from the previous file")
        elif origin[series_id] is None:
            print(f"{series_id}: no data")
    return series, origin


def series_from_history(history):
    """{id: [(date, value)]} rebuilt from a previous rates-history.json (weekly + daily, daily wins)."""
    out = {}
    if not isinstance(history, dict):
        return out
    merged = {}
    for block in ("weekly", "daily"):
        part = history.get(block) or {}
        dates = part.get("dates") or []
        for series_id, values in (part.get("series") or {}).items():
            target = merged.setdefault(series_id, {})
            for day, value in zip(dates, values or []):
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                    target[day] = float(value)
    for series_id, points in merged.items():
        if points:
            out[series_id] = sorted(points.items())
    return out


# --------------------------------------------------------------------------- ETF prices


def extract_etf_points(frame, symbol):
    """[(date, close, adjClose)] for one symbol from a yfinance frame (either column-level order)."""
    if frame is None or getattr(frame, "empty", True):
        return []
    columns = frame.columns
    try:
        if getattr(columns, "nlevels", 1) > 1:
            if symbol in set(columns.get_level_values(0)):
                close, adj = frame[symbol]["Close"], frame[symbol]["Adj Close"]
            elif symbol in set(columns.get_level_values(1)):
                close, adj = frame["Close"][symbol], frame["Adj Close"][symbol]
            else:
                return []
        else:
            close, adj = frame["Close"], frame["Adj Close"]
    except KeyError:
        return []
    points = []
    for index, raw_close in close.items():
        raw_adj = adj.get(index)
        try:
            c, a = float(raw_close), float(raw_adj)
        except (TypeError, ValueError):
            continue
        if math.isfinite(c) and math.isfinite(a) and c > 0 and a > 0:
            points.append((index.strftime("%Y-%m-%d"), c, a))
    points.sort()
    return points


def download_etf_prices(symbols, downloader=None, sleep=time.sleep):
    """{symbol: [(date, close, adjClose)]}; up to 3 attempts, then one more for symbols still empty."""
    if downloader is None:
        def downloader(batch):
            import yfinance as yf

            return yf.download(
                batch, period="max", interval="1d", auto_adjust=False, actions=False,
                threads=False, progress=False, group_by="ticker", timeout=30,
            )

    prices = {}

    def run(pending):
        frame = None
        for number in range(1, 4):
            try:
                frame = downloader(pending)
                break
            except Exception as exc:  # noqa: BLE001 - network errors vary by library version
                print(f"ETF download attempt {number} failed ({type(exc).__name__})")
                sleep(5 * number)
        for symbol in pending:
            points = extract_etf_points(frame, symbol) if frame is not None else []
            if points:
                prices[symbol] = points

    run(list(symbols))
    missing = [s for s in symbols if s not in prices]
    if missing and len(missing) < len(symbols):
        print(f"retrying ETF prices for {' '.join(missing)}")
        run(missing)
    return prices


def drop_open_session(prices, now):
    """Removes today's bar while the US session may still be open (a delayed run would see a partial bar)."""
    try:
        from zoneinfo import ZoneInfo

        eastern = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001 - no tz database: keep the data as downloaded
        return prices
    if eastern.weekday() >= 5 or (eastern.hour, eastern.minute) >= (16, 15):
        return prices
    today = eastern.date().isoformat()
    return {symbol: (points[:-1] if points and points[-1][0] == today else points) for symbol, points in prices.items()}


# --------------------------------------------------------------------------- payloads


def fresh_series(series, origin):
    """Series usable for the payload: anything fetched now or kept from the previous file, from 2003 on."""
    out = {}
    for series_id, points in series.items():
        if origin.get(series_id) is None or not points:
            continue
        out[series_id] = ra.Series([(d, v) for d, v in points if d >= HISTORY_START])
    return out


def check_rates(series, origin, today):
    """Raises InsufficientData when the core of the payload is not fresh enough to publish."""
    stale_core = [i for i in CORE_IDS if origin.get(i) not in FRESH or not series.get(i)]
    if stale_core:
        raise InsufficientData(f"core series not fresh: {' '.join(stale_core)}")
    fresh_tenors = sum(1 for i in TENOR_IDS if origin.get(i) in FRESH and series.get(i))
    if fresh_tenors < MIN_FRESH_TENORS:
        raise InsufficientData(f"only {fresh_tenors} of {len(TENOR_IDS)} tenors fresh")
    as_of = max(d for d, _ in series["DGS10"])
    age = (today - date.fromisoformat(as_of)).days
    if age > MAX_STALE_DAYS:
        raise InsufficientData(f"DGS10 is {age} days old ({as_of})")
    return as_of


def rates_source(origin):
    used = {origin.get(i) for i in FRED_IDS} & set(FRESH)
    if len(used) == 1:
        return SOURCE_LABELS[used.pop()]
    return "mixed" if used else None


def change_1m(series, day, value, digits=3):
    prior = series.on_or_before(ra.shift_months(day, -1))
    return round_or_none(value - prior[1], digits) if prior else None


def build_curves(data, as_of):
    anchor = data["DGS10"]
    nominal, real = [], []
    for key, target in ra.snapshot_targets(as_of):
        snap = ra.snapshot_curve(data, TENOR_IDS, anchor, target)
        if snap is None:
            continue
        nominal.append({"key": key, **snap})
        real_snap = ra.snapshot_curve(data, REAL_IDS, anchor, target)
        real.append({"key": key, **real_snap})
    return {"nominal": nominal, "real": real}


def build_policy(data):
    upper, lower = data.get("DFEDTARU"), data.get("DFEDTARL")
    if not upper or not lower:
        return None
    day, high = upper.last()
    low = lower.on_or_before(day)
    if low is None:
        return None
    return {"lower": round_or_none(low[1], 3), "upper": round_or_none(high, 3), "date": day}


def build_inflation(data):
    out = {}
    for key, series_id in INFLATION:
        series = data.get(series_id)
        if not series:
            out[key] = {"value": None, "date": None, "chg1m": None}
            continue
        day, value = series.last()
        out[key] = {"value": round_or_none(value, 3), "date": day, "chg1m": change_1m(series, day, value)}
    for key, nominal_id, real_id in DERIVED_INFLATION:
        nominal, real = data.get(nominal_id), data.get(real_id)
        # spread_points is in bp; breakevens stay in %
        points = ra.Series([(d, bp / 100) for d, bp in ra.spread_points(nominal, real)] if nominal and real else [])
        if not points:
            out[key] = {"value": None, "date": None, "chg1m": None, "derived": True}
            continue
        day, value = points.last()
        out[key] = {"value": round_or_none(value, 3), "date": day, "chg1m": change_1m(points, day, value), "derived": True}
    return out


def build_spreads(data):
    out = []
    for key, long_id, short_id in SPREADS:
        entry = {"key": key, "long": long_id, "short": short_id}
        stats = None
        if data.get(long_id) and data.get(short_id):
            stats = ra.spread_stats(ra.spread_points(data[long_id], data[short_id]))
        if stats is None:
            stats = {"valueBp": None, "chg1mBp": None, "inverted": False, "invertedDays": 0,
                     "lastInversion": None, "pct5y": None}
        entry.update(stats)
        out.append(entry)
    return out


def clean_etf_points(symbol, points, leverage):
    """Drops bad adjusted-close points (isolated spikes); prints what was dropped."""
    limit = BAD_POINT_LIMIT.get(leverage, BAD_POINT_LIMIT[3])
    _, dropped = ra.filter_bad_points([(d, a) for d, _, a in points], limit)
    if dropped:
        print(f"{symbol}: dropped {len(dropped)} bad price points ({' '.join(dropped[:5])})")
        bad = set(dropped)
        points = [p for p in points if p[0] not in bad]
    return points


def etf_metrics(points, data):
    """price / returns / risk / sensitivity blocks from [(date, close, adjClose)]."""
    adj = ra.Series([(d, a) for d, _, a in points])
    last_day, close, adj_close = points[-1]
    end = date.fromisoformat(last_day)
    returns = ra.period_returns(adj) or {}
    rounded = {}
    for key in ("1y", "3y", "5y", "10y", "inception"):
        block = returns.get(key)
        rounded[key] = None if block is None else {
            k: (round_or_none(v, 2) if k != "start" else v) for k, v in block.items()
        }
    mdd = ra.max_drawdown(adj)
    mdd5 = ra.max_drawdown(adj, ra.shift_years(end, -5))
    risk = {
        "vol1y": round_or_none(ra.ann_vol(adj, ra.shift_years(end, -1)), 2),
        "vol3y": round_or_none(ra.ann_vol(adj, ra.shift_years(end, -3)), 2),
        "vol5y": round_or_none(ra.ann_vol(adj, ra.shift_years(end, -5)), 2),
        "maxDrawdown": dict(mdd, value=round_or_none(mdd["value"], 2)),
        "mdd5y": round_or_none(mdd5["value"], 2) if mdd5 else None,
        "currentDrawdown": round_or_none(ra.current_drawdown(adj), 2),
    }
    sensitivity = {}
    for key, series_id in (("dgs20", "DGS20"), ("dgs30", "DGS30")):
        yields = data.get(series_id)
        if not yields:
            sensitivity[key] = {"daily1y": None, "weekly3y": None}
            continue
        sensitivity[key] = {
            "daily1y": ra.regress_beta(adj, yields, ra.shift_years(end, -1), "daily"),
            "weekly3y": ra.regress_beta(adj, yields, ra.shift_years(end, -3), "weekly"),
        }
    price = {"asOf": last_day, "close": round_or_none(close, 2), "adjClose": round_or_none(adj_close, 4)}
    return {"price": price, "returns": rounded, "risk": risk, "sensitivity": sensitivity}


def weekly_index(points, dates):
    """Adjusted close on (or up to a week before) each date, rebased so the first value is 100."""
    adj = ra.Series([(d, a) for d, _, a in points])
    raw = []
    for day in dates:
        found = adj.on_or_before(day, ETF_WEEKLY_LOOKBACK_DAYS)
        raw.append(found[1] if found else None)
    base = next((v for v in raw if v is not None), None)
    return [None if v is None else round(v / base * 100, 3) + 0.0 for v in raw]


def build_etfs(prices, etfs, data, previous_rates, previous_history):
    """(etf entries, history etf block, source label, etfAsOf). Missing symbols reuse the previous file."""
    dgs20, dgs30 = data.get("DGS20"), data.get("DGS30")
    y20 = dgs20.last()[1] if dgs20 else None
    y30 = dgs30.last()[1] if dgs30 else None
    old_entries = {e.get("symbol"): e for e in ((previous_rates or {}).get("etfs") or []) if isinstance(e, dict)}
    old_block = (previous_history or {}).get("etf") or {}
    old_sources = (previous_rates or {}).get("sources") or {}

    cleaned = {}
    for etf in etfs:
        points = prices.get(etf["symbol"]) or []
        points = clean_etf_points(etf["symbol"], points, etf["leverage"]) if points else []
        if len(points) >= 2:
            cleaned[etf["symbol"]] = points

    entries = []
    for etf in etfs:
        symbol = etf["symbol"]
        entry = {k: v for k, v in etf.items() if k != "modelProxy"}
        entry["model"] = ra.model_duration_convexity(etf["modelProxy"], y20, y30)
        if symbol in cleaned:
            entry.update(etf_metrics(cleaned[symbol], data))
        else:
            old = old_entries.get(symbol) or {}
            for key in ("price", "returns", "risk", "sensitivity"):
                entry[key] = old.get(key)
            if old:
                print(f"{symbol}: no fresh prices, keeping the previous blocks")
        entries.append(entry)

    if not cleaned:
        if old_block.get("dates"):
            return entries, old_block, old_sources.get("etf"), old_sources.get("etfAsOf")
        return entries, {"dates": [], "series": {e["symbol"]: [] for e in etfs}}, None, None

    old_dates = old_block.get("dates") or []
    old_series = old_block.get("series") or {}
    days = {p[0] for points in cleaned.values() for p in points}
    reused = [etf["symbol"] for etf in etfs if etf["symbol"] not in cleaned]
    if reused:
        # Funds kept from the previous file must keep their whole history even when the fresh ones
        # cover a shorter span (a partial download); their weekly values are matched by ISO week.
        days |= {day for day in old_dates if isinstance(day, str) and ra.valid_iso(day)}
    dates = ra.weekly_sample(sorted(days))
    series = {}
    for etf in etfs:
        symbol = etf["symbol"]
        if symbol in cleaned:
            series[symbol] = weekly_index(cleaned[symbol], dates)
        else:
            by_week = {ra.iso_week(day): value for day, value in zip(old_dates, old_series.get(symbol) or [])
                       if isinstance(day, str) and ra.valid_iso(day)}
            series[symbol] = [by_week.get(ra.iso_week(day)) for day in dates]
    as_of = max(points[-1][0] for points in cleaned.values())
    return entries, {"dates": dates, "series": series}, ETF_SOURCE, as_of


def history_block(data, dates, lookback):
    out = {}
    for series_id in HISTORY_IDS:
        series = data.get(series_id)
        values = []
        for day in dates:
            if series is None:
                values.append(None)
                continue
            if lookback:
                found = series.on_or_before(day, lookback)
                values.append(round_or_none(found[1], 3) if found else None)
            else:
                values.append(round_or_none(series.value_at(day), 3) if series.value_at(day) is not None else None)
        out[series_id] = values
    return {"dates": dates, "series": out}


def build_payloads(series, origin, prices, etfs, previous_rates, previous_history, today, now):
    """Pure: (rates, history) payloads. Raises InsufficientData when the old files must be kept."""
    as_of = check_rates(series, origin, today)
    data = fresh_series(series, origin)
    generated = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    missing = [i for i in FRED_IDS if origin.get(i) not in FRESH]

    entries, etf_history, etf_source, etf_as_of = build_etfs(prices, etfs, data, previous_rates, previous_history)

    rates = {
        "version": VERSION,
        "generatedAt": generated,
        "asOf": as_of,
        "sources": {"rates": rates_source(origin), "etf": etf_source, "etfAsOf": etf_as_of},
        "missing": missing,
        "tenors": TENORS,
        "tenorYears": TENOR_YEARS,
        "realTenors": REAL_TENORS,
        "curves": build_curves(data, as_of),
        "policy": build_policy(data),
        "inflation": build_inflation(data),
        "spreads": build_spreads(data),
        "etfs": entries,
        "notes": NOTES,
    }
    dgs10_dates = data["DGS10"].dates
    history = {
        "version": VERSION,
        "generatedAt": generated,
        "asOf": as_of,
        "weekly": history_block(data, ra.weekly_sample(dgs10_dates), ra.SNAPSHOT_LOOKBACK_DAYS),
        "daily": history_block(data, dgs10_dates[-DAILY_POINTS:], 0),
        "etf": etf_history,
    }
    return rates, history


# --------------------------------------------------------------------------- files and main


def read_json(path):
    try:
        with open(path, encoding="utf-8") as file:
            value = json.load(file)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_atomic(path, value):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as file:
            file.write(body)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return len(body.encode("utf-8"))


def run(out_dir, http_get=None, price_source=None, api_key=None, today=None, now=None, sleep=time.sleep, etfs=None):
    """Fetches everything, builds and writes both files. Returns the process exit code."""
    etfs = ETFS if etfs is None else etfs
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    rates_path = os.path.join(out_dir, RATES_FILE)
    history_path = os.path.join(out_dir, HISTORY_FILE)
    previous_rates = read_json(rates_path)
    previous_history = read_json(history_path)

    fetchers = make_fetchers(http_get or default_http_get(), api_key, sleep)
    series, origin = load_series(FRED_IDS, fetchers, series_from_history(previous_history))
    try:
        check_rates(series, origin, today)
    except InsufficientData as exc:
        print(f"rates.json not updated: {exc}")
        return 1

    symbols = [etf["symbol"] for etf in etfs]
    if price_source is None:
        def price_source(batch):
            return download_etf_prices(batch, sleep=sleep)
    try:
        prices = price_source(symbols) or {}
    except Exception as exc:  # noqa: BLE001 - keep the previous ETF blocks
        print(f"ETF prices unavailable ({type(exc).__name__})")
        prices = {}
    prices = drop_open_session(prices, now)

    try:
        rates, history = build_payloads(series, origin, prices, etfs, previous_rates, previous_history, today, now)
    except InsufficientData as exc:
        print(f"rates.json not updated: {exc}")
        return 1
    # history first: the page loads rates.json first and must never point at an older history file
    size_history = write_atomic(history_path, history)
    size_rates = write_atomic(rates_path, rates)
    print(
        f"wrote {rates_path} ({size_rates} bytes) and {history_path} ({size_history} bytes); "
        f"asOf {rates['asOf']}, rates {rates['sources']['rates']}, missing {' '.join(rates['missing']) or 'none'}, "
        f"etfAsOf {rates['sources']['etfAsOf']}"
    )
    return 0


def main():
    out_dir = os.environ.get("RATES_OUT_DIR") or DEFAULT_OUT_DIR
    api_key = (os.environ.get("FRED_API_KEY") or "").strip() or None
    return run(out_dir, api_key=api_key)


if __name__ == "__main__":
    sys.exit(main())
