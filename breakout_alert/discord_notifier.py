import math
import time

import requests

from breakout_alert.config import (
    get_discord_webhook_url,
)


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        result = float(value)

        if not math.isfinite(result):
            return default

        return result

    except (TypeError, ValueError):
        return default


def format_number(value):
    number_value = safe_float(value)

    if number_value is None:
        return "--"

    return f"{number_value:,.2f}"


def format_volume_ratio(volume_ratio):
    volume_ratio = safe_float(
        volume_ratio
    )

    if (
        volume_ratio is None
        or volume_ratio <= 0
    ):
        return "無資料"

    return (
        f"昨日同時段 "
        f"{volume_ratio:.2f} 倍"
    )


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
        price_difference
        / ma_value
        * 100
    )

    return (
        f"價差 {price_difference:+,.2f}"
        f"｜距離 {percentage_difference:+.2f}%"
    )


def build_other_ma_text(
    ma_period,
    ma_values
):
    if not isinstance(ma_values, dict):
        return "無"

    other_ma_items = []

    for raw_period, raw_value in (
        ma_values.items()
    ):
        try:
            current_period = int(
                raw_period
            )
        except (TypeError, ValueError):
            continue

        if current_period == int(ma_period):
            continue

        ma_value = safe_float(
            raw_value
        )

        if ma_value is None:
            continue

        other_ma_items.append(
            (
                current_period,
                ma_value
            )
        )

    other_ma_items.sort(
        key=lambda item: item[0]
    )

    if not other_ma_items:
        return "無"

    return "｜".join(
        f"MA{period} {value:,.2f}"
        for period, value
        in other_ma_items
    )


class DiscordNotifier:
    def __init__(self):
        self.webhook_url = (
            get_discord_webhook_url()
        )

        self.session = requests.Session()

    def send_breakout(
        self,
        *,
        ticker,
        display_name,
        group_name,
        ma_period,
        ma_value,
        ma_values,
        price,
        volume_ratio,
        direction
    ):
        if direction == "breakout_up":
            icon = "🚨"
            direction_text = "向上突破 1%"
        elif direction == "breakout_down":
            icon = "⚠️"
            direction_text = "向下跌破 1.5%"
        else:
            raise ValueError(
                f"不支援的提醒方向：{direction}"
            )

        title_parts = [ticker]

        if display_name:
            title_parts.append(
                display_name
            )

        title = " ".join(title_parts)

        distance_text = build_distance_text(
            price,
            ma_value
        )

        other_ma_text = build_other_ma_text(
            ma_period,
            ma_values
        )

        content = (
            f"{icon} {title}｜{group_name}\n"
            f"{direction_text}｜"
            f"MA{int(ma_period)} "
            f"{format_number(ma_value)}｜"
            f"現價 {format_number(price)}\n"
            f"{distance_text}\n"
            f"其他均線｜{other_ma_text}\n"
            f"量能｜"
            f"{format_volume_ratio(volume_ratio)}"
        )

        payload = {
            "content": content[:1950],
            "allowed_mentions": {
                "parse": []
            }
        }

        for attempt in range(3):
            response = self.session.post(
                self.webhook_url,
                json=payload,
                timeout=15
            )

            if response.status_code == 204:
                print(
                    f"✅ Discord 提醒成功："
                    f"{ticker} MA{ma_period}"
                )

                return True

            if response.status_code == 429:
                retry_after = 1.0

                try:
                    response_data = (
                        response.json()
                    )

                    retry_after = float(
                        response_data.get(
                            "retry_after",
                            1.0
                        )
                    )

                except (
                    ValueError,
                    TypeError
                ):
                    retry_after = 1.0

                time.sleep(
                    min(
                        max(retry_after, 1.0),
                        10.0
                    )
                )

                continue

            raise RuntimeError(
                "Discord 發送失敗，"
                f"HTTP {response.status_code}："
                f"{response.text[:300]}"
            )

        raise RuntimeError(
            "Discord 發送失敗："
            "超過重試次數"
        )

    def close(self):
        self.session.close()