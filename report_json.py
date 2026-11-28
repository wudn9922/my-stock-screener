import json
import os
import re
import tempfile
from datetime import datetime, timezone

import pandas as pd

from breakout_alert.account_permissions import is_public_report_owner
from group_config import get_ma_list_from_item, normalize_group_name
from index_history import INDEX_HISTORY_CACHE
from index_trend import analyze_index_trend
from screener_common import clean_json_value, get_tw_stock_name, safe_float, safe_int
from screener_config import REPORT_DIR, REPORT_JSON_VERSION
from sector_weekly import SECTOR_WEEKLY_MA_LIST
from scripts.output_guard import finite, require, retain_count, unique_keys


# =========================================================================
# Atlas 報告 JSON：docs/report/latest.json 與 docs/report/series/*.json
# =========================================================================

# LINE 訊息的「亞洲市場」在 JSON 拆成日本、韓國
REPORT_MARKETS = [
    {
        "key": "tw",
        "name": "台灣",
        "flag": "🇹🇼"
    },
    {
        "key": "jp",
        "name": "日本",
        "flag": "🇯🇵"
    },
    {
        "key": "kr",
        "name": "韓國",
        "flag": "🇰🇷"
    },
    {
        "key": "eu",
        "name": "歐洲",
        "flag": "🇪🇺"
    },
    {
        "key": "us",
        "name": "美國",
        "flag": "🇺🇸"
    }
]

# 只出現在 JSON 的世界指數（不加入 LINE 訊息）
REPORT_EXTRA_INDEX_MA_LIST = [20, 60]

WORLD_EXTRA_INDEX_CONFIGS = [
    {
        "ticker": "^HSI",
        "name": "香港恆生指數",
        "market": "hk"
    },
    {
        "ticker": "000001.SS",
        "name": "上證綜合指數",
        "market": "cn"
    },
    {
        "ticker": "^BSESN",
        "name": "印度孟買SENSEX",
        "market": "in"
    },
    {
        "ticker": "^AXJO",
        "name": "澳洲ASX 200",
        "market": "au"
    },
    {
        "ticker": "^STOXX50E",
        "name": "歐洲斯托克50",
        "market": "eu"
    },
    {
        "ticker": "^GSPTSE",
        "name": "加拿大S&P/TSX",
        "market": "ca"
    },
    {
        "ticker": "^BVSP",
        "name": "巴西聖保羅指數",
        "market": "br"
    }
]

# LINE 指數清單中，Supabase 未啟用時在 worldIndices 顯示的名稱
REPORT_CORE_INDEX_NAMES = {
    "^TWII": "台灣加權指數",
    "^TWOII": "台灣櫃買指數(OTC)",
    "^GSPC": "美國標普500",
    "^DJI": "美國道瓊工業",
    "^IXIC": "美國那斯達克",
    "^RUT": "美國羅素2000",
    "^SOX": "美國費城半導體",
    "^FCHI": "法國CAC40",
    "^FTSE": "英國富時100",
    "^GDAXI": "德國DAX",
    "^N225": "日經225",
    "^KS11": "韓國綜合指數"
}

REPORT_SCAN_GROUP_NAMES = {
    "tw_all": "台股-全市場潛伏",
    "us_all": "美股-全市場潛伏"
}

# 趨勢圖示 → bull / bear / neutral
# 🔺 多頭趨勢中的多頭走勢、🔻 空頭趨勢中的空頭走勢、
# 💡 多頭趨勢中的空頭走勢（回檔）、⚡ 空頭趨勢中的多頭走勢（反彈）
INDEX_TREND_ICON_MAP = {
    "🔺": "bull",
    "🟢": "bull",
    "📈": "bull",
    "🔻": "bear",
    "🔴": "bear",
    "📉": "bear",
    "💡": "neutral",
    "⚡": "neutral",
    "🟡": "neutral"
}


from screener_common import round_report_number


