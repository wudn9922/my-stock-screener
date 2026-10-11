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
