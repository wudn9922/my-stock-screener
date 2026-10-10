"""
Atlas 選股篩選器指標（F2）：由 data/*.csv 日 K 計算報酬、均線乖離、52 週高點、相對強弱與量比，
供 docs/report/latest.json（groups[].items[].metrics）與 docs/report/universe.json 使用。

- 不 import main；yfinance 只在下載基準指數時才延遲 import。
- 資料不足的欄位一律為 None（JSON null），絕不輸出 NaN / inf。
- 百分比單位為 %，四捨五入到小數 2 位。
"""
import bisect
import json
import math
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone

import pandas as pd

UNIVERSE_VERSION = 1
UNIVERSE_FILE = "universe.json"

UNIVERSE_FIELDS = [
    "symbol",
    "name",
    "close",
    "changePct",
    "volume",
    "r5",
    "r21",
    "r63",
    "ma20",
    "ma50",
    "ma200",
    "ma20Slope5",
    "trend",
    "hi52",
    "fromHi52",
    "hiBars",
    "rs21",
    "rs63",
    "rsRank",
    "volRatio",
    "turnover20",
]

MARKET_BENCHMARKS = {
    "TW": "^TWII",
    "US": "SPY"
}

UNIVERSE_LABELS = {
    "TW": "台股流動性名單（當日成交≥100萬股）",
    "US": "S&P 500"
}

RETURN_PERIODS = (5, 21, 63)
MA_WINDOWS = (20, 50, 200)
SLOPE_BARS = 5
HI52_BARS = 252
VOLUME_AVG_BARS = 20
TURNOVER_BARS = 20

# 基準指數最後一根 K 比個股最後一根早超過此天數，相對強弱給 None
BENCH_STALE_DAYS = 7
# 基準指數快取少於此根數時改為下載
BENCH_MIN_BARS = 64
BENCH_DOWNLOAD_PERIOD = "2y"

# universe 內最後一根 K 早於市場 asOf 超過此天數視為過期，不列入
UNIVERSE_STALE_DAYS = 7
# 有 rs63 的股票少於此數時不計算 RS 百分位
RS_RANK_MIN_COUNT = 10

PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


# =========================================================================
# 共用工具
# =========================================================================
def finite_or_none(value):
    if value is None or isinstance(value, bool):
        return None

    try:
        value = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(value):
        return None

    return value


def round_or_none(value, digits=2):
    value = finite_or_none(value)

    if value is None:
        return None

    value = round(value, digits)

    # 避免 JSON 出現 -0.0
    return value + 0.0 if value != 0 else 0.0


def pct_change(current, base):
    current = finite_or_none(current)
    base = finite_or_none(base)

    if current is None or base is None or base <= 0:
        return None

    return (current / base - 1) * 100


def market_of(symbol):
    symbol = str(symbol or "").strip().upper()

    if symbol.endswith(".TW") or symbol.endswith(".TWO"):
        return "TW"

    return "US"


def clean_prices(df):
    """OHLCV 清理：數值化、去掉非有限值或收盤 <= 0 的列、日期去重排序。"""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()

    if not all(column in df.columns for column in PRICE_COLUMNS):
        return pd.DataFrame()

    result = df[PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    result = result.replace([float("inf"), float("-inf")], float("nan"))
    result = result.dropna(subset=["Open", "High", "Low", "Close"])
    result = result[result["Close"] > 0]
    result["Volume"] = result["Volume"].fillna(0).clip(lower=0)

    index = pd.to_datetime(result.index, errors="coerce")

    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)

    result.index = index
    result = result[~result.index.isna()]
    result = result[~result.index.duplicated(keep="last")].sort_index()

    return result


def read_price_csv(path):
    if not os.path.exists(path):
        return pd.DataFrame()

    try:
        return clean_prices(pd.read_csv(path, index_col=0, parse_dates=True))
    except Exception as exc:
        print(f"⚠️ {os.path.basename(path)} 讀取失敗：{type(exc).__name__}: {exc}")
        return pd.DataFrame()


def clean_close_series(series):
    if series is None:
        return None

    series = pd.to_numeric(pd.Series(series), errors="coerce")
    series = series.replace([float("inf"), float("-inf")], float("nan")).dropna()
    series = series[series > 0]

    index = pd.to_datetime(series.index, errors="coerce")

    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)

    series.index = index
    series = series[~series.index.isna()]
    series = series[~series.index.duplicated(keep="last")].sort_index()

    return series.astype(float) if not series.empty else None


