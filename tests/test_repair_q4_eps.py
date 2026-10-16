"""
Q4 EPS rule shared by seed_financials_sec.py and the offline repair atlas/scripts/repair_q4_eps.py:
build_financials.q4_eps / share_basis / fill_q4_eps / repair_q4_document, and the script's directory run.

Offline: every document here is a hand-written SYNTHETIC stored file ("Synthetic Corp."), every file lives in
a temporary directory. Run: python -m unittest discover -s tests -p "test_repair_q4_eps.py" -v
"""
import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_financials as fin  # noqa: E402
import repair_q4_eps as repair  # noqa: E402

B = 1_000_000_000


def period(end, fy, fq, src, **values):
    return {"end": end, "fy": fy, "fq": fq, "src": src, "values": values, "extra": {}}


def annual_item(end, src="sec", **values):
    return period(end, fin.fiscal_year(end), None, src, **values)


def quarter_item(end, end_month, src="sec", **values):
    fy, fq = fin.fiscal_quarter(end, end_month)
    return period(end, fy, fq, src, **values)


def stored_document(symbol="NFX"):
    """
    SYNTHETIC NFX-like stored file (calendar year, 10:1 split on 2024-11-20, stored after the adjustment):
    FY2023 annual EPS on the new basis, Q1..Q3 2023 adjusted, Q4 2023 EPS null (the gap the repair fills).
    """
    annual = {
        "end": ["2022-12-31", "2023-12-31"], "fy": [2022, 2023], "src": ["sec", "sec"],
        "values": {
            "netIncome": [4_500_000_000, 5_400_000_000],
            "epsDiluted": [0.995, 1.2],
            "epsBasic": [1.01, 1.22],
            "dilutedShares": [4_512_900_000, 4_494_966_000],
        },
    }
    quarterly = {
        "end": ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31"],
        "fy": [2023, 2023, 2023, 2023], "fq": [1, 2, 3, 4], "src": ["sec", "sec", "sec", "sec"],
        "values": {
            "netIncome": [1_305_120_000, 1_487_610_000, 1_677_422_000, 937_838_000],
            "epsDiluted": [0.288, 0.329, 0.373, None],
            "epsBasic": [0.293, 0.335, 0.38, None],
            "dilutedShares": [4_524_170_000, 4_515_720_000, 4_500_110_000, None],
        },
        "derived": [None, None, None, 1],
    }
    quality = {"level": "good", "annualYears": 2, "quarters": 4, "issues": ["split-adjusted"]}
    return {
        "version": 1, "symbol": symbol, "market": "US", "currency": "USD", "foreignReporter": False,
        "generatedAt": "2026-10-15T13:41:43Z", "checkedAt": "2026-10-15", "source": "SEC EDGAR companyfacts",
        "fyEndMonth": 12, "annual": annual, "quarterly": quarterly,
        "split": {"adjusted": True, "events": [["2024-11-20", 10]]}, "quality": quality,
    }


class Q4RuleTest(unittest.TestCase):
    def test_share_basis_prefers_the_diluted_count_then_net_income_over_eps(self):
        self.assertEqual(fin.share_basis(4 * B, 1 * B, 0.25), 4 * B)
        self.assertEqual(fin.share_basis(None, 1 * B, 0.25), 4 * B)
        self.assertEqual(fin.share_basis(0, 1 * B, 0.25), 4 * B)
        self.assertIsNone(fin.share_basis(None, None, 0.25))
        self.assertIsNone(fin.share_basis(None, 1 * B, 0))            # no EPS: no basis

    def test_q4_eps_is_annual_minus_the_three_quarters(self):
        value = fin.q4_eps(1.20, [0.288, 0.329, 0.373], [4.5 * B] * 4)
        self.assertAlmostEqual(value, 0.21, places=9)

    def test_missing_piece_or_mixed_basis_gives_none(self):
        self.assertIsNone(fin.q4_eps(1.20, [0.288, None, 0.373], [4.5 * B] * 4))
        self.assertIsNone(fin.q4_eps(None, [0.288, 0.329, 0.373], [4.5 * B] * 4))
        self.assertIsNone(fin.q4_eps(1.20, [0.288, 0.329, 0.373], [4.5 * B, 4.5 * B, 4.5 * B, None]))
        self.assertIsNone(fin.q4_eps(12.0, [2.88, 3.29, 3.73], [4.5 * B, 0.45 * B, 0.45 * B, 0.45 * B]))  # ×10 apart
        self.assertIsNotNone(fin.q4_eps(1.20, [0.288, 0.329, 0.373], [4.5 * B, 4.9 * B, 4.5 * B, 4.5 * B]))  # 9%


