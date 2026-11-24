import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from breakout_alert import atlas_links as al  # noqa: E402
from breakout_alert.discord_notifier import DiscordNotifier  # noqa: E402

BASE = "https://wudn9922.github.io/my-stock-screener/atlas/?page=chart&tf=1D&symbol="
LIFF = "https://liff.line.me/2010330411-6JhrotT9/atlas/"


class AtlasLinkTest(unittest.TestCase):
    def test_symbol_encoding(self):
        self.assertEqual(al.atlas_chart_url("2330.TW"), BASE + "2330.TW")
        self.assertEqual(al.atlas_chart_url("6488.TWO"), BASE + "6488.TWO")
        self.assertEqual(al.atlas_chart_url("^TWII"), BASE + "%5ETWII")
        self.assertEqual(al.atlas_chart_url("BRK-B"), BASE + "BRK-B")
        self.assertEqual(al.atlas_chart_url(""), "")

    def test_append_keeps_link_when_truncating(self):
        text = al.append_atlas_link("x" * 3000, "2330.TW", 1950)
        self.assertLessEqual(len(text), 1950)
        self.assertTrue(text.endswith(f"[開啟 Atlas 圖表](<{BASE}2330.TW>)"))

    def test_notification_links_keep_the_same_symbol_and_timeframe(self):
        query = "?page=chart&tf=1W&symbol=%5ETWII"
        self.assertEqual(al.atlas_alert_links("^TWII", "1W"),
                         f"[在 LINE 開啟 Atlas](<{LIFF}{query}>) · [開啟 Atlas 圖表](<{al.ATLAS_BASE_URL}{query}>)")
        alert = al.append_atlas_link("x" * 3000, "2330.TW", 1950, with_line=True)
        self.assertLessEqual(len(alert), 1950)
        self.assertTrue(alert.endswith(al.atlas_alert_links("2330.TW")))

    def test_ticker_links_truncate(self):
        self.assertEqual(al.ticker_links(["2330", "^TWII"]), f"[2330](<{BASE}2330>) [^TWII](<{BASE}%5ETWII>)")
        out = al.ticker_links([f"S{i}" for i in range(100)], limit=300)
        self.assertLessEqual(len(out), 300)
        self.assertIn("等另", out)

    def test_plain_ticker_links(self):
        self.assertEqual(al.plain_ticker_links(["AAPL", " "]), f"AAPL {BASE}AAPL")


class NotifierMessageTest(unittest.TestCase):
    def test_breakout_message_has_link(self):
        sent = {}

        class Resp:
            status_code = 204

        class Session:
            def post(self, url, json, timeout):
                sent.update(json)
                return Resp()

        notifier = DiscordNotifier.__new__(DiscordNotifier)
        notifier.webhook_url = "https://example.invalid"
        notifier.session = Session()
        notifier.send_breakout(
            ticker="2330.TW", display_name="台積電", group_name="g", ma_period=20,
            ma_value=100.0, ma_values={"20": 100.0}, price=101.0, volume_ratio=1.2,
            direction="breakout_up",
        )
        self.assertIn("🚨 2330.TW 台積電｜g", sent["content"])
        self.assertIn(f"[在 LINE 開啟 Atlas](<{LIFF}?page=chart&tf=1D&symbol=2330.TW>)", sent["content"])
        self.assertTrue(sent["content"].endswith(f"[開啟 Atlas 圖表](<{BASE}2330.TW>)"))


if __name__ == "__main__":
    unittest.main()
