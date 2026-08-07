import math
from dataclasses import asdict, dataclass
from datetime import datetime

import pandas as pd
import yfinance as yf

from breakout_alert.market_clock import (
    NEW_YORK_TIMEZONE,
    TAIPEI_TIMEZONE,
)
from breakout_alert.price_provider import (
    extract_yfinance_ticker,
    normalize_ticker,
)


YAHOO_CHUNK_SIZE = 20


@dataclass(frozen=True)
class MarketAnalytics:
    ticker: str
    market: str
    ma_values: dict
    current_cumulative_volume: int | None
    previous_cumulative_volume: int | None
    volume_ratio: float | None
    volume_source: str | None
    current_session_date: str | None
    previous_session_date: str | None
    volume_cutoff_minute: int | None
    metadata: dict | None = None

    def to_dict(self):
        return asdict(self)


def safe_float(value, default=None):
    try:
        if value is None or pd.isna(value):
            return default

        result = float(value)

        if not math.isfinite(result):
            return default

        return result

    except (TypeError, ValueError):
        return default


def safe_int(value, default=None):
    try:
        if value is None or pd.isna(value):
            return default

        return int(float(value))

    except (TypeError, ValueError):
        return default


def get_market_timezone(market):
    market = str(
        market or ""
    ).strip().upper()

    if market == "TW":
        return TAIPEI_TIMEZONE

    if market == "US":
        return NEW_YORK_TIMEZONE

    raise ValueError(
        f"不支援的市場：{market}"
    )


def get_regular_open_minutes(market):
    market = str(
        market or ""
    ).strip().upper()

    if market == "TW":
        return 9 * 60

    if market == "US":
        return 9 * 60 + 30

    raise ValueError(
        f"不支援的市場：{market}"
    )


def normalize_local_datetime(
    local_time_value,
    market
):
    timezone_value = get_market_timezone(
        market
    )

    if isinstance(
        local_time_value,
        datetime
    ):
        result = local_time_value
    else:
        result = datetime.fromisoformat(
            str(local_time_value)
        )

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=timezone_value
        )
    else:
        result = result.astimezone(
            timezone_value
        )

    return result


def get_minute_offset_from_open(
    local_datetime,
    market
):
    open_minutes = (
        get_regular_open_minutes(
            market
        )
    )

    current_minutes = (
        local_datetime.hour * 60
        + local_datetime.minute
    )

    return max(
        0,
        current_minutes - open_minutes
    )


def get_timestamp_local_date(
    timestamp_value,
    market
):
    timezone_value = get_market_timezone(
        market
    )

    timestamp = pd.Timestamp(
        timestamp_value
    )

    if pd.isna(timestamp):
        return None

    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(
            timezone_value
        )
    else:
        timestamp = timestamp.tz_convert(
            timezone_value
        )

    return timestamp.date()


def get_daily_download_period(max_ma):
    max_ma = max(
        int(max_ma or 1),
        1
    )

    if max_ma <= 120:
        return "1y"

    if max_ma <= 250:
        return "2y"

    if max_ma <= 500:
        return "5y"

    return "10y"


def clean_daily_close_series(
    df,
    market,
    current_session_date
):
    if (
        df is None
        or df.empty
        or "Close" not in df.columns
    ):
        return pd.Series(
            dtype="float64"
        )

    close_series = pd.to_numeric(
        df["Close"],
        errors="coerce"
    ).dropna()

    if close_series.empty:
        return close_series

    valid_positions = []

    for index_value in close_series.index:
        try:
            local_date = (
                get_timestamp_local_date(
                    index_value,
                    market
                )
            )

            valid_positions.append(
                local_date
                < current_session_date
            )

        except Exception:
            valid_positions.append(False)

    close_series = close_series[
        valid_positions
    ]

    return close_series


def calculate_intraday_ma(
    completed_closes,
    current_price,
    ma_period
):
    ma_period = int(ma_period)

    if ma_period <= 0:
        return None

    current_price = safe_float(
        current_price
    )

    if current_price is None:
        return None

    required_previous_count = (
        ma_period - 1
    )

    if required_previous_count == 0:
        return current_price

    if len(completed_closes) < required_previous_count:
        return None

    previous_values = (
        completed_closes
        .tail(required_previous_count)
        .astype(float)
    )

    total_value = (
        float(previous_values.sum())
        + current_price
    )

    return total_value / ma_period


