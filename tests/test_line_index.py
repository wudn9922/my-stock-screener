import unittest

try:
    import main
except ImportError:  # main.py needs yfinance and friends
    main = None


def trend_text(label, score, total, macro, micro):
    return (
        "📈 測試指數\n"
        f"   ├ 均線: {label} ({score}/{total}MA - 5/10)\n"
        f"   └ 趨勢: {macro}趨勢中的{micro}走勢"
    )


@unittest.skipIf(main is None, "main.py dependencies not installed")
class LineIndexSummaryTest(unittest.TestCase):
    def line(self, label, score, total, macro="多頭", micro="多頭"):
        return main.build_index_line_short("^GSPC", "x", trend_text(label, score, total, macro, micro))

    def test_five_ma_levels(self):
        cases = [("看多", 4, "▲▲"), ("偏多", 2, "▲"), ("多空不明", 0, "－"), ("偏空", -1, "▼"), ("看空", -4, "▼▼")]
        for label, score, symbol in cases:
            self.assertIn(f"均線{symbol} ", self.line(label, score, 4) + " ")

    def test_dow_symbols(self):
        self.assertTrue(self.line("看多", 4, 4, "多頭", "多頭").endswith("道氏🔺"))
        self.assertTrue(self.line("看多", 4, 4, "空頭", "空頭").endswith("道氏🔻"))
        self.assertTrue(self.line("看多", 4, 4, "多頭", "空頭").endswith("道氏💡"))
        self.assertTrue(self.line("看多", 4, 4, "空頭", "多頭").endswith("道氏⚡"))


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(main is None, "main.py dependencies not installed")
class CombinedLineMessagesTest(unittest.TestCase):
    def send(self, messages, **kwargs):
        sent = []

        def fake_send(message, access_token, user_id):
            sent.append(message)
            return 200

        original = main.send_line_message
        main.send_line_message = fake_send
        try:
            count = main.send_line_messages_combined(messages, "t", "U1", **kwargs)
        finally:
            main.send_line_message = original
        return count, sent

    def test_short_messages_become_one(self):
        count, sent = self.send(["stocks", "index", "sector"])
        self.assertEqual(count, 1)
        self.assertEqual(sent, ["stocks\n\nindex\n\nsector"])

    def test_blank_messages_are_skipped(self):
        count, sent = self.send(["stocks", "", None, "  "])
        self.assertEqual((count, sent), (1, ["stocks"]))

    def test_overflow_splits_on_message_boundaries(self):
        a, b, c = "a" * 40, "b" * 40, "c" * 40
        count, sent = self.send([a, b, c], max_length=90)
        self.assertEqual(count, 2)
        self.assertEqual(sent, [f"{a}\n\n{b}", c])

    def test_nothing_to_send(self):
        self.assertEqual(self.send([]), (0, []))
