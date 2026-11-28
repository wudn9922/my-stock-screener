import json
import os
import time
from datetime import datetime, timezone

import pandas as pd

import screener_config
from index_history import INDEX_HISTORY_CACHE
from report_json import write_json_atomic
from screener_common import clean_ohlcv_dataframe, get_tw_stock_name, round_report_number, safe_float
from screener_config import REPORT_DIR, REPORT_SERIES_DIR
from tpex_otc import TPEX_OTC_NAME, TPEX_OTC_SYMBOL, TPEX_SOURCE_LABEL, get_tpex_otc_full_history
from scripts.output_guard import OutputRejected, finite, require, retain_count
from scripts.report_run_status import record_failure


# =========================================================================
# Atlas 選股篩選器：全市場 universe（docs/report/universe.json）與群組指標
# 計算邏輯在 report_metrics.py；台股基準 ^TWII 優先取 INDEX_HISTORY_CACHE
# =========================================================================
def build_report_universe(
    tw_tickers,
    us_tickers,
    data_dir=screener_config.DATA_DIR,
    downloader=None,
    report_dir=REPORT_DIR
):
    import report_metrics

    tw_cache = (
        INDEX_HISTORY_CACHE.get("^TWII")
        or {}
    ).get("df")

    bench_map = {
        "TW": {
            "symbol": "^TWII",
            "close": report_metrics.load_benchmark(
                "^TWII",
                cache_df=tw_cache,
                downloader=downloader
            )
        },
        "US": {
            "symbol": "SPY",
            "close": report_metrics.load_benchmark(
                "SPY",
                downloader=downloader
            )
        }
    }

    universes = {
        "TW": report_metrics.build_universe(
            tw_tickers,
            "TW",
            data_dir,
            bench_map["TW"],
            name_fn=get_tw_stock_name
        ),
        "US": report_metrics.build_universe(
            us_tickers,
            "US",
            data_dir,
            bench_map["US"]
        )
    }

    # 已有夠新的全市場檔（scripts/build_universe_full.py）時，RS 百分位改用全市場分布
    adopted = report_metrics.adopt_full_rs_distribution(
        universes,
        report_metrics.read_universe_json(report_dir)
    )

    if adopted:
        print(f"📊 RS 百分位改用全市場分布：{', '.join(adopted)}")

    lookup = report_metrics.make_lookup(
        universes,
        data_dir,
        bench_map
    )

    return universes, lookup


def write_report_universe(
    universes,
    report_date,
    report_dir=REPORT_DIR
):
    import report_metrics

    payload = report_metrics.build_universe_payload(
        universes,
        report_date=report_date
    )

    # 既有檔案中夠新的全市場（scope=full）市場保留，不被每日掃描名單覆蓋
    payload, kept = report_metrics.keep_fresh_full_markets(
        payload,
        report_metrics.read_universe_json(report_dir)
    )

    if kept:
        print(f"📊 保留全市場 universe：{', '.join(kept)}")

    return report_metrics.write_universe_json(
        payload,
        report_dir
    )


def build_series_payload(
    symbol,
    df,
    name,
    source,
    interval="1D",
    as_of=None
):
    df = clean_ohlcv_dataframe(df)

    bars = []

    for stamp, row in df.iterrows():
        open_value = safe_float(row["Open"], None)
        high_value = safe_float(row["High"], None)
        low_value = safe_float(row["Low"], None)
        close_value = safe_float(row["Close"], None)

        if (
            None in (
                open_value,
                high_value,
                low_value,
                close_value
            )
            or min(
                open_value,
                high_value,
                low_value,
                close_value
            ) <= 0
        ):
            continue

        volume = safe_float(row["Volume"], 0.0)

        bars.append(
            {
                "time": int(
                    pd.Timestamp(
                        stamp.year,
                        stamp.month,
                        stamp.day,
                        tz="UTC"
                    ).timestamp()
                ),
                "open": round_report_number(
                    open_value,
                    4
                ),
                "high": round_report_number(
                    max(
                        high_value,
                        open_value,
                        close_value
                    ),
                    4
                ),
                "low": round_report_number(
                    min(
                        low_value,
                        open_value,
                        close_value
                    ),
                    4
                ),
                "close": round_report_number(
                    close_value,
                    4
                ),
                "volume": (
                    int(volume)
                    if float(volume).is_integer()
                    else round(volume, 2)
                )
            }
        )

    return {
        "symbol": symbol,
        "name": name,
        "source": source,
        "interval": interval,
        "asOf": int(
            as_of
            if as_of is not None
            else time.time()
        ),
        "bars": bars
    }


