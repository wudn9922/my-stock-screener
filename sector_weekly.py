import pandas as pd
import yfinance as yf
from chart_data import build_stock_data
from market_data import extract_yfinance_data
from screener_common import calculate_return, clean_ohlcv_dataframe, safe_float


# 類股固定使用週均線，不讀取 Supabase
SECTOR_WEEKLY_MA_LIST = [20, 60]
SECTOR_BENCHMARK = "SPY"
SECTOR_CHART_WEEKS = 160

SECTOR_CONFIGS = [
    {
        "ticker": "XLK",
        "name": "科技類股",
        "sort_order": 1
    },
    {
        "ticker": "XLC",
        "name": "通訊服務",
        "sort_order": 2
    },
    {
        "ticker": "XLY",
        "name": "非必需消費",
        "sort_order": 3
    },
    {
        "ticker": "XLP",
        "name": "必需消費",
        "sort_order": 4
    },
    {
        "ticker": "XLE",
        "name": "能源類股",
        "sort_order": 5
    },
    {
        "ticker": "XLF",
        "name": "金融類股",
        "sort_order": 6
    },
    {
        "ticker": "XLV",
        "name": "醫療保健",
        "sort_order": 7
    },
    {
        "ticker": "XLI",
        "name": "工業類股",
        "sort_order": 8
    },
    {
        "ticker": "XLB",
        "name": "原物料",
        "sort_order": 9
    },
    {
        "ticker": "XLRE",
        "name": "房地產",
        "sort_order": 10
    },
    {
        "ticker": "XLU",
        "name": "公用事業",
        "sort_order": 11
    }
]


def convert_daily_to_weekly(
    daily_df,
    drop_incomplete_week=True
):
    daily_df = clean_ohlcv_dataframe(
        daily_df
    )

    if daily_df.empty:
        return pd.DataFrame()

    last_daily_date = (
        daily_df.index[-1].normalize()
    )

    weekly_df = daily_df.resample(
        "W-FRI"
    ).agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum"
        }
    )

    weekly_df = clean_ohlcv_dataframe(
        weekly_df
    )

    if weekly_df.empty:
        return weekly_df

    # 若資料最後日期尚未到該週星期五，
    # resample 仍會產生一根未完成週 K。
    # 為避免星期一推播誤用本週資料，將它移除。
    if (
        drop_incomplete_week
        and weekly_df.index[-1].normalize()
        > last_daily_date
    ):
        weekly_df = weekly_df.iloc[:-1]

    return weekly_df


def determine_sector_price_volume_status(
    weekly_df,
    weekly_return,
    volume_ratio
):
    if weekly_df.empty:
        return "資料不足"

    if (
        weekly_return > 0
        and volume_ratio >= 1.05
    ):
        return "上漲放量，資金流入"

    if (
        weekly_return > 0
        and volume_ratio < 0.95
    ):
        return "上漲縮量，動能普通"

    if (
        weekly_return < 0
        and volume_ratio >= 1.05
    ):
        return "下跌放量，資金流出"

    if (
        weekly_return < 0
        and volume_ratio < 0.95
    ):
        return "下跌縮量，賣壓減弱"

    return "量價中性"


def determine_sector_trend(
    latest_close,
    ma20,
    ma60
):
    above_ma20 = (
        ma20 is not None
        and latest_close > ma20
    )

    above_ma60 = (
        ma60 is not None
        and latest_close > ma60
    )

    if above_ma20 and above_ma60:
        return "週線多頭"

    if not above_ma20 and not above_ma60:
        return "週線空頭"

    if above_ma60 and not above_ma20:
        return "多頭回檔"

    return "空頭反彈"


