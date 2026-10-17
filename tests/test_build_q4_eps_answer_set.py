"""
The Q4 EPS answer set (atlas/scripts/build_q4_eps_answer_set.py): synthetic git repositories for each rule, the
committed docs/report/q4-8k/answer-set.json, and a rebuild from 95184ade (handoff §0: 2,649 rows in 563 keys,
2,387 basic rows, the fiscal-year distribution of §0).

Offline. The tests that read 95184ade are skipped when that commit is not in the local clone.
Run: python3 -m unittest discover -s tests -p "test_build_q4_eps_answer_set.py" -v
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_q4_eps_answer_set as bans  # noqa: E402

REF = "95184ade77cd30c565d3873e9f9050f3ec7c0054"
COMMITTED = os.path.join(ROOT, "docs", "report", "q4-8k", "answer-set.json")
# handoff §0: diluted A rows by fiscal year of the period end (the calendar year of `end`)
FY_BY_END_YEAR = {"2010": 7, "2011": 128, "2012": 173, "2013": 200, "2014": 218, "2015": 238, "2016": 258,
                  "2017": 267, "2018": 288, "2019": 284, "2020": 227, "2021": 100, "2022": 69, "2023": 67,
                  "2024": 57, "2025": 53, "2026": 15}
TW_SUFFIXES = (".TW", ".TWO")
BOTH_A = {"epsDiluted": "A", "epsBasic": "A"}


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True).stdout.decode("utf-8").strip()


def ref_present():
    result = subprocess.run(["git", "-C", ROOT, "rev-parse", "--verify", "--quiet", REF + "^{commit}"],
                            capture_output=True)
    return result.returncode == 0


def quarter(end, fy, fq, grade=None, diluted=None, basic=None, net_income=None):
    return {"end": end, "fy": fy, "fq": fq, "grade": grade, "diluted": diluted, "basic": basic,
            "netIncome": net_income}


def document(market, quarters, facts):
    return {
        "symbol": "SYN",
        "market": market,
        "quarterly": {
            "end": [q["end"] for q in quarters],
            "fy": [q["fy"] for q in quarters],
            "fq": [q["fq"] for q in quarters],
            "epsQuality": [q["grade"] for q in quarters],
            "values": {"epsDiluted": [q["diluted"] for q in quarters],
                       "epsBasic": [q["basic"] for q in quarters],
                       "netIncome": [q["netIncome"] for q in quarters]},
        },
        "secQ4EpsFacts": facts,
    }


def sec(value, accession, filed="2021-02-01", form="10-K"):
    return {"value": value, "accession": accession, "filed": filed, "form": form}


def make_repo(files):
    """A temporary git repository whose one commit holds the given financials documents."""
    repo = tempfile.mkdtemp(prefix="q4-answer-set-")
    folder = os.path.join(repo, "docs", "report", "financials")
    os.makedirs(folder)
    for name, doc in files.items():
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            json.dump(doc, f)
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "synthetic")
    return repo, git(repo, "rev-parse", "HEAD")


def canonical(answer):
    return (json.dumps(answer, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


class SyntheticRepoTests(unittest.TestCase):
    def setUp(self):
        self.repo, self.commit = make_repo({
            "AAA.json": document("US", [
                quarter("2019-03-31", 2019, 1, None, 0.5, 0.5, 10),
                quarter("2019-12-31", 2019, 4, BOTH_A, 1.2, 1.25, 40),
                quarter("2020-12-31", 2020, 4, {"epsDiluted": "A", "epsBasic": "Y"}, 2.0, 2.1, 80),
                quarter("2021-03-31", 2021, 3, BOTH_A, 0.3, 0.3, 5),
            ], {
                "2019-12-31": {"epsDiluted": sec(1.19, "0000000001-20-000001", "2020-02-14"),
                               "epsBasic": sec(1.24, "0000000001-20-000001", "2020-02-14")},
                "2020-12-31": {"epsDiluted": sec(1.9, "0000000001-21-000002", "2021-02-12"),
                               "epsBasic": sec(2.05, "0000000001-21-000002", "2021-02-12")},
            }),
            "ZZZ.json": document("US", [quarter("2019-12-31", 2019, 4, BOTH_A, 0.9, 0.95, 20)], {
                "2019-12-31": {"epsDiluted": sec(0.92, "0000000002-20-000001", "2020-02-10"),
                               "epsBasic": sec(0.96, "0000000002-20-000001", "2020-02-10")},
            }),
            "BBB.TW.json": document("US", [quarter("2019-12-31", 2019, 4, BOTH_A, 1, 1, 1)], {
                "2019-12-31": {"epsDiluted": sec(1, "0000000003-20-000001"),
                               "epsBasic": sec(1, "0000000003-20-000001")},
            }),
            "CCC.TWO.json": document("US", [quarter("2019-12-31", 2019, 4, BOTH_A, 1, 1, 1)], {
                "2019-12-31": {"epsDiluted": sec(1, "0000000004-20-000001"),
                               "epsBasic": sec(1, "0000000004-20-000001")},
            }),
            "DDD.json": document("TW", [quarter("2019-12-31", 2019, 4, BOTH_A, 1, 1, 1)], {
                "2019-12-31": {"epsDiluted": sec(1, "0000000005-20-000001"),
                               "epsBasic": sec(1, "0000000005-20-000001")},
            }),
        })

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_selects_us_q4_grade_a_rows_sorted_by_key_and_end(self):
        answer = bans.collect(self.commit, repo=self.repo)
        self.assertEqual([(r["key"], r["end"]) for r in answer["rows"]],
                         [("AAA", "2019-12-31"), ("AAA", "2020-12-31"), ("ZZZ", "2019-12-31")])
        self.assertEqual([(r["key"], r["end"]) for r in answer["rowsBasic"]],
                         [("AAA", "2019-12-31"), ("ZZZ", "2019-12-31")])
        self.assertEqual(answer["counts"]["usFiles"], 2)

    def test_row_fields_come_from_the_sec_fact_and_the_stored_cells(self):
        first = bans.collect(self.commit, repo=self.repo)["rows"][0]
        self.assertEqual(first, {"key": "AAA", "end": "2019-12-31", "fy": 2019, "value": 1.19, "stored": 1.2,
                                 "netIncome": 40, "accession": "0000000001-20-000001", "filed": "2020-02-14",
                                 "form": "10-K"})

    def test_basic_rows_use_the_basic_grade_and_fact(self):
        basic = bans.collect(self.commit, repo=self.repo)["rowsBasic"]
        self.assertEqual(basic[0]["value"], 1.24)
        self.assertEqual(basic[0]["stored"], 1.25)
        self.assertNotIn(("AAA", "2020-12-31"), [(r["key"], r["end"]) for r in basic])

    def test_counts_and_fiscal_year_distribution(self):
        counts = bans.collect(self.commit, repo=self.repo)["counts"]
        self.assertEqual(counts, {"usFiles": 2, "rows": 3, "keys": 2, "rowsBasic": 2, "keysBasic": 2,
                                  "byEndYear": {"2019": 2, "2020": 1}})

    def test_output_is_canonical_json_with_the_full_commit(self):
        answer = bans.collect(self.commit, repo=self.repo)
        self.assertEqual(answer["commit"], self.commit)
        self.assertEqual(answer["ref"], self.commit[:8])
        self.assertEqual(bans.render(answer), canonical(answer))

    def test_reads_the_ref_not_the_working_tree(self):
        before = bans.render(bans.collect(self.commit, repo=self.repo))
        with open(os.path.join(self.repo, "docs", "report", "financials", "AAA.json"), "w", encoding="utf-8") as f:
            json.dump(document("US", [quarter("2019-12-31", 2019, 4, BOTH_A, 9, 9, 9)], {}), f)
        self.assertEqual(bans.render(bans.collect(self.commit, repo=self.repo)), before)

    def test_a_grade_a_row_without_a_sec_fact_is_an_error(self):
        repo, commit = make_repo({
            "EEE.json": document("US", [quarter("2019-12-31", 2019, 4, BOTH_A, 1.0, 1.0, 1)], {}),
        })
        try:
            with self.assertRaises(bans.BuildError):
                bans.collect(commit, repo=repo)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_cli_exits_1_and_writes_nothing_for_an_unknown_ref(self):
        out = os.path.join(self.repo, "answer-set.json")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bans.main(["--ref", "refs/heads/no-such-ref-for-s2a-1", "--out", out]), 1)
        self.assertFalse(os.path.exists(out))


class CommittedAnswerSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(COMMITTED, "rb") as f:
            cls.raw = f.read()
        cls.doc = json.loads(cls.raw.decode("utf-8"))

    def test_pinned_to_95184ade(self):
        self.assertEqual(self.doc["ref"], "95184ade")
        self.assertEqual(self.doc["commit"], REF)
        self.assertEqual(self.doc["field"], "epsDiluted")

    def test_counts(self):
        self.assertEqual(len(self.doc["rows"]), 2649)
        self.assertEqual(len({r["key"] for r in self.doc["rows"]}), 563)
        self.assertEqual(len(self.doc["rowsBasic"]), 2387)
        self.assertEqual(len({r["key"] for r in self.doc["rowsBasic"]}), 531)
        self.assertEqual(self.doc["counts"]["rows"], 2649)
        self.assertEqual(self.doc["counts"]["keys"], 563)
        self.assertEqual(self.doc["counts"]["rowsBasic"], 2387)

    def test_fiscal_year_distribution_matches_handoff_section_0(self):
        self.assertEqual(self.doc["counts"]["byEndYear"], FY_BY_END_YEAR)
        by_rows = {}
        for r in self.doc["rows"]:
            by_rows[r["end"][:4]] = by_rows.get(r["end"][:4], 0) + 1
        self.assertEqual(by_rows, FY_BY_END_YEAR)

    def test_every_row_has_net_income_and_sec_fields(self):
        for r in self.doc["rows"] + self.doc["rowsBasic"]:
            self.assertIsNotNone(r["netIncome"], r)
            self.assertTrue(r["accession"] and r["filed"] and r["form"], r)

    def test_rows_are_us_only_sorted_and_unique(self):
        for name in ("rows", "rowsBasic"):
            pairs = [(r["key"], r["end"]) for r in self.doc[name]]
            self.assertEqual(pairs, sorted(pairs), name)
            self.assertEqual(len(pairs), len(set(pairs)), name)
            self.assertFalse([k for k, _ in pairs if k.endswith(TW_SUFFIXES)], name)

    def test_file_is_the_canonical_rendering(self):
        self.assertEqual(self.raw, canonical(self.doc))


@unittest.skipUnless(ref_present(), f"{REF} is not in the local clone")
class RebuildFromRefTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="q4-answer-set-ref-")
        cls.first = os.path.join(cls.tmp, "first.json")
        cls.second = os.path.join(cls.tmp, "second.json")
        assert bans.main(["--ref", REF, "--out", cls.first]) == 0
        assert bans.main(["--ref", REF, "--out", cls.second]) == 0
        with open(cls.first, "rb") as f:
            cls.first_bytes = f.read()
        with open(cls.second, "rb") as f:
            cls.second_bytes = f.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_rebuild_counts_match_handoff(self):
        doc = json.loads(self.first_bytes.decode("utf-8"))
        self.assertEqual(doc["ref"], "95184ade")
        self.assertEqual(len(doc["rows"]), 2649)
        self.assertEqual(len({r["key"] for r in doc["rows"]}), 563)
        self.assertEqual(len(doc["rowsBasic"]), 2387)
        self.assertEqual(doc["counts"]["byEndYear"], FY_BY_END_YEAR)

    def test_two_rebuilds_are_byte_identical(self):
        self.assertEqual(self.first_bytes, self.second_bytes)

    def test_rebuild_is_byte_identical_to_the_committed_file(self):
        with open(COMMITTED, "rb") as f:
            self.assertEqual(self.first_bytes, f.read())


if __name__ == "__main__":
    unittest.main()
