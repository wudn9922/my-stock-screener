import io
import time

import pandas as pd
import requests

from screener_common import register_tw_stock_name
from screener_config import HTTP_HEADERS


# =========================================================================
# 台美股清單
# =========================================================================
def get_tw_tickers():
    tickers = []

    twse_url = (
        "https://www.twse.com.tw/"
        "exchangeReport/STOCK_DAY_ALL"
        "?response=open_data"
    )

    for attempt in range(3):
        try:
            response = requests.get(
                twse_url,
                headers=HTTP_HEADERS,
                timeout=20
            )

            if response.status_code != 200:
                print(
                    "⚠️ TWSE HTTP 狀態："
                    f"{response.status_code}"
                )
                time.sleep(2)
                continue

            df_twse = pd.read_csv(
                io.StringIO(response.text)
            )

            code_col = (
                "證券代號"
                if "證券代號" in df_twse.columns
                else df_twse.columns[0]
            )

            name_col = (
                "證券名稱"
                if "證券名稱" in df_twse.columns
                else None
            )

            for _, row in df_twse.iterrows():
                try:
                    code = str(
                        row[code_col]
                    ).strip()

                    if (
                        len(code) != 4
                        or not code.isdigit()
                        or code.startswith("0")
                    ):
                        continue

                    ticker = f"{code}.TW"

                    if name_col:
                        register_tw_stock_name(
                            ticker,
                            row.get(name_col, "")
                        )

                    tickers.append(ticker)

                except Exception:
                    continue

            break

        except Exception as exc:
            print(
                "⚠️ TWSE 讀取失敗 "
                f"({attempt + 1}/3)：{exc}"
            )
            time.sleep(2)

    tpex_url = (
        "https://www.tpex.org.tw/"
        "openapi/v1/"
        "tpex_mainboard_daily_close_quotes"
    )

    for attempt in range(3):
        try:
            response = requests.get(
                tpex_url,
                headers=HTTP_HEADERS,
                timeout=20
            )

            if response.status_code != 200:
                print(
                    "⚠️ TPEx HTTP 狀態："
                    f"{response.status_code}"
                )
                time.sleep(2)
                continue

            items = response.json()

            if not isinstance(items, list):
                print(
                    "⚠️ TPEx 回傳格式不是清單"
                )
                break

            for item in items:
                code = str(
                    item.get(
                        "SecuritiesCompanyCode",
                        ""
                    )
                ).strip()

                if (
                    len(code) != 4
                    or not code.isdigit()
                ):
                    continue

                ticker = f"{code}.TWO"
                company_name = ""

                for name_key in [
                    "CompanyName",
                    "SecuritiesCompanyName",
                    "SecuritiesName",
                    "公司名稱",
                    "證券名稱"
                ]:
                    if item.get(name_key):
                        company_name = str(
                            item.get(name_key)
                        ).strip()
                        break

                register_tw_stock_name(
                    ticker,
                    company_name
                )

                tickers.append(ticker)

            break

        except Exception as exc:
            print(
                "⚠️ TPEx 讀取失敗 "
                f"({attempt + 1}/3)：{exc}"
            )
            time.sleep(2)

    tickers = sorted(set(tickers))

    if not tickers:
        print(
            "⚠️ 無法取得台股清單，"
            "改用預設股票"
        )

        return [
            "2330.TW",
            "2317.TW",
            "2454.TW",
            "2603.TW",
            "0050.TW"
        ]

    print(
        f"📋 台股候選清單："
        f"{len(tickers)} 檔"
    )

    return tickers


def get_us_tickers():
    try:
        url = (
            "https://en.wikipedia.org/wiki/"
            "List_of_S%26P_500_companies"
        )

        response = requests.get(
            url,
            headers=HTTP_HEADERS,
            timeout=20
        )

        response.raise_for_status()

        table = pd.read_html(
            io.StringIO(response.text)
        )[0]

        tickers = [
            str(ticker)
            .strip()
            .upper()
            .replace(".", "-")
            for ticker in table[
                "Symbol"
            ].tolist()
        ]

        tickers = sorted(set(tickers))

        print(
            f"📋 美股候選清單："
            f"{len(tickers)} 檔"
        )

        return tickers

    except Exception as exc:
        print(
            f"⚠️ 美股清單取得失敗：{exc}"
        )

        return [
            "AAPL",
            "MSFT",
            "NVDA"
        ]