# =========================================================================
# 個股指標
# =========================================================================
def _bench_return(bench_close, start_date, end_date):
    """基準在 [start_date, end_date] 的報酬 %（各取當日或之前最近一根）。"""
    if bench_close is None or len(bench_close) == 0:
        return None

    end_slice = bench_close.loc[:end_date]
    start_slice = bench_close.loc[:start_date]

    if end_slice.empty or start_slice.empty:
        return None

    if (end_date - end_slice.index[-1]).days > BENCH_STALE_DAYS:
        return None

    # 基準在起點之前沒有資料（只有 start 當天之前很久的點）也視為不足
    if (start_date - start_slice.index[-1]).days > BENCH_STALE_DAYS:
        return None

    return pct_change(end_slice.iloc[-1], start_slice.iloc[-1])


def _relative_strength(stock_return, bench_return):
    if stock_return is None or bench_return is None:
        return None

    denominator = 1 + bench_return / 100

    if denominator <= 0:
        return None

    return ((1 + stock_return / 100) / denominator - 1) * 100


def compute_stock_metrics(df, bench_close=None, bench_symbol="SPY"):
    """
    df：OHLCV 日 K（DatetimeIndex）；bench_close：基準收盤 Series 或 None。
    回傳 metrics dict（資料契約見 DESIGN 第 1 節），沒有任何有效 K 線時回傳 None。
    rsRank 由 assign_rs_rank / make_lookup 另外填入。
    """
    df = clean_prices(df)

    if df.empty:
        return None

    bench_close = clean_close_series(bench_close)

    close = df["Close"].to_numpy(dtype=float)
    high = df["High"].to_numpy(dtype=float)
    volume = df["Volume"].to_numpy(dtype=float)
    dates = df.index
    bars = len(close)
    last = close[-1]

    returns = {}

    for period in RETURN_PERIODS:
        returns[period] = (
            pct_change(last, close[-1 - period])
            if bars > period
            else None
        )

    sma = {}

    for window in MA_WINDOWS:
        sma[window] = (
            float(close[-window:].mean())
            if bars >= window
            else None
        )

    ma_deviation = {
        str(window): round_or_none(pct_change(last, sma[window]))
        for window in MA_WINDOWS
    }

    # MA20 對 5 根前的變化
    slope = None

    if bars >= 20 + SLOPE_BARS:
        previous_ma20 = float(close[-20 - SLOPE_BARS:-SLOPE_BARS].mean())
        slope = pct_change(sma[20], previous_ma20)

    trend = None

    if sma[50] is not None and slope is not None:
        if last > sma[20] > sma[50] and slope > 0:
            trend = "up"
        elif last < sma[20] < sma[50] and slope < 0:
            trend = "down"
        else:
            trend = "mixed"

    hi_bars = min(bars, HI52_BARS)
    hi52 = max(float(high[-hi_bars:].max()), float(close[-hi_bars:].max()))
    from_hi52 = pct_change(last, hi52)

    if from_hi52 is not None:
        from_hi52 = min(from_hi52, 0.0)

    relative = {}

    for period in (21, 63):
        bench_return = None

        if bars > period:
            bench_return = _bench_return(
                bench_close,
                dates[-1 - period],
                dates[-1]
            )

        relative[period] = _relative_strength(returns[period], bench_return)

    vol_ratio = None

    if bars > VOLUME_AVG_BARS:
        previous_volume = float(volume[-1 - VOLUME_AVG_BARS:-1].mean())

        if previous_volume > 0:
            vol_ratio = volume[-1] / previous_volume

    turnover = None

    if bars >= TURNOVER_BARS:
        turnover = finite_or_none(
            (close[-TURNOVER_BARS:] * volume[-TURNOVER_BARS:]).mean()
        )

        if turnover is not None:
            turnover = int(round(turnover))

    return {
        "bars": int(bars),
        "r5": round_or_none(returns[5]),
        "r21": round_or_none(returns[21]),
        "r63": round_or_none(returns[63]),
        "ma": ma_deviation,
        "ma20Slope5": round_or_none(slope),
        "trend": trend,
        "hi52": round_or_none(hi52),
        "fromHi52": round_or_none(from_hi52),
        "hiBars": int(hi_bars),
        "rs21": round_or_none(relative[21]),
        "rs63": round_or_none(relative[63]),
        "rsBench": bench_symbol,
        "rsRank": None,
        "volRatio": round_or_none(vol_ratio),
        "turnover20": turnover
    }


