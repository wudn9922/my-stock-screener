import math
import os

import pandas as pd

import kline_integrity as kline
import screener_config
from chart_data import build_stock_data
from market_data import extract_yfinance_data, write_kline_csv
from screener_common import clean_ohlcv_dataframe, get_tw_stock_name, safe_float
from screener_config import BACKFILL_MAX_PER_RUN, BACKFILL_MIN_ROWS, MAX_DAYS, REPORT_NON_TRADING_DATES


# =========================================================================
# 全市場掃描
# =========================================================================

from market_data import (
    download_single_ticker_with_retry,
    download_market_data,
)

def _report_analysis_prices(ticker, frame):
    from scripts.market_sessions import analysis_prices
    market = "TW" if ticker.endswith((".TW", ".TWO")) else "US"
    return analysis_prices(frame, REPORT_NON_TRADING_DATES.get(market, ()))


def _scan_analysis_frames(tickers):
    """Read all freshly collected histories before identifying market-wide closures."""
    from report_metrics import read_price_csv
    from scripts.market_sessions import market_prices
    frames_by_market = {"TW": {}, "US": {}}
    requested_markets = set()
    for ticker in tickers:
        market = "TW" if ticker.endswith((".TW", ".TWO")) else "US"
        requested_markets.add(market)
        path = os.path.join(screener_config.DATA_DIR, f"{ticker}.csv")
        if not os.path.exists(path):
            continue
        try:
            # Retain missing-volume lineage: unknown source values must not
            # become evidence that the whole market traded zero shares.
            frame = read_price_csv(path)
            if not frame.empty:
                frames_by_market[market][ticker] = frame
        except Exception as exc:
            print(f"⚠️ {ticker} 全市場篩選讀取失敗：{type(exc).__name__}: {exc}")
    frames = {}
    for market in requested_markets:
        projected, excluded = market_prices(frames_by_market[market])
        REPORT_NON_TRADING_DATES[market] = excluded
        frames.update(projected)
    return frames


def scan_market(tickers, report_min_volume=0, **report_options):
    # report_min_volume／report_options 只篩報告名單；所有 tickers 的 CSV 都會先下載更新。
    return _market_scan_report(
        tickers,
        collect_market_frames(tickers),
        report_min_volume,
        **report_options
    )


