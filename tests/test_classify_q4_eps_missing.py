"""Synthetic tests for atlas/scripts/classify_q4_eps_missing.py: every class, the row definition, and the offline and
determinism guarantees. Every document is built here; no financial file is read.
Run: python3 -m unittest discover -s tests -p "test_classify_q4_eps_missing.py" -v
"""
import ast
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import audit_q4_eps_backfill as audit  # noqa: E402
import classify_q4_eps_missing as classify  # noqa: E402

SYMBOL = "TEST"
MONTH_DAY = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
FORBIDDEN_IMPORTS = {"urllib", "urllib3", "requests", "http", "socket", "ssl", "curl_cffi", "httpx", "aiohttp",
                     "ftplib", "smtplib", "yfinance"}


def quarter(fy, fq, eps=None, income=None, shares=None, src="sec"):
    return {"fy": fy, "fq": fq, "end": f"{fy}-{MONTH_DAY[fq]}", "src": src,
            "eps": eps, "income": income, "shares": shares}


def annual(fy, eps=4.0, income=4000, shares=1000, src="sec"):
    return {"fy": fy, "fq": None, "end": f"{fy}-12-31", "src": src, "eps": eps, "income": income, "shares": shares}


def block(rows, with_fq, with_eps_list=True):
    values = {"netIncome": [row["income"] for row in rows], "dilutedShares": [row["shares"] for row in rows]}
    if with_eps_list:
        values["epsDiluted"] = [row["eps"] for row in rows]
    result = {"end": [row["end"] for row in rows], "fy": [row["fy"] for row in rows],
              "src": [row["src"] for row in rows], "values": values}
    if with_fq:
        result["fq"] = [row["fq"] for row in rows]
    return result


def document(quarters, annuals, market="US", with_eps_list=True, symbol=SYMBOL):
    return {"symbol": symbol, "market": market, "annual": block(annuals, with_fq=False),
            "quarterly": block(quarters, with_fq=True, with_eps_list=with_eps_list)}


def year(fy=2019, *, annuals=None, q1=None, q2=None, q3=None, q4=None):
    """A fiscal year whose derivation is clean: annual EPS 4.0, Q1..Q3 EPS 1.0, Q4 net income 1000, all on 1000 shares.
    Its Q4 diluted EPS is null, and the derivation fills it with 1.0 (no ledger row means the run stops)."""
    if annuals is None:
        annuals = [annual(fy)]
    quarters = [
        q1 if q1 is not None else quarter(fy, 1, 1.0, 1000, 1000),
        q2 if q2 is not None else quarter(fy, 2, 1.0, 1000, 1000),
        q3 if q3 is not None else quarter(fy, 3, 1.0, 1000, 1000),
        q4 if q4 is not None else quarter(fy, 4, None, 1000, 1000),
    ]
    return document(quarters, annuals)


def records_of(doc, blocked=None):
    return classify.classify_document(doc, SYMBOL, blocked or {})


def only(doc, blocked=None):
    records = records_of(doc, blocked)
    if len(records) != 1:
        raise AssertionError(f"expected one missing row, got {records}")
    return records[0]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def ledger_row(symbol, end, reason, field="epsDiluted"):
    return {"symbol": symbol, "end": end, "field": field, "old": 0.5, "new": 0.6, "reason": reason}


