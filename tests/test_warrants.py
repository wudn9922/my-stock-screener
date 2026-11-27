from collections import defaultdict
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import build_warrants as w


SESSION = date(2026, 11, 26)
AS_OF = {"TWSE": "2026-11-25", "TPEx": "2026-11-25"}
INDEX = {"entries": [
    {"ticker": "2454", "symbol": "2454.TW", "name": "聯發科"},
    {"ticker": "3526", "symbol": "3526.TWO", "name": "凡甲"},
    {"ticker": "00635U", "symbol": "00635U.TW", "name": "期元大S&amp;P黃金(原名：元大S&amp;P黃金)"},
]}
TWSE = {
    "出表日期": "1151125", "權證代號": "066186", "權證簡稱": "聯發科元大64購06",
    "權證類型": "認購", "標的證券/指數": "聯發科", "最後交易日": "1160401", "履約截止日": "1160407",
    "最新履約價格(元)/履約指數": "4,279.4300", "最新標的履約配發數量(每仟單位權證)": "1.00",
}
EARLY = {
    "出表日期": "1151125", "權證代號": "72984U", "權證簡稱": "凡甲國票61售01",
    "權證類型": "認售", "標的證券/指數": "凡甲", "最後交易日": "1151126", "履約截止日": "1151130",
    "最新履約價格(元)/履約指數": "250.0000", "最新標的履約配發數量(每仟單位權證)": "20.00",
    "備註": "權證提前到期終止上櫃",
}
ISSUES = {
    "72984U": {"Date": "1151125", "Code": "72984U", "Name": EARLY["權證簡稱"],
              "ListedDate": "20260729", "ExpiryDate": "20270128", "UnderlyingStockCode": "3526",
              "LatestExercisePrice": "250", "Latest ExerciseRatio": "0.02"},
}
ISSUER = "元大證券股份有限公司"


