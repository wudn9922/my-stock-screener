"""
seed_financials_sec.py --foreign --upgrade: Yahoo-only foreign files merged with their SEC document
(seed_financials_sec.upgrade_document / seed_upgrade, sec_ifrs.plan_upgrade). SYNTHETIC fixtures only: the SEC
payloads come from test_sec_ifrs, the Yahoo files are written here. No network: every test passes --no-splits.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
TESTS = os.path.dirname(os.path.abspath(__file__))
for path in (SCRIPTS, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

import build_financials as fin  # noqa: E402
import seed_financials_sec as sec  # noqa: E402
import sec_ifrs as ifrs  # noqa: E402
from test_sec_ifrs import (  # noqa: E402
    NOW, FakeClient, NoNetworkClient, eur_us_gaap_concepts, twd_concepts,
)

YAHOO_CHECKED = "2026-10-01"       # the Yahoo file's check date: kept by --upgrade (build_financials schedules by it)
ADR = 5                            # TSM-like: one ADR = 5 ordinary shares
TSM_QUARTERS = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
TSM_REVENUE_Q = [14_000_000_000, 15_000_000_000, 16_000_000_000, 17_000_000_000, 18_000_000_000, 19_000_000_000]


def yahoo_file(key, currency, annual, quarterly):
    """A stored Yahoo-only file (SYNTHETIC): annual / quarterly = [(period end, {field: value})]."""
    annual_items = [fin.period(end, values) for end, values in annual]
    quarterly_items = [fin.period(end, values) for end, values in quarterly]
    for item in annual_items:
        item["fy"] = fin.fiscal_year(item["end"])
    for item in quarterly_items:
        item["fy"], item["fq"] = fin.fiscal_quarter(item["end"], 12)
    return {
        "version": fin.VERSION, "symbol": key, "market": "US", "currency": currency, "foreignReporter": True,
        "generatedAt": "2026-10-01T00:00:00Z", "checkedAt": YAHOO_CHECKED, "source": fin.SOURCE, "fyEndMonth": 12,
        "annual": fin.write_section(annual_items, {}, False),
        "quarterly": fin.write_section(quarterly_items, {}, True),
        "split": {"adjusted": False, "events": []},
        "quality": {"level": "partial", "annualYears": len(annual_items), "quarters": len(quarterly_items),
                    "issues": ["foreign-currency"]},
    }


def tsm_yahoo(key="TSM", multipliers=None):
    """
    TSM-like Yahoo file (SYNTHETIC): per-ADR EPS = k x the SEC per-share EPS of the same year (k = ADR, default 5;
    multipliers {year: k} overrides one year). Annual FY2022-FY2025, quarterly 2025Q1-2026Q2.
    """
    multipliers = multipliers or {}
    annual, quarterly = [], []
    for year in (2022, 2023, 2024, 2025):
        k = multipliers.get(year, ADR) if multipliers else ADR
        basic = (year - 2019) * 2.5
        rev = (year - 2019) * 10_000_000_000
        if year == 2022:
            rev = rev * 101 // 100          # the restated FY2022 revenue of the SEC fixture
        annual.append((f"{year}-12-31", {
            "revenue": rev, "netIncome": rev * 20 // 100, "operatingCashFlow": rev * 3 // 10,
            "equity": rev * 4 // 10, "epsBasic": k * basic, "epsDiluted": k * (basic - 0.1),
            "dilutedShares": 5_200_000_000,
        }))
    for end, rev in zip(TSM_QUARTERS, TSM_REVENUE_Q):
        quarterly.append((end, {"revenue": rev, "netIncome": rev * 20 // 100, "epsBasic": ADR * 2.5,
                                "epsDiluted": ADR * 2.4, "dilutedShares": 5_200_000_000}))
    return yahoo_file(key, "TWD", annual, quarterly)


def asml_yahoo(currency="EUR"):
    """ASML-like Yahoo file (SYNTHETIC): EUR, EPS on the SEC basis (k = 1), FY2022-FY2025 and FY2025 quarters."""
    annual = []
    for year in (2022, 2023, 2024, 2025):
        rev = 5_800_000_000 if year == 2025 else (year - 2020) * 500_000_000   # FY2025 = the four quarters below
        annual.append((f"{year}-12-31", {"revenue": rev, "netIncome": rev // 5, "operatingCashFlow": rev // 4,
                                         "equity": rev, "epsBasic": (year - 2020) * 1.5}))
    quarterly = []
    for end, rev in zip(["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31"],
                        [1_300_000_000, 1_400_000_000, 1_500_000_000, 1_600_000_000]):
        quarterly.append((end, {"revenue": rev}))
    return yahoo_file("ASML", currency, annual, quarterly)


def tsm_sec(key="TSM"):
    """The SEC document of the TSM-like fixture: IFRS, TWD (the payload of test_sec_ifrs.twd_concepts)."""
    document, reason = ifrs.build_foreign_document(twd_concepts(), key, NOW)
    assert document is not None, reason
    return document


def asml_sec():
    """The SEC document of the ASML-like fixture: no ifrs-full facts, so the US GAAP foreign-filer basis, EUR."""
    document, reason = ifrs.build_foreign_document(eur_us_gaap_concepts(), "ASML", NOW)
    assert document is not None, reason
    return document


def column(section, field):
    """{period end: value} of one stored field (None where the period has no value)."""
    values = section["values"].get(field, [None] * len(section["end"]))
    return dict(zip(section["end"], values))


def write_json(path, document):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(document, file)


def yahoo_for(*keys):
    """One TSM-like Yahoo file per key (SYNTHETIC), keyed by the file's own symbol."""
    return {key: tsm_yahoo(key) for key in keys}


