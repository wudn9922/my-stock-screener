from datetime import datetime
from zoneinfo import ZoneInfo

from breakout_alert.supabase_store import (
    SupabaseStore,
)


TAIPEI_TIMEZONE = ZoneInfo(
    "Asia/Taipei"
)

NEW_YORK_TIMEZONE = ZoneInfo(
    "America/New_York"
)


def normalize_query_ticker(value):
    ticker = str(
        value or ""
    ).strip().upper()

    ticker = ticker.replace(" ", "")

    if not ticker:
        return ""

    # 美股 Yahoo 代號格式
    if not ticker.isdigit():
        if (
            not ticker.endswith(".TW")
            and not ticker.endswith(".TWO")
        ):
            ticker = ticker.replace(".", "-")

    return ticker


def get_ticker_candidates(value):
    ticker = normalize_query_ticker(
        value
    )

    if not ticker:
        return []

    if (
        ticker.endswith(".TW")
        or ticker.endswith(".TWO")
    ):
        return [ticker]

    # 純數字代號同時嘗試上市與上櫃
    if ticker.isdigit():
        return [
            f"{ticker}.TW",
            f"{ticker}.TWO"
        ]

    return [ticker]


def safe_float(value):
    try:
        if value is None:
            return None

        return float(value)

    except (TypeError, ValueError):
        return None


def format_price(value):
    value = safe_float(value)

    if value is None:
        return "--"

    return f"{value:,.2f}"


def format_ratio(value):
    value = safe_float(value)

    if value is None:
        return "無資料"

    return f"{value:.2f} 倍"


def format_datetime(
    value,
    market
):
    if not value:
        return "--"

    try:
        timestamp = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00"
            )
        )

        timezone_value = (
            TAIPEI_TIMEZONE
            if market == "TW"
            else NEW_YORK_TIMEZONE
        )

        local_timestamp = (
            timestamp.astimezone(
                timezone_value
            )
        )

        return local_timestamp.strftime(
            "%m/%d %H:%M"
        )

    except (TypeError, ValueError):
        return "--"


def get_display_name(rows):
    for row in rows:
        metadata = row.get(
            "metadata"
        )

        if not isinstance(metadata, dict):
            continue

        display_name = str(
            metadata.get(
                "display_name"
            )
            or ""
        ).strip()

        if display_name:
            return display_name

    return ""


def get_group_name(row):
    metadata = row.get(
        "metadata"
    )

    if isinstance(metadata, dict):
        group_name = str(
            metadata.get(
                "group_name"
            )
            or ""
        ).strip()

        if group_name:
            return group_name

    return str(
        row.get("group_id")
        or ""
    ).strip()


def build_distance_text(
    price,
    ma_value
):
    price = safe_float(price)
    ma_value = safe_float(ma_value)

    if (
        price is None
        or ma_value is None
        or ma_value == 0
    ):
        return "價差 --｜距離 --"

    price_difference = (
        price - ma_value
    )

    percentage_difference = (
        price_difference / ma_value
    ) * 100

    return (
        f"價差 {price_difference:+,.2f}"
        f"｜距離 {percentage_difference:+.2f}%"
    )


def select_latest_value(
    rows,
    column_name
):
    available_rows = [
        row
        for row in rows
        if row.get(column_name) is not None
    ]

    if not available_rows:
        return None

    available_rows.sort(
        key=lambda row: str(
            row.get(
                "last_regular_checked_at"
            )
            or row.get(
                "last_checked_at"
            )
            or ""
        ),
        reverse=True
    )

    return available_rows[0].get(
        column_name
    )


class CacheQueryService:
    def __init__(self):
        self.store = SupabaseStore()

    def find_states(self, raw_ticker):
        for candidate in get_ticker_candidates(
            raw_ticker
        ):
            rows = (
                self.store
                .get_cached_ticker_states(
                    candidate
                )
            )

            if rows:
                return candidate, rows

        return None, []

    def build_volume_ratio_message(
        self,
        raw_ticker
    ):
        ticker, rows = self.find_states(
            raw_ticker
        )

        if not rows:
            return (
                "查無此股票的監控快取，"
                "請確認代號或先加入監控群組。"
            )

        # 同一股票可能存在不同群組；
        # 依群組分開顯示，避免不同均線混在一起。
        grouped_rows = {}

        for row in rows:
            group_id = str(
                row.get("group_id")
                or ""
            )

            grouped_rows.setdefault(
                group_id,
                []
            ).append(row)

        first_row = rows[0]
        market = str(
            first_row.get("market")
            or ""
        )

        display_name = get_display_name(
            rows
        )

        name_text = (
            f" {display_name}"
            if display_name
            else ""
        )

        latest_rows = sorted(
            rows,
            key=lambda row: str(
                row.get(
                    "last_checked_at"
                )
                or ""
            ),
            reverse=True
        )

        latest_row = latest_rows[0]

        latest_price = safe_float(
            latest_row.get(
                "last_price"
            )
        )

        latest_checked_at = (
            latest_row.get(
                "last_checked_at"
            )
        )

        price_source = str(
            latest_row.get(
                "price_source"
            )
            or "--"
        )

        regular_ratio = select_latest_value(
            rows,
            "last_regular_volume_ratio"
        )

        regular_checked_at = (
            select_latest_value(
                rows,
                "last_regular_checked_at"
            )
        )

        lines = [
            f"📊 {ticker}{name_text}",
            (
                f"監控價 {format_price(latest_price)}"
                f"｜更新 "
                f"{format_datetime(latest_checked_at, market)}"
                f"｜{price_source}"
            ),
            (
                f"量比 {format_ratio(regular_ratio)}"
                f"｜正常盤更新 "
                f"{format_datetime(regular_checked_at, market)}"
            )
        ]

        for group_id, group_rows in (
            grouped_rows.items()
        ):
            group_rows.sort(
                key=lambda row: int(
                    row.get("ma_period")
                    or 0
                )
            )

            if len(grouped_rows) > 1:
                lines.append(
                    f"【{get_group_name(group_rows[0])}】"
                )

            for row in group_rows:
                ma_period = int(
                    row.get("ma_period")
                    or 0
                )

                ma_value = safe_float(
                    row.get(
                        "last_ma_value"
                    )
                )

                row_price = safe_float(
                    row.get(
                        "last_price"
                    )
                )

                if row_price is None:
                    row_price = latest_price

                lines.append(
                    f"MA{ma_period} "
                    f"{format_price(ma_value)}"
                    f"｜{build_distance_text(row_price, ma_value)}"
                )

        message = "\n".join(lines)

        # Discord 單則訊息上限為 2,000 字元
        return message[:1950]

    def close(self):
        self.store.close()