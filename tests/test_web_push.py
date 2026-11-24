"""breakout_alert 手機通知（Web Push）：只在 Discord 成功後推播，且推播失敗不影響提醒。"""
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

import requests

from breakout_alert import drawing_alerts
from breakout_alert.web_push import (
    PUSH_TIMEOUT_SECONDS,
    PushingNotifier,
    WebPushClient,
    breakout_message,
    drawing_message,
    push_chart_url,
    with_web_push,
)

USER = "U" + "1" * 32
SECRET = "eyJhbGciOiJIUzI1NiJ9.offline-service-role.signature"
ENV = {
    "WEB_PUSH_NOTIFY_URL": "https://abc.supabase.co/functions/v1/user-settings",
    "SUPABASE_URL": "https://abc.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": SECRET,
}
BREAKOUT = dict(
    ticker="2330.TW", display_name="台積電", group_name="台股權值", ma_period=20, ma_value=1000,
    ma_values={"20": 1000}, price=1050, volume_ratio=1.35, direction="breakout_up",
)


class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {"ok": True, "data": {"devices": 1, "sent": 1}}

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response or FakeResponse()
        self.error = error
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response

    def close(self):
        self.closed = True


class FakeDiscord:
    def __init__(self, sent=True, error=None):
        self.sent = sent
        self.error = error
        self.closed = False
        self.calls = 0

    def send_breakout(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.sent

    def send_drawing_breakout(self, content):
        return True

    def close(self):
        self.closed = True


class WebPushTests(unittest.TestCase):
    def quiet(self, function, *args, **kwargs):
        output = io.StringIO()
        with redirect_stdout(output):
            result = function(*args, **kwargs)
        return result, output.getvalue()

    def test_skips_silently_without_configuration_or_with_unsafe_url(self):
        discord = FakeDiscord()
        self.assertIs(with_web_push(discord, USER, environ={}), discord)
        self.assertIs(with_web_push(discord, USER, environ={**ENV, "WEB_PUSH_NOTIFY_URL": "off"}), discord)
        derived = WebPushClient.from_env(environ={**ENV, "WEB_PUSH_NOTIFY_URL": "", "SUPABASE_URL": "https://abc.supabase.co/rest/v1"})
        self.assertEqual(derived.notify_url, "https://abc.supabase.co/functions/v1/user-settings")
        for url in ("http://abc.supabase.co/functions/v1/user-settings", "https://evil.example/functions/v1/user-settings",
                    "https://other.supabase.co/functions/v1/user-settings"):
            result, output = self.quiet(with_web_push, discord, USER, environ={**ENV, "WEB_PUSH_NOTIFY_URL": url})
            self.assertIs(result, discord)
            self.assertNotIn(SECRET, output)

    def test_breakout_alert_pushes_chart_link_after_discord_success(self):
        session = FakeSession()
        notifier, _ = self.quiet(with_web_push, FakeDiscord(), USER, session=session, environ=ENV)
        self.assertIsInstance(notifier, PushingNotifier)
        sent, _ = self.quiet(notifier.send_breakout, **BREAKOUT)
        self.assertTrue(sent)
        url, kwargs = session.calls[0]
        self.assertEqual(url, ENV["WEB_PUSH_NOTIFY_URL"])
        self.assertEqual(kwargs["headers"], {"Authorization": f"Bearer {SECRET}"})
        self.assertLessEqual(kwargs["timeout"], 5)
        self.assertEqual(kwargs["timeout"], PUSH_TIMEOUT_SECONDS)
        self.assertEqual(kwargs["json"], {
            "action": "push.notify",
            "line_user_id": USER,
            "title": "🚨 2330.TW 台積電 向上突破 MA20",
            "body": "現價 1,050.00（距 MA20 +5.00%）｜量比 1.35",
            "url": "https://wudn9922.github.io/my-stock-screener/atlas/?page=chart&tf=1D&symbol=2330.TW",
            "tag": "2330.TW:ma20",
        })

    def test_no_push_when_discord_fails_and_discord_errors_still_propagate(self):
        session = FakeSession()
        client = WebPushClient(ENV["WEB_PUSH_NOTIFY_URL"], SECRET, session=session)
        self.assertFalse(PushingNotifier(FakeDiscord(sent=False), client, USER).send_breakout(**BREAKOUT))
        with self.assertRaises(RuntimeError):
            PushingNotifier(FakeDiscord(error=RuntimeError("Discord 429")), client, USER).send_breakout(**BREAKOUT)
        self.assertEqual(session.calls, [])

    def test_push_failures_never_break_the_alert_and_never_log_secrets(self):
        for session in (FakeSession(error=requests.Timeout("slow")), FakeSession(response=FakeResponse(503, {"ok": False})),
                        FakeSession(error=ValueError(SECRET))):
            notifier = PushingNotifier(FakeDiscord(), WebPushClient(ENV["WEB_PUSH_NOTIFY_URL"], SECRET, session=session), USER)
            sent, output = self.quiet(notifier.send_breakout, **BREAKOUT)
            self.assertTrue(sent)
            self.assertIn("手機通知失敗", output)
            self.assertNotIn(SECRET, output)
            self.assertNotIn("1,050", output)

    def test_messages_encode_symbols_and_use_drawing_timeframe(self):
        self.assertEqual(push_chart_url("^TWII"), "https://wudn9922.github.io/my-stock-screener/atlas/?page=chart&tf=1D&symbol=%5ETWII")
        down = breakout_message(**{**BREAKOUT, "direction": "breakout_down", "display_name": "", "volume_ratio": None})
        self.assertEqual(down["title"], "⚠️ 2330.TW 向下跌破 MA20")
        self.assertEqual(down["body"], "現價 1,050.00（距 MA20 +5.00%）")
        line = drawing_message(ticker="NVDA", symbol="NVDA", display_name="NVIDIA", label="水平線", level=180,
                               price=181.8, direction="breakout_up", timeframe="4H")
        self.assertEqual(line["title"], "🚨 NVDA NVIDIA 向上突破 水平線 180.00")
        self.assertEqual(line["body"], "現價 181.80（+1.00%）｜週期 4H")
        self.assertEqual(line["url"], "https://wudn9922.github.io/my-stock-screener/atlas/?page=chart&tf=4H&symbol=NVDA")
        self.assertEqual(line["tag"], "NVDA:line")

    def test_no_device_delivery_is_a_failure_even_with_http_200(self):
        for devices in (0, 1):
            session = FakeSession(response=FakeResponse(200, {"ok": True, "data": {"devices": devices, "sent": 0}}))
            client = WebPushClient(ENV["WEB_PUSH_NOTIFY_URL"], SECRET, session=session)
            sent, output = self.quiet(client.notify, USER, breakout_message(**BREAKOUT))
            self.assertFalse(sent)
            self.assertIn(f"手機通知失敗：0/{devices}", output)

    def test_drawing_alert_hook_pushes_each_sent_line(self):
        session = FakeSession()
        notifier = PushingNotifier(FakeDiscord(), WebPushClient(ENV["WEB_PUSH_NOTIFY_URL"], SECRET, session=session), USER)
        alert = SimpleNamespace(
            plan=SimpleNamespace(entry=SimpleNamespace(ticker="2330.TW", symbol="2330.TW"), quote=SimpleNamespace(display_name="台積電"), price=1050),
            line=SimpleNamespace(label="趨勢線", level=1000, timeframe="1W"),
            direction="breakout_down",
        )
        self.quiet(drawing_alerts.push_alert, notifier, alert)
        payload = session.calls[0][1]["json"]
        self.assertEqual(payload["title"], "⚠️ 2330.TW 台積電 向下跌破 趨勢線 1,000.00")
        self.assertIn("tf=1W", payload["url"])
        # A plain Discord notifier has no push hook: nothing happens.
        drawing_alerts.push_alert(FakeDiscord(), alert)

    def test_close_closes_both(self):
        session, discord = FakeSession(), FakeDiscord()
        PushingNotifier(discord, WebPushClient("https://abc.supabase.co/x", SECRET, session=session), USER).close()
        self.assertTrue(session.closed and discord.closed)


if __name__ == "__main__":
    unittest.main()
