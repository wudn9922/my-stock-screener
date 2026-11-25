import pandas as pd

from index_history import download_index_history
from market_data import extract_yfinance_data
from screener_common import clean_ohlcv_dataframe, safe_float


# =========================================================================
# 大盤多空趨勢分析
# 1. 均線糾纏自適應
# 2. 0.1% 過濾
# 3. 大趨勢與小走勢：道氏狀態機
#    - 小走勢：波峰波谷取前後各 5 根（狀態約持續 3 週至 3 個月）
#    - 大趨勢：波峰波谷取前後各 30 根（狀態約持續 4 個月以上），
#      收盤創三年新高時直接轉為多頭
# =========================================================================
DOW_SWING_PIVOT_BARS = 5
DOW_TREND_PIVOT_BARS = 30
DOW_TREND_NEW_HIGH_BARS = 756


def compute_dow_states(
    highs,
    lows,
    closes,
    pivot_bars,
    new_high_bars=0
):
    """
    道氏狀態機，回傳每一天的狀態：1 為多頭，-1 為空頭。

    波峰（波谷）是前後各 pivot_bars 根 K 棒中的最高（最低）點，
    要等後面 pivot_bars 根走完才算確認，所以不會用到未來資料。

    多轉空：最近一個波谷之後的反彈高點，低於該波谷之前的波峰，
            且收盤跌破該波谷。
    空轉多：最近一個波峰之後的回檔低點，高於該波峰之前的波谷，
            且收盤突破該波峰。
    其餘時間維持原狀態。

    new_high_bars 大於 0 時，空頭期間只要收盤高於
    前 new_high_bars 根的最高價，就直接轉為多頭。
    """
    count = len(closes)
    states = []
    state = 1

    # 交替排列的波峰波谷：[種類, 位置, 價格]
    swings = []

    for today in range(count):
        pivot = today - pivot_bars

        if pivot - pivot_bars >= 0:
            is_peak = (
                highs[pivot]
                > max(highs[pivot - pivot_bars:pivot])
                and highs[pivot]
                >= max(highs[pivot + 1:today + 1])
            )

            if is_peak:
                # 連續兩個波峰時，補上中間的最低點當波谷
                if swings and swings[-1][0] == "H":
                    start = swings[-1][1] + 1

                    if pivot > start:
                        between = min(
                            range(start, pivot),
                            key=lows.__getitem__
                        )

                        swings.append(
                            ["L", between, lows[between]]
                        )

                        swings.append(
                            ["H", pivot, highs[pivot]]
                        )

                else:
                    swings.append(
                        ["H", pivot, highs[pivot]]
                    )

            is_trough = (
                lows[pivot]
                < min(lows[pivot - pivot_bars:pivot])
                and lows[pivot]
                <= min(lows[pivot + 1:today + 1])
            )

            if is_trough:
                # 連續兩個波谷時，補上中間的最高點當波峰
                if swings and swings[-1][0] == "L":
                    start = swings[-1][1] + 1

                    if pivot > start:
                        between = max(
                            range(start, pivot),
                            key=highs.__getitem__
                        )

                        swings.append(
                            ["H", between, highs[between]]
                        )

                        swings.append(
                            ["L", pivot, lows[pivot]]
                        )

                else:
                    swings.append(
                        ["L", pivot, lows[pivot]]
                    )

        if len(swings) >= 2:
            if state == 1:
                # 多轉空：先有較低的高點，再收盤跌破前一個波谷
                position = (
                    len(swings) - 1
                    if swings[-1][0] == "L"
                    else len(swings) - 2
                )

                if position >= 1:
                    trough_index = swings[position][1]
                    trough_price = swings[position][2]
                    prior_peak = swings[position - 1][2]

                    if (
                        closes[today] < trough_price
                        and today > trough_index
                        and max(
                            highs[trough_index + 1:today + 1]
                        ) < prior_peak
                    ):
                        state = -1

            else:
                # 空轉多：先有較高的低點，再收盤突破前一個波峰
                position = (
                    len(swings) - 1
                    if swings[-1][0] == "H"
                    else len(swings) - 2
                )

                if position >= 1:
                    peak_index = swings[position][1]
                    peak_price = swings[position][2]
                    prior_trough = swings[position - 1][2]

                    if (
                        closes[today] > peak_price
                        and today > peak_index
                        and min(
                            lows[peak_index + 1:today + 1]
                        ) > prior_trough
                    ):
                        state = 1

        if (
            new_high_bars
            and state == -1
            and today >= new_high_bars
            and closes[today] > max(
                highs[today - new_high_bars + 1:today]
            )
        ):
            state = 1

        states.append(state)

    return states