def parse_index_trend_text(text):
    """
    從 analyze_index_trend 的 LINE 文字讀出結構化欄位，不重算趨勢。
    例：
        🔺 台灣加權指數
           ├ 均線: 看多 (3/3MA - 23/29/62)
           └  多頭趨勢中的多頭走勢
    失敗時的單行文字（⚪ 名稱: 數據不足無法分析）→ trend=unknown。
    """
    text = str(text or "").strip()

    result = {
        "trend": "unknown",
        "trendLabel": "",
        "score": None,
        "scoreMax": None,
        "scoreLabel": None,
        "macroTrend": None,
        "microTrend": None
    }

    if not text:
        return result

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    first_line = lines[0]
    icon = first_line.split(" ", 1)[0]

    if len(lines) == 1:
        message = (
            first_line.split(":", 1)[1].strip()
            if ":" in first_line
            else first_line
        )

        result["trendLabel"] = (
            f"{icon} {message}".strip()
        )
        return result

    status_text = re.sub(
        r"^[├└│\s]+",
        "",
        lines[-1]
    ).strip()

    result["trendLabel"] = (
        f"{icon} {status_text}".strip()
    )

    score_match = re.search(
        r"均線[:：]\s*(\S+)\s*\((-?\d+)\s*/\s*(\d+)\s*MA",
        text
    )

    if score_match:
        result["scoreLabel"] = score_match.group(1)
        result["score"] = int(score_match.group(2))
        result["scoreMax"] = int(score_match.group(3))

    phase_match = re.search(
        r"(多頭|空頭)趨勢中的(多頭|空頭)走勢",
        text
    )

    if phase_match:
        result["macroTrend"] = (
            "bull"
            if phase_match.group(1) == "多頭"
            else "bear"
        )
        result["microTrend"] = (
            "bull"
            if phase_match.group(2) == "多頭"
            else "bear"
        )

    if icon in INDEX_TREND_ICON_MAP:
        result["trend"] = INDEX_TREND_ICON_MAP[icon]

    elif result["macroTrend"]:
        result["trend"] = (
            result["macroTrend"]
            if result["macroTrend"]
            == result["microTrend"]
            else "neutral"
        )

    elif "多頭" in status_text and "空頭" not in status_text:
        result["trend"] = "bull"

    elif "空頭" in status_text and "多頭" not in status_text:
        result["trend"] = "bear"

    else:
        result["trend"] = "neutral"

    return result


def build_index_snapshot(ticker, ma_list):
    """從 INDEX_HISTORY_CACHE 讀出收盤、漲跌幅、均線值與日期。"""
    cached = INDEX_HISTORY_CACHE.get(
        str(ticker).strip().upper()
    ) or {}

    df = cached.get("df")

    snapshot = {
        "close": None,
        "changePct": None,
        "maValues": {},
        "asOf": None,
        "source": cached.get("source")
    }

    if df is None or df.empty:
        return snapshot

    close_series = df["Close"].dropna()

    if close_series.empty:
        return snapshot

    latest_close = safe_float(
        close_series.iloc[-1],
        None
    )

    snapshot["close"] = round_report_number(
        latest_close
    )

    snapshot["asOf"] = (
        pd.Timestamp(
            close_series.index[-1]
        ).strftime("%Y-%m-%d")
    )

    if len(close_series) >= 2:
        previous_close = safe_float(
            close_series.iloc[-2],
            None
        )

        if previous_close:
            snapshot["changePct"] = round(
                (latest_close / previous_close - 1)
                * 100,
                2
            )

    for ma_window in ma_list:
        if len(close_series) < ma_window:
            continue

        snapshot["maValues"][
            str(ma_window)
        ] = round_report_number(
            close_series.tail(ma_window).mean()
        )

    return snapshot


