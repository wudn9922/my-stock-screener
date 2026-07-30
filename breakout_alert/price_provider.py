import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf


TWSE_MIS_URL = (
    "https://mis.twse.com.tw/"
    "stock/api/getStockInfo.jsp"
)

TWSE_INDEX_URL = (
    "https://mis.twse.com.tw/"
    "stock/index.jsp"
)

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0.0.0 "
        "Safari/537.36"
    ),
    "Accept": (
        "application/json,"
        "text/plain,*/*"
    ),
    "Referer": TWSE_INDEX_URL
}

YAHOO_CHUNK_SIZE = 20
TWSE_CHUNK_SIZE = 40


@dataclass(frozen=True)
class PriceQuote:
    ticker: str
    market: str
    price: float
    source: str
    quoted_at: str
    display_name: str = ""
    session_type: str = ""
    metadata: dict | None = None

    def to_dict(self):
        return asdict(self)


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = (
                value.strip()
                .replace(",", "")
            )

            if value in {
                "",
                "-",
                "--",
                "null",
                "None"
            }:
                return default

        result = float(value)

        if not math.isfinite(result):
            return default

        if result <= 0:
            return default

        return result

    except (TypeError, ValueError):
        return default


def normalize_ticker(ticker, market):
    ticker = str(
        ticker or ""
    ).strip().upper()

    if market == "US":
        return ticker.replace(".", "-")

    return ticker


def utc_now_string():
    return (
        datetime.now(timezone.utc)
        .isoformat()
    )


def normalize_timestamp(index_value):
    try:
        timestamp = pd.Timestamp(
            index_value
        )

        if pd.isna(timestamp):
            return utc_now_string()

        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(
                "UTC"
            )
        else:
            timestamp = timestamp.tz_convert(
                "UTC"
            )

        return timestamp.isoformat()

    except Exception:
        return utc_now_string()


def parse_twse_timestamp(item):
    tlong = str(
        item.get("tlong") or ""
    ).strip()

    if tlong.isdigit():
        try:
            timestamp = datetime.fromtimestamp(
                int(tlong) / 1000,
                tz=timezone.utc
            )

            return timestamp.isoformat()

        except (
            ValueError,
            OSError,
            OverflowError
        ):
            pass

    date_text = str(
        item.get("d") or ""
    ).strip()

    time_text = str(
        item.get("t") or ""
    ).strip()

    if len(date_text) == 8 and time_text:
        try:
            local_datetime = datetime.strptime(
                f"{date_text} {time_text}",
                "%Y%m%d %H:%M:%S"
            )

            # TWSE MIS 的 d、t 為台北時間。
            try:
                from zoneinfo import ZoneInfo

                local_datetime = (
                    local_datetime.replace(
                        tzinfo=ZoneInfo(
                            "Asia/Taipei"
                        )
                    )
                )

                return (
                    local_datetime
                    .astimezone(timezone.utc)
                    .isoformat()
                )

            except Exception:
                return local_datetime.isoformat()

        except ValueError:
            pass

    return utc_now_string()


def get_twse_channel(ticker):
    ticker = str(
        ticker or ""
    ).strip().upper()

    if ticker.endswith(".TWO"):
        code = ticker[:-4]

        if code:
            return f"otc_{code}.tw"

    if ticker.endswith(".TW"):
        code = ticker[:-3]

        if code:
            return f"tse_{code}.tw"

    return None


