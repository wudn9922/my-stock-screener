from urllib.parse import quote

import requests


# Yahoo Finance 常見的美國交易所代碼與名稱
US_EXCHANGES = {
    "NMS",          # Nasdaq Global Select
    "NGM",          # Nasdaq Global Market
    "NCM",          # Nasdaq Capital Market
    "NYQ",          # NYSE
    "ASE",          # NYSE American
    "PCX",          # NYSE Arca
    "BTS",          # Cboe
    "NASDAQ",
    "NYSE",
    "NYSEArca",
    "NasdaqGS",
    "NasdaqGM",
    "NasdaqCM",
    "NYSE American",
    "Cboe US",
}


def get_yahoo_chart_metadata(symbol, session):
    """取得 Yahoo 商品類型與交易所資訊。"""
    encoded_symbol = quote(symbol, safe="")

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{encoded_symbol}"
    )

    params = {
        "interval": "1d",
        "range": "1d",
    }

    response = session.get(
        url,
        params=params,
        timeout=15,
    )
    response.raise_for_status()

    data = response.json()
    results = data.get("chart", {}).get("result") or []

    if not results:
        return None

    return results[0].get("meta", {})


def is_us_listed_stock(symbol, session):
    """
    只保留美國交易所掛牌的 EQUITY。

    排除：
    - ETF
    - 指數
    - 加密貨幣
    - 期貨
    - 共同基金
    - 非美國交易所股票
    """
    try:
        metadata = get_yahoo_chart_metadata(
            symbol,
            session,
        )

        if not metadata:
            return False

        instrument_type = str(
            metadata.get("instrumentType", "")
        ).upper()

        exchange_name = str(
            metadata.get("exchangeName", "")
        ).strip()

        full_exchange_name = str(
            metadata.get("fullExchangeName", "")
        ).strip()

        # 只接受股票，會排除 ETF、INDEX、CRYPTOCURRENCY、FUTURE 等
        if instrument_type != "EQUITY":
            print(
                f"排除 {symbol}：商品類型為 "
                f"{instrument_type or '未知'}"
            )
            return False

        if (
            exchange_name not in US_EXCHANGES
            and full_exchange_name not in US_EXCHANGES
        ):
            print(
                f"排除 {symbol}：非指定美國交易所 "
                f"({exchange_name} / {full_exchange_name})"
            )
            return False

        print(
            f"保留 {symbol}："
            f"{instrument_type}, "
            f"{full_exchange_name or exchange_name}"
        )

        return True

    except Exception as e:
        print(f"驗證 {symbol} 失敗：{e}")
        return False


def fetch_yahoo_realtime_trending(limit=5):
    """取得 Yahoo 熱門商品，再篩選為美國交易所掛牌股票。"""
    print("正在取得 Yahoo Finance 美國熱門商品...")

    url = "https://query1.finance.yahoo.com/v1/finance/trending/US"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }

    try:
        with requests.Session() as session:
            session.headers.update(headers)

            response = session.get(
                url,
                timeout=15,
            )
            response.raise_for_status()

            data = response.json()

            results = data.get("finance", {}).get("result") or []

            if not results:
                print("Yahoo 熱門商品清單為空。")
                return ""

            trending_list = results[0].get("quotes") or []

            filtered_symbols = []
            checked_symbols = set()

            for item in trending_list:
                symbol = item.get("symbol")
                quote_type = str(
                    item.get("quoteType", "")
                ).upper()

                if not symbol or symbol in checked_symbols:
                    continue

                checked_symbols.add(symbol)

                # 若熱門 API 已明確表示不是股票，直接排除
                if quote_type and quote_type != "EQUITY":
                    print(
                        f"排除 {symbol}："
                        f"熱門 API 類型為 {quote_type}"
                    )
                    continue

                if is_us_listed_stock(symbol, session):
                    filtered_symbols.append(symbol)

                if len(filtered_symbols) >= limit:
                    break

            if not filtered_symbols:
                print("目前沒有取得符合條件的美國熱門股票。")
                return ""

            result = ", ".join(filtered_symbols)

            print(f"篩選後的美國熱門股票：{result}")

            return result

    except Exception as e:
        print(f"Yahoo 熱門商品 API 呼叫失敗：{e}")
        return ""