def build_index_status(
    ticker,
    name,
    market,
    ma_list,
    line_text,
    configured=True
):
    ma_list = [
        int(value)
        for value in ma_list
    ]

    snapshot = build_index_snapshot(
        ticker,
        ma_list
    )

    parsed = parse_index_trend_text(
        line_text
    )

    return {
        "symbol": ticker,
        "name": name,
        "market": market,
        "maList": ma_list,
        "close": snapshot["close"],
        "changePct": snapshot["changePct"],
        "trend": parsed["trend"],
        "trendLabel": parsed["trendLabel"],
        "score": parsed["score"],
        "scoreMax": parsed["scoreMax"],
        "scoreLabel": parsed["scoreLabel"],
        "macroTrend": parsed["macroTrend"],
        "microTrend": parsed["microTrend"],
        "maValues": snapshot["maValues"],
        "asOf": snapshot["asOf"],
        "source": snapshot["source"],
        "configured": bool(configured),
        "lineText": str(line_text or "")
    }


def analyze_index_for_report(
    ticker,
    name,
    ma_list
):
    """JSON 專用的額外指數分析；任何錯誤只影響該指數。"""
    try:
        return analyze_index_trend(
            ticker,
            name,
            ma_list
        )
    except Exception as exc:
        print(
            f"⚠️ {ticker} 報告指數分析失敗："
            f"{type(exc).__name__}: {exc}"
        )
        return f"⚪ {name}: 分析發生異常"