def process_sector_weekly_charts():
    sector_tickers = [
        item["ticker"]
        for item in SECTOR_CONFIGS
    ]

    download_tickers = list(
        dict.fromkeys(
            sector_tickers
            + [SECTOR_BENCHMARK]
        )
    )

    print(
        "\n===================================="
    )
    print(
        "🧭 開始建立美股 11 大類股週 K"
    )
    print(
        "📏 固定週均線："
        f"{SECTOR_WEEKLY_MA_LIST}"
    )
    print(
        "📊 相對強弱基準："
        f"{SECTOR_BENCHMARK}"
    )
    print(
        "===================================="
    )

    try:
        downloaded = yf.download(
            download_tickers,
            period="10y",
            progress=False,
            threads=False,
            auto_adjust=False,
            group_by="column"
        )

    except Exception as exc:
        print(
            "❌ 類股批次下載失敗："
            f"{type(exc).__name__}: {exc}"
        )

        return [], []

    if downloaded.empty:
        print(
            "❌ 類股批次下載結果為空"
        )
        return [], []

    benchmark_daily = (
        extract_yfinance_data(
            downloaded,
            SECTOR_BENCHMARK
        )
    )

    benchmark_daily = (
        clean_ohlcv_dataframe(
            benchmark_daily
        )
    )

    benchmark_weekly = (
        convert_daily_to_weekly(
            benchmark_daily,
            drop_incomplete_week=True
        )
    )

    if benchmark_weekly.empty:
        print(
            f"❌ {SECTOR_BENCHMARK} "
            "週線資料不足"
        )
        return [], []

    benchmark_return_13w = (
        calculate_return(
            benchmark_weekly["Close"],
            13
        )
    )

    sector_results = []

    for config in SECTOR_CONFIGS:
        ticker = str(
            config["ticker"]
        ).strip().upper()

        name = str(
            config["name"]
        ).strip()

        try:
            daily_df = (
                extract_yfinance_data(
                    downloaded,
                    ticker
                )
            )

            daily_df = (
                clean_ohlcv_dataframe(
                    daily_df
                )
            )

            weekly_df = (
                convert_daily_to_weekly(
                    daily_df,
                    drop_incomplete_week=True
                )
            )

            if (
                weekly_df.empty
                or len(weekly_df) < 61
            ):
                print(
                    f"⚠️ {ticker} "
                    "週線資料不足"
                )
                continue

            for ma_window in (
                SECTOR_WEEKLY_MA_LIST
            ):
                weekly_df[
                    f"MA{ma_window}"
                ] = (
                    weekly_df["Close"]
                    .rolling(
                        window=ma_window,
                        min_periods=ma_window
                    )
                    .mean()
                )

            weekly_df["VOL_MA10"] = (
                weekly_df["Volume"]
                .rolling(
                    window=10,
                    min_periods=5
                )
                .mean()
            )

            latest_close = safe_float(
                weekly_df[
                    "Close"
                ].iloc[-1]
            )

            latest_volume = safe_float(
                weekly_df[
                    "Volume"
                ].iloc[-1]
            )

            return_1w = calculate_return(
                weekly_df["Close"],
                1
            )

            return_4w = calculate_return(
                weekly_df["Close"],
                4
            )

            return_13w = calculate_return(
                weekly_df["Close"],
                13
            )

            relative_strength_13w = (
                return_13w
                - benchmark_return_13w
            )

            # 使用前十個完整週的平均量，
            # 不將當週量納入基準。
            previous_volumes = (
                weekly_df["Volume"]
                .iloc[-11:-1]
            )

            average_volume_10w = safe_float(
                previous_volumes.mean(),
                0.0
            )

            if average_volume_10w > 0:
                volume_ratio = (
                    latest_volume
                    / average_volume_10w
                )
            else:
                volume_ratio = 1.0

            volume_change_pct = (
                volume_ratio - 1
            ) * 100

            ma20_value = weekly_df[
                "MA20"
            ].iloc[-1]

            ma60_value = weekly_df[
                "MA60"
            ].iloc[-1]

            ma20 = (
                None
                if pd.isna(ma20_value)
                else float(ma20_value)
            )

            ma60 = (
                None
                if pd.isna(ma60_value)
                else float(ma60_value)
            )

            trend_status = (
                determine_sector_trend(
                    latest_close,
                    ma20,
                    ma60
                )
            )

            volume_status = (
                determine_sector_price_volume_status(
                    weekly_df,
                    return_1w,
                    volume_ratio
                )
            )

            # 類股動能綜合分數：
            # 20% 一週、25% 四週、
            # 30% 十三週、25% 相對 SPY。
            momentum_score = (
                return_1w * 0.20
                + return_4w * 0.25
                + return_13w * 0.30
                + relative_strength_13w
                * 0.25
            )

            title = (
                "(完整週K | "
                f"本週:{return_1w:+.2f}% | "
                f"4週:{return_4w:+.2f}% | "
                f"13週:{return_13w:+.2f}% | "
                "相對SPY:"
                f"{relative_strength_13w:+.2f}% | "
                f"{trend_status} | "
                f"{volume_status} "
                f"{volume_change_pct:+.1f}%)"
            )

            chart_data = build_stock_data(
                weekly_df.tail(
                    SECTOR_CHART_WEEKS
                ),
                ticker,
                title,
                SECTOR_WEEKLY_MA_LIST,
                show_volume=True,
                display_name=name,
                timeframe_label="週K"
            )

            sector_results.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "sort_order": config[
                        "sort_order"
                    ],
                    "volume": int(
                        latest_volume
                    ),
                    "latest_close": (
                        latest_close
                    ),
                    "return_1w": return_1w,
                    "return_4w": return_4w,
                    "return_13w": return_13w,
                    "benchmark_return_13w": (
                        benchmark_return_13w
                    ),
                    "relative_strength_13w": (
                        relative_strength_13w
                    ),
                    "volume_ratio": (
                        volume_ratio
                    ),
                    "volume_change_pct": (
                        volume_change_pct
                    ),
                    "volume_status": (
                        volume_status
                    ),
                    "trend_status": (
                        trend_status
                    ),
                    "momentum_score": (
                        momentum_score
                    ),
                    "ma_list": (
                        SECTOR_WEEKLY_MA_LIST.copy()
                    ),
                    "chart_data": chart_data
                }
            )

            print(
                f"✅ {ticker} {name}："
                f"動能={momentum_score:+.2f}，"
                f"13週={return_13w:+.2f}%，"
                "相對SPY="
                f"{relative_strength_13w:+.2f}%"
            )

        except Exception as exc:
            print(
                "❌ 類股處理失敗："
                f"{ticker} / "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    sector_results.sort(
        key=lambda item: (
            item["momentum_score"]
        ),
        reverse=True
    )

    sector_summary = []

    result_count = len(
        sector_results
    )

    for rank, item in enumerate(
        sector_results,
        start=1
    ):
        item["rank"] = rank

        if rank <= 3:
            strength_group = "strong"
            strength_label = "強勢領先"

        elif rank > max(
            3,
            result_count - 3
        ):
            strength_group = "weak"
            strength_label = "弱勢落後"

        else:
            strength_group = "neutral"
            strength_label = "中性輪動"

        item["strength_group"] = (
            strength_group
        )

        item["strength_label"] = (
            strength_label
        )

        sector_summary.append(
            {
                "rank": rank,
                "ticker": item["ticker"],
                "name": item["name"],
                "return_1w": (
                    item["return_1w"]
                ),
                "return_4w": (
                    item["return_4w"]
                ),
                "return_13w": (
                    item["return_13w"]
                ),
                "relative_strength_13w": (
                    item[
                        "relative_strength_13w"
                    ]
                ),
                "momentum_score": (
                    item["momentum_score"]
                ),
                "volume_status": (
                    item["volume_status"]
                ),
                "trend_status": (
                    item["trend_status"]
                ),
                "strength_group": (
                    strength_group
                ),
                "strength_label": (
                    strength_label
                )
            }
        )

    print(
        "📊 類股週 K 圖表數量："
        f"{len(sector_results)}"
    )

    return (
        sector_results,
        sector_summary
    )


def build_sector_line_message(
    today_str,
    sector_summary
):
    """LINE 用的精簡類股週線輪動（無連結）；網頁沿用 build_sector_monday_message。"""
    if not sector_summary:
        return (
            f"📅 {today_str} 美股類股週線\n"
            "⚠️ 本週類股資料不足"
        )

    def describe(item):
        return (
            f"{item['ticker']} "
            f"{item['name']} "
            f"{item['return_13w']:+.1f}%"
        )

    strongest = sector_summary[:3]
    weakest = sector_summary[-3:]

    inflow = [
        item["name"]
        for item in sector_summary
        if "資金流入" in item["volume_status"]
    ]

    outflow = [
        item["name"]
        for item in sector_summary
        if "資金流出" in item["volume_status"]
    ]

    lines = [
        f"📅 {today_str} 美股類股週線（13週）",
        "🔴 強：" + "、".join(
            describe(item)
            for item in strongest
        ),
        "🟢 弱：" + "、".join(
            describe(item)
            for item in weakest
        ),
        "💰 流入："
        + ("、".join(inflow) if inflow else "無")
        + "｜流出："
        + ("、".join(outflow) if outflow else "無")
    ]

    return "\n".join(lines)


def build_sector_monday_message(
    today_str,
    sector_summary,
    report_url
):
    if not sector_summary:
        return (
            f"📅 {today_str} "
            "美股類股週線輪動\n\n"
            "⚠️ 本週類股資料不足，"
            "請至網頁稍後重新查看。\n\n"
            f"🔗 完整圖表：\n{report_url}"
        )

    strongest = sector_summary[:3]
    weakest = sector_summary[-3:]

    lines = [
        f"📅 {today_str} "
        "美股類股週線輪動",
        "📊 週均線：MA20 / MA60",
        (
            "📌 排名依據：1週、4週、"
            "13週與相對SPY強弱"
        ),
        "",
        "🟢 【強勢前三名】"
    ]

    for item in strongest:
        lines.append(
            f"{item['rank']}. "
            f"{item['ticker']} "
            f"{item['name']}\n"
            "   ├ 13週："
            f"{item['return_13w']:+.2f}%\n"
            "   ├ 相對SPY："
            f"{item['relative_strength_13w']:+.2f}%\n"
            f"   └ {item['trend_status']}｜"
            f"{item['volume_status']}"
        )

    lines.extend(
        [
            "",
            "🔴 【弱勢後三名】"
        ]
    )

    for item in weakest:
        lines.append(
            f"{item['rank']}. "
            f"{item['ticker']} "
            f"{item['name']}\n"
            "   ├ 13週："
            f"{item['return_13w']:+.2f}%\n"
            "   ├ 相對SPY："
            f"{item['relative_strength_13w']:+.2f}%\n"
            f"   └ {item['trend_status']}｜"
            f"{item['volume_status']}"
        )

    inflow_items = [
        item
        for item in sector_summary
        if "資金流入"
        in item["volume_status"]
    ]

    outflow_items = [
        item
        for item in sector_summary
        if "資金流出"
        in item["volume_status"]
    ]

    lines.extend(
        [
            "",
            "💰 【量價資金狀態】"
        ]
    )

    if inflow_items:
        lines.append(
            "資金流入："
            + "、".join(
                item["name"]
                for item in inflow_items
            )
        )
    else:
        lines.append(
            "資金流入：目前無明顯類股"
        )

    if outflow_items:
        lines.append(
            "資金流出："
            + "、".join(
                item["name"]
                for item in outflow_items
            )
        )
    else:
        lines.append(
            "資金流出：目前無明顯類股"
        )

    lines.extend(
        [
            "",
            "🔗 查看完整類股週K：",
            report_url
        ]
    )

    return "\n".join(lines)