class FillQ4EpsTest(unittest.TestCase):
    def setUp(self):
        self.annual = [annual_item("2023-12-31", netIncome=5_400_000_000, epsDiluted=1.2, epsBasic=1.22,
                                   dilutedShares=4_494_966_000)]
        self.quarterly = [
            quarter_item("2023-03-31", 12, netIncome=1_305_120_000, epsDiluted=0.288, epsBasic=0.293,
                         dilutedShares=4_524_170_000),
            quarter_item("2023-06-30", 12, netIncome=1_487_610_000, epsDiluted=0.329, epsBasic=0.335,
                         dilutedShares=4_515_720_000),
            quarter_item("2023-09-30", 12, netIncome=1_677_422_000, epsDiluted=0.373, epsBasic=0.38,
                         dilutedShares=4_500_110_000),
            quarter_item("2023-12-31", 12, netIncome=937_838_000),
        ]

    def fill(self, annual=None, quarterly=None):
        annual = self.annual if annual is None else annual
        quarterly = self.quarterly if quarterly is None else quarterly
        filled = fin.fill_q4_eps(annual, quarterly)
        return filled, quarterly[-1]["values"], quarterly[-1]["extra"]

    def test_fills_both_eps_fields_with_the_flag(self):
        filled, values, extra = self.fill()
        self.assertEqual(filled, [("2023-12-31", "epsDiluted"), ("2023-12-31", "epsBasic")])
        self.assertEqual(values["epsDiluted"], 0.21)
        self.assertEqual(values["epsBasic"], 0.212)
        self.assertEqual(extra["derived"], 1)
        self.assertNotIn("dilutedShares", values)           # a Q4 share count is never derived

    def test_each_field_is_filled_only_when_null(self):
        self.quarterly[-1]["values"]["epsDiluted"] = 0.25   # reported: kept as it is
        filled, values, _ = self.fill()
        self.assertEqual(filled, [("2023-12-31", "epsBasic")])
        self.assertEqual(values["epsDiluted"], 0.25)
        self.assertEqual(values["epsBasic"], 0.212)

    def test_pieces_must_all_be_sec_periods(self):
        self.quarterly[1]["src"] = "yahoo"
        filled, values, _ = self.fill()
        self.assertEqual(filled, [])
        self.assertNotIn("epsDiluted", values)

    def test_a_derived_value_of_the_wrong_sign_stays_null(self):
        self.quarterly[-1]["values"]["netIncome"] = -937_838_000
        self.assertEqual(self.fill()[0], [])

    def test_derived_value_must_agree_with_the_fourth_quarter_income(self):
        self.quarterly[-1]["values"]["netIncome"] = 300_000_000   # 0.067 per share, not the derived 0.21
        self.assertEqual(self.fill()[0], [])

    def test_inconsistent_annual_eps_blocks_its_q4(self):
        # AMST 2022-like: annual EPS −56.04 next to net income / shares of −4.67
        self.annual = [annual_item("2023-12-31", netIncome=-9_059_923, epsDiluted=-56.04, epsBasic=-56.04,
                                   dilutedShares=1_939_124)]
        self.quarterly[0]["values"].update(epsDiluted=-1.32, epsBasic=-1.32)
        self.quarterly[1]["values"].update(epsDiluted=-1.32, epsBasic=-1.32)
        self.quarterly[2]["values"].update(epsDiluted=-1.12, epsBasic=-1.12)
        self.quarterly[-1]["values"]["netIncome"] = -2_143_416
        self.assertEqual(self.fill()[0], [])

    def test_cent_rounded_small_eps_is_tolerated(self):
        annual = [annual_item("2023-12-31", netIncome=100_000_000, epsDiluted=0.10, epsBasic=0.10,
                              dilutedShares=1 * B)]
        quarterly = [
            quarter_item("2023-03-31", 12, netIncome=30_000_000, epsDiluted=0.03, epsBasic=0.03,
                         dilutedShares=1 * B),
            quarter_item("2023-06-30", 12, netIncome=30_000_000, epsDiluted=0.03, epsBasic=0.03,
                         dilutedShares=1 * B),
            quarter_item("2023-09-30", 12, netIncome=30_000_000, epsDiluted=0.03, epsBasic=0.03,
                         dilutedShares=1 * B),
            quarter_item("2023-12-31", 12, netIncome=20_000_000),
        ]
        filled, values, _ = self.fill(annual, quarterly)
        self.assertEqual(values["epsDiluted"], 0.01)          # 0.10 − 0.09; 0.02 of net income per share
        self.assertEqual(len(filled), 2)

    def test_non_calendar_fiscal_years_pair_the_right_periods(self):
        # NVDA-like (fiscal year ends late January, SYNTHETIC numbers): 2.5B shares, 7.35B net income, EPS 2.94
        nvda = [annual_item("2025-01-26", netIncome=7_350_000_000, epsDiluted=2.94, epsBasic=2.99,
                            dilutedShares=2_500_000_000)]
        quarters = [quarter_item(end, 1, netIncome=1_700_000_000, epsDiluted=0.68, epsBasic=0.7,
                                 dilutedShares=2_500_000_000)
                    for end in ("2024-04-28", "2024-07-28", "2024-10-27")]
        quarters.append(quarter_item("2025-01-26", 1, netIncome=2_250_000_000))
        self.assertEqual([(item["fy"], item["fq"]) for item in quarters],
                         [(2025, 1), (2025, 2), (2025, 3), (2025, 4)])
        filled = fin.fill_q4_eps(nvda, quarters)
        self.assertEqual(filled, [("2025-01-26", "epsDiluted"), ("2025-01-26", "epsBasic")])
        self.assertAlmostEqual(quarters[-1]["values"]["epsDiluted"], 0.90, places=9)   # 2.94 − 3 × 0.68

        aapl_annual = [annual_item("2025-09-27", netIncome=112 * B, epsDiluted=7.5, epsBasic=7.55,
                                   dilutedShares=15 * B)]
        aapl_quarters = [quarter_item("2024-12-28", 9, netIncome=36 * B, epsDiluted=2.4, epsBasic=2.41,
                                      dilutedShares=15 * B),
                         quarter_item("2025-03-29", 9, netIncome=24 * B, epsDiluted=1.65, epsBasic=1.66,
                                      dilutedShares=15 * B),
                         quarter_item("2025-06-28", 9, netIncome=21 * B, epsDiluted=1.57, epsBasic=1.58,
                                      dilutedShares=15 * B),
                         quarter_item("2025-09-27", 9, netIncome=31 * B)]
        self.assertEqual(aapl_quarters[-1]["fq"], 4)
        filled = fin.fill_q4_eps(aapl_annual, aapl_quarters)
        self.assertEqual(filled[0], ("2025-09-27", "epsDiluted"))
        self.assertAlmostEqual(aapl_quarters[-1]["values"]["epsDiluted"], round(7.5 - 2.4 - 1.65 - 1.57, 4))