class WarrantTests(unittest.TestCase):
    def test_listed_otc_terms_and_early_expiry(self):
        self.assertEqual(w.parse_master([TWSE], "TWSE", SESSION)[0], "2026-11-25")
        listed = w.parse_master([TWSE], "TWSE", SESSION)[1]
        otc = w.parse_master([EARLY], "TPEx", SESSION)[1]
        lookup = {("066186", TWSE["權證簡稱"]): {ISSUER}}
        docs, unmapped = w.documents(listed + otc, AS_OF, SESSION, ISSUES, w.Underlyings(INDEX), lookup, {})
        self.assertFalse(unmapped)
        self.assertEqual(docs["2454.TW"], {
            "version": 1, "underlying": "2454.TW", "asOf": AS_OF,
            "warrants": [{
                "code": "066186", "symbol": "066186.TW", "name": TWSE["權證簡稱"], "type": "call",
                "strikePrice": 4279.43, "exerciseRatio": 0.001, "expiryDate": "2027-04-07",
                "lastTradingDate": "2027-04-01", "issuer": ISSUER, "market": "TWSE",
            }],
        })
        rows = docs["3526.TWO"]["warrants"]
        self.assertEqual(rows[0]["expiryDate"], "2026-11-30")
        self.assertEqual(rows[0]["lastTradingDate"], "2026-11-26")
        self.assertEqual(rows[0]["exerciseRatio"], 0.02)
        self.assertEqual(rows[0]["type"], "put")
        self.assertIsNone(rows[0]["issuer"])
        later = w.parse_master([EARLY, TWSE], "TPEx", date(2026, 11, 27))[1]
        self.assertNotIn("72984U", [item.code for item in later])

    def test_missing_terms_stay_null_while_malformed_terms_fail(self):
        values = {"最新履約價格(元)/履約指數": " - ", "最新標的履約配發數量(每仟單位權證)": "0"}
        _, (row,) = w.parse_master([{**TWSE, **values}], "TWSE", SESSION)
        self.assertIsNone(row.strike_price)
        self.assertIsNone(row.exercise_ratio)
        for invalid in ("NaN", "-1", "1,00", "Infinity"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                w.positive_decimal(invalid)

    def test_invalid_exchange_response_is_rejected_before_output(self):
        for payload in (None, [], {}, ["oops"], [TWSE, TWSE],
                        [{**TWSE, "出表日期": "1151127"}],
                        [{**TWSE, "最後交易日": "1160408"}],
                        [{**TWSE, "履約截止日": "1160230"}],
                        [{**TWSE, "權證代號": "../066186"}]):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                w.parse_master(payload, "TWSE", SESSION)

    def test_issue_listed_date_and_direct_code(self):
        _, otc = w.parse_master([EARLY], "TPEx", SESSION)
        future = {"72984U": {**ISSUES["72984U"], "ListedDate": "20261127"}}
        docs, _ = w.documents(otc, AS_OF, SESSION, future, w.Underlyings(INDEX), {}, {})
        self.assertEqual(docs, {})
        names = w.Underlyings(INDEX)
        self.assertEqual(names.resolve("期元大S&P黃金"), "00635U.TW")
        self.assertEqual(names.resolve("臺股指數", "IX0001"), "^TWII")
        self.assertEqual(names.resolve("電子類", "IX0007"), "IX0007")
        names.names["同名"] = {"2454.TW", "3526.TWO"}
        self.assertIsNone(names.resolve("同名"))
        self.assertEqual(names.resolve("同名", "3526"), "3526.TWO")

    def test_official_otc_daily_list_can_confirm_an_etf_venue(self):
        names = w.Underlyings(INDEX)
        names.add_official_directory([
            {"SecuritiesCompanyCode": "00679B", "CompanyName": "元大美債20年"},
            {"SecuritiesCompanyCode": "714060", "CompanyName": "群聯富邦65購05"},
        ], "TWO", "SecuritiesCompanyCode", "CompanyName", {"元大美債20年"})
        self.assertEqual(names.resolve("元大美債20年", "00679B"), "00679B.TWO")
        self.assertIsNone(names.resolve("群聯富邦65購05", "714060"))

    def test_mops_selector_identity_and_abnormal_pages(self):
        body = f"""<html>
          <select name="co_id"><option value="9800" selected="selected">{ISSUER}</option></select>
          <select name="warrant_class"><option value="1" selected>認購權證</option></select>
          <select name="warrant_id"><option></option><option value="066186">{TWSE['權證簡稱']}</option></select>
          <select name="stock_no"><option value="2454">聯發科</option></select>
          </html>"""
        self.assertEqual(w.parse_issuer_names(body), {"9800": ISSUER})
        self.assertEqual(w.parse_issuer_options(body, "9800", "1"),
                         ([("066186", TWSE["權證簡稱"])], [("2454", "聯發科")]))
        for raw, code in [(body, "9600"), (body[:-8], "9800"), ("<html>Service unavailable</html>", "9800")]:
            with self.subTest(code=code), self.assertRaises(ValueError):
                w.parse_issuer_options(raw, code, "1")

    def test_mops_detail_uses_issuer_and_not_the_appointed_broker(self):
        body = f"""<table><tr><td>權證代號</td><td>066186</td></tr>
          <tr><td>申請機構名稱</td><td>{ISSUER}</td></tr>
          <tr><td>發行人委任證券商</td><td>別家證券</td></tr></table>
          <table><tr><td>標的代號</td><td>中/英文名稱</td></tr>
          <tr><td>2454</td><td>聯發科</td></tr></table>"""
        self.assertEqual(w.parse_detail(body, "066186", "聯發科"), (ISSUER, "2454"))
        self.assertEqual(w.parse_detail(body, "066186", "另一檔"), (ISSUER, None))
        with self.assertRaises(ValueError):
            w.parse_detail(body, "066187", "聯發科")

    def test_reuses_only_the_same_official_warrant_and_rejects_regressive_snapshots(self):
        _, listed = w.parse_master([TWSE], "TWSE", SESSION)
        with TemporaryDirectory() as temp:
            target = Path(temp) / "warrants"
            lookup = defaultdict(set, {("066186", TWSE["權證簡稱"]): {ISSUER}})
            docs, _ = w.documents(listed, AS_OF, SESSION, ISSUES, w.Underlyings(INDEX), lookup, {})
            w.write_documents(target, docs)
            previous = w.read_previous(target, AS_OF)
            rebuilt, _ = w.documents(listed, AS_OF, SESSION, ISSUES, w.Underlyings(INDEX), {}, previous)
            self.assertEqual(rebuilt, docs)
            ambiguous = {("066186", TWSE["權證簡稱"]): {ISSUER, "另一發行商"}}
            unknown, _ = w.documents(listed, AS_OF, SESSION, ISSUES, w.Underlyings(INDEX), ambiguous, previous)
            self.assertIsNone(unknown["2454.TW"]["warrants"][0]["issuer"])
            with self.assertRaisesRegex(ValueError, "move backwards"):
                w.read_previous(target, {"TWSE": "2026-11-24", "TPEx": AS_OF["TPEx"]})
            with self.assertRaises(ValueError):
                w.write_documents(target, {})
            self.assertEqual(json.loads((target / "2454.TW.json").read_text()), docs["2454.TW"])
            w.write_documents(target, {"3526.TWO": {"version": 1, "underlying": "3526.TWO", "asOf": AS_OF, "warrants": []}})
            self.assertEqual([p.name for p in target.iterdir()], ["3526.TWO.json"])

    def test_rejects_oversized_upstream_body(self):
        from io import BytesIO
        opener = type("Opener", (), {"open": lambda _self, *_args, **_kwargs: BytesIO(b"123")})()
        with patch.object(w, "build_opener", return_value=opener), self.assertRaises(ValueError):
            w.fetch(w.MASTERS["TWSE"], max_bytes=2)

    def test_generated_storage_owns_the_per_underlying_reports(self):
        config = json.loads(Path(".github/generated-paths.json").read_text())
        self.assertEqual(config["owners"]["warrants"], ["docs/report/warrants/*.json"])
        self.assertNotIn("warrants", config["records"])