class ClassTests(unittest.TestCase):
    def assert_check_name(self, detail):
        """A derivation-rejected detail must be a message the existing audit code reports."""
        self.assertIn(detail, Path(audit.__file__).read_text(encoding="utf-8"))

    def test_clean_year_is_accepted_by_the_derivation(self):
        # The fixture itself: its missing Q4 is filled by build_financials, so it needs a ledger row to be classified.
        with self.assertRaisesRegex(RuntimeError, "no class applies"):
            records_of(year())

    def test_class_1_no_q4_net_income(self):
        doc = year(q4=quarter(2019, 4, None, None, 1000))
        record = only(doc)
        self.assertEqual(record["class"], "no-q4-net-income")
        self.assertEqual((record["symbol"], record["end"], record["fy"]), (SYMBOL, "2019-12-31", 2019))

    def test_class_1_outranks_the_later_classes(self):
        doc = document([quarter(2019, 1, None, 1000, 1000), quarter(2019, 4, None, None, 1000)], [])
        self.assertEqual(only(doc)["class"], "no-q4-net-income")

    def test_class_2_no_annual_row_for_the_fiscal_year(self):
        record = only(year(annuals=[]))
        self.assertEqual(record["class"], "no-annual-diluted-eps")
        self.assertEqual(record["detail"], "0 annual rows for fy 2019")

    def test_class_2_two_annual_rows_are_not_one_lookup(self):
        record = only(year(annuals=[annual(2019), annual(2019)]))
        self.assertEqual(record["class"], "no-annual-diluted-eps")
        self.assertEqual(record["detail"], "2 annual rows for fy 2019")

    def test_class_2_null_annual_eps(self):
        record = only(year(annuals=[annual(2019, eps=None)]))
        self.assertEqual(record["class"], "no-annual-diluted-eps")
        self.assertEqual(record["detail"], "annual epsDiluted null for fy 2019")

    def test_class_2_outranks_class_3(self):
        doc = year(annuals=[annual(2019, eps=None)], q2=quarter(2019, 2, None, 1000, 1000))
        self.assertEqual(only(doc)["class"], "no-annual-diluted-eps")

    def test_class_3_null_q2_eps(self):
        record = only(year(q2=quarter(2019, 2, None, 1000, 1000)))
        self.assertEqual(record["class"], "q1-q3-incomplete")
        self.assertEqual(record["detail"], "Q2 null")

    def test_class_3_missing_q1_row(self):
        doc = document([quarter(2019, 2, 1.0, 1000, 1000), quarter(2019, 3, 1.0, 1000, 1000),
                        quarter(2019, 4, None, 1000, 1000)], [annual(2019)])
        record = only(doc)
        self.assertEqual(record["class"], "q1-q3-incomplete")
        self.assertEqual(record["detail"], "Q1 rows=0")

    def test_class_3_duplicated_q1_row(self):
        doc = document([quarter(2019, 1, 1.0, 1000, 1000), quarter(2019, 1, 1.0, 1000, 1000),
                        quarter(2019, 2, 1.0, 1000, 1000), quarter(2019, 3, 1.0, 1000, 1000),
                        quarter(2019, 4, None, 1000, 1000)], [annual(2019)])
        self.assertEqual(only(doc)["detail"], "Q1 rows=2")

    def test_class_4_share_basis_differs_by_more_than_25_percent(self):
        record = only(year(q1=quarter(2019, 1, 1.0, 1000, 1500)))
        self.assertEqual(record["class"], "derivation-rejected")
        self.assertEqual(record["detail"], "方法 A 股數基準差超過 25%")
        self.assert_check_name(record["detail"])

    def test_class_4_annual_eps_disagrees_with_its_net_income(self):
        record = only(year(annuals=[annual(2019, eps=4.0, income=8000, shares=1000)]))
        self.assertEqual(record["class"], "derivation-rejected")
        self.assertEqual(record["detail"], "年度 EPS 與淨利／股數不一致")
        self.assert_check_name(record["detail"])

    def test_class_4_derived_eps_and_q4_income_differ_in_sign(self):
        record = only(year(q4=quarter(2019, 4, None, -1000, 1000)))
        self.assertEqual(record["class"], "derivation-rejected")
        self.assertEqual(record["detail"], "方法 A 推算 EPS 與 Q4 淨利符號不一致")
        self.assert_check_name(record["detail"])

    def test_class_4_derived_eps_fails_the_q4_income_over_shares_check(self):
        record = only(year(q4=quarter(2019, 4, None, 1500, 1000)))
        self.assertEqual(record["class"], "derivation-rejected")
        self.assertEqual(record["detail"], "方法 A 推算值未通過 Q4 淨利／股數檢查")
        self.assert_check_name(record["detail"])

    def test_class_4_a_non_sec_piece_is_rejected(self):
        record = only(year(q1=quarter(2019, 1, 1.0, 1000, 1000, src="yahoo")))
        self.assertEqual(record["class"], "derivation-rejected")
        self.assertEqual(record["detail"], "年度或季度來源非 SEC")
        self.assert_check_name(record["detail"])

    def test_class_4_outranks_the_ledger(self):
        blocked = {(SYMBOL, "2019-12-31", "epsDiluted"): "not-in-first-batch"}
        record = only(year(q1=quarter(2019, 1, 1.0, 1000, 1500)), blocked)
        self.assertEqual(record["class"], "derivation-rejected")

    def test_class_5_ledger_blocked_when_the_derivation_would_fill(self):
        blocked = {(SYMBOL, "2019-12-31", "epsDiluted"): "not-in-first-batch"}
        record = only(year(), blocked)
        self.assertEqual(record["class"], "ledger-blocked")
        self.assertEqual(record["detail"], "not-in-first-batch")

    def test_a_fillable_row_missing_from_the_ledger_stops_the_run(self):
        with self.assertRaisesRegex(RuntimeError, "no class applies"):
            records_of(year(), {(SYMBOL, "2018-12-31", "epsDiluted"): "not-in-first-batch"})

    def test_ledger_rows_for_other_fields_do_not_match(self):
        blocked = classify.load_blocked({"blocked": [ledger_row(SYMBOL, "2019-12-31", "x", field="epsBasic")]})
        self.assertEqual(blocked, {})
        with self.assertRaisesRegex(RuntimeError, "no class applies"):
            records_of(year(), blocked)

    def test_duplicate_ledger_rows_are_refused(self):
        with self.assertRaises(ValueError):
            classify.load_blocked({"blocked": [ledger_row(SYMBOL, "2019-12-31", "a"),
                                               ledger_row(SYMBOL, "2019-12-31", "b")]})