def extract_yfinance_ticker(
    downloaded_data,
    ticker
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

    ticker = str(ticker)

    level_0 = [
        str(value)
        for value in df.columns.get_level_values(0)
    ]

    level_1 = [
        str(value)
        for value in df.columns.get_level_values(1)
    ]

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


class PriceProvider:
    def __init__(self):
        self.http = requests.Session()

        self.http.headers.update(
            HTTP_HEADERS
        )

        self._twse_session_primed = False

    def _prime_twse_session(self):
        if self._twse_session_primed:
            return

        try:
            self.http.get(
                TWSE_INDEX_URL,
                timeout=10
            )

        except requests.RequestException:
            # 首頁 Cookie 取得失敗時仍可直接呼叫 API。
            pass

        self._twse_session_primed = True

    def get_twse_mis_quotes(
        self,
        tickers,
        session_type="regular"
    ):
        quotes = {}

        ticker_to_channel = {}

        for raw_ticker in tickers:
            ticker = normalize_ticker(
                raw_ticker,
                "TW"
            )

            channel = get_twse_channel(
                ticker
            )

            if channel:
                ticker_to_channel[
                    ticker
                ] = channel

        if not ticker_to_channel:
            return quotes

        self._prime_twse_session()

        ticker_items = list(
            ticker_to_channel.items()
        )

        for start in range(
            0,
            len(ticker_items),
            TWSE_CHUNK_SIZE
        ):
            chunk = ticker_items[
                start:start + TWSE_CHUNK_SIZE
            ]

            channels = "|".join(
                channel
                for _, channel in chunk
            )

            try:
                response = self.http.get(
                    TWSE_MIS_URL,
                    params={
                        "ex_ch": channels,
                        "json": "1",
                        "delay": "0",
                        "_": str(
                            int(time.time() * 1000)
                        )
                    },
                    timeout=15
                )

                response.raise_for_status()

                payload = response.json()

                msg_array = payload.get(
                    "msgArray",
                    []
                )

                if not isinstance(
                    msg_array,
                    list
                ):
                    continue

                for item in msg_array:
                    if not isinstance(item, dict):
                        continue

                    code = str(
                        item.get("c") or ""
                    ).strip()

                    exchange_code = str(
                        item.get("ex") or ""
                    ).strip().lower()

                    if not code:
                        continue

                    if exchange_code == "otc":
                        ticker = f"{code}.TWO"
                    else:
                        ticker = f"{code}.TW"

                    price = safe_float(
                        item.get("z")
                    )

                    # 無最新成交價時，不使用委買／委賣價
                    # 假裝成成交價，直接交給 Yahoo 備援。
                    if price is None:
                        continue

                    display_name = str(
                        item.get("n") or ""
                    ).strip()

                    quotes[ticker] = PriceQuote(
                        ticker=ticker,
                        market="TW",
                        price=price,
                        source="twse_mis",
                        quoted_at=(
                            parse_twse_timestamp(
                                item
                            )
                        ),
                        display_name=display_name,
                        session_type=session_type,
                        metadata={
                            "exchange": (
                                exchange_code
                            ),
                            "reference_price": (
                                safe_float(
                                    item.get("y")
                                )
                            )
                        }
                    )

            except requests.RequestException as exc:
                print(
                    "⚠️ TWSE MIS 請求失敗："
                    f"{type(exc).__name__}"
                )

            except ValueError:
                print(
                    "⚠️ TWSE MIS 回傳格式錯誤"
                )

            except Exception as exc:
                print(
                    "⚠️ TWSE MIS 處理失敗："
                    f"{type(exc).__name__}: {exc}"
                )

        return quotes

    def get_yahoo_minute_quotes(
        self,
        tickers,
        market,
        session_type
    ):
        quotes = {}

        normalized_tickers = []

        for ticker in tickers:
            normalized_ticker = (
                normalize_ticker(
                    ticker,
                    market
                )
            )

            if (
                normalized_ticker
                and normalized_ticker
                not in normalized_tickers
            ):
                normalized_tickers.append(
                    normalized_ticker
                )

        if not normalized_tickers:
            return quotes

        include_prepost = (
            market == "US"
        )

        for start in range(
            0,
            len(normalized_tickers),
            YAHOO_CHUNK_SIZE
        ):
            chunk = normalized_tickers[
                start:start + YAHOO_CHUNK_SIZE
            ]

            try:
                downloaded = yf.download(
                    tickers=chunk,
                    period="5d",
                    interval="1m",
                    prepost=include_prepost,
                    progress=False,
                    threads=False,
                    auto_adjust=False,
                    group_by="column",
                    timeout=20
                )

            except Exception as exc:
                print(
                    "⚠️ Yahoo 1分鐘K批次下載失敗："
                    f"{type(exc).__name__}: {exc}"
                )

                continue

            if downloaded.empty:
                continue

            for ticker in chunk:
                try:
                    df = extract_yfinance_ticker(
                        downloaded,
                        ticker
                    )

                    if (
                        df.empty
                        or "Close" not in df.columns
                    ):
                        continue

                    close_values = pd.to_numeric(
                        df["Close"],
                        errors="coerce"
                    )

                    valid_close = (
                        close_values.dropna()
                    )

                    if valid_close.empty:
                        continue

                    latest_index = (
                        valid_close.index[-1]
                    )

                    latest_price = safe_float(
                        valid_close.iloc[-1]
                    )

                    if latest_price is None:
                        continue

                    latest_volume = None

                    if "Volume" in df.columns:
                        volume_values = pd.to_numeric(
                            df["Volume"],
                            errors="coerce"
                        )

                        if latest_index in volume_values.index:
                            raw_volume = volume_values.loc[
                                latest_index
                            ]

                            if not pd.isna(raw_volume):
                                latest_volume = float(
                                    raw_volume
                                )

                    quotes[ticker] = PriceQuote(
                        ticker=ticker,
                        market=market,
                        price=latest_price,
                        source="yahoo_1m",
                        quoted_at=(
                            normalize_timestamp(
                                latest_index
                            )
                        ),
                        session_type=session_type,
                        metadata={
                            "prepost": (
                                include_prepost
                            ),
                            "minute_volume": (
                                latest_volume
                            )
                        }
                    )

                except Exception as exc:
                    print(
                        f"⚠️ {ticker} Yahoo 1分鐘K"
                        f"解析失敗："
                        f"{type(exc).__name__}: {exc}"
                    )

        return quotes

    def get_fast_info_quote(
        self,
        ticker,
        market,
        session_type
    ):
        ticker = normalize_ticker(
            ticker,
            market
        )

        try:
            fast_info = yf.Ticker(
                ticker
            ).fast_info

            price = safe_float(
                fast_info.get(
                    "last_price"
                )
            )

            if price is None:
                price = safe_float(
                    fast_info.get(
                        "regular_market_price"
                    )
                )

            if price is None:
                return None

            return PriceQuote(
                ticker=ticker,
                market=market,
                price=price,
                source="yahoo_fast_info",
                quoted_at=utc_now_string(),
                session_type=session_type,
                metadata={
                    "fallback": True
                }
            )

        except Exception as exc:
            print(
                f"⚠️ {ticker} fast_info "
                f"取得失敗："
                f"{type(exc).__name__}: {exc}"
            )

            return None

    def get_market_quotes(
        self,
        tickers,
        market,
        session_type
    ):
        market = str(
            market or ""
        ).strip().upper()

        normalized_tickers = []

        for ticker in tickers:
            normalized_ticker = (
                normalize_ticker(
                    ticker,
                    market
                )
            )

            if (
                normalized_ticker
                and normalized_ticker
                not in normalized_tickers
            ):
                normalized_tickers.append(
                    normalized_ticker
                )

        quotes = {}

        if market == "TW":
            mis_quotes = (
                self.get_twse_mis_quotes(
                    normalized_tickers,
                    session_type=session_type
                )
            )

            quotes.update(
                mis_quotes
            )

        unresolved = [
            ticker
            for ticker in normalized_tickers
            if ticker not in quotes
        ]

        if unresolved:
            yahoo_quotes = (
                self.get_yahoo_minute_quotes(
                    unresolved,
                    market,
                    session_type
                )
            )

            quotes.update(
                yahoo_quotes
            )

        unresolved = [
            ticker
            for ticker in normalized_tickers
            if ticker not in quotes
        ]

        for ticker in unresolved:
            fast_info_quote = (
                self.get_fast_info_quote(
                    ticker,
                    market,
                    session_type
                )
            )

            if fast_info_quote:
                quotes[ticker] = (
                    fast_info_quote
                )

        return quotes

    def get_due_quotes(
        self,
        monitor_configs,
        due_decisions
    ):
        all_quotes = {}

        for market, decision in (
            due_decisions.items()
        ):
            market_tickers = []

            for config in monitor_configs:
                if config.get("market") != market:
                    continue

                ticker = normalize_ticker(
                    config.get("ticker"),
                    market
                )

                if (
                    ticker
                    and ticker not in market_tickers
                ):
                    market_tickers.append(
                        ticker
                    )

            if not market_tickers:
                continue

            market_quotes = (
                self.get_market_quotes(
                    market_tickers,
                    market,
                    decision.session_type
                )
            )

            all_quotes.update(
                market_quotes
            )

        return all_quotes

    def close(self):
        self.http.close()
