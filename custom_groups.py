import os

import pandas as pd

import kline_integrity as kline
import screener_config
from chart_data import build_stock_data
from market_data import download_single_ticker_with_retry, write_kline_csv
from market_scan import _report_analysis_prices
from screener_common import (
    clean_ohlcv_dataframe,
    get_tw_stock_name,
    round_report_number,
    safe_float,
    safe_int,
)
from screener_config import CUSTOM_GROUP_TEST_MODE, CUSTOM_MA_MAX_RATIO, CUSTOM_MA_MIN_RATIO, MAX_DAYS


# =========================================================================
# Supabase 固定與動態自訂群組
# =========================================================================
def get_ticker_candidates(
    raw_ticker,
    group_key=None,
    group_market=None
):
    ticker = str(
        raw_ticker or ""
    ).strip().upper()

    if not ticker:
        return []

    if (
        ticker.endswith(".TW")
        or ticker.endswith(".TWO")
    ):
        return [ticker]

    # 自訂群組可混合輸入格式；
    # 純數字一律嘗試台股上市與上櫃。
    if ticker.isdigit():
        return [
            f"{ticker}.TW",
            f"{ticker}.TWO"
        ]

    return [
        ticker.replace(".", "-")
    ]


def download_custom_stock(
    raw_ticker,
    group_key=None,
    group_market=None
):
    candidates = get_ticker_candidates(
        raw_ticker,
        group_key=group_key,
        group_market=group_market
    )

    for candidate in candidates:
        print(
            f"⬇️ 嘗試下載自訂股票："
            f"{candidate}"
        )

        df = download_single_ticker_with_retry(
            candidate,
            period="2y",
            max_attempts=3
        )

        if df.empty:
            print(
                f"⚠️ {candidate} "
                "重試後仍無資料"
            )
            continue

        print(
            f"✅ {candidate} 下載成功，"
            f"共 {len(df)} 筆，"
            f"最新日期："
            f"{df.index[-1]:%Y-%m-%d}"
        )

        return candidate, df

    return None, pd.DataFrame()


def _failed_stock_symbol(
    raw_ticker,
    group_key=None,
    group_market=None
):
    """
    下載失敗時要顯示的代號：純數字台股有 .TW／.TWO 兩個候選，
    優先用 data/ 已經有 CSV 的那個（過去成功過的後綴），否則取第一個。
    """
    candidates = (
        get_ticker_candidates(
            raw_ticker,
            group_key=group_key,
            group_market=group_market
        )
        or [str(raw_ticker).strip().upper()]
    )

    for candidate in candidates:
        if os.path.exists(
            os.path.join(
                screener_config.DATA_DIR,
                f"{candidate}.csv"
            )
        ):
            return candidate

    return candidates[0]


def _record_failed_stock(
    unmatched_out,
    ticker,
    group_key,
    reason,
    raw_ma_list=None,
    group_market=None
):
    """
    未成功股票（沒有行情）：只給網頁報告列出，不影響 matched_list。
    unmatched_out 為 None 時什麼都不做；任何錯誤只記錄，絕不讓日報中斷
    （這個函式會在 except 區塊裡被呼叫）。
    """
    if unmatched_out is None:
        return

    try:
        unmatched_out.append(
            {
                "status": "failed",
                "ticker": str(ticker),
                "name": (
                    get_tw_stock_name(str(ticker))
                    if group_market == "tw"
                    else ""
                ),
                "group_key": group_key,
                "ma_list": sorted(
                    {
                        ma_value
                        for ma_value in (
                            safe_int(raw_ma)
                            for raw_ma in (raw_ma_list or [])
                        )
                        if ma_value is not None
                        and ma_value > 0
                    }
                ),
                "volume": 0,
                "close": None,
                "change_pct": None,
                "ma_values": {},
                "as_of": None,
                "note": reason
            }
        )

    except Exception as exc:
        print(
            f"⚠️ {ticker} 失敗項目略過："
            f"{type(exc).__name__}: {exc}"
        )