class RowDefinitionTests(unittest.TestCase):
    def test_only_q4_rows_are_counted(self):
        doc = document([quarter(2019, 1, None, 1000, 1000), quarter(2019, 2, 1.0, 1000, 1000),
                        quarter(2019, 3, 1.0, 1000, 1000), quarter(2019, 4, 1.0, 1000, 1000)], [annual(2019)])
        self.assertEqual(records_of(doc), [])

    def test_a_file_without_an_eps_list_counts_every_q4_row_as_missing(self):
        doc = document(
            [quarter(2019, fq, None, 1000, 1000) for fq in (1, 2, 3)] + [quarter(2019, 4, None, 1000, 1000)]
            + [quarter(2020, fq, None, 1000, 1000) for fq in (1, 2, 3)] + [quarter(2020, 4, None, 1000, 1000)],
            [annual(2019), annual(2020)], with_eps_list=False)
        records = records_of(doc)
        self.assertEqual([(r["fy"], r["class"], r["detail"]) for r in records],
                         [(2019, "q1-q3-incomplete", "Q1 null, Q2 null, Q3 null"),
                          (2020, "q1-q3-incomplete", "Q1 null, Q2 null, Q3 null")])

    def test_build_skips_non_us_files_and_index_json(self):
        with tempfile.TemporaryDirectory() as directory:
            write_json(os.path.join(directory, "US.json"),
                       document([quarter(2019, 1, 1.0, 1000, 1000), quarter(2019, 2, 1.0, 1000, 1000),
                                 quarter(2019, 3, 1.0, 1000, 1000), quarter(2019, 4, None, None, 1000)],
                                [annual(2019)], symbol="US"))
            write_json(os.path.join(directory, "TW.json"),
                       document([quarter(2019, 4, None, None, 1000)], [annual(2019)], market="TW", symbol="TW"))
            write_json(os.path.join(directory, "index.json"),
                       document([quarter(2019, 4, None, None, 1000)], [annual(2019)], symbol="INDEX"))
            report = classify.build(directory, {"blocked": []})
        self.assertEqual(report["reconciliation"]["usFiles"], 1)
        self.assertEqual([row["symbol"] for row in report["rows"]], ["US"])

    def test_reconciliation_splits_rows_by_eps_list(self):
        with tempfile.TemporaryDirectory() as directory:
            write_json(os.path.join(directory, "WITH.json"), document(
                [quarter(2019, 1, 1.0, 1000, 1000), quarter(2019, 2, 1.0, 1000, 1000),
                 quarter(2019, 3, 1.0, 1000, 1000), quarter(2019, 4, None, None, 1000)], [annual(2019)],
                symbol="WITH"))
            write_json(os.path.join(directory, "WITHOUT.json"), document(
                [quarter(2019, fq, None, 1000, 1000) for fq in (1, 2, 3, 4)]
                + [quarter(2020, fq, None, 1000, 1000) for fq in (1, 2, 3, 4)],
                [annual(2019), annual(2020)], with_eps_list=False, symbol="WITHOUT"))
            report = classify.build(directory, {"blocked": []})
        reconciliation = report["reconciliation"]
        self.assertEqual(reconciliation["usQ4Rows"], 1 + 2)
        self.assertEqual(reconciliation["missing"], 1 + 2)
        self.assertEqual(reconciliation["q4RowsInFilesWithEpsDilutedList"], 1)
        self.assertEqual(reconciliation["missingInFilesWithEpsDilutedList"], 1)
        self.assertEqual(reconciliation["q4RowsInFilesWithoutEpsDilutedList"], 2)
        self.assertEqual(reconciliation["missingInFilesWithoutEpsDilutedList"], 2)
        self.assertEqual(reconciliation["filesWithoutEpsDilutedList"], 1)
        self.assertEqual(sum(report["counts"].values()), reconciliation["missing"])

    def test_every_class_is_listed_even_when_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            report = classify.build(directory, {"blocked": []})
        self.assertEqual(report["counts"], {name: 0 for name in classify.CLASSES})
        self.assertEqual(report["rowDefinition"]["classOrder"], list(classify.CLASSES))
        self.assertIn("usRow", report["rowDefinition"])
        self.assertIn("missing", report["rowDefinition"])


