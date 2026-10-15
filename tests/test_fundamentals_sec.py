"""
US fundamentals from SEC XBRL frames: atlas/scripts/build_fundamentals_sec.py → docs/report/fundamentals/us-sec.json.

Offline: frames payloads are small hand-written dicts in the frames API layout, plus the frames /
company_tickers fixtures of atlas/tests/fixtures/valuation for an end-to-end run with a fake client.
Run: python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "atlas", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import build_fundamentals_sec as sec  # noqa: E402

VALUATION_FIXTURES = os.path.join(ROOT, "atlas", "tests", "fixtures", "valuation")
TODAY = date(2026, 10, 15)
NOW = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)
COL = {name: index for index, name in enumerate(sec.FIELDS)}

DILUTED = "us-gaap:EarningsPerShareDiluted"
BASIC = "us-gaap:EarningsPerShareBasic"
BOTH = "us-gaap:EarningsPerShareBasicAndDiluted"
REVENUES = "us-gaap:Revenues"
CONTRACT = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
SHARES = "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"


def quarter_dates(year, quarter):
    starts = {1: "01-01", 2: "04-01", 3: "07-01", 4: "10-01"}
    ends = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}
    return f"{year}-{starts[quarter]}", f"{year}-{ends[quarter]}"


def frame(key, period, points, foreign=False, currency="USD"):
    """(Frame, points) as fetch_frames returns them; points: [(cik, start, end, value)]."""
    kind = "q" if "Q" in period else "y"
    label = sec.quarter_number(int(period[2:6]), int(period[-1])) if kind == "q" else int(period[2:])
    role = "shares" if "Shares" in key or key.endswith("Shares") else "rev" if "Revenue" in key else "eps"
    unit = "shares" if role == "shares" else f"{currency}-per-shares" if role == "eps" else currency
    request = sec.Frame(key, None if role == "shares" else currency, unit, period, label, kind, foreign)
    return request, [(cik, date.fromisoformat(start), date.fromisoformat(end), value) for cik, start, end, value in points]


def calendar_company(cik, quarters, annual=None, key=DILUTED):
    """quarters: {(year, q): value} of one concept → frame list (calendar fiscal year)."""
    frames = []
    for (year, quarter), value in quarters.items():
        start, end = quarter_dates(year, quarter)
        frames.append(frame(key, f"CY{year}Q{quarter}", [(cik, start, end, value)]))
    for year, value in (annual or {}).items():
        frames.append(frame(key, f"CY{year}", [(cik, f"{year}-01-01", f"{year}-12-31", value)]))
    return frames


def blocks(frames, today=TODAY, cik=1):
    companies = sec.collect(frames)
    return sec.company_blocks(companies[cik], today)


class PayloadTest(unittest.TestCase):
    def test_parses_the_frames_layout_and_checks_what_was_asked(self):
        with open(os.path.join(VALUATION_FIXTURES, "frames-EarningsPerShareDiluted-CY2025Q3.json"), encoding="utf-8") as file:
            payload = json.load(file)
        request = sec.Frame(DILUTED, "USD", "USD-per-shares", "CY2025Q3", sec.quarter_number(2025, 3), "q", False)
        points, dropped = sec.parse_frame(payload, request)
        self.assertEqual(dropped, 0)
        self.assertIn((1045810, date(2025, 7, 28), date(2025, 10, 26), 1.3), points)
        wrong = sec.Frame(BASIC, "USD", "USD-per-shares", "CY2025Q3", 0, "q", False)
        with self.assertRaisesRegex(sec.FrameFormatError, "tag.*EarningsPerShareBasic"):
            sec.parse_frame(payload, wrong)
        with self.assertRaisesRegex(sec.FrameFormatError, "no 'data' list"):
            sec.parse_frame({"tag": "EarningsPerShareDiluted"}, request)
        with self.assertRaisesRegex(sec.FrameFormatError, "not a frames object"):
            sec.parse_frame([], request)

    def test_drops_bad_points_wrong_durations_and_duplicated_companies(self):
        request = sec.Frame(DILUTED, "USD", "USD-per-shares", "CY2026Q1", 0, "q", False)
        payload = {"data": [
            {"cik": 1, "start": "2026-01-01", "end": "2026-03-31", "val": 1.0},
            {"cik": "2", "start": "2026-01-01", "end": "2026-03-31", "val": 2.0},     # numeric string CIK
            {"cik": 3, "start": "2025-10-01", "end": "2026-03-31", "val": 3.0},       # six months: not a quarter
            {"cik": 4, "start": "2026-01-01", "end": "2026-03-31", "val": "x"},
            {"cik": 5, "start": "2026-01-01", "end": "2026-03-31", "val": 5.0},
            {"cik": 5, "start": "2026-01-02", "end": "2026-03-30", "val": 6.0},      # twice: ambiguous
        ]}
        points, dropped = sec.parse_frame(payload, request)
        self.assertEqual(sorted(point[0] for point in points), [1, 2])
        self.assertEqual(dropped, 4)
        with self.assertRaisesRegex(sec.FrameFormatError, "none with cik"):
            sec.parse_frame({"data": [{"x": 1}]}, request)

    def test_tickers_keep_every_share_class_of_a_cik(self):
        with open(os.path.join(VALUATION_FIXTURES, "company_tickers.json"), encoding="utf-8") as file:
            by_cik, by_key = sec.parse_tickers(json.load(file))
        self.assertEqual(by_cik[1652044], ["GOOGL", "GOOG"])
        self.assertEqual(by_key["BRK-B"], 5555)       # BRK.B → the BRK-B key of growthKey
        self.assertNotIn("BAD^TICK", by_key)
        by_cik, by_key = sec.parse_tickers({"0": {"cik_str": 1, "ticker": "AAA"}, "1": {"cik_str": 2, "ticker": "AAA"}})
        self.assertEqual(by_key, {"AAA": 1})           # a ticker under two CIKs keeps the first
        with self.assertRaises(sec.FrameFormatError):
            sec.parse_tickers({"0": {"x": 1}})

    def test_request_plan(self):
        plan = sec.frame_plan(TODAY)
        core = [request for request in plan if not request.foreign]
        quarters = sorted({request.period for request in core if request.kind == "q"})
        self.assertEqual(quarters[-1], "CY2026Q3")
        self.assertEqual(len(quarters), sec.QUARTER_FRAMES)
        self.assertEqual(sorted({request.period for request in core if request.kind == "y"}),
                         [f"CY{year}" for year in range(2015, 2027)])
        urls = {request.url for request in plan}
        self.assertEqual(len(urls), len(plan))         # BasicAndDiluted is fetched once
        self.assertIn("https://data.sec.gov/api/xbrl/frames/us-gaap/EarningsPerShareDiluted/USD-per-shares/CY2026Q2.json", urls)
        self.assertIn("https://data.sec.gov/api/xbrl/frames/ifrs-full/BasicEarningsLossPerShare/EUR-per-shares/CY2025.json", urls)
        self.assertIn("https://data.sec.gov/api/xbrl/frames/ifrs-full/Revenue/USD/CY2022.json", urls)
        self.assertNotIn("https://data.sec.gov/api/xbrl/frames/us-gaap/Revenues/USD/CY2021.json",
                         {request.url for request in plan if request.foreign})
        self.assertEqual(len(sec.frame_plan(TODAY, foreign=False)), len(core))


class QuarterTest(unittest.TestCase):
    def test_four_quarters_against_the_same_calendar_quarter_a_year_earlier(self):
        values = {(2025, 1): 1.0, (2025, 2): 1.0, (2025, 3): 1.0, (2025, 4): 4.0,
                  (2026, 1): 1.2, (2026, 2): 1.5, (2024, 3): 2.0, (2024, 4): 2.0}
        q, y, foreign, currency = blocks(calendar_company(1, values))
        self.assertEqual(q["eps"], [50.0, 20.0, 100.0, -50.0])
        self.assertEqual(q["end"], "2026-06-30")
        self.assertEqual(q["src"], "frames")
        self.assertIsNone(y)
        self.assertFalse(foreign)

    def test_q4_is_the_year_minus_three_quarters_and_flagged(self):
        frames = calendar_company(1, {(2024, 1): 1, (2024, 2): 1, (2024, 3): 1, (2025, 1): 1, (2025, 2): 1, (2025, 3): 1},
                                  annual={2024: 5.0, 2025: 7.0})
        frames += calendar_company(1, {(2024, q): 100 for q in (1, 2, 3)} | {(2025, q): 110 for q in (1, 2, 3)},
                                   annual={2024: 500, 2025: 600}, key=REVENUES)
        frames += calendar_company(1, {(year, q): 1000 for year in (2024, 2025) for q in (1, 2, 3)},
                                   annual={2024: 1000, 2025: 1010}, key=SHARES)
        q, y, _, _ = blocks(frames, today=date(2026, 3, 1))
        # Q4 2025 = 7 - 3 = 4 against Q4 2024 = 5 - 3 = 2; revenue 600 - 330 = 270 against 500 - 300 = 200
        self.assertEqual(q["end"], "2025-12-31")
        self.assertEqual(q["eps"][0], 100.0)
        self.assertEqual(q["rev"][0], 35.0)
        self.assertEqual(q["derived"], 0b1)
        self.assertEqual(y["eps"], [40.0])
        self.assertEqual(y["rev"], [20.0])

    def test_q4_eps_needs_one_share_basis_inside_the_year(self):
        frames = calendar_company(1, {(2025, 1): 1, (2025, 2): 1, (2025, 3): 0.25}, annual={2025: 3.0})
        frames += calendar_company(1, {(2025, 1): 100, (2025, 2): 100, (2025, 3): 400}, annual={2025: 250}, key=SHARES)
        frames += calendar_company(1, {(2025, q): 10 for q in (1, 2, 3)}, annual={2025: 45}, key=REVENUES)
        companies = sec.collect(frames)
        quarters = {label: sec.canonical_period(entries) for label, entries in companies[1]["q"].items()}
        years = {label: sec.canonical_period(entries) for label, entries in companies[1]["y"].items()}
        added = sec.derive_q4(quarters, years)
        self.assertEqual(added, [sec.quarter_number(2025, 4)])
        derived = quarters[added[0]]["values"]
        self.assertNotIn("epsD", derived)                  # a 4:1 split inside the year
        self.assertEqual(derived["rev"], {REVENUES: (15, "USD")})
        self.assertTrue(quarters[added[0]]["derived"])

    def test_no_q4_without_shares_negative_revenue_or_when_reported(self):
        frames = calendar_company(1, {(2025, 1): 1, (2025, 2): 1, (2025, 3): 1}, annual={2025: 4.0})
        frames += calendar_company(1, {(2025, q): 50 for q in (1, 2, 3)}, annual={2025: 120}, key=REVENUES)
        companies = sec.collect(frames)
        quarters = {label: sec.canonical_period(entries) for label, entries in companies[1]["q"].items()}
        years = {label: sec.canonical_period(entries) for label, entries in companies[1]["y"].items()}
        self.assertEqual(sec.derive_q4(quarters, years), [])   # EPS: no share counts; revenue: 120 - 150 < 0
        reported = calendar_company(1, {(2025, q): 1 for q in (1, 2, 3, 4)}, annual={2025: 4.0})
        companies = sec.collect(reported)
        quarters = {label: sec.canonical_period(entries) for label, entries in companies[1]["q"].items()}
        years = {label: sec.canonical_period(entries) for label, entries in companies[1]["y"].items()}
        self.assertEqual(sec.derive_q4(quarters, years), [])

    def test_non_calendar_fiscal_year_lands_on_the_nearest_calendar_quarter(self):
        # Apple-like year ending late September: fiscal Q4 (Jun 29 - Sep 27) is only in the 10-K
        cik = 320193
        rows = [
            ("CY2023Q4", "2023-10-01", "2023-12-30", 2.18, 1000), ("CY2024Q1", "2023-12-31", "2024-03-30", 1.53, 1000),
            ("CY2024Q2", "2024-03-31", "2024-06-29", 1.40, 1000), ("CY2024Q4", "2024-09-29", "2024-12-28", 2.40, 1000),
            ("CY2025Q1", "2024-12-29", "2025-03-29", 1.65, 1000), ("CY2025Q2", "2025-03-30", "2025-06-28", 1.57, 1000),
        ]
        frames = []
        for period, start, end, eps, shares in rows:
            frames.append(frame(DILUTED, period, [(cik, start, end, eps)]))
            frames.append(frame(SHARES, period, [(cik, start, end, shares)]))
        frames.append(frame(DILUTED, "CY2024", [(cik, "2023-10-01", "2024-09-28", 6.08)]))
        frames.append(frame(DILUTED, "CY2025", [(cik, "2024-09-29", "2025-09-27", 7.46)]))
        frames.append(frame(SHARES, "CY2024", [(cik, "2023-10-01", "2024-09-28", 1000)]))
        frames.append(frame(SHARES, "CY2025", [(cik, "2024-09-29", "2025-09-27", 1000)]))
        q, y, _, _ = blocks(frames, today=date(2025, 11, 30), cik=cik)
        # Fiscal Q4s derived into CY2024Q3 (6.08 - 5.11 = 0.97) and CY2025Q3 (7.46 - 5.62 = 1.84)
        self.assertEqual(q["end"], "2025-09-27")
        self.assertEqual(q["eps"], [89.7, 12.1, 7.8, 10.1])
        self.assertEqual(q["derived"], 0b1)
        self.assertEqual(y["eps"], [22.7])

    def test_pairs_must_be_a_year_apart(self):
        # Same calendar label but the period moved (fiscal year change): not the same period
        frames = [frame(DILUTED, "CY2025Q2", [(1, "2025-02-15", "2025-05-15", 1.0)]),
                  frame(DILUTED, "CY2026Q2", [(1, "2026-04-01", "2026-06-30", 2.0)])]
        q, _, _, _ = blocks(frames)
        self.assertIsNone(q)

    def test_stale_latest_quarter_gives_no_quarterly_block(self):
        frames = calendar_company(1, {(2024, 4): 1.0, (2025, 4): 2.0})
        self.assertEqual(blocks(frames, today=date(2026, 3, 1))[0]["eps"], [100.0])
        self.assertIsNone(blocks(frames, today=date(2026, 10, 15))[0])


class ConceptTest(unittest.TestCase):
    def test_diluted_first_then_basic_never_mixed(self):
        frames = calendar_company(1, {(2025, 2): 1.0, (2026, 2): 1.5})
        frames += calendar_company(1, {(2025, 2): 2.0, (2026, 2): 2.0}, key=BASIC)
        self.assertEqual(blocks(frames)[0]["eps"], [50.0])
        mixed = calendar_company(1, {(2026, 2): 1.5}) + calendar_company(1, {(2025, 2): 1.0}, key=BASIC)
        self.assertIsNone(blocks(mixed)[0])
        basic_only = calendar_company(1, {(2025, 2): 1.0, (2026, 2): 1.2}, key=BASIC)
        self.assertEqual(blocks(basic_only)[0]["eps"], [20.0])
        combined = calendar_company(1, {(2025, 2): 1.0, (2026, 2): 1.1}, key=BOTH)
        self.assertEqual(blocks(combined)[0]["eps"], [10.0])

    def test_revenue_concept_order_same_concept_on_both_sides(self):
        frames = calendar_company(1, {(2025, 2): 100, (2026, 2): 120}, key=REVENUES)
        frames += calendar_company(1, {(2025, 2): 90, (2026, 2): 99}, key=CONTRACT)
        self.assertEqual(blocks(frames)[0]["rev"], [20.0])       # Revenues comes first
        # Revenues only on one side: the next concept both sides have
        one_side = calendar_company(1, {(2026, 2): 120}, key=REVENUES)
        one_side += calendar_company(1, {(2025, 2): 90, (2026, 2): 99}, key=CONTRACT)
        self.assertEqual(blocks(one_side)[0]["rev"], [10.0])

    def test_a_partial_revenue_line_is_skipped(self):
        frames = calendar_company(1, {(2025, 2): 8, (2026, 2): 16}, key=REVENUES)          # a fee line re-using the tag
        frames += calendar_company(1, {(2025, 2): 900, (2026, 2): 990}, key=CONTRACT)
        self.assertEqual(blocks(frames)[0]["rev"], [10.0])

    def test_split_between_the_pair_nulls_eps_only(self):
        frames = calendar_company(1, {(2025, 2): 4.0, (2026, 2): 1.2})
        frames += calendar_company(1, {(2025, 2): 1000, (2026, 2): 4000}, key=SHARES)
        frames += calendar_company(1, {(2025, 2): 100, (2026, 2): 130}, key=REVENUES)
        q = blocks(frames)[0]
        self.assertEqual(q["eps"], [])
        self.assertEqual(q["rev"], [30.0])
        restated = calendar_company(1, {(2025, 2): 1.0, (2026, 2): 1.2})
        restated += calendar_company(1, {(2025, 2): 4000, (2026, 2): 4000}, key=SHARES)
        self.assertEqual(blocks(restated)[0]["eps"], [20.0])

    def test_scale_errors_and_turnarounds(self):
        frames = calendar_company(1, {(2025, 2): 100_000, (2026, 2): 100_000_000}, key=REVENUES)
        frames += calendar_company(1, {(2025, 2): -0.5, (2026, 2): 0.3})
        q = blocks(frames)[0]
        self.assertEqual(q["rev"], [])
        self.assertEqual(q["eps"], [])
        self.assertEqual(q["mask"], 0b1)
        loss = calendar_company(1, {(2025, 2): -1.0, (2026, 2): -0.5})
        self.assertIsNone(blocks(loss)[0])


class ForeignTest(unittest.TestCase):
    def test_ifrs_annual_in_the_reporting_currency(self):
        cik = 7000
        frames = []
        for year, eps, revenue, shares in ((2022, 10.0, 1000, 50), (2023, 12.0, 1100, 50), (2024, 15.0, 1210, 50), (2025, 12.0, 1331, 50)):
            start, end = f"{year}-01-01", f"{year}-12-31"
            frames.append(frame("ifrs-full:DilutedEarningsLossPerShare", f"CY{year}", [(cik, start, end, eps)], True, "EUR"))
            frames.append(frame("ifrs-full:Revenue", f"CY{year}", [(cik, start, end, revenue)], True, "EUR"))
            frames.append(frame("ifrs-full:AdjustedWeightedAverageShares", f"CY{year}", [(cik, start, end, shares)], True))
        q, y, foreign, currency = blocks(frames, cik=cik)
        self.assertIsNone(q)
        self.assertEqual(y["eps"], [-20.0, 25.0, 20.0])
        self.assertEqual(y["rev"], [10.0, 10.0, 10.0])
        self.assertTrue(foreign)
        self.assertEqual(currency, "EUR")

    def test_a_currency_change_breaks_the_pair(self):
        cik = 7001
        frames = [
            frame("ifrs-full:Revenue", "CY2024", [(cik, "2024-01-01", "2024-12-31", 1000)], True, "EUR"),
            frame("ifrs-full:Revenue", "CY2025", [(cik, "2025-01-01", "2025-12-31", 1200)], True, "USD"),
        ]
        self.assertIsNone(blocks(frames, cik=cik)[1])

    def test_chinese_adr_us_gaap_in_cny(self):
        cik = 7002
        frames = [
            frame(DILUTED, "CY2024", [(cik, "2024-01-01", "2024-12-31", 20.0)], True, "CNY"),
            frame(DILUTED, "CY2025", [(cik, "2025-01-01", "2025-12-31", 25.0)], True, "CNY"),
        ]
        _, y, foreign, currency = blocks(frames, cik=cik)
        self.assertEqual(y["eps"], [25.0])
        self.assertEqual((foreign, currency), (True, "CNY"))


class DocumentTest(unittest.TestCase):
    def tickers(self):
        return {"0": {"cik_str": 1, "ticker": "GOOGL"}, "1": {"cik_str": 1, "ticker": "GOOG"},
                "2": {"cik_str": 2, "ticker": "OTHER"}, "3": {"cik_str": 3, "ticker": "BRK.B"}}

    def universe(self, symbols):
        return {"fields": ["symbol"], "markets": {"US": {"rows": [[symbol] for symbol in symbols]}}}

    def test_rows_for_every_class_restricted_to_the_universe(self):
        frames = calendar_company(1, {(2025, 2): 1.0, (2026, 2): 1.3}) + calendar_company(2, {(2025, 2): 1.0, (2026, 2): 2.0})
        frames += calendar_company(3, {(2025, 2): 1000.0, (2026, 2): 1100.0})
        document = sec.build_document(frames, self.tickers(), self.universe(["GOOG", "GOOGL", "BRK.B"]), TODAY, NOW)
        self.assertEqual([row[0] for row in document["rows"]], ["BRK-B", "GOOG", "GOOGL"])
        googl = dict(zip(sec.FIELDS, document["rows"][2]))
        self.assertEqual(googl["epsQ"], [30.0])
        self.assertEqual(googl["cik"], 1)
        self.assertEqual(googl["qEnd"], "2026-06-30")
        self.assertEqual(document["fields"], sec.FIELDS)
        everything = sec.build_document(frames, self.tickers(), None, TODAY, NOW)
        self.assertEqual(len(everything["rows"]), 4)

    def test_skipped_foreign_part_keeps_the_stored_foreign_rows(self):
        frames = calendar_company(1, {(2025, 2): 1.0, (2026, 2): 1.3})
        previous = {"fields": sec.FIELDS, "foreignAt": "2026-10-10", "rows": [
            ["TSM", 9, None, "2025-12-31", [], [], [40.0], [30.0], 0, 0, 0, 1, "TWD"],
            ["GOOGL", 1, "2025-06-30", None, [1.0], [], [], [], 0, 0, 0, 0, None],
        ]}
        document = sec.build_document(frames, self.tickers(), None, TODAY, NOW, previous, foreign=False)
        self.assertEqual([row[0] for row in document["rows"]], ["GOOG", "GOOGL", "TSM"])
        self.assertEqual(document["foreignAt"], "2026-10-10")
        self.assertEqual(dict(zip(sec.FIELDS, document["rows"][1]))["epsQ"], [30.0])   # fresh, not the stored row
        self.assertTrue(sec.foreign_due({"foreignAt": "2026-10-01"}, TODAY, 7))
        self.assertFalse(sec.foreign_due({"foreignAt": "2026-10-10"}, TODAY, 7))
        self.assertTrue(sec.foreign_due(None, TODAY, 7))


class FakeSec:
    """Serves company_tickers.json and the frames fixtures of atlas/tests/fixtures/valuation; 404 otherwise."""

    def __init__(self, refuse=False):
        self.refuse, self.urls = refuse, []

    def get_json(self, url, allow_404=False):
        from seed_financials_sec import FetchError

        self.urls.append(url)
        if self.refuse:
            raise FetchError(f"HTTP 403 for {url}")
        if url.endswith("company_tickers.json"):
            name = "company_tickers.json"
        else:
            parts = url.split("/")
            name = f"frames-{parts[-3]}-{parts[-1]}" if parts[-4] == "us-gaap" and parts[-2] == "USD-per-shares" else None
        path = os.path.join(VALUATION_FIXTURES, name) if name else None
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as file:
                return json.load(file)
        if allow_404:
            return None
        raise FetchError(f"HTTP 404 for {url}")


class RunTest(unittest.TestCase):
    def args(self, tmp, **extra):
        values = {"report_dir": tmp, "out": os.path.join(tmp, "fundamentals", "us-sec.json"), "no_foreign": False,
                  "foreign_max_age_days": 0, "force": False}
        values.update(extra)
        return SimpleNamespace(**values)

    def test_end_to_end_with_the_valuation_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(tmp)
            logs = []
            fake = FakeSec()
            self.assertEqual(sec.run(args, client=fake, now=datetime(2026, 8, 20, tzinfo=timezone.utc), log=logs.append), 0)
            with open(args.out, encoding="utf-8") as file:
                document = json.load(file)
            rows = {row[0]: dict(zip(sec.FIELDS, row)) for row in document["rows"]}
            # NVDA: CY2026Q2 2.1 vs CY2025Q2 1.08, CY2026Q1 1.9 vs CY2025Q1 0.76
            self.assertEqual(rows["NVDA"]["epsQ"][:2], [94.4, 150.0])
            # Alphabet has no quarter a year apart in these fixtures: neither class gets a row
            self.assertNotIn("GOOGL", rows)
            self.assertNotIn("GOOG", rows)
            # Loss maker: no YoY and no turnaround, and the annual pair has nothing comparable
            self.assertNotIn("LOSS", rows)
            # AAPL: no share-count frames here, so its fiscal Q4 (CY2025Q3) EPS is not derived: null slot
            self.assertEqual(rows["AAPL"]["epsQ"], [0.0, 0.0, 18.3])
            self.assertEqual(rows["AAPL"]["epsY"], [22.7])
            self.assertEqual(rows["AAPL"]["yEnd"], "2025-09-27")
            self.assertTrue(any("frames" in line for line in logs))
            self.assertTrue(all("data.sec.gov" in url or "sec.gov/files" in url for url in fake.urls))
            # Unchanged second run: not rewritten
            before = os.path.getmtime(args.out)
            self.assertEqual(sec.run(args, client=FakeSec(), now=datetime(2026, 8, 20, tzinfo=timezone.utc), log=logs.append), 0)
            self.assertEqual(os.path.getmtime(args.out), before)

    def test_a_refusal_keeps_the_file_and_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(tmp)
            os.makedirs(os.path.dirname(args.out))
            with open(args.out, "w", encoding="utf-8") as file:
                file.write("keep")
            logs = []
            self.assertEqual(sec.run(args, client=FakeSec(refuse=True), now=NOW, log=logs.append), 0)
            self.assertIn("403", logs[-1])
            with open(args.out, encoding="utf-8") as file:
                self.assertEqual(file.read(), "keep")

    def test_a_missing_key_frame_or_a_bad_payload_writes_nothing(self):
        class Gap(FakeSec):
            def get_json(self, url, allow_404=False):
                if url.endswith("EarningsPerShareDiluted/USD-per-shares/CY2025Q4.json"):
                    return None                                   # 404 for a frame that must have thousands of filers
                return super().get_json(url, allow_404)

        class Garbled(FakeSec):
            def get_json(self, url, allow_404=False):
                if "frames" in url:
                    return {"tag": "Something", "data": []}
                return super().get_json(url, allow_404)

        for client, message in ((Gap(), "key diluted-EPS frames"), (Garbled(), "field 'tag'")):
            with tempfile.TemporaryDirectory() as tmp:
                args = self.args(tmp)
                logs = []
                self.assertEqual(sec.run(args, client=client, now=datetime(2026, 8, 20, tzinfo=timezone.utc), log=logs.append), 1)
                self.assertIn(message, logs[-1])
                self.assertFalse(os.path.exists(args.out))

    def test_a_large_drop_in_rows_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.args(tmp)
            os.makedirs(os.path.dirname(args.out))
            stored = {"version": 1, "fields": sec.FIELDS, "rows": [[f"S{i}", i, None, None, [], [], [], [], 0, 0, 0, 0, None]
                                                                for i in range(100)]}
            with open(args.out, "w", encoding="utf-8") as file:
                json.dump(stored, file)
            logs = []
            self.assertEqual(sec.run(args, client=FakeSec(), now=datetime(2026, 8, 20, tzinfo=timezone.utc), log=logs.append), 1)
            self.assertIn("not written", logs[-1])
            with open(args.out, encoding="utf-8") as file:
                self.assertEqual(len(json.load(file)["rows"]), 100)


if __name__ == "__main__":
    unittest.main()