def collect_market_frames(tickers):
    """下載／更新所有 tickers 的 CSV（不設成交股數門檻），回傳報告分析用的日 K（_scan_analysis_frames）。"""
    os.makedirs(
        screener_config.DATA_DIR,
        exist_ok=True
    )

    matched_list = []
    chunk_size = 40
    need_init = []
    need_update = []

    # -----------------------------------------------------------------
    # 判斷哪些股票需要初始化、哪些只需要更新
    # -----------------------------------------------------------------
    for ticker in tickers:
        csv_path = os.path.join(
            screener_config.DATA_DIR,
            f"{ticker}.csv"
        )

        if os.path.exists(csv_path):
            need_update.append(ticker)
        else:
            need_init.append(ticker)

    print(
        f"📂 需要初始化：{len(need_init)} 檔，"
        f"需要更新：{len(need_update)} 檔"
    )

    # -----------------------------------------------------------------
    # 初始化尚未建立 CSV 的股票
    # -----------------------------------------------------------------
    for start in range(
        0,
        len(need_init),
        chunk_size
    ):
        chunk = need_init[
            start:start + chunk_size
        ]

        downloaded = download_market_data(
            chunk,
            "250d"
        )

        batch_frames = {}

        if not downloaded.empty:
            for ticker in chunk:
                try:
                    ticker_df = (
                        extract_yfinance_data(
                            downloaded,
                            ticker
                        )
                    )

                    ticker_df = (
                        clean_ohlcv_dataframe(
                            ticker_df
                        )
                    )

                    if not ticker_df.empty:
                        batch_frames[
                            ticker
                        ] = ticker_df

                except Exception as exc:
                    print(
                        f"⚠️ {ticker} "
                        "初始化批次資料解析失敗："
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    )

        for ticker in chunk:
            try:
                df = batch_frames.get(
                    ticker,
                    pd.DataFrame()
                )

                # 批次下載漏掉個別股票時，改用單檔重試
                if df.empty:
                    print(
                        f"⚠️ {ticker} "
                        "初始化批次資料為空，"
                        "改用單檔下載"
                    )

                    df = (
                        download_single_ticker_with_retry(
                            ticker,
                            period="250d",
                            max_attempts=3
                        )
                    )

                if df.empty:
                    print(
                        f"❌ {ticker} "
                        "初始化失敗，無可用資料"
                    )
                    continue

                df = (
                    clean_ohlcv_dataframe(
                        df
                    )
                )

                if df.empty:
                    continue

                df = df.tail(
                    MAX_DAYS
                )

                csv_path = os.path.join(
                    screener_config.DATA_DIR,
                    f"{ticker}.csv"
                )

                write_kline_csv(df, csv_path)

                print(
                    f"✅ {ticker} 初始化完成，"
                    "最新 K 線："
                    f"{df.index[-1]:%Y-%m-%d}"
                )

            except Exception as exc:
                print(
                    f"⚠️ {ticker} 初始化失敗："
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

    # -----------------------------------------------------------------
    # 更新已經存在 CSV 的股票
    # -----------------------------------------------------------------
    for start in range(
        0,
        len(need_update),
        chunk_size
    ):
        chunk = need_update[
            start:start + chunk_size
        ]

        # 改抓 15 日資料，增加連假及 Yahoo 漏資料時的容錯空間
        downloaded = download_market_data(
            chunk,
            "15d"
        )

        batch_frames = {}
        batch_latest_date = None

        if not downloaded.empty:
            for ticker in chunk:
                try:
                    ticker_df = (
                        extract_yfinance_data(
                            downloaded,
                            ticker
                        )
                    )

                    ticker_df = (
                        clean_ohlcv_dataframe(
                            ticker_df
                        )
                    )

                    if ticker_df.empty:
                        continue

                    batch_frames[
                        ticker
                    ] = ticker_df

                    ticker_latest_date = (
                        ticker_df.index[-1]
                        .normalize()
                    )

                    if (
                        batch_latest_date is None
                        or ticker_latest_date
                        > batch_latest_date
                    ):
                        batch_latest_date = (
                            ticker_latest_date
                        )

                except Exception as exc:
                    print(
                        f"⚠️ {ticker} "
                        "解析批次資料失敗："
                        f"{type(exc).__name__}: "
                        f"{exc}"
                    )

        for ticker in chunk:
            try:
                today_data = batch_frames.get(
                    ticker,
                    pd.DataFrame()
                )

                need_single_retry = (
                    today_data.empty
                )

                # 如果個股日期落後同一批股票的最新日期，
                # 視為 Yahoo 批次下載可能漏掉最新資料。
                if (
                    not need_single_retry
                    and batch_latest_date is not None
                ):
                    ticker_latest_date = (
                        today_data.index[-1]
                        .normalize()
                    )

                    if (
                        ticker_latest_date
                        < batch_latest_date
                    ):
                        print(
                            f"⚠️ {ticker} "
                            "批次資料日期落後："
                            f"{ticker_latest_date:%Y-%m-%d}，"
                            "批次最新日期："
                            f"{batch_latest_date:%Y-%m-%d}，"
                            "改用單檔重試"
                        )

                        need_single_retry = True

                if need_single_retry:
                    retry_data = (
                        download_single_ticker_with_retry(
                            ticker,
                            period="15d",
                            max_attempts=3
                        )
                    )

                    if not retry_data.empty:
                        today_data = retry_data

                if today_data.empty:
                    print(
                        f"❌ {ticker} "
                        "無法取得更新資料，"
                        "本次保留原 CSV"
                    )
                    continue

                csv_path = os.path.join(
                    screener_config.DATA_DIR,
                    f"{ticker}.csv"
                )

                if os.path.exists(csv_path):
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
                else:
                    local_data = (
                        pd.DataFrame()
                    )

                combined = pd.concat(
                    [
                        local_data,
                        today_data
                    ]
                )

                combined = (
                    clean_ohlcv_dataframe(
                        combined
                    )
                )

                if combined.empty:
                    continue

                combined = combined.tail(
                    MAX_DAYS
                )

                write_kline_csv(combined, csv_path)

                print(
                    f"✅ {ticker} CSV 已更新，"
                    "最新 K 線："
                    f"{combined.index[-1]:%Y-%m-%d}"
                )

            except Exception as exc:
                print(
                    f"⚠️ {ticker} 更新失敗："
                    f"{type(exc).__name__}: "
                    f"{exc}"
                )

    # -----------------------------------------------------------------
    # 全部 CSV 更新後才篩選報告清單（_market_scan_report）：台股 tw_all 最新日 K 至少 100 萬股、
    # MA20 上彎並依 RS 百分位排序；美股不限量、依成交量排序。這些條件只影響每日 LINE 報告，不影響行情收集。
    # -----------------------------------------------------------------
    return _scan_analysis_frames(tickers)


def _market_scan_report(
    tickers,
    analysis_frames,
    report_min_volume=0,
    latent_rules=False,
    rs_rank_of=None
):
    """
    Evaluate already collected histories; no downloads, cache writes or messages.

    共同條件：收盤在 MA20 的 −2%～+1%、最新日 K 成交量 ≥ report_min_volume；預設依成交量由大到小排序。
    latent_rules=True（只給台股 tw_all「全市場潛伏」）：另需 MA20 上彎（5 根斜率 > 0，與
    report_metrics 的 ma20Slope5 同定義），並依 RS 百分位由高到低排序（rs_rank_of(ticker) 取得；
    沒有百分位的排最後，同百分位依成交量由大到小）。
    """
    from report_metrics import ma20_slope5

    matched_list = []
    rs_ranks = {}
    for ticker in tickers:
        try:
            df = analysis_frames.get(ticker)
            if df is None or len(df) < 20:
                continue

            latest_volume = safe_float(
                df["Volume"].iloc[-1]
            )

            is_tw_stock = ticker.endswith((".TW", ".TWO"))

            if latest_volume < report_min_volume:
                continue

            df["MA20"] = (
                df["Close"]
                .rolling(
                    window=20,
                    min_periods=20
                )
                .mean()
            )

            price = safe_float(
                df["Close"].iloc[-1]
            )

            ma20_value = (
                df["MA20"].iloc[-1]
            )

            if pd.isna(ma20_value):
                continue

            ma20 = float(
                ma20_value
            )

            if not (
                ma20 * 0.98
                <= price
                <= ma20 * 1.01
            ):
                continue

            if latent_rules:
                slope = ma20_slope5(
                    df["Close"].to_numpy(dtype=float)
                )

                if slope is None or slope <= 0:
                    continue

            diff_pct = (
                (price / ma20) - 1
            ) * 100

            title = (
                f"(現價:{price:.2f} | "
                f"距MA20:{diff_pct:.2f}%)"
            )

            display_name = (
                get_tw_stock_name(ticker)
                if is_tw_stock
                else ""
            )

            chart_data = build_stock_data(
                df.tail(180),
                ticker,
                title,
                [20],
                show_volume=True,
                display_name=display_name,
                timeframe_label="日K"
            )

            matched_list.append(
                {
                    "ticker": ticker,
                    "name": display_name,
                    "volume": int(
                        latest_volume
                    ),
                    "ma_list": [20],
                    "chart_data": chart_data
                }
            )

            if latent_rules:
                rs_ranks[ticker] = _rs_rank_or_none(
                    rs_rank_of,
                    ticker
                )

        except Exception as exc:
            print(
                f"⚠️ {ticker} "
                "全市場篩選失敗："
                f"{type(exc).__name__}: "
                f"{exc}"
            )

    if latent_rules:
        matched_list.sort(
            key=lambda item: (
                rs_ranks[item["ticker"]] is None,
                -(rs_ranks[item["ticker"]] or 0),
                -item["volume"]
            )
        )
    else:
        matched_list.sort(
            key=lambda item: item["volume"],
            reverse=True
        )

    return matched_list


def _rs_rank_or_none(rs_rank_of, ticker):
    """rs_rank_of(ticker) 的 RS 百分位（1–99）；沒有函式、例外或非數值時為 None。"""
    if rs_rank_of is None:
        return None

    try:
        value = rs_rank_of(ticker)
    except Exception as exc:
        print(
            f"⚠️ {ticker} RS 百分位取得失敗："
            f"{type(exc).__name__}: {exc}"
        )
        return None

    if isinstance(value, bool):
        return None

    value = safe_float(value, None)

    if value is None or not math.isfinite(value):
        return None

    return value


def report_rs_rank_of(metrics_lookup):
    """
    tw_all 排序用的 RS 百分位：與報告群組、選股頁相同的 metrics_lookup（build_report_universe）的 rsRank；
    沒有 lookup（指標產生失敗）時一律 None（清單改依成交量排序）。不另外下載任何資料。
    """
    def rank(ticker):
        if metrics_lookup is None:
            return None

        metrics = metrics_lookup(ticker) or {}
        return metrics.get("rsRank")

    return rank


def backfill_short_csvs(
    tickers,
    max_per_run=BACKFILL_MAX_PER_RUN,
    data_dir=screener_config.DATA_DIR
):
    """
    少於 BACKFILL_MIN_ROWS 根的 CSV 下載 1 年日 K 合併（同日期以本地資料為準），
    每次最多 max_per_run 檔、最短的優先，約 8 天補完全部。
    只在 BACKFILL_ENABLED 時由 main() 呼叫；回傳實際更新的股票。
    """
    short_list = []

    for ticker in dict.fromkeys(tickers or []):
        csv_path = os.path.join(
            data_dir,
            f"{ticker}.csv"
        )

        if not os.path.exists(csv_path):
            continue

        try:
            local_data = clean_ohlcv_dataframe(
                kline.read_csv(
                    csv_path,
                    index_col=0,
                    parse_dates=True
                )
            )
        except Exception:
            continue

        if len(local_data) < BACKFILL_MIN_ROWS:
            short_list.append(
                (len(local_data), ticker)
            )

    short_list.sort()

    targets = [
        ticker
        for _, ticker in short_list[:max_per_run]
    ]

    updated = []
    chunk_size = 40

    for start in range(
        0,
        len(targets),
        chunk_size
    ):
        chunk = targets[
            start:start + chunk_size
        ]

        downloaded = download_market_data(
            chunk,
            "1y"
        )

        if downloaded.empty:
            continue

        for ticker in chunk:
            try:
                history = clean_ohlcv_dataframe(
                    extract_yfinance_data(
                        downloaded,
                        ticker
                    )
                )

                if history.empty:
                    continue

                csv_path = os.path.join(
                    data_dir,
                    f"{ticker}.csv"
                )

                local_data = clean_ohlcv_dataframe(
                    kline.read_csv(
                        csv_path,
                        index_col=0,
                        parse_dates=True
                    )
                )

                # 後者優先：同日期保留本地 CSV 的資料
                combined = clean_ohlcv_dataframe(
                    pd.concat(
                        [
                            history,
                            local_data
                        ]
                    )
                ).tail(MAX_DAYS)

                if len(combined) <= len(local_data):
                    continue

                write_kline_csv(combined, csv_path)
                updated.append(ticker)

            except Exception as exc:
                print(
                    f"⚠️ {ticker} 歷史回補失敗："
                    f"{type(exc).__name__}: {exc}"
                )

    print(
        f"📥 CSV 歷史回補：{len(updated)} 檔"
        f"（待補 {len(short_list)} 檔）"
    )

    return updated