class OutputTests(unittest.TestCase):
    def fixture_dir(self, directory):
        """Two missing rows with classes 2 and 4, one complete row; no ledger row is needed."""
        os.makedirs(directory, exist_ok=True)
        write_json(os.path.join(directory, "A.json"), year(annuals=[annual(2019, eps=None)]))
        write_json(os.path.join(directory, "B.json"), year(q1=quarter(2019, 1, 1.0, 1000, 1500)))
        write_json(os.path.join(directory, "C.json"), document(
            [quarter(2019, 1, 1.0, 1000, 1000), quarter(2019, 2, 1.0, 1000, 1000),
             quarter(2019, 3, 1.0, 1000, 1000), quarter(2019, 4, 1.0, 1000, 1000)], [annual(2019)],
            symbol="C"))
        return directory

    def run_main(self, directory, out):
        # The ledger sits beside the financials directory, never inside it.
        ledger = os.path.join(os.path.dirname(os.path.abspath(directory)), "ledger-for-test.json")
        write_json(ledger, {"blocked": []})
        with contextlib.redirect_stdout(io.StringIO()):
            return classify.main(["--dir", directory, "--ledger", ledger, "--out", out])

    def test_rebuild_is_byte_identical_with_sorted_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            financials = self.fixture_dir(os.path.join(directory, "financials"))
            first = os.path.join(directory, "first.json")
            second = os.path.join(directory, "second.json")
            self.run_main(financials, first)
            self.run_main(financials, second)
            first_bytes = Path(first).read_bytes()
            self.assertEqual(first_bytes, Path(second).read_bytes())
            parsed = json.loads(first_bytes)
            self.assertEqual(first_bytes.decode("utf-8").rstrip("\n"),
                             json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            self.assertEqual(parsed["schema"], classify.SCHEMA)
            self.assertNotIn("generatedAt", parsed)

    def test_the_financials_directory_is_not_written(self):
        with tempfile.TemporaryDirectory() as directory:
            financials = os.path.join(directory, "financials")
            self.fixture_dir(financials)

            def snapshot():
                return {name: (Path(financials, name).read_bytes(), os.stat(os.path.join(financials, name)).st_mtime_ns)
                        for name in sorted(os.listdir(financials))}

            before = snapshot()
            self.run_main(financials, os.path.join(directory, "out.json"))
            self.assertEqual(snapshot(), before)

    def test_the_report_is_refused_inside_the_financials_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            financials = os.path.join(directory, "financials")
            os.makedirs(financials)
            ledger = os.path.join(directory, "ledger.json")
            write_json(ledger, {"blocked": []})
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                classify.main(["--dir", financials, "--ledger", ledger,
                               "--out", os.path.join(financials, "x.json")])

    def test_the_script_imports_no_network_module_and_names_no_sec_host(self):
        source = Path(classify.__file__).read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported & FORBIDDEN_IMPORTS, set())
        self.assertNotIn("http", source)
        self.assertNotIn("sec.gov", source.lower())

    def test_the_run_makes_no_socket_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            financials = self.fixture_dir(os.path.join(directory, "financials"))
            with mock.patch.object(socket, "socket", side_effect=AssertionError("network use")):
                report = self.run_main(financials, os.path.join(directory, "out.json"))
        self.assertEqual(report["reconciliation"]["missing"], sum(report["counts"].values()))

    def test_the_no_failed_check_text_is_the_one_the_audit_code_returns(self):
        self.assertIn(classify.NO_FAILED_CHECK, Path(audit.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