def build_report_indices(
    index_map,
    market_tickers,
    captured_index_lines,
    market_line_texts,
    extra_index_configs=None,
    analyze=analyze_index_for_report
):
    """
    回傳 (markets, world_indices)。
    markets 與 LINE 指數訊息一致（只含 Supabase 啟用的指數）；
    world_indices 另外加上：其他已啟用的 index_configs、
    LINE 清單中未啟用的指數（configured=false，預設 MA）、
    以及 WORLD_EXTRA_INDEX_CONFIGS。
    """
    if extra_index_configs is None:
        extra_index_configs = (
            WORLD_EXTRA_INDEX_CONFIGS
        )

    markets = []
    world_indices = []
    seen = set()

    for market_meta in REPORT_MARKETS:
        market_key = market_meta["key"]
        statuses = []

        for raw_ticker in market_tickers.get(
            market_key,
            []
        ):
            ticker = str(raw_ticker).strip().upper()
            item = index_map.get(ticker)

            if not item:
                continue

            name = item.get("name", ticker)
            ma_list = get_ma_list_from_item(item)

            line_text = captured_index_lines.get(
                ticker
            )

            if line_text is None:
                line_text = analyze(
                    ticker,
                    name,
                    ma_list
                )

            try:
                status = build_index_status(
                    ticker,
                    name,
                    market_key,
                    ma_list,
                    line_text
                )
            except Exception as exc:
                print(
                    f"⚠️ {ticker} 報告指數整理失敗："
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            statuses.append(status)
            world_indices.append(status)
            seen.add(ticker)

        markets.append(
            {
                "key": market_key,
                "name": market_meta["name"],
                "flag": market_meta["flag"],
                "lineText": str(
                    market_line_texts.get(
                        market_key,
                        ""
                    )
                ),
                "indices": statuses
            }
        )

    ticker_market = {
        str(ticker).strip().upper(): market_key
        for market_key, tickers in (
            market_tickers.items()
        )
        for ticker in tickers
    }

    pending = []

    # 其他已啟用、但不在 LINE 清單的 index_configs
    for ticker, item in index_map.items():
        if ticker in seen:
            continue

        pending.append(
            (
                ticker,
                item.get("name", ticker),
                ticker_market.get(
                    ticker,
                    "world"
                ),
                get_ma_list_from_item(item),
                True
            )
        )

    # LINE 清單中 Supabase 未啟用的指數（只供比較）
    for ticker, market_key in ticker_market.items():
        if ticker in seen or ticker in index_map:
            continue

        pending.append(
            (
                ticker,
                REPORT_CORE_INDEX_NAMES.get(
                    ticker,
                    ticker
                ),
                market_key,
                list(REPORT_EXTRA_INDEX_MA_LIST),
                False
            )
        )

    for config in extra_index_configs:
        ticker = str(
            config.get("ticker", "")
        ).strip().upper()

        if not ticker:
            continue

        pending.append(
            (
                ticker,
                config.get("name", ticker),
                config.get("market", "world"),
                list(
                    config.get("ma_list")
                    or REPORT_EXTRA_INDEX_MA_LIST
                ),
                False
            )
        )

    for (
        ticker,
        name,
        market_key,
        ma_list,
        configured
    ) in pending:
        if ticker in seen:
            continue

        seen.add(ticker)

        try:
            line_text = analyze(
                ticker,
                name,
                ma_list
            )

            world_indices.append(
                build_index_status(
                    ticker,
                    name,
                    market_key,
                    ma_list,
                    line_text,
                    configured=configured
                )
            )

        except Exception as exc:
            print(
                f"⚠️ {ticker} 世界指數失敗："
                f"{type(exc).__name__}: {exc}"
            )

    return markets, world_indices


def _chart_last_ma_values(chart_data):
    candles = chart_data.get("candles") or []

    if not candles:
        return {}

    last_time = candles[-1].get("time")
    values = {}

    for ma_config in (
        chart_data.get("moving_averages") or []
    ):
        points = ma_config.get("data") or []

        if (
            not points
            or points[-1].get("time") != last_time
        ):
            continue

        window = safe_int(
            ma_config.get("window")
        )

        if window is None:
            continue

        values[str(window)] = round_report_number(
            points[-1].get("value")
        )

    return values


def _chart_close_change(chart_data):
    candles = chart_data.get("candles") or []

    close_value = safe_float(
        chart_data.get("latest_close"),
        None
    )

    if close_value is None and candles:
        close_value = safe_float(
            candles[-1].get("close"),
            None
        )

    change_pct = None

    if len(candles) >= 2 and close_value is not None:
        previous_close = safe_float(
            candles[-2].get("close"),
            None
        )

        if previous_close:
            change_pct = round(
                (close_value / previous_close - 1)
                * 100,
                2
            )

    as_of = (
        candles[-1].get("time")
        if candles
        else None
    )

    return (
        round_report_number(close_value),
        change_pct,
        as_of
    )


def _strip_title_parentheses(text):
    text = str(text or "").strip()

    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()

    return text


def build_report_group_item(item, market, metrics_lookup=None):
    chart_data = item.get("chart_data") or {}

    symbol = str(
        item.get("ticker")
        or chart_data.get("ticker")
        or ""
    ).strip().upper()

    name = str(
        item.get("name")
        or chart_data.get("display_name")
        or ""
    ).strip()

    if not name and (
        symbol.endswith(".TW")
        or symbol.endswith(".TWO")
    ):
        name = get_tw_stock_name(symbol)

    ma_list = [
        int(value)
        for value in (
            item.get("ma_list")
            or [
                ma_config.get("window")
                for ma_config in (
                    chart_data.get(
                        "moving_averages"
                    )
                    or []
                )
                if safe_int(
                    ma_config.get("window")
                )
            ]
            or [20]
        )
    ]

    (
        close_value,
        change_pct,
        as_of
    ) = _chart_close_change(chart_data)

    report_item = {
        "symbol": symbol,
        "name": name,
        "maList": ma_list,
        "close": close_value,
        "changePct": change_pct,
        "maValues": _chart_last_ma_values(
            chart_data
        ),
        "note": _strip_title_parentheses(
            chart_data.get("title_suffix")
        ),
        "volume": int(
            safe_float(item.get("volume"), 0)
        ),
        "asOf": as_of
    }

    # 選股篩選器指標（report_metrics.py）；沒有 lookup 時輸出維持原樣
    if metrics_lookup is not None:
        try:
            report_item["metrics"] = metrics_lookup(
                symbol
            )
        except Exception as exc:
            print(
                f"⚠️ {symbol} 篩選指標略過："
                f"{type(exc).__name__}: {exc}"
            )
            report_item["metrics"] = None

    return report_item


def build_report_unmatched_item(record, metrics_lookup=None):
    """
    未成功股票的報告項目（status = unmatched／failed）。
    價格欄位已在 process_custom_groups 算好；failed 沒有價格也不查指標。
    """
    symbol = str(
        record.get("ticker") or ""
    ).strip().upper()

    status = (
        "failed"
        if record.get("status") == "failed"
        else "unmatched"
    )

    report_item = {
        "symbol": symbol,
        "name": str(record.get("name") or ""),
        "maList": [
            int(value)
            for value in (record.get("ma_list") or [])
        ],
        "close": record.get("close"),
        "changePct": record.get("change_pct"),
        "maValues": dict(record.get("ma_values") or {}),
        "note": str(record.get("note") or ""),
        "volume": int(
            safe_float(record.get("volume"), 0)
        ),
        "asOf": record.get("as_of"),
        "status": status
    }

    if metrics_lookup is not None:
        metrics = None

        if status == "unmatched":
            try:
                metrics = metrics_lookup(symbol)
            except Exception as exc:
                print(
                    f"⚠️ {symbol} 篩選指標略過："
                    f"{type(exc).__name__}: {exc}"
                )

        report_item["metrics"] = metrics

    return report_item


def build_report_groups(
    data_dict,
    group_metadata,
    display_order,
    metrics_lookup=None,
    unmatched_by_group=None
):
    groups = []

    for group_key in display_order:
        if group_key in {"indices", "sectors"}:
            continue

        metadata = group_metadata.get(
            group_key,
            {}
        )

        market = str(
            metadata.get("market")
            or (
                "tw"
                if group_key.startswith("tw_")
                else "us"
                if group_key.startswith("us_")
                else ""
            )
        ).upper()

        if group_key in REPORT_SCAN_GROUP_NAMES:
            kind = "scan"
        elif (
            group_key.startswith("custom_")
            or metadata.get("is_custom")
        ):
            kind = "custom"
        else:
            kind = "fixed"

        name = normalize_group_name(
            metadata.get("name")
            or REPORT_SCAN_GROUP_NAMES.get(
                group_key
            )
            or group_key
        )

        items = []

        for item in data_dict.get(
            group_key,
            []
        ) or []:
            try:
                items.append(
                    build_report_group_item(
                        item,
                        market,
                        metrics_lookup=metrics_lookup
                    )
                )
            except Exception as exc:
                print(
                    f"⚠️ {group_key} 報告項目略過："
                    f"{type(exc).__name__}: {exc}"
                )

        # 未成功股票（scan 群組不輸出）：先 unmatched 再 failed，各依代號排序；
        # 已在成功清單或前面出現過的代號不重複列出
        unmatched_items = []

        if kind != "scan":
            seen_symbols = {
                report_item["symbol"]
                for report_item in items
            }

            for status in ("unmatched", "failed"):
                records = sorted(
                    (
                        record
                        for record in (
                            (unmatched_by_group or {}).get(
                                group_key
                            )
                            or []
                        )
                        if record.get("status") == status
                    ),
                    key=lambda record: str(
                        record.get("ticker")
                    ).strip().upper()
                )

                for record in records:
                    try:
                        report_item = (
                            build_report_unmatched_item(
                                record,
                                metrics_lookup=metrics_lookup
                            )
                        )
                    except Exception as exc:
                        print(
                            f"⚠️ {group_key} 未成功項目略過："
                            f"{type(exc).__name__}: {exc}"
                        )
                        continue

                    if report_item["symbol"] in seen_symbols:
                        continue

                    seen_symbols.add(report_item["symbol"])
                    unmatched_items.append(report_item)

        group_ma_list = [
            int(value)
            for value in (
                metadata.get("ma_list")
                or [20]
            )
        ]

        groups.append(
            {
                "key": group_key,
                "name": name,
                "market": market,
                "kind": kind,
                "maList": group_ma_list,
                "count": len(items),
                "unmatchedCount": len(unmatched_items),
                "items": items + unmatched_items
            }
        )

    return groups


def build_report_sectors(sector_items):
    sectors = []

    for item in sorted(
        sector_items or [],
        key=lambda value: safe_int(
            value.get("rank"),
            9999
        )
    ):
        chart_data = item.get("chart_data") or {}
        candles = chart_data.get("candles") or []

        sectors.append(
            {
                "symbol": item.get("ticker"),
                "name": item.get("name"),
                "trend": item.get("trend_status"),
                "status": item.get("volume_status"),
                "rank": safe_int(item.get("rank")),
                "changePct": round_report_number(
                    item.get("return_1w")
                ),
                "return4w": round_report_number(
                    item.get("return_4w")
                ),
                "return13w": round_report_number(
                    item.get("return_13w")
                ),
                "relativeStrength13w": (
                    round_report_number(
                        item.get(
                            "relative_strength_13w"
                        )
                    )
                ),
                "momentumScore": round_report_number(
                    item.get("momentum_score")
                ),
                "strength": item.get(
                    "strength_group"
                ),
                "strengthLabel": item.get(
                    "strength_label"
                ),
                "close": round_report_number(
                    item.get("latest_close")
                ),
                "volumeChangePct": round_report_number(
                    item.get("volume_change_pct")
                ),
                "timeframe": "1W",
                "maList": [
                    int(value)
                    for value in (
                        item.get("ma_list")
                        or SECTOR_WEEKLY_MA_LIST
                    )
                ],
                "maValues": _chart_last_ma_values(
                    chart_data
                ),
                "asOf": (
                    candles[-1].get("time")
                    if candles
                    else None
                )
            }
        )

    return sectors


def build_report_payload(
    report_date,
    data_dict,
    group_metadata,
    display_order,
    index_map,
    market_tickers,
    captured_index_lines,
    market_line_texts,
    line_messages,
    generated_at=None,
    extra_index_configs=None,
    analyze=analyze_index_for_report,
    metrics_lookup=None,
    unmatched_by_group=None
):
    generated_at = generated_at or (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )

    markets, world_indices = build_report_indices(
        index_map,
        market_tickers,
        captured_index_lines,
        market_line_texts,
        extra_index_configs=extra_index_configs,
        analyze=analyze
    )

    return {
        "version": REPORT_JSON_VERSION,
        "generatedAt": generated_at,
        "reportDate": report_date,
        "markets": markets,
        "worldIndices": world_indices,
        "lineMessages": {
            "index": line_messages.get("index"),
            "sectors": line_messages.get("sectors"),
            "stocks": line_messages.get("stocks")
        },
        "groups": build_report_groups(
            data_dict,
            group_metadata,
            display_order,
            metrics_lookup=metrics_lookup,
            unmatched_by_group=unmatched_by_group
        ),
        "sectors": build_report_sectors(
            data_dict.get("sectors")
        )
    }


def _json_default(value):
    if hasattr(value, "item"):
        return value.item()

    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")

    return str(value)


def write_json_atomic(path, payload):
    """寫到同目錄暫存檔後 os.replace，避免網站讀到寫到一半的檔案。"""
    directory = os.path.dirname(path) or "."

    os.makedirs(directory, exist_ok=True)

    file_descriptor, tmp_path = tempfile.mkstemp(
        prefix=".tmp-",
        suffix=".json",
        dir=directory
    )

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                clean_json_value(payload),
                file,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                default=_json_default
            )

        os.replace(tmp_path, path)

    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return path


