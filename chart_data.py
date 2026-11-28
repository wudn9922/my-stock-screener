import pandas as pd
from screener_common import safe_float


def build_stock_data(
    df_chart,
    ticker,
    title_suffix,
    ma_list,
    show_volume=True,
    display_name=None,
    timeframe_label="日K"
):
    """
    將 DataFrame 轉成 TradingView Lightweight Charts 使用的格式。

    此函數只改變圖表資料格式，不改變：
    - 股票篩選
    - 均線計算
    - 選股頁的成交量條件
    - Supabase 參數
    """

    if df_chart is None or df_chart.empty:
        return {
            "ticker": str(ticker),
            "display_name": str(
                display_name or ""
            ),
            "title_suffix": str(
                title_suffix or ""
            ),
            "timeframe": str(
                timeframe_label or "日K"
            ),
            "drawing_timeframe": (
                "1w"
                if timeframe_label == "週K"
                else "1d"
            ),
            "candles": [],
            "volume": [],
            "moving_averages": [],
            "ohlcv": []
        }

    display_name = str(
        display_name or ""
    ).strip()

    timeframe_label = str(
        timeframe_label or "日K"
    ).strip()

    drawing_timeframe = (
        "1w"
        if timeframe_label == "週K"
        else "1d"
    )

    chart_df = df_chart.copy()

    chart_df = chart_df[
        ~chart_df.index.duplicated(
            keep="last"
        )
    ].sort_index()

    candles = []
    volume_data = []
    ohlcv_records = []

    ma_series_map = {}

    colors = [
        "#ffb74d",
        "#42a5f5",
        "#66bb6a",
        "#ec407a",
        "#ab47bc",
        "#26c6da"
    ]

    for index, ma_window in enumerate(
        ma_list
    ):
        ma_column = f"MA{ma_window}"

        if ma_column not in chart_df.columns:
            continue

        ma_series_map[ma_column] = {
            "name": ma_column,
            "window": int(ma_window),
            "color": colors[
                index % len(colors)
            ],
            "data": []
        }

    for row_index, row in chart_df.iterrows():
        date_value = pd.Timestamp(
            row_index
        ).strftime("%Y-%m-%d")

        open_value = safe_float(
            row.get("Open"),
            None
        )

        high_value = safe_float(
            row.get("High"),
            None
        )

        low_value = safe_float(
            row.get("Low"),
            None
        )

        close_value = safe_float(
            row.get("Close"),
            None
        )

        volume_value = safe_float(
            row.get("Volume"),
            0.0
        )

        if (
            open_value is None
            or high_value is None
            or low_value is None
            or close_value is None
        ):
            continue

        candle_item = {
            "time": date_value,
            "open": float(open_value),
            "high": float(high_value),
            "low": float(low_value),
            "close": float(close_value)
        }

        candles.append(candle_item)

        # 台灣市場習慣：上漲紅、下跌綠
        if close_value > open_value:
            volume_color = (
                "rgba(239,83,80,0.62)"
            )
        elif close_value < open_value:
            volume_color = (
                "rgba(38,166,154,0.62)"
            )
        else:
            volume_color = (
                "rgba(148,163,184,0.52)"
            )

        if show_volume:
            volume_data.append(
                {
                    "time": date_value,
                    "value": max(
                        0.0,
                        float(volume_value)
                    ),
                    "color": volume_color
                }
            )

        ohlcv_records.append(
            {
                "date": date_value,
                "time": date_value,
                "open": float(open_value),
                "high": float(high_value),
                "low": float(low_value),
                "close": float(close_value),
                "volume": max(
                    0.0,
                    float(volume_value)
                ),
                "timeframe": timeframe_label
            }
        )

        for (
            ma_column,
            ma_config
        ) in ma_series_map.items():
            ma_value = row.get(ma_column)

            if pd.isna(ma_value):
                continue

            numeric_ma = safe_float(
                ma_value,
                None
            )

            if numeric_ma is None:
                continue

            ma_config["data"].append(
                {
                    "time": date_value,
                    "value": float(
                        numeric_ma
                    )
                }
            )

    moving_averages = []

    for ma_config in ma_series_map.values():
        if not ma_config["data"]:
            continue

        moving_averages.append(
            ma_config
        )

    latest_close = (
        candles[-1]["close"]
        if candles
        else None
    )

    full_display_name = str(ticker)

    if display_name:
        full_display_name = (
            f"{ticker}　{display_name}"
        )

    return {
        "ticker": str(ticker),
        "display_name": display_name,
        "full_display_name": (
            full_display_name
        ),
        "title_suffix": str(
            title_suffix or ""
        ),
        "timeframe": timeframe_label,
        "drawing_timeframe": (
            drawing_timeframe
        ),
        "show_volume": bool(show_volume),
        "latest_close": latest_close,
        "candles": candles,
        "volume": volume_data,
        "moving_averages": (
            moving_averages
        ),
        "ohlcv": ohlcv_records
    }