def validate_series(payload, previous):
    """Validate the published daily bars, allowing an intentionally rolling start."""
    symbol = payload["symbol"]
    market = "TW" if symbol == TPEX_OTC_SYMBOL or symbol.endswith((".TW", ".TWO")) else "US" if not symbol.startswith("^") else "index"
    label = f"{market} series {symbol}"

    def dates(doc, context):
        bars = doc.get("bars")
        require(isinstance(bars, list) and bool(bars), context, "0 usable bars")
        days = []
        for row in bars:
            require(isinstance(row, dict), context, f"invalid bar ({len(bars)} rows)")
            stamp = row.get("time")
            require(finite(stamp) and int(stamp) == stamp, context, f"invalid timestamp ({len(bars)} rows)")
            try:
                day = datetime.fromtimestamp(stamp, timezone.utc).date()
            except (OverflowError, OSError, ValueError):
                require(False, context, f"invalid timestamp {stamp} ({len(bars)} rows)")
            require(finite(row.get("close")) and row["close"] > 0,
                    context, f"invalid close on {day} ({len(bars)} rows)")
            require(not days or day > days[-1], context,
                    f"duplicate or non-increasing date {day} ({len(bars)} rows)")
            days.append(day)
        return days

    current = dates(payload, f"{label} asOf={payload.get('asOf')}")
    if previous is None:
        return
    require(isinstance(previous, dict) and previous.get("symbol") == symbol,
            label, f"invalid previous series identity ({len(current)} bars through {current[-1]})")
    prior = dates(previous, label + " previous")
    context = f"{label} {current[0]}..{current[-1]}"
    require(current[-1] >= prior[-1], context,
            f"last date regressed from {prior[-1]} ({len(current)}/{len(prior)} bars)")
    expected = {day for day in prior if day >= current[0]}
    retained = len(expected.intersection(current))
    retain_count(retained, len(expected), context + " overlapping prior dates",
                 min_share=.95, minimum=0)


def write_report_series(
    symbol,
    df,
    name,
    source,
    interval="1D",
    series_dir=REPORT_SERIES_DIR,
    as_of=None
):
    """
    寫出 docs/report/series/<SYMBOL>.json（原始代號，例如 ^TWOII.json），
    並更新 series/index.json 的 symbols 清單。
    """
    payload = build_series_payload(
        symbol,
        df,
        name,
        source,
        interval=interval,
        as_of=as_of
    )

    path = os.path.join(
        series_dir,
        f"{symbol}.json"
    )
    try:
        previous = None
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as file:
                    previous = json.load(file)
            except (OSError, ValueError) as exc:
                raise OutputRejected(f"{symbol} series asOf={payload.get('asOf')}: cannot read previous snapshot; previous output kept") from exc
        validate_series(payload, previous)
    except OutputRejected as exc:
        print(f"⚠️ {exc}")
        record_failure(f"series/{symbol}.json", exc)
        return None

    write_json_atomic(path, payload)

    index_path = os.path.join(
        series_dir,
        "index.json"
    )

    symbols = {symbol}

    try:
        with open(
            index_path,
            encoding="utf-8"
        ) as file:
            symbols.update(
                json.load(file).get(
                    "symbols",
                    []
                )
            )
    except (OSError, ValueError, AttributeError):
        pass

    symbols = sorted(
        value
        for value in symbols
        if isinstance(value, str)
        and os.path.exists(
            os.path.join(
                series_dir,
                f"{value}.json"
            )
        )
    )

    write_json_atomic(
        index_path,
        {"symbols": symbols}
    )

    print(
        f"✅ series JSON 已產生：{path}"
        f"（{len(payload['bars'])} 根）"
    )

    return path


def write_tpex_otc_series():
    history = get_tpex_otc_full_history()

    # Empty history must also reach the per-symbol gate/status path.
    return write_report_series(
        TPEX_OTC_SYMBOL,
        history,
        TPEX_OTC_NAME,
        TPEX_SOURCE_LABEL
    )