def _record_unmatched_stock(
    unmatched_out,
    ticker,
    group_key,
    group_market,
    ma_list,
    combined
):
    """
    未成功股票（有行情但未符合均線條件）：直接由 combined 算出
    收盤、漲跌、均線值與備註，不建立圖表。失敗只記錄，不影響主流程。
    """
    if unmatched_out is None:
        return

    try:
        price = safe_float(
            combined["Close"].iloc[-1],
            None
        )

        previous_close = (
            safe_float(
                combined["Close"].iloc[-2],
                None
            )
            if len(combined) >= 2
            else None
        )

        change_pct = (
            round(
                (price / previous_close - 1) * 100,
                2
            )
            if price is not None and previous_close
            else None
        )

        ma_values = {}
        diff_info = []
        ma_status_list = []

        for ma_window in ma_list:
            ma_value = safe_float(
                combined[f"MA{ma_window}"].iloc[-1],
                None
            )

            if ma_value is None or price is None:
                continue

            ma_values[str(ma_window)] = (
                round_report_number(ma_value)
            )

            diff_info.append(
                f"MA{ma_window}差距"
                f"({(price / ma_value - 1) * 100:.2f}%)"
            )

            ma_status_list.append(
                f"MA{ma_window}:{ma_value:.2f}"
            )

        note = " | ".join(
            [
                "未符合均線條件",
                f"現價:{price:.2f}",
                " / ".join(diff_info)
                if diff_info
                else "均線資料不足"
            ]
            + (
                [" | ".join(ma_status_list)]
                if ma_status_list
                else []
            )
        )

        unmatched_out.append(
            {
                "status": "unmatched",
                "ticker": ticker,
                "name": (
                    get_tw_stock_name(ticker)
                    if group_market == "tw"
                    else ""
                ),
                "group_key": group_key,
                "ma_list": list(ma_list),
                "volume": int(
                    safe_float(
                        combined["Volume"].iloc[-1]
                    )
                ),
                "close": round_report_number(price),
                "change_pct": change_pct,
                "ma_values": ma_values,
                "as_of": pd.Timestamp(
                    combined.index[-1]
                ).strftime("%Y-%m-%d"),
                "note": note
            }
        )

    except Exception as exc:
        print(
            f"⚠️ {ticker} 未成功項目改記為失敗："
            f"{type(exc).__name__}: {exc}"
        )

        _record_failed_stock(
            unmatched_out,
            ticker,
            group_key,
            "處理失敗",
            ma_list,
            group_market=group_market
        )


