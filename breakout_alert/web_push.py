"""
Atlas 手機通知（標準 Web Push）。

Discord 提醒成功後，另外請 Supabase user-settings 函式（push.notify）把同一則提醒
推播到該帳號訂閱的手機；點通知直接開啟該股票的 Atlas 圖表。

- 未設定 WEB_PUSH_NOTIFY_URL 時完全略過，不影響原本的 Discord 提醒。
- 驗證用 Cloud Run 已有的 SUPABASE_SERVICE_ROLE_KEY，不需要新的密鑰。
- 推播失敗只記錄類型／HTTP 狀態，不記錄金鑰、網址參數或價格，也不會中斷提醒流程。
"""
import math
import os
import re
from urllib.parse import quote, urlparse

import requests

PUSH_TIMEOUT_SECONDS = 5
ATLAS_CHART_BASE_URL = "https://wudn9022.github.io/my-stock-screener/atlas/"
TAG_UNSAFE = re.compile(r"[^A-Za-z0-9._:^=-]")


def push_chart_url(symbol, timeframe="1D"):
    """Atlas 圖表網址（與 Discord 連結相同格式）；symbol 會 URL 編碼。"""
    return (
        f"{ATLAS_CHART_BASE_URL}?page=chart"
        f"&tf={quote(str(timeframe or '1D'), safe='')}"
        f"&symbol={quote(str(symbol).strip(), safe='')}"
    )


def push_tag(*parts):
    """同一檔、同一種提醒用同一個 tag，手機上新的通知會取代舊的。"""
    return TAG_UNSAFE.sub("_", ":".join(str(part) for part in parts if part))[:64] or "atlas"


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _title(icon, ticker, display_name, action):
    name = f" {display_name}" if display_name else ""
    return f"{icon} {ticker}{name} {action}"


def breakout_message(*, ticker, display_name, ma_period, ma_value, price, volume_ratio, direction, **_):
    up = direction == "breakout_up"
    period = int(ma_period)
    price = _number(price)
    ma_value = _number(ma_value)
    volume_ratio = _number(volume_ratio)
    parts = []
    if price is not None:
        text = f"現價 {price:,.2f}"
        if ma_value:
            text += f"（距 MA{period} {(price / ma_value - 1) * 100:+.2f}%）"
        parts.append(text)
    if volume_ratio is not None and volume_ratio > 0:
        parts.append(f"量比 {volume_ratio:.2f}")
    return {
        "title": _title("🚨" if up else "⚠️", ticker, display_name, f"{'向上突破' if up else '向下跌破'} MA{period}"),
        "body": "｜".join(parts) or "點開查看 Atlas 圖表",
        "url": push_chart_url(ticker),
        "tag": push_tag(ticker, f"ma{period}"),
    }


def drawing_message(*, ticker, symbol, display_name, label, level, price, direction, timeframe):
    up = direction == "breakout_up"
    price = _number(price)
    level = _number(level)
    action = f"{'向上突破' if up else '向下跌破'} {label}"
    if level is not None:
        action += f" {level:,.2f}"
    body = []
    if price is not None:
        text = f"現價 {price:,.2f}"
        if level:
            text += f"（{(price / level - 1) * 100:+.2f}%）"
        body.append(text)
    body.append(f"週期 {timeframe}")
    return {
        "title": _title("🚨" if up else "⚠️", ticker, display_name, action),
        "body": "｜".join(body),
        "url": push_chart_url(symbol or ticker, timeframe),
        "tag": push_tag(ticker, "line"),
    }


class WebPushClient:
    def __init__(self, notify_url, service_key, session=None):
        self.notify_url = notify_url
        self.service_key = service_key
        self.session = session or requests.Session()

    @classmethod
    def from_env(cls, session=None, environ=None):
        """未設定或設定不安全時回傳 None（略過推播）。"""
        environ = os.environ if environ is None else environ
        notify_url = environ.get("WEB_PUSH_NOTIFY_URL", "").strip()
        service_key = environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not notify_url or not service_key:
            return None
        parsed = urlparse(notify_url)
        supabase_host = urlparse(environ.get("SUPABASE_URL", "").strip()).hostname
        # 金鑰只送往同一個 Supabase 專案的 HTTPS 網址
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or (supabase_host and parsed.hostname != supabase_host)
            or (not supabase_host and not parsed.hostname.endswith(".supabase.co"))
        ):
            print("⚠️ WEB_PUSH_NOTIFY_URL 必須是同一個 Supabase 專案的 HTTPS 網址，已停用手機通知")
            return None
        return cls(notify_url, service_key, session=session)

    def notify(self, line_user_id, message):
        """送出一則推播；任何錯誤都只記錄並回傳 False。"""
        try:
            response = self.session.post(
                self.notify_url,
                json={"action": "push.notify", "line_user_id": line_user_id, **message},
                headers={"Authorization": f"Bearer {self.service_key}"},
                timeout=PUSH_TIMEOUT_SECONDS,
            )
            if response.status_code != 200:
                print(f"⚠️ 手機通知失敗，HTTP {response.status_code}")
                return False
            data = (response.json() or {}).get("data") or {}
            if data.get("devices"):
                print(f"📱 手機通知：{data.get('sent', 0)}/{data.get('devices', 0)} 台裝置")
            return True
        except Exception as exc:  # noqa: BLE001 - push must never break the alert loop
            print(f"⚠️ 手機通知失敗：{type(exc).__name__}")
            return False

    def close(self):
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass


class PushingNotifier:
    """
    包住 DiscordNotifier：Discord 提醒成功後再推播到手機。
    Discord 的成功／失敗與例外完全照舊回傳，推播結果不影響提醒狀態。
    """

    def __init__(self, notifier, push_client, line_user_id):
        self._notifier = notifier
        self._push = push_client
        self._line_user_id = line_user_id

    def __getattr__(self, name):
        return getattr(self._notifier, name)

    def send_breakout(self, **kwargs):
        sent = self._notifier.send_breakout(**kwargs)
        if sent:
            try:
                self._push.notify(self._line_user_id, breakout_message(**kwargs))
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️ 手機通知內容建立失敗：{type(exc).__name__}")
        return sent

    def push_drawing_alert(self, **kwargs):
        """drawing_alerts 在 Discord 畫線提醒成功後逐則呼叫。"""
        try:
            self._push.notify(self._line_user_id, drawing_message(**kwargs))
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 手機通知內容建立失敗：{type(exc).__name__}")

    def close(self):
        try:
            self._push.close()
        finally:
            self._notifier.close()


def with_web_push(notifier, line_user_id, session=None, environ=None):
    """有設定推播時回傳包裝後的 notifier，否則原樣回傳。"""
    client = WebPushClient.from_env(session=session, environ=environ)
    if client is None or not line_user_id:
        return notifier
    print("📱 手機通知已啟用")
    return PushingNotifier(notifier, client, line_user_id)