# =========================================================================
# RS 百分位
# =========================================================================
def rs_rank_from_distribution(value, distribution):
    """value 在已排序的 rs63 分布中的百分位（1–99）；分布太小回傳 None。"""
    value = finite_or_none(value)

    if value is None or len(distribution) < RS_RANK_MIN_COUNT:
        return None

    below = bisect.bisect_left(distribution, value)
    equal = bisect.bisect_right(distribution, value) - below
    percentile = (below + 0.5 * equal) / len(distribution) * 100

    return int(min(99, max(1, round(percentile))))


def assign_rs_rank(rows):
    """
    rows：含 rs63 的 dict（metrics）。就地填入 rsRank，回傳排序後的 rs63 分布，
    供不在 universe 內的自選股以二分搜尋對照。
    """
    distribution = sorted(
        value
        for value in (finite_or_none(row.get("rs63")) for row in rows)
        if value is not None
    )

    for row in rows:
        row["rsRank"] = rs_rank_from_distribution(row.get("rs63"), distribution)

    return distribution


# =========================================================================
# 基準指數
# =========================================================================
def _extract_close(data, symbol):
    if data is None:
        return None

    if isinstance(data, pd.Series):
        return clean_close_series(data)

    if not isinstance(data, pd.DataFrame) or data.empty:
        return None

    if isinstance(data.columns, pd.MultiIndex):
        for level in range(data.columns.nlevels):
            if "Close" in data.columns.get_level_values(level):
                close = data.xs("Close", level=level, axis=1)

                if isinstance(close, pd.DataFrame):
                    if symbol in close.columns:
                        close = close[symbol]
                    elif close.shape[1] >= 1:
                        close = close.iloc[:, 0]
                    else:
                        return None

                return clean_close_series(close)

        return None

    if "Close" not in data.columns:
        return None

    return clean_close_series(data["Close"])


def download_benchmark(symbol):
    import yfinance as yf

    return yf.download(
        symbol,
        period=BENCH_DOWNLOAD_PERIOD,
        interval="1d",
        progress=False,
        threads=False,
        auto_adjust=False,
        actions=False,
        group_by="column",
        timeout=30
    )


def load_benchmark(symbol, cache_df=None, downloader=None):
    """
    回傳基準收盤 Series；cache_df（例如 INDEX_HISTORY_CACHE 的 ^TWII）夠長時直接使用，
    否則下載 2 年日 K。失敗回傳 None（相對強弱與 RS 百分位會是 null）。
    """
    cached = _extract_close(cache_df, symbol)

    if cached is not None and len(cached) >= BENCH_MIN_BARS:
        return cached

    try:
        downloaded = (downloader or download_benchmark)(symbol)
        close = _extract_close(downloaded, symbol)
    except Exception as exc:
        print(f"⚠️ 基準 {symbol} 下載失敗：{type(exc).__name__}: {exc}")
        return None

    if close is None:
        print(f"⚠️ 基準 {symbol} 沒有可用資料")
        return None

    return close


# =========================================================================
# 全市場 universe
# =========================================================================
def _bench_parts(bench, market):
    bench = bench or {}
    symbol = bench.get("symbol") or MARKET_BENCHMARKS.get(market, "SPY")
    return symbol, bench.get("close")


def build_stock_record(symbol, df, bench, market, name=""):
    """單一股票的 universe 紀錄；資料不足回傳 None。"""
    df = clean_prices(df)

    if df.empty:
        return None

    bench_symbol, bench_close = _bench_parts(bench, market)
    metrics = compute_stock_metrics(df, bench_close, bench_symbol)

    if metrics is None:
        return None

    close = df["Close"]
    change_pct = pct_change(close.iloc[-1], close.iloc[-2]) if len(close) >= 2 else None

    return {
        "symbol": symbol,
        "name": name or "",
        "close": round_or_none(close.iloc[-1]),
        "changePct": round_or_none(change_pct),
        "volume": int(df["Volume"].iloc[-1]),
        "asOf": df.index[-1].strftime("%Y-%m-%d"),
        "metrics": metrics
    }