def validate_report(payload, previous):
    """Check the complete report before touching latest.json; group membership is dynamic."""
    day = payload.get("reportDate") if isinstance(payload, dict) else None
    label = f"latest.json {day}"
    require(isinstance(payload, dict), label, "invalid report object")

    def valid_day(value):
        try:
            return isinstance(value, str) and datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
        except ValueError:
            return False

    require(valid_day(day), label, "invalid reportDate")
    if previous is not None:
        require(isinstance(previous, dict) and valid_day(previous.get("reportDate")), label, "invalid previous reportDate")
        require(day >= previous["reportDate"], label, f"date regressed from {previous['reportDate']}")
    prior = previous or {}

    def rows(doc, field, context):
        value = doc.get(field)
        require(isinstance(value, list) and all(isinstance(row, dict) for row in value),
                context, f"invalid {field} rows")
        return value

    def market_map(doc):
        markets = rows(doc, "markets", label)
        unique_keys((m.get("key") for m in markets), label + " markets")
        return {m["key"]: m for m in markets}

    def positive_indices(market, value):
        market_label = f"{label} {market}"
        indices = rows(value, "indices", market_label)
        unique_keys((i.get("symbol") for i in indices), market_label)
        return {i["symbol"] for i in indices if finite(i.get("close")) and i["close"] > 0}

    current_markets = market_map(payload)
    prior_markets = market_map(prior) if previous is not None else {}
    missing = set(prior_markets) - set(current_markets)
    require(not missing, label, f"missing markets {sorted(missing)} ({len(current_markets)}/{len(prior_markets)})")
    current_indices = {key: positive_indices(key, market) for key, market in current_markets.items()}
    for key, market in prior_markets.items():
        before = positive_indices(key, market)
        after = current_indices[key]
        require(before <= after, f"{label} {key}", f"missing positive indices {sorted(before - after)} ({len(after)}/{len(before)})")
    for market, symbol in (("tw", "^TWII"), ("us", "^GSPC")):
        available = current_indices.get(market, set())
        require(symbol in available, f"{label} {market}", f"missing positive anchor {symbol} ({len(available)} usable indices)")

    def group_counts(doc):
        groups = rows(doc, "groups", label)
        unique_keys((g.get("key") for g in groups), label + " groups")
        stocks = {"TW": set(), "US": set()}
        for group in groups:
            market = group.get("market")
            items = rows(group, "items", f"{label} {market} {group['key']}")
            if market in stocks:
                # A stock in several groups is one usable identity, not several.
                stocks[market].update(item["symbol"] for item in items
                                      if isinstance(item.get("symbol"), str) and item["symbol"].strip()
                                      and finite(item.get("close")))
        return {market: len(symbols) for market, symbols in stocks.items()}

    counts = group_counts(payload)
    prior_counts = group_counts(prior) if previous is not None else {}
    for market, count in counts.items():
        before = prior_counts.get(market, 0)
        if before >= 20:
            retain_count(count, before, f"{label} {market} groups", min_share=.85)

    def world_count(doc):
        return len({i["symbol"] for i in rows(doc, "worldIndices", label)
                    if isinstance(i.get("symbol"), str) and i["symbol"].strip() and finite(i.get("close"))})

    world = world_count(payload)
    prior_world = world_count(prior) if previous is not None else 0
    if prior_world >= 5:
        retain_count(world, prior_world, label + " worldIndices", min_share=.85)


def write_report_json(
    payload,
    report_dir=REPORT_DIR,
    *,
    source_user_id=None
):
    if not is_public_report_owner(source_user_id):
        raise PermissionError("Private account reports must not be published to GitHub")
    path = os.path.join(
        report_dir,
        "latest.json"
    )

    previous = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            previous = json.load(file)
    validate_report(payload, previous)
    write_json_atomic(path, payload)

    print(
        f"✅ Atlas 報告 JSON 已產生：{path}"
        f"（{os.path.getsize(path) / 1024:,.1f} KB）"
    )

    return path
