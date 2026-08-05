import time

import requests

from breakout_alert.config import (
    get_discord_webhook_url,
)


def format_number(value):
    number_value = float(value)

    return f"{number_value:,.2f}"


def format_volume_ratio(volume_ratio):
    if volume_ratio is None:
        return "無資料"

    return f"昨日同時段 {float(volume_ratio):.2f} 倍"


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
        price,
        volume_ratio,
        direction
    ):
        if direction == "breakout_up":
            icon = "🚨"
            direction_text = "向上突破 +0.5%"
        elif direction == "breakout_down":
            icon = "⚠️"
            direction_text = "向下跌破 -1.0%"
        else:
            raise ValueError(
                f"不支援的提醒方向：{direction}"
            )

        title_parts = [
            ticker
        ]

        if display_name:
            title_parts.append(
                display_name
            )

        title = " ".join(title_parts)

        content = (
            f"{icon} {title}｜{group_name}\n"
            f"{direction_text} MA{int(ma_period)} "
            f"{format_number(ma_value)}｜"
            f"現價 {format_number(price)}\n"
            f"量能｜"
            f"{format_volume_ratio(volume_ratio)}"
        )

        payload = {
            "content": content,
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