def analyze_index_trend(
    ticker,
    name,
    ma_list
):
    if not ma_list:
        return (
            f"⚪ {name}: "
            "未設定任何均線參數"
        )

    try:
        # ^TWOII 優先使用櫃買中心資料，其餘照舊走 Yahoo
        df = download_index_history(
            ticker,
            period="10y",
            progress=False,
            threads=False,
            auto_adjust=False
        )

        if df.empty or len(df) < 750:
            return (
                f"⚪ {name}: "
                "數據不足無法分析"
            )

        if isinstance(
            df.columns,
            pd.MultiIndex
        ):
            df = extract_yfinance_data(
                df,
                ticker
            )

        df = clean_ohlcv_dataframe(df)

        if df.empty or len(df) < 750:
            return (
                f"⚪ {name}: "
                "數據不足無法分析"
            )

        available_mas = []

        for ma in ma_list:
            col_name = f"MA{ma}"

            df[col_name] = (
                df["Close"]
                .rolling(
                    window=ma,
                    min_periods=ma
                )
                .mean()
            )

            available_mas.append(
                col_name
            )

        df = df.dropna(
            subset=available_mas
        )

        if len(df) < 5:
            return (
                f"⚪ {name}: "
                "計算後可用數據小於5日"
            )

        latest = df.iloc[-1]
        score = 0

        total_ma_count = len(
            available_mas
        )

        df_last5 = df.tail(5)

        # 最近五天至少三天 K 棒碰到均線，
        # 該均線視為糾纏，計為中性 0 分。
        for ma_col in available_mas:
            touch_count = 0

            for _, row_5 in (
                df_last5.iterrows()
            ):
                if (
                    row_5["Low"]
                    <= row_5[ma_col]
                    <= row_5["High"]
                ):
                    touch_count += 1

            if touch_count >= 3:
                continue

            latest_close = safe_float(
                latest["Close"]
            )

            latest_ma = safe_float(
                latest[ma_col]
            )

            if latest_close > (
                latest_ma * 1.001
            ):
                score += 1

            elif latest_close < (
                latest_ma * 0.999
            ):
                score -= 1

        if score == total_ma_count:
            score_label = "看多"

        elif score > 0:
            score_label = "偏多"

        elif score == 0:
            score_label = "多空不明"

        elif score == -total_ma_count:
            score_label = "看空"

        else:
            score_label = "偏空"

        # -------------------------------------------------------------
        # 道氏狀態機：大趨勢與小走勢
        # 兩者規則相同，只差波段大小。
        # 狀態會延續，直到出現反轉訊號才切換。
        # -------------------------------------------------------------
        highs = [
            safe_float(value)
            for value in df["High"].tolist()
        ]

        lows = [
            safe_float(value)
            for value in df["Low"].tolist()
        ]

        closes = [
            safe_float(value)
            for value in df["Close"].tolist()
        ]

        # 大趨勢：4 個月以上的波段
        macro_state = compute_dow_states(
            highs,
            lows,
            closes,
            DOW_TREND_PIVOT_BARS,
            DOW_TREND_NEW_HIGH_BARS
        )[-1]

        # 小走勢：3 週至 3 個月的波段
        micro_state = compute_dow_states(
            highs,
            lows,
            closes,
            DOW_SWING_PIVOT_BARS
        )[-1]

        macro_trend = (
            "多頭趨勢"
            if macro_state == 1
            else "空頭趨勢"
        )

        micro_trend = (
            "多頭走勢"
            if micro_state == 1
            else "空頭走勢"
        )

        final_status = (
            f"{macro_trend}中的"
            f"{micro_trend}"
        )

        if (
            macro_trend == "多頭趨勢"
            and micro_trend == "多頭走勢"
        ):
            icon = "🔺"

        elif (
            macro_trend == "多頭趨勢"
            and micro_trend == "空頭走勢"
        ):
            icon = "💡"

        elif (
            macro_trend == "空頭趨勢"
            and micro_trend == "空頭走勢"
        ):
            icon = "🔻"

        else:
            icon = "⚡"

        ma_string = "/".join(
            str(value)
            for value in ma_list
        )

        return (
            f"{icon} {name}\n"
            f"   ├ 均線: {score_label} "
            f"({score}/{total_ma_count}MA - "
            f"{ma_string})\n"
            f"   └  {final_status}"
        )

    except Exception as exc:
        print(
            f"⚠️ {ticker} "
            "大盤分析異常："
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        return (
            f"⚪ {name}: "
            "分析發生異常"
        )