def normalize_minute_dataframe(
    df,
    market
):
    if df is None or df.empty:
        return pd.DataFrame()

    if "Volume" not in df.columns:
        return pd.DataFrame()

    result = pd.DataFrame(
        index=df.index.copy()
    )

    result["Volume"] = pd.to_numeric(
        df["Volume"],
        errors="coerce"
    ).fillna(0)

    result["Volume"] = (
        result["Volume"]
        .clip(lower=0)
    )

    timezone_value = get_market_timezone(
        market
    )

    normalized_index = pd.to_datetime(
        result.index,
        errors="coerce",
        utc=True
    )

    valid_mask = ~normalized_index.isna()

    result = result.loc[
        valid_mask
    ].copy()

    normalized_index = (
        normalized_index[valid_mask]
        .tz_convert(timezone_value)
    )

    result.index = normalized_index

    result = result[
        ~result.index.duplicated(
            keep="last"
        )
    ].sort_index()

    result["local_date"] = (
        result.index.date
    )

    result["minute_of_day"] = (
        result.index.hour * 60
        + result.index.minute
    )

    return result


def calculate_same_time_volume(
    minute_df,
    market,
    local_datetime
):
    minute_df = normalize_minute_dataframe(
        minute_df,
        market
    )

    if minute_df.empty:
        return {
            "current_volume": None,
            "previous_volume": None,
            "volume_ratio": None,
            "current_date": None,
            "previous_date": None,
            "cutoff_minute": None
        }

    current_date = (
        local_datetime.date()
    )

    open_minute = (
        get_regular_open_minutes(
            market
        )
    )

    elapsed_minutes = (
        get_minute_offset_from_open(
            local_datetime,
            market
        )
    )

    cutoff_minute = (
        open_minute
        + elapsed_minutes
    )

    if market == "TW":
        regular_close_minute = (
            13 * 60 + 30
        )
    else:
        regular_close_minute = (
            16 * 60
        )

    regular_rows = minute_df[
        (
            minute_df["minute_of_day"]
            >= open_minute
        )
        & (
            minute_df["minute_of_day"]
            < regular_close_minute
        )
    ].copy()

    if regular_rows.empty:
        return {
            "current_volume": None,
            "previous_volume": None,
            "volume_ratio": None,
            "current_date": (
                current_date.isoformat()
            ),
            "previous_date": None,
            "cutoff_minute": (
                elapsed_minutes
            )
        }

    available_dates = sorted(
        set(regular_rows["local_date"])
    )

    previous_dates = [
        date_value
        for date_value in available_dates
        if date_value < current_date
    ]

    if not previous_dates:
        return {
            "current_volume": None,
            "previous_volume": None,
            "volume_ratio": None,
            "current_date": (
                current_date.isoformat()
            ),
            "previous_date": None,
            "cutoff_minute": (
                elapsed_minutes
            )
        }

    previous_date = previous_dates[-1]

    current_rows = regular_rows[
        (
            regular_rows["local_date"]
            == current_date
        )
        & (
            regular_rows["minute_of_day"]
            <= cutoff_minute
        )
    ]

    previous_rows = regular_rows[
        (
            regular_rows["local_date"]
            == previous_date
        )
        & (
            regular_rows["minute_of_day"]
            <= cutoff_minute
        )
    ]

    if (
        current_rows.empty
        or previous_rows.empty
    ):
        return {
            "current_volume": None,
            "previous_volume": None,
            "volume_ratio": None,
            "current_date": (
                current_date.isoformat()
            ),
            "previous_date": (
                previous_date.isoformat()
            ),
            "cutoff_minute": (
                elapsed_minutes
            )
        }

    current_volume = safe_int(
        current_rows["Volume"].sum()
    )

    previous_volume = safe_int(
        previous_rows["Volume"].sum()
    )

    volume_ratio = None

    if (
        current_volume is not None
        and current_volume > 0
        and previous_volume is not None
        and previous_volume > 0
    ):
        volume_ratio = (
            current_volume
            / previous_volume
        )

    return {
        "current_volume": current_volume,
        "previous_volume": previous_volume,
        "volume_ratio": volume_ratio,
        "current_date": (
            current_date.isoformat()
        ),
        "previous_date": (
            previous_date.isoformat()
        ),
        "cutoff_minute": elapsed_minutes
    }


