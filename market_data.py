import os
import time

import pandas as pd
import yfinance as yf
import kline_integrity as kline
from screener_common import clean_ohlcv_dataframe


TPEX_OTC_SYMBOL = "^TWOII"
# 舊版網站（aaData 格式，日期參數為民國 YYY/MM）
TPEX_INDEX_HISTORY_LEGACY_URL = (
    "https://www.tpex.org.tw/web/stock/iNdex_info/"
    "inxh/Inx_result.php"
)


def _extract_yfinance_data(
    downloaded_data,
    ticker=None
):
    if (
        downloaded_data is None
        or downloaded_data.empty
    ):
        return pd.DataFrame()

    df = downloaded_data.copy()

    if not isinstance(
        df.columns,
        pd.MultiIndex
    ):
        return df

    level_0 = [
        str(value)
        for value in df.columns.get_level_values(0)
    ]

    level_1 = [
        str(value)
        for value in df.columns.get_level_values(1)
    ]

    if ticker:
        ticker = str(ticker)

        if ticker in level_1:
            return df.xs(
                ticker,
                level=1,
                axis=1
            ).copy()

        if ticker in level_0:
            return df.xs(
                ticker,
                level=0,
                axis=1
            ).copy()

    price_fields = {
        "Open",
        "High",
        "Low",
        "Close",
        "Adj Close",
        "Volume"
    }

    if price_fields.intersection(
        set(level_0)
    ):
        df.columns = (
            df.columns.get_level_values(0)
        )
        return df

    if price_fields.intersection(
        set(level_1)
    ):
        df.columns = (
            df.columns.get_level_values(1)
        )
        return df

    return pd.DataFrame()


def refetch_kline_once(ticker):
    return _extract_yfinance_data(download_market_data([ticker], "5d"), ticker)


def extract_yfinance_data(downloaded_data, ticker=None):
    frame = _extract_yfinance_data(downloaded_data, ticker)
    if not ticker or frame.empty or not set(kline.FIELDS).issubset(frame.columns):
        return frame
    return kline.accept_frame(
        frame, ticker, refetch=lambda: refetch_kline_once(ticker),
        retained_path=kline.ROOT / 'data' / (str(ticker) + '.csv'),
        source_url="https://query1.finance.yahoo.com/v8/finance/chart/" + str(ticker),
        fetched_at=kline.utcnow(),
    )


def write_kline_csv(frame, csv_path):
    ticker = os.path.basename(csv_path).removesuffix(".csv")
    return kline.write_csv(
        csv_path, frame, symbol=ticker, refetch=lambda: refetch_kline_once(ticker),
        source_url=(TPEX_INDEX_HISTORY_LEGACY_URL if ticker == TPEX_OTC_SYMBOL else
                    "https://query1.finance.yahoo.com/v8/finance/chart/" + ticker),
        fetched_at=kline.utcnow(),
    )


def download_single_ticker_with_retry(
    ticker,
    period="15d",
    max_attempts=3
):
    ticker = str(
        ticker or ""
    ).strip().upper()

    if not ticker:
        return pd.DataFrame()

    for attempt in range(
        1,
        max_attempts + 1
    ):
        try:
            print(
                f"⬇️ 單檔下載 {ticker} "
                f"({attempt}/{max_attempts})"
            )

            downloaded = yf.download(
                ticker,
                period=period,
                interval="1d",
                progress=False,
                threads=False,
                auto_adjust=False,
                actions=False,
                group_by="column",
                timeout=30
            )

            df = extract_yfinance_data(
                downloaded,
                ticker
            )

            df = clean_ohlcv_dataframe(df)

            if not df.empty:
                print(
                    f"✅ {ticker} 單檔下載成功，"
                    f"最新日期："
                    f"{df.index[-1]:%Y-%m-%d}"
                )

                return df

            print(
                f"⚠️ {ticker} 單檔下載為空"
            )

        except Exception as exc:
            print(
                f"⚠️ {ticker} 單檔下載失敗："
                f"{type(exc).__name__}: {exc}"
            )

        time.sleep(
            min(attempt * 2, 6)
        )

    return pd.DataFrame()

def download_market_data(
    tickers,
    period,
    max_attempts=3
):
    if not tickers:
        return pd.DataFrame()

    for attempt in range(
        1,
        max_attempts + 1
    ):
        try:
            downloaded = yf.download(
                tickers,
                period=period,
                interval="1d",
                progress=False,
                threads=False,
                auto_adjust=False,
                actions=False,
                group_by="column",
                timeout=45
            )

            if (
                downloaded is not None
                and not downloaded.empty
            ):
                return downloaded

            print(
                "⚠️ 全市場批次下載為空："
                f"第 {attempt}/{max_attempts} 次"
            )

        except Exception as exc:
            print(
                "⚠️ 全市場批次下載失敗："
                f"第 {attempt}/{max_attempts} 次，"
                f"{type(exc).__name__}: {exc}"
            )

        time.sleep(
            min(attempt * 3, 9)
        )

    print(
        "❌ 全市場批次下載重試後仍失敗"
    )

    return pd.DataFrame()