class RepairDocumentTest(unittest.TestCase):
    def test_the_filled_quarter_gets_the_flag_and_the_quality_code(self):
        document = stored_document()
        filled = fin.repair_q4_document(document)
        self.assertEqual(filled, [("2023-12-31", "epsDiluted"), ("2023-12-31", "epsBasic")])
        quarterly = document["quarterly"]
        self.assertEqual(quarterly["values"]["epsDiluted"][3], 0.21)
        self.assertEqual(quarterly["values"]["epsBasic"][3], 0.212)
        self.assertEqual(quarterly["derived"], [None, None, None, 1])
        self.assertEqual(document["quality"]["issues"], ["split-adjusted", "q4-derived"])

    def test_the_derived_column_is_created_when_missing(self):
        document = stored_document()
        del document["quarterly"]["derived"]
        fin.repair_q4_document(document)
        self.assertEqual(document["quarterly"]["derived"], [None, None, None, 1])

    def test_nothing_to_fill_leaves_the_document_untouched(self):
        document = stored_document()
        document["quarterly"]["values"]["epsDiluted"][3] = 0.2
        document["quarterly"]["values"]["epsBasic"][3] = 0.21
        before = copy.deepcopy(document)
        self.assertEqual(fin.repair_q4_document(document), [])
        self.assertEqual(document, before)

    def test_second_repair_is_a_no_op(self):
        document = stored_document()
        fin.repair_q4_document(document)
        after_first = copy.deepcopy(document)
        self.assertEqual(fin.repair_q4_document(document), [])
        self.assertEqual(document, after_first)

    def test_existing_values_are_never_overwritten(self):
        document = stored_document()
        document["quarterly"]["values"]["netIncome"][0] = 1_000
        document["annual"]["values"]["epsDiluted"][1] = 1.2
        fin.repair_q4_document(document)
        self.assertEqual(document["quarterly"]["values"]["netIncome"][0], 1_000)
        self.assertEqual(document["annual"]["values"]["epsDiluted"][1], 1.2)


class RepairScriptTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="repair-q4-")
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.write("NFX.json", stored_document())
        self.write("TWX.json", dict(stored_document("TWX"), market="TW"))     # not a US file: never touched
        with open(os.path.join(self.directory, "BAD.json"), "w", encoding="utf-8") as file:
            file.write("{not json")
        self.write("index.json", {"symbols": []})

    def write(self, name, document):
        with open(os.path.join(self.directory, name), "w", encoding="utf-8") as file:
            json.dump(document, file)

    def read(self, name):
        with open(os.path.join(self.directory, name), encoding="utf-8") as file:
            return file.read()

    def run_main(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = repair.main(["--dir", self.directory, *args])
        self.assertEqual(code, 0)
        return output.getvalue()

    def test_dry_run_reports_and_writes_nothing(self):
        before = {name: self.read(name) for name in os.listdir(self.directory)}
        text = self.run_main("--dry-run")
        self.assertIn("files to repair: 1", text)
        self.assertIn("periods filled: 2 (epsDiluted: 1, epsBasic: 1)", text)
        self.assertIn("skipped: corrupt 1, not-us 1", text)
        self.assertIn("dry run: nothing written", text)
        self.assertEqual({name: self.read(name) for name in os.listdir(self.directory)}, before)

    def test_repair_writes_the_filled_file_and_a_second_run_changes_nothing(self):
        bad = self.read("BAD.json")
        twx = self.read("TWX.json")
        self.run_main()
        document = json.loads(self.read("NFX.json"))
        self.assertEqual(document["quarterly"]["values"]["epsDiluted"][3], 0.21)
        self.assertIn("q4-derived", document["quality"]["issues"])
        self.assertEqual(self.read("BAD.json"), bad)
        self.assertEqual(self.read("TWX.json"), twx)
        text = self.run_main()
        self.assertIn("files to repair: 0", text)
        self.assertIn("nothing to write", text)

    def test_symbol_filter_prints_each_filled_value(self):
        text = self.run_main("--dry-run", "--symbol", "NFX")
        self.assertIn("NFX 2023-12-31 epsDiluted = 0.21", text)
        self.assertIn("NFX 2023-12-31 epsBasic = 0.212", text)
        self.assertIn("scanned 1 files", text)


if __name__ == "__main__":
    unittest.main()