class MarketDataProvider:
    def get_daily_frames(
        self,
        ticker_configs,
        market
    ):
        frames = {}

        if not ticker_configs:
            return frames

        max_ma = max(
            max(config.get("ma_list") or [1])
            for config in ticker_configs
        )

        period = get_daily_download_period(
            max_ma
        )

        tickers = []

        for config in ticker_configs:
            ticker = normalize_ticker(
                config.get("ticker"),
                market
            )

            if ticker and ticker not in tickers:
                tickers.append(ticker)

        for start in range(
            0,
            len(tickers),
            YAHOO_CHUNK_SIZE
        ):
            chunk = tickers[
                start:start + YAHOO_CHUNK_SIZE
            ]

            try:
                downloaded = yf.download(
                    tickers=chunk,
                    period=period,
                    interval="1d",
                    progress=False,
                    threads=False,
                    auto_adjust=False,
                    group_by="column",
                    timeout=25
                )

            except Exception as exc:
                print(
                    "⚠️ Yahoo 日K批次下載失敗："
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            if downloaded.empty:
                continue

            for ticker in chunk:
                try:
                    frame = (
                        extract_yfinance_ticker(
                            downloaded,
                            ticker
                        )
                    )

                    if (
                        frame is not None
                        and not frame.empty
                    ):
                        frames[ticker] = frame

                except Exception as exc:
                    print(
                        f"⚠️ {ticker} 日K解析失敗："
                        f"{type(exc).__name__}: {exc}"
                    )

        return frames

    def get_minute_frames(
        self,
        tickers,
        market
    ):
        frames = {}

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
                    prepost=False,
                    progress=False,
                    threads=False,
                    auto_adjust=False,
                    group_by="column",
                    timeout=25
                )

            except Exception as exc:
                print(
                    "⚠️ Yahoo 成交量1分鐘K"
                    "批次下載失敗："
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            if downloaded.empty:
                continue

            for ticker in chunk:
                try:
                    frame = (
                        extract_yfinance_ticker(
                            downloaded,
                            ticker
                        )
                    )

                    if (
                        frame is not None
                        and not frame.empty
                    ):
                        frames[ticker] = frame

                except Exception as exc:
                    print(
                        f"⚠️ {ticker} 成交量1分鐘K"
                        f"解析失敗："
                        f"{type(exc).__name__}: {exc}"
                    )

        return frames

    def build_analytics(
        self,
        monitor_configs,
        quotes,
        due_decisions
    ):
        analytics = {}

        for market, decision in (
            due_decisions.items()
        ):
            market_configs = [
                config
                for config in monitor_configs
                if config.get("market") == market
            ]

            if not market_configs:
                continue

            unique_configs = {}
            combined_ma_lists = {}

            for config in market_configs:
                ticker = normalize_ticker(
                    config.get("ticker"),
                    market
                )

                if not ticker:
                    continue

                unique_configs[ticker] = config

                combined_ma_lists.setdefault(
                    ticker,
                    set()
                )

                combined_ma_lists[ticker].update(
                    config.get("ma_list") or []
                )

            daily_request_configs = []

            for ticker, config in (
                unique_configs.items()
            ):
                daily_request_configs.append(
                    {
                        **config,
                        "ticker": ticker,
                        "ma_list": sorted(
                            combined_ma_lists[ticker]
                        )
                    }
                )

            daily_frames = (
                self.get_daily_frames(
                    daily_request_configs,
                    market
                )
            )

            minute_frames = {}

            if decision.session_type == "regular":
                minute_frames = (
                    self.get_minute_frames(
                        list(unique_configs),
                        market
                    )
                )

            local_datetime = (
                normalize_local_datetime(
                    decision.local_time,
                    market
                )
            )

            current_session_date = (
                local_datetime.date()
            )

            for ticker, config in (
                unique_configs.items()
            ):
                quote = quotes.get(ticker)

                if not quote:
                    continue

                daily_frame = daily_frames.get(
                    ticker
                )

                completed_closes = (
                    clean_daily_close_series(
                        daily_frame,
                        market,
                        current_session_date
                    )
                )

                ma_values = {}

                for ma_period in sorted(
                    combined_ma_lists[ticker]
                ):
                    ma_value = (
                        calculate_intraday_ma(
                            completed_closes,
                            quote.price,
                            ma_period
                        )
                    )

                    ma_values[
                        str(int(ma_period))
                    ] = ma_value

                volume_data = {
                    "current_volume": None,
                    "previous_volume": None,
                    "volume_ratio": None,
                    "current_date": (
                        current_session_date
                        .isoformat()
                    ),
                    "previous_date": None,
                    "cutoff_minute": None
                }

                if decision.session_type == "regular":
                    minute_frame = (
                        minute_frames.get(
                            ticker
                        )
                    )

                    if minute_frame is not None:
                        volume_data = (
                            calculate_same_time_volume(
                                minute_frame,
                                market,
                                local_datetime
                            )
                        )

                analytics[ticker] = (
                    MarketAnalytics(
                        ticker=ticker,
                        market=market,
                        ma_values=ma_values,
                        current_cumulative_volume=(
                            volume_data[
                                "current_volume"
                            ]
                        ),
                        previous_cumulative_volume=(
                            volume_data[
                                "previous_volume"
                            ]
                        ),
                        volume_ratio=(
                            volume_data[
                                "volume_ratio"
                            ]
                        ),
                        volume_source=(
                            "yahoo_1m"
                            if decision.session_type
                            == "regular"
                            else None
                        ),
                        current_session_date=(
                            volume_data[
                                "current_date"
                            ]
                        ),
                        previous_session_date=(
                            volume_data[
                                "previous_date"
                            ]
                        ),
                        volume_cutoff_minute=(
                            volume_data[
                                "cutoff_minute"
                            ]
                        ),
                        metadata={
                            "ma_method": (
                                "previous_n_minus_1_"
                                "closes_plus_live_price"
                            ),
                            "session_type": (
                                decision.session_type
                            )
                        }
                    )
                )

        return analytics