def build_universe(tickers, market, data_dir, bench, name_fn=None):
    """
    tickers 的 CSV 全部計算指標；過期（最後一根 K 早於市場眾數日期 7 天以上）與讀不到的略過。
    bench：{"symbol": "^TWII", "close": Series 或 None}。
    回傳 {"market","benchmark","asOf","universe","records","rsDistribution"}。
    """
    market = str(market).upper()
    bench_symbol, _ = _bench_parts(bench, market)
    records = []
    seen = set()

    for ticker in tickers or []:
        symbol = str(ticker or "").strip().upper()

        if not symbol or symbol in seen:
            continue

        seen.add(symbol)

        try:
            df = read_price_csv(os.path.join(data_dir, f"{symbol}.csv"))

            if df.empty:
                continue

            name = name_fn(symbol) if name_fn else ""
            record = build_stock_record(symbol, df, bench, market, name)

            if record is not None:
                records.append(record)

        except Exception as exc:
            print(f"⚠️ {symbol} universe 指標失敗：{type(exc).__name__}: {exc}")

    as_of = None

    if records:
        as_of = Counter(record["asOf"] for record in records).most_common(1)[0][0]
        cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=UNIVERSE_STALE_DAYS)
        records = [
            record
            for record in records
            if pd.Timestamp(record["asOf"]) >= cutoff
        ]

    distribution = assign_rs_rank([record["metrics"] for record in records])

    print(
        f"📊 {market} universe：{len(records)} 檔"
        f"（基準 {bench_symbol}，asOf {as_of}）"
    )

    return {
        "market": market,
        "benchmark": bench_symbol,
        "asOf": as_of,
        "universe": UNIVERSE_LABELS.get(market, market),
        "records": records,
        "rsDistribution": distribution
    }


def make_lookup(universe, data_dir, bench_map):
    """
    回傳 lookup(symbol) → metrics 或 None。
    universe 內的股票直接取用；不在 universe 的自選股即時讀 CSV 計算，
    rsRank 以二分搜尋對照同市場 universe 的 rs63 分布。
    """
    universe = universe or {}
    bench_map = bench_map or {}
    known = {}

    for market_universe in universe.values():
        for record in (market_universe or {}).get("records") or []:
            known[record["symbol"]] = record["metrics"]

    cache = {}

    def lookup(symbol):
        symbol = str(symbol or "").strip().upper()

        if not symbol:
            return None

        if symbol in known:
            return known[symbol]

        if symbol in cache:
            return cache[symbol]

        market = market_of(symbol)
        bench_symbol, bench_close = _bench_parts(bench_map.get(market), market)
        metrics = None

        try:
            df = read_price_csv(os.path.join(data_dir, f"{symbol}.csv"))
            metrics = compute_stock_metrics(df, bench_close, bench_symbol)

            if metrics is not None:
                distribution = (universe.get(market) or {}).get("rsDistribution") or []
                metrics["rsRank"] = rs_rank_from_distribution(metrics["rs63"], distribution)

        except Exception as exc:
            print(f"⚠️ {symbol} 指標計算失敗：{type(exc).__name__}: {exc}")
            metrics = None

        cache[symbol] = metrics
        return metrics

    return lookup


def record_to_row(record):
    metrics = record["metrics"]
    values = {
        "symbol": record["symbol"],
        "name": record["name"],
        "close": record["close"],
        "changePct": record["changePct"],
        "volume": record["volume"],
        "ma20": metrics["ma"]["20"],
        "ma50": metrics["ma"]["50"],
        "ma200": metrics["ma"]["200"],
    }

    return [
        values[field] if field in values else metrics.get(field)
        for field in UNIVERSE_FIELDS
    ]


def build_universe_payload(universes, generated_at=None, report_date=None):
    generated_at = generated_at or (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    markets = {}

    for market, bench_symbol in MARKET_BENCHMARKS.items():
        market_universe = (universes or {}).get(market) or {}
        markets[market] = {
            "benchmark": market_universe.get("benchmark") or bench_symbol,
            "asOf": market_universe.get("asOf"),
            "universe": UNIVERSE_LABELS[market],
            "rows": [
                record_to_row(record)
                for record in market_universe.get("records") or []
            ]
        }

    return {
        "version": UNIVERSE_VERSION,
        "generatedAt": generated_at,
        "reportDate": report_date,
        "fields": list(UNIVERSE_FIELDS),
        "markets": markets
    }


def _clean_json(value):
    if isinstance(value, dict):
        return {key: _clean_json(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_clean_json(item) for item in value]

    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value

    if isinstance(value, int):
        return value

    if hasattr(value, "item"):
        value = value.item()

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    return value


def write_universe_json(payload, report_dir):
    """原子寫入 <report_dir>/universe.json（嚴格 JSON：NaN / inf 轉為 null）。"""
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, UNIVERSE_FILE)
    file_descriptor, tmp_path = tempfile.mkstemp(
        prefix=".tmp-",
        suffix=".json",
        dir=report_dir
    )

    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
            json.dump(
                _clean_json(payload),
                file,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":")
            )

        os.replace(tmp_path, path)

    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    print(
        f"✅ 全市場 universe JSON 已產生：{path}"
        f"（{os.path.getsize(path) / 1024:,.1f} KB）"
    )

    return path