def read_bytes(path):
    with open(path, "rb") as file:
        return file.read()


def read_json(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def run_main(argv, client=None, clock=None):
    """main(argv) with the network kept out → (exit code, printed text)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = sec.main(argv, client=client, now=NOW,
                        environ={"SEC_USER_AGENT": "TestAgent test@example.com"}, clock=clock)
    return code, out.getvalue()


class UpgradeMergeTests(unittest.TestCase):
    def test_tsm_type_merges_sec_years_with_yahoo_quarters_and_rescales_eps(self):
        """IFRS, TWD, Yahoo per ADR with k = 5: SEC years and quarters kept, Yahoo's newer quarters added."""
        document, info = sec.upgrade_document(tsm_yahoo(), tsm_sec(), "TSM", NOW, None)
        self.assertEqual(info["status"], "upgraded")
        self.assertEqual(info["epsBasis"], "rescaled")
        self.assertEqual(document["adrRatio"]["epsFactor"], ADR)
        self.assertEqual(document["adrRatio"]["basis"], "ADR")
        self.assertEqual(document["source"], ifrs.SOURCE)
        self.assertEqual(document["currency"], "TWD")
        self.assertTrue(document["foreignReporter"])

        annual = document["annual"]
        self.assertEqual(annual["end"], ["2020-12-31", "2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31",
                                         "2025-12-31"])
        self.assertEqual(annual["src"], ["sec"] * 5 + ["yahoo"])
        quarterly = document["quarterly"]
        self.assertEqual(quarterly["end"], TSM_QUARTERS)
        self.assertEqual(quarterly["src"], ["sec", "sec", "yahoo", "yahoo", "yahoo", "yahoo"])

        # EPS is per ADR on every period both sides carry: SEC x k equals Yahoo's own value
        eps = column(annual, "epsBasic")
        self.assertAlmostEqual(eps["2023-12-31"], ADR * 10.0)
        self.assertAlmostEqual(eps["2022-12-31"], ADR * 7.5)
        diluted = column(annual, "epsDiluted")
        self.assertAlmostEqual(diluted["2023-12-31"], ADR * 9.9)
        self.assertAlmostEqual(column(quarterly, "epsBasic")["2025-03-31"], ADR * 2.5)
        # per-share fields move as a set: SEC periods with EPS take no Yahoo share count (merge_section), Yahoo-only
        # periods keep theirs
        self.assertIsNone(column(annual, "dilutedShares")["2023-12-31"])
        self.assertEqual(column(annual, "dilutedShares")["2025-12-31"], 5_200_000_000)
        self.assertEqual(column(quarterly, "dilutedShares")["2026-06-30"], 5_200_000_000)

        # SEC values are kept where both sides have them; Yahoo's newer quarters are added
        self.assertEqual(column(quarterly, "revenue")["2025-03-31"], 14_000_000_000)
        self.assertEqual(column(quarterly, "revenue")["2026-06-30"], 19_000_000_000)

        self.assertEqual(document["checkedAt"], YAHOO_CHECKED)
        self.assertIn("secConcepts", document)
        self.assertIn("ifrsCurrencies", document)
        issues = document["quality"]["issues"]
        for code in ("ifrs", "ifrs-fallback", "adr-eps-rescaled", "foreign-currency"):
            self.assertIn(code, issues)
        self.assertNotIn("short-history", issues)
        self.assertEqual(document["quality"]["level"], "good")
        self.assertEqual(document["split"], {"adjusted": False, "events": [], "eventsComplete": False,
                                             "eventsSource": None, "eventsCheckedAt": None})

    def test_stored_yahoo_adr_ratio_is_not_carried(self):
        """The Yahoo file's own adrRatio (Yahoo against Yahoo, epsFactor 1) says nothing about the ADR: dropped."""
        previous = tsm_yahoo()
        previous["adrRatio"] = {"epsFactor": 1, "basis": "ADR", "derivedAt": "2026-10-01", "pairs": 18}
        document, info = sec.upgrade_document(previous, tsm_sec(), "TSM", NOW, None)
        self.assertEqual(document["adrRatio"]["epsFactor"], ADR)
        self.assertEqual(info["epsBasis"], "rescaled")

    def test_one_outlier_year_still_gives_the_ratio(self):
        """FY2023's Yahoo EPS is off (x7): the other pairs, quarters included, still agree on k = 5 (loose rule)."""
        document, info = sec.upgrade_document(tsm_yahoo(multipliers={2023: 7}), tsm_sec(), "TSM", NOW, None)
        self.assertEqual(info["epsBasis"], "rescaled")
        self.assertEqual(document["adrRatio"]["epsFactor"], ADR)
        annual = document["annual"]
        self.assertAlmostEqual(column(annual, "epsBasic")["2023-12-31"], ADR * 10.0)    # SEC x k, not Yahoo's
        self.assertAlmostEqual(column(annual, "epsBasic")["2022-12-31"], ADR * 7.5)
        self.assertNotIn("eps-yahoo-only", document["quality"]["issues"])

    def test_no_sec_eps_takes_yahoo_eps_on_one_basis(self):
        """SEC carries no EPS at all: every EPS of the result is Yahoo's (per ADR, one basis), so they merge."""
        sec_document = tsm_sec()
        for name in ("annual", "quarterly"):
            for field in ("epsBasic", "epsDiluted"):
                sec_document[name]["values"].pop(field, None)
        document, info = sec.upgrade_document(tsm_yahoo(), sec_document, "TSM", NOW, None)
        self.assertIsNone(info["epsBasis"])
        self.assertNotIn("adrRatio", document)
        self.assertAlmostEqual(column(document["annual"], "epsBasic")["2023-12-31"], ADR * 10.0)
        self.assertEqual(column(document["quarterly"], "dilutedShares")["2026-06-30"], 5_200_000_000)

    def test_asml_type_us_gaap_eur_has_unity_ratio(self):
        """US GAAP foreign filer, EUR, EPS equal on both sides (k = 1): unity, no rescale."""
        document, info = sec.upgrade_document(asml_yahoo(), asml_sec(), "ASML", NOW, None)
        self.assertEqual(document["source"], ifrs.US_GAAP_SOURCE)
        self.assertEqual(document["currency"], "EUR")
        self.assertEqual(info["epsBasis"], "unity")
        self.assertEqual(document["adrRatio"]["epsFactor"], 1)
        issues = document["quality"]["issues"]
        self.assertIn("foreign-gaap", issues)
        self.assertNotIn("adr-eps-rescaled", issues)
        self.assertEqual(document["annual"]["end"], ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31",
                                                     "2025-12-31"])
        self.assertEqual(document["annual"]["src"], ["sec"] * 4 + ["yahoo"])
        self.assertAlmostEqual(column(document["annual"], "epsBasic")["2022-12-31"], 3.0)
        self.assertAlmostEqual(column(document["annual"], "epsBasic")["2025-12-31"], 7.5)
        self.assertEqual(document["quarterly"]["src"], ["sec", "sec", "yahoo", "yahoo"])

    def test_currency_change_keeps_only_the_sec_document(self):
        """SEC reports EUR, the Yahoo file USD: no Yahoo amount is merged; quality gets currency-changed."""
        previous = asml_yahoo(currency="USD")
        document, info = sec.upgrade_document(previous, asml_sec(), "ASML", NOW, None)
        self.assertEqual(info["status"], "currency-changed")
        self.assertIn("USD", info["detail"])
        self.assertIn("EUR", info["detail"])
        self.assertEqual(document["currency"], "EUR")
        self.assertIn("currency-changed", document["quality"]["issues"])
        self.assertEqual(document["annual"]["end"], ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31"])
        self.assertEqual(document["quarterly"]["end"], ["2025-03-31", "2025-06-30"])
        self.assertNotIn("adrRatio", document)
        self.assertEqual(document["checkedAt"], YAHOO_CHECKED)

    def test_is_yahoo_only_reads_source_and_annual_src(self):
        yahoo = tsm_yahoo()
        self.assertTrue(ifrs.is_yahoo_only(yahoo))
        self.assertFalse(ifrs.is_yahoo_only(tsm_sec()))
        mixed = tsm_yahoo()
        mixed["annual"]["src"] = ["yahoo", "sec", "yahoo", "yahoo"]
        self.assertFalse(ifrs.is_yahoo_only(mixed))


class UpgradeWiringTests(unittest.TestCase):
    def layout(self, files, state=None):
        """A temporary report / financials layout (SYNTHETIC): universe, US market progress, stored files, state."""
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        report = os.path.join(root, "report")
        os.makedirs(os.path.join(report, "fundamentals"))
        out = os.path.join(root, "financials")
        os.makedirs(out)
        universe = {"markets": {"US": {"fields": ["symbol", "marketCap"], "rows": [
            ["AAA", 5], ["BBB", 4], ["CCC", 3], ["DDD", 2], ["EEE", 1], ["ZZZ", 0]]}}}
        progress = {
            "issuers": {"0000000101": {"status": "foreign-filer"}, "0000000102": {"status": "no-us-gaap"},
                        "0000000103": {"status": "non-usd"}, "0000000104": {"status": "foreign-filer"},
                        "0000000105": {"status": "foreign-filer"}, "0000000199": {"status": "short-history"}},
            "symbols": {"AAA": {"cik": "0000000101"}, "BBB": {"cik": "0000000102"}, "CCC": {"cik": "0000000103"},
                        "DDD": {"cik": "0000000104"}, "EEE": {"cik": "0000000105"}, "ZZZ": {"cik": "0000000199"}},
            # the CIKs of --foreign (progress_cik_map reads this SEC ticker map only)
            "tickerMap": {"AAA": "0000000101", "BBB": "0000000102", "CCC": "0000000103", "DDD": "0000000104",
                          "EEE": "0000000105", "ZZZ": "0000000199"},
        }
        write_json(os.path.join(report, "universe.json"), universe)
        write_json(os.path.join(report, "fundamentals", "us-market-progress.json"), progress)
        for key, content in files.items():
            path = os.path.join(out, key + ".json")
            if isinstance(content, str):
                with open(path, "w", encoding="utf-8") as file:
                    file.write(content)
            else:
                write_json(path, content)
        state_path = os.path.join(root, "state.json")
        if state is not None:
            write_json(state_path, state)
        return report, out, state_path

    def test_dry_run_counts_and_lists_the_yahoo_only_tickers(self):
        merged = ifrs.build_foreign_document(twd_concepts(), "BBB", NOW)[0]
        report, out, state_path = self.layout({
            "AAA": tsm_yahoo("AAA"),
            "BBB": merged,
            "CCC": tsm_yahoo("CCC"),
            "EEE": "{ not json",
        }, state={"version": 1, "checked": {}, "upgraded": {
            "CCC": {"result": "no-sec-data", "detail": "no-us-gaap", "checkedAt": "2026-10-14"}}})
        code, text = run_main(["--foreign", "--upgrade", "--dry-run", "--no-splits", "--report-dir", report,
                               "--out", out, "--state", state_path])
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertIn("--foreign --upgrade: 5 candidate ticker(s) (foreign-filer 3, no-us-gaap 1, non-usd 1)", text)
        self.assertIn("1 without a file (run --foreign first)", text)
        self.assertIn("1 already hold SEC periods", text)
        self.assertIn("1 answered earlier with no SEC data", text)
        self.assertIn("1 unreadable", text)
        self.assertIn("1 Yahoo-only to merge with SEC", text)
        self.assertIn("Dry run: 1 ticker(s) would be merged", text)
        self.assertTrue(text.rstrip().endswith("AAA"))
        self.assertEqual(read_json(os.path.join(out, "AAA.json"))["checkedAt"], YAHOO_CHECKED)

    def test_already_upgraded_files_are_skipped_without_requests(self):
        report, out, state_path = self.layout({"AAA": tsm_sec("AAA")})
        code, text = run_main(["--foreign", "--upgrade", "--no-splits", "--report-dir", report, "--out", out,
                               "--state", state_path], client=NoNetworkClient({}))
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertIn("Nothing to upgrade.", text)
        self.assertEqual(read_json(os.path.join(out, "AAA.json"))["source"], ifrs.SOURCE)

    def test_no_sec_data_keeps_the_file_and_records_the_answer_apart(self):
        report, out, state_path = self.layout(yahoo_for("AAA", "BBB"))
        before = read_bytes(os.path.join(out, "AAA.json"))
        payloads = {"0000000101": {"cik": 101, "facts": {}}}
        client = FakeClient(payloads)
        code, text = run_main(["--foreign", "--upgrade", "--no-splits", "--report-dir", report, "--out", out,
                               "--state", state_path], client=client)
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertEqual(read_bytes(os.path.join(out, "AAA.json")), before)
        self.assertIn("AAA no-sec-data (no-us-gaap)", text)
        state = read_json(state_path)
        self.assertEqual(state["checked"], {})
        self.assertEqual(state["upgraded"]["AAA"]["result"], "no-sec-data")
        self.assertEqual(client.asked, ["0000000101", "0000000102"])
        self.assertIn("BBB no-facts", text)

    def test_merged_file_is_not_asked_again_on_the_next_run(self):
        report, out, state_path = self.layout(yahoo_for("AAA", "BBB"))
        client = FakeClient({"0000000101": twd_concepts(), "0000000102": twd_concepts()})
        code, text = run_main(["--foreign", "--upgrade", "--no-splits", "--report-dir", report, "--out", out,
                               "--state", state_path], client=client)
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertIn("[1/2] AAA upgraded annual=6 quarters=6 level=good (EPS basis: SEC EPS x 5 (per ADR))", text)
        self.assertIn("[2/2] BBB upgraded annual=6 quarters=6", text)
        self.assertIn("  processed: 2 of 2 ticker(s)", text)
        self.assertNotIn("merged into existing file", text)
        self.assertEqual(read_json(os.path.join(out, "AAA.json"))["source"], ifrs.SOURCE)
        self.assertEqual(read_json(os.path.join(out, "BBB.json"))["source"], ifrs.SOURCE)
        code, text = run_main(["--foreign", "--upgrade", "--no-splits", "--report-dir", report, "--out", out,
                               "--state", state_path], client=NoNetworkClient({}))
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertIn("Nothing to upgrade.", text)

    def test_first_sec_403_stops_the_run(self):
        class ForbiddenOnSecond(FakeClient):
            def company_facts(self, cik):
                self.requests += 1
                if self.requests == 2:
                    raise sec.SecForbidden(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
                return self.payloads.get(cik)

        report, out, state_path = self.layout(yahoo_for("AAA", "BBB", "CCC"))
        bbb_before = read_bytes(os.path.join(out, "BBB.json"))
        ccc_before = read_bytes(os.path.join(out, "CCC.json"))
        client = ForbiddenOnSecond({"0000000101": twd_concepts(), "0000000102": twd_concepts()})
        code, text = run_main(["--foreign", "--upgrade", "--no-splits", "--report-dir", report, "--out", out,
                               "--state", state_path], client=client)
        self.assertEqual(code, sec.EXIT_STOPPED)
        self.assertIn("STOPPED: SEC returned HTTP 403", text)
        self.assertEqual(client.requests, 2)
        self.assertEqual(read_json(os.path.join(out, "AAA.json"))["source"], ifrs.SOURCE)
        self.assertEqual(read_bytes(os.path.join(out, "BBB.json")), bbb_before)
        self.assertEqual(read_bytes(os.path.join(out, "CCC.json")), ccc_before)

    def test_budget_pauses_and_the_next_run_continues(self):
        report, out, state_path = self.layout(yahoo_for("AAA", "BBB", "CCC"))
        values = iter([0, 0])
        clock = lambda: next(values, 100)   # noqa: E731 - the first ticker fits in the budget, the next does not
        client = FakeClient({"0000000101": twd_concepts(), "0000000102": twd_concepts(),
                             "0000000103": twd_concepts()})
        code, text = run_main(["--foreign", "--upgrade", "--no-splits", "--budget", "1", "--report-dir", report,
                               "--out", out, "--state", state_path], client=client, clock=clock)
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertIn("PAUSED: time budget of 1 s used up; 2 not processed yet", text)
        self.assertEqual(client.requests, 1)
        client = FakeClient({"0000000102": twd_concepts(), "0000000103": twd_concepts()})
        code, text = run_main(["--foreign", "--upgrade", "--no-splits", "--report-dir", report, "--out", out,
                               "--state", state_path], client=client)
        self.assertEqual(code, sec.EXIT_DONE)
        self.assertEqual(client.requests, 2)
        self.assertNotIn("PAUSED", text)
        for key in ("AAA", "BBB", "CCC"):
            self.assertEqual(read_json(os.path.join(out, key + ".json"))["source"], ifrs.SOURCE)

    def test_upgrade_needs_foreign(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            sec.parse_args(["--upgrade"])
        args = sec.parse_args(["--foreign", "--upgrade", "--dry-run"])
        self.assertTrue(args.upgrade and args.foreign)


if __name__ == "__main__":
    unittest.main()
