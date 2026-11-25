import pandas as pd
import yfinance as yf

from market_data import extract_yfinance_data
from screener_common import clean_ohlcv_dataframe
from tpex_otc import TPEX_OTC_SYMBOL, get_tpex_otc_history


# 指數歷史快取：ticker → {"df": 清理後 DataFrame, "source": "TPEx|Yahoo"}
INDEX_HISTORY_CACHE = {}


def download_index_history(
    ticker,
    **yf_kwargs
):
    """
    指數日 K 下載入口：
    - ^TWOII 有新鮮的櫃買中心資料時直接使用；
    - 其他指數（或 TPEx 不可用時）照舊呼叫 yf.download(ticker, **yf_kwargs)。
    下載結果會清理後存入 INDEX_HISTORY_CACHE，供報告 JSON 讀取收盤、漲跌與均線。
    """
    normalized_ticker = str(
        ticker
    ).strip().upper()

    if normalized_ticker == TPEX_OTC_SYMBOL:
        try:
            tpex_df = get_tpex_otc_history()
        except Exception as exc:
            print(
                "⚠️ 讀取 TPEx 櫃買指數失敗："
                f"{type(exc).__name__}: {exc}"
            )
            tpex_df = pd.DataFrame()

        if not tpex_df.empty:
            INDEX_HISTORY_CACHE[
                normalized_ticker
            ] = {
                "df": tpex_df.copy(),
                "source": "TPEx"
            }

            return tpex_df.copy()

    downloaded = yf.download(
        ticker,
        **yf_kwargs
    )

    try:
        INDEX_HISTORY_CACHE[
            normalized_ticker
        ] = {
            "df": clean_ohlcv_dataframe(
                extract_yfinance_data(
                    downloaded,
                    ticker
                )
            ),
            "source": "Yahoo"
        }
    except Exception:
        pass

    return downloaded