def process_custom_groups(
    group_dict,
    group_key,
    test_mode=CUSTOM_GROUP_TEST_MODE,
    group_metadata=None,
    unmatched_out=None
):
    # unmatched_out（選用 list）會收到未成功股票（unmatched／failed），
    # 只供網頁報告使用；matched_list 與回傳值不受影響。
    matched_list = []

    group_metadata = (
        group_metadata or {}
    )

    if not group_dict:
        print(
            f"⚠️ {group_key} "
            "沒有讀到 Supabase 股票"
        )

        return matched_list

    group_market = group_metadata.get(
        "market"
    )

    group_name = group_metadata.get(
        "name",
        group_key
    )

    os.makedirs(
        screener_config.DATA_DIR,
        exist_ok=True
    )

    print(
        "\n===================================="
    )
    print(
        f"📋 開始處理自訂群組："
        f"{group_key} / {group_name}"
    )
    print(
        f"📋 Supabase 股票數："
        f"{len(group_dict)}"
    )
    print(
        f"🧪 測試模式：{test_mode}"
    )
    print(
        "===================================="
    )

    for (
        raw_ticker,
        raw_ma_list
    ) in group_dict.items():
        actual_ticker = None

        try:
            print(
                f"\n🔍 開始處理："
                f"{raw_ticker}，"
                f"MA={raw_ma_list}"
            )

            (
                actual_ticker,
                downloaded
            ) = download_custom_stock(
                raw_ticker,
                group_key=group_key,
                group_market=group_market
            )

            if (
                not actual_ticker
                or downloaded.empty
            ):
                _record_failed_stock(
                    unmatched_out,
                    _failed_stock_symbol(
                        raw_ticker,
                        group_key=group_key,
                        group_market=group_market
                    ),
                    group_key,
                    "下載失敗",
                    raw_ma_list,
                    group_market=group_market
                )
                continue

            csv_path = os.path.join(
                screener_config.DATA_DIR,
                f"{actual_ticker}.csv"
            )

            combined = downloaded.copy()

            if os.path.exists(csv_path):
                try:
                    local_data = kline.read_csv(
                        csv_path,
                        index_col=0,
                        parse_dates=True
                    )

                    local_data = (
                        clean_ohlcv_dataframe(
                            local_data
                        )
                    )

                    if not local_data.empty:
                        combined = pd.concat(
                            [
                                local_data,
                                downloaded
                            ]
                        )

                except Exception as exc:
                    print(
                        f"⚠️ {actual_ticker} "
                        "讀取 CSV 失敗："
                        f"{exc}"
                    )

            combined = (
                clean_ohlcv_dataframe(
                    combined
                )
            )

            if combined.empty:
                _record_failed_stock(
                    unmatched_out,
                    actual_ticker,
                    group_key,
                    "資料不足",
                    raw_ma_list,
                    group_market=group_market
                )
                continue

            ma_list = []

            for raw_ma in raw_ma_list:
                ma_value = safe_int(raw_ma)

                if (
                    ma_value is not None
                    and ma_value > 0
                ):
                    ma_list.append(ma_value)

            ma_list = (
                sorted(set(ma_list))
                or [20]
            )

            max_ma = max(ma_list)

            keep_days = max(
                MAX_DAYS,
                max_ma + 60
            )

            combined = combined.tail(
                keep_days
            ).copy()

            combined = write_kline_csv(combined[kline.FIELDS], csv_path)
            combined = _report_analysis_prices(actual_ticker, combined)
            if combined.empty:
                _record_failed_stock(unmatched_out, actual_ticker, group_key,
                                     "沒有有效交易日資料", raw_ma_list, group_market=group_market)
                continue

            for ma_window in ma_list:
                combined[
                    f"MA{ma_window}"
                ] = (
                    combined["Close"]
                    .rolling(
                        window=ma_window,
                        min_periods=ma_window
                    )
                    .mean()
                )

            price = safe_float(
                combined["Close"].iloc[-1]
            )

            latest_volume = safe_float(
                combined["Volume"].iloc[-1]
            )

            matched_any_ma = False
            available_ma_count = 0
            triggered_info = []

            for ma_window in ma_list:
                ma_col = f"MA{ma_window}"

                ma_value = combined[
                    ma_col
                ].iloc[-1]

                if pd.isna(ma_value):
                    continue

                available_ma_count += 1
                ma_value = float(ma_value)

                diff_pct = (
                    (price / ma_value) - 1
                ) * 100

                if test_mode:
                    matched_any_ma = True

                    triggered_info.append(
                        f"MA{ma_window}差距"
                        f"({diff_pct:.2f}%)"
                    )

                elif (
                    ma_value
                    * CUSTOM_MA_MIN_RATIO
                    <= price
                    <= ma_value
                    * CUSTOM_MA_MAX_RATIO
                ):
                    matched_any_ma = True

                    triggered_info.append(
                        f"近MA{ma_window}"
                        f"({diff_pct:.2f}%)"
                    )

            if (
                test_mode
                and available_ma_count == 0
            ):
                matched_any_ma = True

                triggered_info.append(
                    "均線資料不足，K線正常"
                )

            if not matched_any_ma:
                print(
                    f"⏭️ {actual_ticker} "
                    "未符合均線條件"
                )
                _record_unmatched_stock(
                    unmatched_out,
                    actual_ticker,
                    group_key,
                    group_market,
                    ma_list,
                    combined
                )
                continue

            ma_status_list = []

            for ma_window in ma_list:
                ma_col = f"MA{ma_window}"

                ma_value = combined[
                    ma_col
                ].iloc[-1]

                if not pd.isna(ma_value):
                    ma_status_list.append(
                        f"MA{ma_window}:"
                        f"{float(ma_value):.2f}"
                    )

            mode_text = (
                "Supabase測試模式"
                if test_mode
                else "均線潛伏"
            )

            trigger_text = (
                " / ".join(triggered_info)
                if triggered_info
                else "無可用均線"
            )

            ma_status_text = (
                " | ".join(ma_status_list)
                if ma_status_list
                else "均線資料不足"
            )

            title = (
                f"({mode_text} | "
                f"現價:{price:.2f} | "
                f"{trigger_text} | "
                f"{ma_status_text})"
            )

            display_name = ""

            if group_market == "tw":
                display_name = (
                    get_tw_stock_name(
                        actual_ticker
                    )
                )

            chart_data = build_stock_data(
                combined.tail(180),
                actual_ticker,
                title,
                ma_list,
                show_volume=True,
                display_name=display_name,
                timeframe_label="日K"
            )

            matched_list.append(
                {
                    "ticker": actual_ticker,
                    "name": display_name,
                    "group_key": group_key,
                    "group_name": group_name,
                    "volume": int(
                        latest_volume
                    ),
                    "ma_list": ma_list,
                    "chart_data": chart_data
                }
            )

            print(
                f"✅ {actual_ticker} "
                "已成功加入圖表"
            )

        except Exception as exc:
            print(
                f"❌ {group_key} / "
                f"{raw_ticker} 處理失敗："
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            _record_failed_stock(
                unmatched_out,
                actual_ticker
                or _failed_stock_symbol(
                    raw_ticker,
                    group_key=group_key,
                    group_market=group_market
                ),
                group_key,
                "處理失敗",
                raw_ma_list,
                group_market=group_market
            )

    matched_list.sort(
        key=lambda item: item["volume"],
        reverse=True
    )

    print(
        f"\n📊 {group_key} 最後產生 "
        f"{len(matched_list)} 張圖表"
    )

    return matched_list


def process_group_for_data_dict(
    data_dict,
    user_configs,
    group_metadata,
    group_key,
    unmatched_by_group=None
):
    group_dict = user_configs.get(
        group_key,
        {}
    )

    metadata = group_metadata.get(
        group_key,
        {}
    )

    unmatched_bucket = (
        []
        if unmatched_by_group is not None
        else None
    )

    data_dict[group_key] = (
        process_custom_groups(
            group_dict,
            group_key,
            test_mode=(
                CUSTOM_GROUP_TEST_MODE
            ),
            group_metadata=metadata,
            unmatched_out=unmatched_bucket
        )
    )

    if unmatched_by_group is not None:
        unmatched_by_group[group_key] = (
            unmatched_bucket
        )
