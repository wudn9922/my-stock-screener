"""Parser tests on synthetic PDFs laid out like 國泰「客戶日買賣報告書」(海外股票交易明細).

The real report has two sub-rows per trade (currency/price/net over currency/rate/actual net) with
the other cells vertically centred; the fixture reproduces that geometry. Names and amounts are
invented.
"""

import io
import unittest
from pathlib import Path
from decimal import Decimal

try:
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    from statement_sync.parser import StatementError, parse_statement
except ImportError as missing:  # the statement-sync workflow installs these; other suites skip
    raise unittest.SkipTest(f"statement sync test dependencies unavailable: {missing.name}")

# Subset of WenQuanYi Zen Hei (GPL with font-embedding exception) holding only the fixture's characters.
FONT = Path(__file__).resolve().parent / "fixtures/statement/cjk-subset.ttf"
pdfmetrics.registerFont(TTFont("CJK", str(FONT)))
COLUMNS = {"ref": 48, "product": 70, "market": 196, "type": 222, "ccy": 250, "qty": 288, "price": 338,
           "amount": 390, "fee": 430, "tax": 462, "net": 508, "date": 560}
UHS = ("00007040", "UHS/Universal Health Servi", "美國", "賣出", "1.000000", "181.315200000", "181.320000", "0.15", "0.01", "181.16", "181.16")
MMS = ("00008934", "MMS/Maximus Inc", "美國", "賣出", "4.000000", "62.159200000", "248.640000", "0.20", "0.01", "248.43", "248.43")
BUY = ("00009001", "BRK.B/Berkshire Hathaway ETF", "美國", "買進", "2.000000", "100.000000000", "200.000000", "0.30", "0.00", "200.30", "200.30")


def report(rows=(UHS, MMS), subtotal="429.59", extra_section=None, password="A123456789"):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont("CJK", 9)
    lines = ["國泰綜合證券 Cathay Securities Corporation", "客戶日買賣報告書", "Client Daily Trade Report",
             "以下是您帳號：D3***00 2026年11月19日 的買賣報告書。",
             "Please find below the Trade Report of your account D3***00 for 2026/11/19.",
             "海外股票交易明細 Transaction Details of Offshore Stocks"]
    y = 800
    for line in lines:
        pdf.drawString(40, y, line)
        y -= 16
    for key, label in (("ref", "交易序號"), ("product", "商品代號/名稱"), ("qty", "股數"), ("net", "應收付"), ("date", "交割日")):
        pdf.drawString(COLUMNS[key] - 10, y, label)
    y -= 30
    pdf.setFont("CJK", 6.5)
    for ref, product, market, side, qty, price, amount, fee, tax, net, settled in rows:
        pdf.drawCentredString(COLUMNS["ref"], y - 3, ref)
        pdf.drawString(COLUMNS["product"], y - 3, product)
        for key, value in (("market", market), ("type", side), ("qty", qty), ("amount", amount), ("fee", fee), ("tax", tax), ("date", "2026/11/23")):
            pdf.drawCentredString(COLUMNS[key], y - 3, value)
        for key, top, bottom in (("ccy", "USD", "USD"), ("price", price, "1.0000"), ("net", net, settled)):
            pdf.drawCentredString(COLUMNS[key], y + 4, top)
            pdf.drawCentredString(COLUMNS[key], y - 10, bottom)
        y -= 34
    pdf.setFont("CJK", 9)
    pdf.drawString(440, y, f"小計 Subtotal: USD {subtotal}")
    if extra_section:
        pdf.drawString(40, y - 30, extra_section)
    pdf.drawString(40, y - 60, "手續費及相關費用說明如下：1.股票費用為該市場之交易稅費。")
    pdf.showPage()
    pdf.save()
    if not password:
        return buffer.getvalue()
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(buffer.getvalue())).pages:
        writer.add_page(page)
    writer.encrypt(password, algorithm="AES-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class CathayDailyReportTests(unittest.TestCase):
    def test_parses_both_sells_from_an_encrypted_report(self):
        statement = parse_statement(report(), passwords=["wrong", "A123456789"])
        self.assertEqual((statement.broker, statement.report_date, statement.account_hint), ("cathay", "2026-11-19", "D3***00"))
        self.assertEqual([(t.key, t.symbol, t.side, t.quantity, t.amount) for t in statement.trades], [
            ("2026-11-19:00007040", "UHS", "sell", Decimal("1.000000"), Decimal("181.16")),
            ("2026-11-19:00008934", "MMS", "sell", Decimal("4.000000"), Decimal("248.43")),
        ])
        self.assertEqual(statement.trades[1].price, Decimal("62.159200000"))
        self.assertTrue(all(t.market == "US" and t.currency == "USD" for t in statement.trades))

    def test_buy_amount_includes_fees_and_class_shares_use_a_dash(self):
        statement = parse_statement(report(rows=(BUY,), subtotal="200.30"), passwords=["A123456789"])
        (trade,) = statement.trades
        self.assertEqual((trade.symbol, trade.side, trade.amount), ("BRK-B", "buy", Decimal("200.30")))

    def test_wrong_password_and_unrelated_pdf(self):
        with self.assertRaises(StatementError) as caught:
            parse_statement(report(), passwords=["nope"])
        self.assertEqual(caught.exception.code, "password")
        with self.assertRaises(StatementError) as caught:
            parse_statement(b"not a pdf")
        self.assertEqual(caught.exception.code, "not-statement")

    def test_numbers_that_do_not_add_up_are_rejected(self):
        broken = UHS[:9] + ("181.36", "181.36")  # net ≠ amount − fee − tax
        with self.assertRaises(StatementError) as caught:
            parse_statement(report(rows=(broken, MMS), subtotal="429.79"), passwords=["A123456789"])
        self.assertEqual((caught.exception.code, caught.exception.detail), ("unrecognized", "arithmetic"))

    def test_subtotal_mismatch_is_rejected(self):
        with self.assertRaises(StatementError) as caught:
            parse_statement(report(subtotal="500.00"), passwords=["A123456789"])
        self.assertEqual(caught.exception.detail, "subtotal")

    def test_unknown_sections_and_markets_are_not_guessed(self):
        with self.assertRaises(StatementError) as caught:
            parse_statement(report(extra_section="國內股票交易明細 Domestic"), passwords=["A123456789"])
        self.assertEqual((caught.exception.code, caught.exception.detail), ("unsupported", "section"))
        hongkong = (UHS[0], "0700/Tencent", "香港") + UHS[3:]
        with self.assertRaises(StatementError) as caught:
            parse_statement(report(rows=(hongkong,), subtotal="181.16"), passwords=["A123456789"])
        self.assertEqual(caught.exception.code, "unsupported")

    def test_sender_domain_identifies_the_broker_when_the_text_does_not(self):
        from statement_sync.parser import detect_broker
        self.assertEqual(detect_broker("", "國泰證券 <report@ebill.cathaysec.com.tw>"), "cathay")
        self.assertIsNone(detect_broker("", "someone@example.com"))


if __name__ == "__main__":
    unittest.main()
