"""Parser tests on synthetic PDFs laid out like 永豐金「當日成交回報」(台股), including its 庫存明細.

The stock name cell has two lines (name over "(code)"); the fixture keeps that geometry. Account,
names and amounts are invented.
"""

import io
import unittest
from decimal import Decimal
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    from statement_sync.parser import StatementError, parse_statement
except ImportError as missing:  # the statement-sync workflow installs these; other suites skip
    raise unittest.SkipTest(f"statement sync test dependencies unavailable: {missing.name}")

FONT = Path(__file__).resolve().parent / "fixtures/statement/cjk-subset.ttf"
pdfmetrics.registerFont(TTFont("CJK", str(FONT)))
X = {"seq": 40, "time": 78, "order": 128, "name": 178, "side": 228, "qty": 262, "price": 300, "loan": 342,
     "net": 386, "fee": 426, "tax": 458, "interest": 490, "lending": 524}
WARRANT = ("1", "20261123", "X-21JF", "台灣50元", "031863", "現買", "14,000", "0.1100", "0", "1,542", "2", "0", "0", "0")
SELL = ("2", "20261123", "X-21JG", "台達電", "2308", "現賣", "10", "300.0000", "0", "2,986", "5", "9", "0", "0")
STOCKS = (("031863", "台灣50元", "14,000"), ("2308", "台達電", "30"))


def report(rows=(WARRANT,), buys="1,540", sells="0", stocks=STOCKS, currency="台幣", password="A123456789",
           inventory_x=0, inventory_page_break=False, merged_code=False):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont("CJK", 9)
    pdf.drawString(40, 800, "當日成交回報")
    pdf.drawString(40, 780, f"證券帳號:9A9*-30**45-1 幣別:{currency}")
    y = 750
    for key, label in (("seq", "筆"), ("time", "成交時間"), ("order", "單號"), ("name", "股票名稱"), ("side", "買賣"),
                       ("qty", "股數"), ("price", "單價"), ("net", "預估收付"), ("fee", "手續費"), ("tax", "交易稅")):
        pdf.drawCentredString(X[key], y, label)
    y -= 30
    pdf.setFont("CJK", 7)
    for seq, day, order, name, code, side, *numbers in rows:
        for key, value in (("seq", seq), ("time", day), ("order", order), ("side", side)):
            pdf.drawCentredString(X[key], y, value)
        pdf.drawCentredString(X["name"], y + 5, name)
        pdf.drawCentredString(X["name"], y - 5, f"({code})")
        for key, value in zip(("qty", "price", "loan", "net", "fee", "tax", "interest", "lending"), numbers):
            pdf.drawRightString(X[key] + 14, y, value)
        y -= 28
    pdf.setFont("CJK", 9)
    pdf.drawString(300, y, f"買進總計：${buys}")
    pdf.drawString(300, y - 14, f"賣出總計：${sells}")
    pdf.drawString(40, y - 50, "庫存明細 到 2026/11/23股票庫存餘額")
    y -= 70
    if inventory_page_break:  # heading on page 1, the list on page 2
        pdf.showPage()
        pdf.setFont("CJK", 9)
        y = 800
    dx = inventory_x
    for x, label in ((45, "證券代號"), (110, "證券名稱"), (200, "集保庫存"), (260, "融資庫存"), (320, "融券庫存")):
        pdf.drawString(x + dx, y, label)
    for code, name, held in stocks:
        y -= 16
        if merged_code:
            pdf.drawString(45 + dx, y, code + name)
        else:
            pdf.drawString(45 + dx, y, code)
            pdf.drawString(110 + dx, y, name)
        for x, value in ((200, held), (260, "0"), (320, "0")):
            pdf.drawRightString(x + 30 + dx, y, value)
    pdf.drawString(40, y - 40, "1.本資料僅供參考，請以永豐金證券理財網(https://www.sinotrade.com.tw)為準。")
    pdf.showPage()
    pdf.save()
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(buffer.getvalue())).pages:
        writer.add_page(page)
    writer.encrypt(password, algorithm="AES-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def parse(pdf):
    return parse_statement(pdf, passwords=["cathay-password", "A123456789"])


class SinopacDailyReportTests(unittest.TestCase):
    def test_trade_and_inventory_snapshot(self):
        statement = parse(report())
        self.assertEqual((statement.broker, statement.report_date, statement.account_hint), ("sinopac", "2026-11-23", "9A9*-30**45-1"))
        (trade,) = statement.trades
        self.assertEqual((trade.key, trade.symbol, trade.side, trade.quantity, trade.price, trade.amount, trade.currency),
                         ("2026-11-23:X-21JF:1", "031863.TW", "buy", Decimal("14000"), Decimal("0.1100"), Decimal("1542"), "TWD"))
        self.assertEqual(statement.snapshot, {"market": "TW", "positions": {"031863.TW": Decimal("14000"), "2308.TW": Decimal("30")}})

    def test_inventory_found_wherever_the_table_sits(self):
        expected = {"031863.TW": Decimal("14000"), "2308.TW": Decimal("30")}
        for layout in (dict(inventory_x=60), dict(inventory_page_break=True), dict(merged_code=True)):
            with self.subTest(**layout):
                self.assertEqual(parse(report(**layout)).snapshot["positions"], expected)

    def test_no_inventory_rows_means_no_snapshot_not_an_empty_account(self):
        statement = parse(report(stocks=()))
        self.assertEqual(len(statement.trades), 1)
        self.assertIsNone(statement.snapshot)

    def test_sell_proceeds_are_net_of_fee_and_tax(self):
        statement = parse(report(rows=(WARRANT, SELL), sells="3,000"))
        self.assertEqual([(t.symbol, t.side, t.amount) for t in statement.trades],
                         [("031863.TW", "buy", Decimal("1542")), ("2308.TW", "sell", Decimal("2986"))])

    def test_wrong_numbers_totals_margin_and_currency_are_rejected(self):
        cases = (
            (dict(rows=(WARRANT[:9] + ("1,600",) + WARRANT[10:],)), ("unrecognized", "arithmetic")),
            (dict(buys="1,600"), ("unrecognized", "total")),
            (dict(rows=(WARRANT[:5] + ("資買",) + WARRANT[6:],)), ("unsupported", "margin")),
            (dict(currency="美元"), ("unsupported", "currency")),
            (dict(stocks=(("9999", "不存在", "1"),)), ("unrecognized", "symbol")),
        )
        for kwargs, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(StatementError) as caught:
                parse(report(**kwargs))
            self.assertEqual((caught.exception.code, caught.exception.detail), expected)

    def test_tpex_listed_stock_gets_two_suffix(self):
        from statement_sync.parser import tw_symbol
        self.assertEqual((tw_symbol("2308"), tw_symbol("6488"), tw_symbol("0050"), tw_symbol("700123")),
                         ("2308.TW", "6488.TWO", "0050.TW", "700123.TWO"))


if __name__ == "__main__":
    unittest.main()
