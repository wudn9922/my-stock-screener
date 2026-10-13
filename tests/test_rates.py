import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import build_rates  # noqa: E402
import rates_analytics as ra  # noqa: E402
import rates_config  # noqa: E402
import rates_synth  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "rates")
SECRET = "abcdef0123456789secretkey"


def daily_calendar(start, count, weekdays_only=True):
    out, cursor = [], start
    while len(out) < count:
        if not weekdays_only or cursor.weekday() < 5:
            out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as file:
        return json.load(file)


_MARKET = None


def market():
    global _MARKET
    if _MARKET is None:
        _MARKET = rates_synth.synthetic_market()
    return _MARKET


def build(series=None, origin=None, today=None):
    series = market() if series is None else series
    origin = rates_synth.origins_all(series) if origin is None else origin
    return build_rates.build_payloads(series, origin, rates_config.ETFS, today or rates_synth.TODAY, rates_synth.NOW)


# --------------------------------------------------------------------------- FRED parsing


class FredParsingTest(unittest.TestCase):
    def test_csv_with_date_header_and_dot_missing(self):
        text = "DATE,DGS10\n2024-01-02,3.95\n2024-01-03,.\n2024-01-04,3.99\n"
        self.assertEqual(build_rates.parse_fred_csv(text, "DGS10"), [("2024-01-02", 3.95), ("2024-01-04", 3.99)])

    def test_csv_with_observation_date_header_empty_values_and_crlf(self):
        text = "observation_date,DGS10\r\n2024-01-02,3.95\r\n2024-01-03,\r\n2024-01-05,4.01\r\n"
        self.assertEqual(build_rates.parse_fred_csv(text, "DGS10"), [("2024-01-02", 3.95), ("2024-01-05", 4.01)])

    def test_csv_rejects_html_and_wrong_series(self):
        with self.assertRaises(ValueError):
            build_rates.parse_fred_csv("<!DOCTYPE html><html>error</html>", "DGS10")
        with self.assertRaises(ValueError):
            build_rates.parse_fred_csv("DATE,DGS2\n2024-01-02,4.3\n", "DGS10")
        with self.assertRaises(ValueError):
            build_rates.parse_fred_csv("DATE,DGS10\n2024-01-02,.\n", "DGS10")  # nothing usable

    def test_csv_skips_garbage_rows_and_sorts(self):
        text = "DATE,DGS10\n2024-01-04,3.99\nnot-a-date,1\n2024-01-02,3.95\n2024-01-03,nan\n"
        self.assertEqual(build_rates.parse_fred_csv(text, "DGS10"), [("2024-01-02", 3.95), ("2024-01-04", 3.99)])

    def test_api_json(self):
        payload = {"observations": [
            {"date": "2024-01-03", "value": "."},
            {"date": "2024-01-02", "value": "3.95"},
            {"date": "2024-01-04", "value": "3.99"},
        ]}
        self.assertEqual(build_rates.parse_fred_api(payload), [("2024-01-02", 3.95), ("2024-01-04", 3.99)])
        with self.assertRaises(ValueError):
            build_rates.parse_fred_api({"error_code": 400, "error_message": "Bad Request"})


class FredFallbackTest(unittest.TestCase):
    CSV = "DATE,{id}\n2024-01-02,3.95\n2024-01-03,4.00\n"

    def run_load(self, http_get, api_key, previous=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            fetchers = build_rates.make_fetchers(http_get, api_key, sleep=lambda _s: None)
            series, origin = build_rates.load_series(["DGS10", "DGS2"], fetchers, previous or {})
        return series, origin, out.getvalue()

    def test_csv_only_without_key(self):
        calls = []

        def http_get(url, params, timeout):
            calls.append(url)
            return 200, self.CSV.format(id=params["id"])

        series, origin, _ = self.run_load(http_get, None)
        self.assertEqual(origin, {"DGS10": "csv", "DGS2": "csv"})
        self.assertEqual(series["DGS10"][-1], ("2024-01-03", 4.0))
        self.assertTrue(all("fredgraph.csv" in url for url in calls))

    def test_api_first_with_key(self):
        calls = []

        def http_get(url, params, timeout):
            calls.append(url)
            return 200, json.dumps({"observations": [{"date": "2024-01-02", "value": "3.9"}]})

        series, origin, _ = self.run_load(http_get, SECRET)
        self.assertEqual(origin, {"DGS10": "api", "DGS2": "api"})
        self.assertTrue(all("fredgraph" not in url for url in calls))

    def test_csv_fallback_and_key_never_printed(self):
        def http_get(url, params, timeout):
            if "fredgraph" in url:
                if params["id"] == "DGS10":
                    return 200, self.CSV.format(id="DGS10")
                raise ConnectionError("Max retries exceeded with url: /graph/fredgraph.csv?id=DGS2")
            # requests puts the full URL (including api_key) into exception messages
            if params["series_id"] == "DGS2":
                raise ConnectionError(f"Max retries exceeded with url: /fred/series/observations?api_key={params['api_key']}")
            return 503, "unavailable"

        previous = {"DGS2": [("2023-12-29", 4.2)]}
        series, origin, output = self.run_load(http_get, SECRET, previous)
        self.assertEqual(origin["DGS10"], "csv")
        self.assertEqual(series["DGS10"][-1], ("2024-01-03", 4.0))
        self.assertEqual(origin["DGS2"], "previous")
        self.assertEqual(series["DGS2"], [("2023-12-29", 4.2)])
        self.assertNotIn(SECRET, output)
        self.assertNotIn("api_key", output)
        self.assertIn("DGS10", output)
        self.assertIn("503", output)

    def test_breaker_skips_failing_source(self):
        calls = []

        def http_get(url, params, timeout):
            calls.append(url)
            raise TimeoutError("read timed out")

        ids = ["DGS1", "DGS2", "DGS3", "DGS5", "DGS7"]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fetchers = build_rates.make_fetchers(http_get, None, sleep=lambda _s: None)
            series, origin = build_rates.load_series(ids, fetchers, {})
        self.assertEqual(set(origin.values()), {None})
        # BREAKER_LIMIT series x FETCH_ATTEMPTS requests, then the source is skipped without a request
        self.assertEqual(len(calls), build_rates.BREAKER_LIMIT * build_rates.FETCH_ATTEMPTS)
        self.assertIn("skipping it from now on", out.getvalue())

    def test_success_resets_breaker(self):
        # two failed series, one success (resets the count), one failed series, one success
        outcomes = iter([TimeoutError] * 4 + ["ok"] + [TimeoutError] * 2 + ["ok"])

        def http_get(url, params, timeout):
            outcome = next(outcomes)
            if outcome == "ok":
                return 200, self.CSV.format(id=params["id"])
            raise outcome("x")

        ids = ["DGS1", "DGS2", "DGS3", "DGS5", "DGS7"]
        with contextlib.redirect_stdout(io.StringIO()):
            fetchers = build_rates.make_fetchers(http_get, None, sleep=lambda _s: None)
            _, origin = build_rates.load_series(ids, fetchers, {})
        self.assertEqual([origin[i] for i in ids], [None, None, "csv", None, "csv"])

    def test_time_budget_stops_requests(self):
        now = [0.0]
        calls = []

        def http_get(url, params, timeout):
            calls.append(params["id"])
            now[0] += 100
            return 200, self.CSV.format(id=params["id"])

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fetchers = build_rates.make_fetchers(
                http_get, None, sleep=lambda _s: None, clock=lambda: now[0], budget=150
            )
            _, origin = build_rates.load_series(["DGS1", "DGS2", "DGS3"], fetchers, {"DGS3": [("2024-01-02", 1.0)]})
        self.assertEqual(origin, {"DGS1": "csv", "DGS2": "csv", "DGS3": "previous"})
        self.assertEqual(calls, ["DGS1", "DGS2"])

    def test_http_error_body_with_key_not_printed(self):
        def http_get(url, params, timeout):
            if "fredgraph" in url:
                return 404, "nope"
            return 400, json.dumps({"error_message": f"bad api_key={params['api_key']}"})

        series, origin, output = self.run_load(http_get, SECRET)
        self.assertEqual(origin, {"DGS10": None, "DGS2": None})
        self.assertNotIn("DGS10", series)
        self.assertNotIn(SECRET, output)

    def test_no_api_key_skips_api(self):
        calls = []

        def http_get(url, params, timeout):
            calls.append(url)
            return 500, ""

        series, origin, _ = self.run_load(http_get, None, {"DGS10": [("2024-01-02", 4.0)]})
        self.assertEqual(origin, {"DGS10": "previous", "DGS2": None})
        self.assertTrue(all("fredgraph" in url for url in calls))

    def test_series_from_previous_history(self):
        history = {
            "weekly": {"dates": ["2024-01-05", "2024-01-12"], "series": {"DGS10": [4.0, None]}},
            "daily": {"dates": ["2024-01-11", "2024-01-12"], "series": {"DGS10": [4.1, 4.2]}},
        }
        self.assertEqual(
            build_rates.series_from_history(history)["DGS10"],
            [("2024-01-05", 4.0), ("2024-01-11", 4.1), ("2024-01-12", 4.2)],
        )
        self.assertEqual(build_rates.series_from_history(None), {})


# --------------------------------------------------------------------------- sampling and snapshots


class SamplingTest(unittest.TestCase):
    def test_weekly_sample_takes_last_observation_of_each_iso_week(self):
        dates = ["2025-12-29", "2025-12-30", "2026-01-02",  # ISO week 2026-W01 spans the new year
                 "2026-01-05", "2026-01-08",                # Friday 01-09 missing (holiday) -> Thursday
                 "2026-01-12"]                              # partial last week is kept
        self.assertEqual(ra.weekly_sample(dates), ["2026-01-02", "2026-01-08", "2026-01-12"])

    def test_shift_months_clamps_month_end(self):
        self.assertEqual(ra.shift_months(date(2024, 3, 31), -1), date(2024, 2, 29))
        self.assertEqual(ra.shift_years(date(2024, 2, 29), -1), date(2023, 2, 28))
        self.assertEqual(ra.shift_months(date(2026, 1, 15), -3), date(2025, 10, 15))

    def test_snapshot_steps_back_to_holiday_and_tenor_lookback(self):
        dgs10 = ra.Series([("2026-01-05", 4.0), ("2026-01-06", 4.1), ("2026-01-07", 4.2), ("2026-01-08", 4.3)])
        short = ra.Series([("2026-01-02", 3.0), ("2026-01-06", 3.6)])
        stale = ra.Series([("2025-12-31", 1.0)])
        # target Sunday 01-11 -> last DGS10 day on or before is Thursday 01-08
        snap = ra.snapshot_curve({"DGS10": dgs10, "DGS1MO": short, "DGS3MO": stale}, ["DGS1MO", "DGS3MO", "DGS10"],
                                 dgs10, date(2026, 1, 11))
        self.assertEqual(snap["date"], "2026-01-08")
        # DGS1MO missing 01-07 and 01-08: two days back is allowed; DGS3MO is 8 days old -> null
        self.assertEqual(snap["values"], [3.6, None, 4.3])
        self.assertIsNone(ra.snapshot_curve({"DGS10": dgs10}, ["DGS10"], dgs10, date(2026, 1, 1)))

    def test_snapshot_targets(self):
        targets = dict(ra.snapshot_targets(date(2026, 10, 9)))
        self.assertEqual(list(targets), ["now", "1m"])
        self.assertEqual(targets["now"], date(2026, 10, 9))
        self.assertEqual(targets["1m"], date(2026, 9, 9))


# --------------------------------------------------------------------------- spreads


class SpreadTest(unittest.TestCase):
    def setUp(self):
        self.days = daily_calendar(date(2026, 1, 5), 9)
        values = [50, -10, -20, 5, -5, -30, -1]
        self.points = list(zip(self.days, values))

    def test_spread_points_use_common_dates_in_bp(self):
        long = ra.Series([("2026-01-05", 4.12), ("2026-01-06", 4.10), ("2026-01-07", 4.00)])
        short = ra.Series([("2026-01-05", 3.95), ("2026-01-07", 4.25)])
        points = ra.spread_points(long, short)
        self.assertEqual([d for d, _ in points], ["2026-01-05", "2026-01-07"])
        self.assertAlmostEqual(points[0][1], 17.0, places=6)
        self.assertAlmostEqual(points[1][1], -25.0, places=6)

    def test_runs_strict(self):
        runs = ra.inversion_runs(self.points, merge_gap=0)
        self.assertEqual([(r["start"], r["end"], r["minBp"], r["minDate"]) for r in runs], [
            (self.days[1], self.days[2], -20, self.days[2]),
            (self.days[4], self.days[6], -30, self.days[5]),
        ])

    def test_runs_merge_short_gaps(self):
        runs = ra.inversion_runs(self.points, merge_gap=1)
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0]["start"], runs[0]["end"], runs[0]["minBp"]), (self.days[1], self.days[6], -30))

    def test_stats_currently_inverted(self):
        stats = ra.spread_stats(self.points, merge_gap=0)
        self.assertTrue(stats["inverted"])
        self.assertEqual(stats["valueBp"], -1)
        self.assertEqual(stats["invertedDays"], 3)
        self.assertEqual(stats["lastInversion"], {"start": self.days[4], "end": None, "minBp": -30, "minDate": self.days[5]})

    def test_stats_after_inversion_and_percentile(self):
        points = self.points + [(self.days[7], 12), (self.days[8], 20)]
        stats = ra.spread_stats(points, merge_gap=0)
        self.assertFalse(stats["inverted"])
        self.assertEqual(stats["invertedDays"], 0)
        self.assertEqual(stats["lastInversion"]["end"], self.days[6])
        # 20 is the 2nd highest of 9 values: below=7, equal=1 -> (7 + 0.5) / 9 = 83.3%
        self.assertEqual(stats["pct5y"], 83)
        self.assertEqual(ra.spread_stats(points[:1], merge_gap=0)["lastInversion"], None)

    def test_default_merge_keeps_one_episode_and_counts_current_days(self):
        days = daily_calendar(date(2026, 1, 5), 30)
        values = [10] * 5 + [-20] * 10 + [3] * 4 + [-15] * 11  # a 4-day blip inside one inversion
        stats = ra.spread_stats(list(zip(days, values)))
        self.assertTrue(stats["inverted"])
        self.assertEqual(stats["invertedDays"], 25)
        self.assertEqual(stats["lastInversion"], {"start": days[5], "end": None, "minBp": -20, "minDate": days[5]})

    def test_chg1m_uses_value_a_month_earlier(self):
        days = daily_calendar(date(2026, 1, 1), 40)
        points = [(d, float(i)) for i, d in enumerate(days)]
        stats = ra.spread_stats(points)
        target = ra.shift_months(date.fromisoformat(days[-1]), -1).isoformat()
        prior = [v for d, v in points if d <= target][-1]
        self.assertEqual(stats["chg1mBp"], round(points[-1][1] - prior))


# --------------------------------------------------------------------------- model duration / convexity


class BondMathTest(unittest.TestCase):
    def test_zero_coupon_closed_form(self):
        y = 0.05
        for maturity in (20, 25, 30):
            price, duration, convexity = ra.bond_measures([(maturity, 100.0)], y)
            self.assertAlmostEqual(price, 100 / (1 + y / 2) ** (2 * maturity), places=9)
            self.assertAlmostEqual(duration, maturity / (1 + y / 2), places=9)
            self.assertAlmostEqual(convexity, maturity * (maturity + 0.5) / (1 + y / 2) ** 2, places=9)

    def test_single_zero_ladder_and_interpolation(self):
        model = ra.ladder_measures(30, 30, 4.8, 5.0, coupon=False)
        self.assertAlmostEqual(model[0], 30 / 1.025, places=9)
        self.assertAlmostEqual(ra.interp_yield(25, 4.5, 4.7), 4.6, places=12)
        self.assertAlmostEqual(ra.interp_yield(20, 4.5, 4.7), 4.5, places=12)

    def test_zero_ladder_is_price_weighted_average(self):
        y20, y30 = 4.5, 4.9
        maturities = [20 + 0.5 * k for k in range(21)]
        prices, ds, cs = [], [], []
        for t in maturities:
            y = (y20 + (y30 - y20) * (t - 20) / 10) / 100
            prices.append((1 + y / 2) ** (-2 * t))
            ds.append(t / (1 + y / 2))
            cs.append(t * (t + 0.5) / (1 + y / 2) ** 2)
        total = sum(prices)
        expected_d = sum(p * d for p, d in zip(prices, ds)) / total
        expected_c = sum(p * c for p, c in zip(prices, cs)) / total
        duration, convexity = ra.ladder_measures(20, 30, y20, y30, coupon=False)
        self.assertAlmostEqual(duration, expected_d, places=9)
        self.assertAlmostEqual(convexity, expected_c, places=9)

    def test_par_bond_matches_finite_differences(self):
        y = 0.047
        cashflows = ra.par_cashflows(25, y)
        price, duration, convexity = ra.bond_measures(cashflows, y)
        self.assertAlmostEqual(price, 100.0, places=9)
        h = 1e-6  # central difference error ~ h^2 * D^3 / 6
        up = ra.bond_measures(cashflows, y + h)[0]
        down = ra.bond_measures(cashflows, y - h)[0]
        self.assertAlmostEqual(-(up - down) / (2 * h) / price, duration, places=5)
        h = 1e-4
        up = ra.bond_measures(cashflows, y + h)[0]
        down = ra.bond_measures(cashflows, y - h)[0]
        self.assertAlmostEqual((up - 2 * price + down) / h ** 2 / price, convexity, delta=convexity * 1e-4)

    def test_ladder_reprices_parallel_shift(self):
        """Second-order approximation vs exact repricing of the whole ladder, ±100bp: error < 0.5pp."""
        y20, y30 = 4.6, 4.8
        for low, high, coupon in ((20, 30, True), (20, 30, False), (25, 30, False)):
            duration, convexity = ra.ladder_measures(low, high, y20, y30, coupon=coupon)
            base = ra.ladder_value(low, high, y20, y30, coupon=coupon)
            for shift in (-1.0, 1.0):
                exact = ra.ladder_value(low, high, y20, y30, coupon=coupon, shift=shift) / base - 1
                dy = shift / 100
                approx = -duration * dy + 0.5 * convexity * dy ** 2
                self.assertLess(abs(exact - approx) * 100, 0.5, (low, high, coupon, shift))

    def test_convexity_unit_matches_published_times_100(self):
        """A 20-30y par ladder has published-style convexity ~3-4, i.e. 300-400 years² here."""
        model = ra.model_duration_convexity("par-20-30", 4.6, 4.8)
        self.assertTrue(12 < model["duration"] < 17, model)
        self.assertTrue(250 < model["convexity"] < 450, model)
        zero = ra.model_duration_convexity("zero-25-30", 4.6, 4.8)
        self.assertTrue(25 < zero["duration"] < 29, zero)
        self.assertTrue(650 < zero["convexity"] < 850, zero)
        self.assertIsNone(ra.model_duration_convexity("zero-25-30", None, 4.8))


# --------------------------------------------------------------------------- config


class ConfigTest(unittest.TestCase):
    def test_static_table_is_valid(self):
        self.assertEqual(rates_config.validate_config(rates_config.ETFS, ra.MODEL_PROXIES), [])
        self.assertEqual([e["symbol"] for e in rates_config.ETFS], ["TLT", "EDV", "GOVZ", "ZROZ", "TMF"])
        self.assertEqual({e["symbol"]: e["leverage"] for e in rates_config.ETFS}["TMF"], 3)

    def test_validator_catches_problems(self):
        bad = dict(rates_config.ETFS[0], expenseRatio=2.5, effectiveDuration=70, staticAsOf="2026-13-01",
                   staticSource="http://example.com", verified=True, modelProxy="nope")
        problems = rates_config.validate_config([bad, dict(rates_config.ETFS[0])], ra.MODEL_PROXIES)
        text = " ".join(problems)
        for word in ("expenseRatio", "effectiveDuration", "staticAsOf", "https", "verified", "modelProxy", "duplicate"):
            self.assertIn(word, text)


# --------------------------------------------------------------------------- payloads


RATES_KEYS = {"version", "generatedAt", "asOf", "sources", "missing", "tenors", "tenorYears", "curves", "policy",
              "spreads", "etfs", "notes"}
ETF_KEYS = {"symbol", "name", "issuer", "index", "inception", "leverage", "expenseRatio", "expenseNote",
            "effectiveDuration", "maturityRange", "holdings", "staticAsOf", "staticSource", "verified", "model"}
SPREAD_KEYS = {"key", "long", "short", "valueBp", "chg1mBp", "inverted", "invertedDays", "lastInversion", "pct5y"}
CORE = ["DGS3MO", "DGS10", "DGS20", "DGS30"]


class PayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rates, cls.history = build()

    def test_contract_keys(self):
        rates, history = self.rates, self.history
        self.assertEqual(set(rates), RATES_KEYS)
        self.assertEqual(rates["version"], 1)
        self.assertEqual(rates["tenors"], ["3M", "10Y", "20Y", "30Y"])
        self.assertEqual(rates["tenorYears"], [0.25, 10, 20, 30])
        self.assertEqual(rates["sources"], {"rates": "FRED fredgraph.csv"})
        self.assertEqual(rates["missing"], [])
        self.assertEqual(set(rates["curves"]), {"nominal"})
        self.assertEqual([c["key"] for c in rates["curves"]["nominal"]], ["now", "1m"])
        for curve in rates["curves"]["nominal"]:
            self.assertEqual(set(curve), {"key", "date", "values"})
            self.assertEqual(len(curve["values"]), len(rates["tenors"]))
        self.assertEqual(set(rates["policy"]), {"lower", "upper", "date"})
        self.assertEqual([s["key"] for s in rates["spreads"]], ["10y3m"])
        for spread in rates["spreads"]:
            self.assertEqual(set(spread), SPREAD_KEYS)
            self.assertIsInstance(spread["valueBp"], int)
        self.assertEqual([e["symbol"] for e in rates["etfs"]], ["TLT", "EDV", "GOVZ", "ZROZ", "TMF"])
        for etf in rates["etfs"]:
            self.assertEqual(set(etf), ETF_KEYS, etf["symbol"])
            self.assertFalse(etf["verified"])
            self.assertEqual(set(etf["model"]), {"duration", "convexity", "proxy"})
        self.assertEqual(set(rates["notes"]), {"rates", "spreads", "model", "static", "tmf", "disclaimer"})
        self.assertEqual(set(history), {"version", "generatedAt", "asOf", "weekly", "daily"})
        for block in ("weekly", "daily"):
            self.assertEqual(set(history[block]), {"dates", "series"})
            self.assertEqual(set(history[block]["series"]), {"DGS10", "DGS3MO", "DFEDTARU", "DFEDTARL"})
            for values in history[block]["series"].values():
                self.assertEqual(len(values), len(history[block]["dates"]))

    def test_only_what_the_page_uses_is_fetched(self):
        self.assertEqual(build_rates.FRED_IDS, CORE + ["DFEDTARU", "DFEDTARL"])
        self.assertEqual(build_rates.HISTORY_IDS, ["DGS3MO", "DGS10", "DFEDTARU", "DFEDTARL"])
        self.assertEqual(set(market()), set(build_rates.FRED_IDS))

    def test_values_and_units(self):
        rates, history = self.rates, self.history
        series = market()
        last10 = series["DGS10"][-1]
        self.assertEqual(rates["asOf"], last10[0])
        now = rates["curves"]["nominal"][0]
        self.assertEqual(now["date"], last10[0])
        self.assertEqual(now["values"][rates["tenors"].index("10Y")], last10[1])
        month_ago = rates["curves"]["nominal"][1]
        target = ra.shift_months(date.fromisoformat(last10[0]), -1).isoformat()
        self.assertEqual(month_ago["date"], [d for d, _ in series["DGS10"] if d <= target][-1])
        spread = rates["spreads"][0]
        self.assertEqual((spread["long"], spread["short"]), ("DGS10", "DGS3MO"))
        self.assertEqual(spread["valueBp"], round((last10[1] - series["DGS3MO"][-1][1]) * 100))
        self.assertEqual(spread["inverted"], spread["valueBp"] < 0)
        self.assertEqual(rates["policy"], {"lower": 4.0, "upper": 4.25, "date": series["DFEDTARU"][-1][0]})
        tlt, tmf = rates["etfs"][0], rates["etfs"][4]
        self.assertEqual(tmf["leverage"], 3)
        self.assertGreater(tlt["model"]["convexity"], 100)  # years², not the published /100 figure
        self.assertEqual(history["weekly"]["dates"], ra.weekly_sample([d for d, _ in series["DGS10"]]))
        self.assertEqual(history["weekly"]["dates"][0][:4], "2020")  # synthetic data starts in 2020
        self.assertEqual(len(history["daily"]["dates"]), build_rates.DAILY_POINTS)
        self.assertEqual(history["daily"]["series"]["DGS10"][-1], last10[1])
        self.assertEqual(history["daily"]["series"]["DFEDTARU"][-1], 4.25)

    def test_model_block_uses_todays_long_yields(self):
        series = market()
        y20, y30 = series["DGS20"][-1][1], series["DGS30"][-1][1]
        for etf, config in zip(self.rates["etfs"], rates_config.ETFS):
            self.assertEqual(etf["model"], ra.model_duration_convexity(config["modelProxy"], y20, y30))
        self.assertEqual(self.rates["etfs"][0]["model"], self.rates["etfs"][4]["model"])  # TMF: unlevered index

    def test_guard_each_core_series_must_be_fresh(self):
        for core in CORE:
            origin = rates_synth.origins_all(market())
            origin[core] = "previous"
            with self.assertRaises(build_rates.InsufficientData, msg=core):
                build(origin=origin)
            trimmed = {k: v for k, v in market().items() if k != core}
            with self.assertRaises(build_rates.InsufficientData, msg=core):
                build(series=trimmed, origin=rates_synth.origins_all(trimmed))

    def test_policy_series_may_come_from_the_previous_file(self):
        origin = rates_synth.origins_all(market())
        origin["DFEDTARU"] = "previous"
        rates, _ = build(origin=origin)
        self.assertEqual(rates["missing"], ["DFEDTARU"])
        self.assertEqual(rates["sources"]["rates"], "FRED fredgraph.csv")
        origin["DFEDTARL"] = None
        rates, _ = build(origin=origin)
        self.assertEqual(rates["missing"], ["DFEDTARU", "DFEDTARL"])

    def test_guard_stale(self):
        with self.assertRaises(build_rates.InsufficientData):
            build(today=rates_synth.TODAY + timedelta(days=11))
        build(today=rates_synth.TODAY + timedelta(days=10))

    def test_mixed_sources(self):
        origin = rates_synth.origins_all(market())
        origin["DFEDTARL"] = "api"
        rates, _ = build(origin=origin)
        self.assertEqual(rates["sources"]["rates"], "mixed")

    def test_payload_is_strict_json(self):
        for payload in (self.rates, self.history):
            text = json.dumps(payload, allow_nan=False)
            self.assertNotIn("-0.0,", text)
            self.assertNotIn("-0.0]", text)


class FixtureTest(unittest.TestCase):
    def assertClose(self, a, b, path="$"):
        if isinstance(a, float) or isinstance(b, float):
            self.assertIsInstance(b, (int, float), path)
            self.assertLessEqual(abs(a - b), 0.02 + 1e-6 * abs(a), path)
        elif isinstance(a, dict):
            self.assertEqual(set(a), set(b), path)
            for key in a:
                self.assertClose(a[key], b[key], f"{path}.{key}")
        elif isinstance(a, list):
            self.assertEqual(len(a), len(b), path)
            for index, (x, y) in enumerate(zip(a, b)):
                self.assertClose(x, y, f"{path}[{index}]")
        else:
            self.assertEqual(a, b, path)

    def test_committed_fixtures_match_generator(self):
        rates, history = rates_synth.build_fixture_payloads()
        committed_rates = load_fixture("sample-rates.json")
        committed_history = load_fixture("sample-rates-history.json")
        self.assertIn("synthetic", committed_rates["_fixture"])
        self.assertIn("synthetic", committed_history["_fixture"])
        for payload in (rates, committed_rates):
            payload.pop("generatedAt")
        for payload in (history, committed_history):
            payload.pop("generatedAt")
        self.assertClose(rates, committed_rates)
        self.assertClose(history, committed_history)


# --------------------------------------------------------------------------- I/O and run()


class RunTest(unittest.TestCase):
    def fred_http(self, broken=(), seen=None):
        series = market()

        def http_get(url, params, timeout):
            if seen is not None:
                seen.append((url, params.get("id") or params.get("series_id")))
            if "fredgraph" not in url:
                return 500, f"error for api_key={params.get('api_key')}"
            key = params["id"]
            if key in broken or key not in series:
                return 404, ""
            rows = ["observation_date," + key] + [f"{d},{v}" for d, v in series[key]]
            return 200, "\n".join(rows) + "\n"

        return http_get

    def run_build(self, out_dir, http_get):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = build_rates.run(
                out_dir, http_get=http_get, api_key=SECRET,
                today=rates_synth.TODAY, now=rates_synth.NOW, sleep=lambda _s: None,
            )
        return code, out.getvalue()

    def test_writes_both_files_and_keeps_old_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, output = self.run_build(tmp, self.fred_http())
            self.assertEqual(code, 0, output)
            self.assertNotIn(SECRET, output)
            names = sorted(os.listdir(tmp))
            self.assertEqual(names, ["rates-history.json", "rates.json"])
            with open(os.path.join(tmp, "rates.json"), encoding="utf-8") as file:
                first = file.read()
            # DGS10 broken: nothing is written, previous files stay byte-identical
            code, output = self.run_build(tmp, self.fred_http(broken={"DGS10"}))
            self.assertEqual(code, 1)
            self.assertNotIn(SECRET, output)
            with open(os.path.join(tmp, "rates.json"), encoding="utf-8") as file:
                self.assertEqual(file.read(), first)
            self.assertEqual(sorted(os.listdir(tmp)), names)  # no stray .tmp files

    def test_requests_only_the_needed_fred_series(self):
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_build(tmp, self.fred_http(seen=seen))[0], 0)
        self.assertEqual({series_id for _, series_id in seen}, set(build_rates.FRED_IDS))
        self.assertTrue(all("stlouisfed.org" in url for url, _ in seen))

    def test_policy_series_falls_back_to_previous_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_build(tmp, self.fred_http())[0], 0)
            code, output = self.run_build(tmp, self.fred_http(broken={"DFEDTARL"}))
            self.assertEqual(code, 0, output)
            with open(os.path.join(tmp, "rates.json"), encoding="utf-8") as file:
                rates = json.load(file)
            self.assertEqual(rates["missing"], ["DFEDTARL"])
            self.assertEqual(rates["policy"]["lower"], 4.0)

    def test_long_yield_failure_keeps_old_files(self):
        # DGS20 / DGS30 are not in the history file, so there is no previous value to fall back on
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_build(tmp, self.fred_http())[0], 0)
            for broken in ("DGS20", "DGS30"):
                code, output = self.run_build(tmp, self.fred_http(broken={broken}))
                self.assertEqual(code, 1, output)
                self.assertIn(broken, output)

    def test_no_yahoo_dependency(self):
        for name in ("build_rates.py", "rates_analytics.py", "rates_config.py"):
            with open(os.path.join(ROOT, "scripts", name), encoding="utf-8") as file:
                source = file.read()
            self.assertNotIn("yfinance", source, name)
            self.assertNotIn("pandas", source, name)
        for gone in ("download_etf_prices", "drop_open_session", "extract_etf_points"):
            self.assertFalse(hasattr(build_rates, gone), gone)


class FixtureCopyTest(unittest.TestCase):
    """The front-end tests read a copy of the generated fixtures; nothing else keeps the two in step."""

    def test_atlas_copies_match_the_generated_fixtures(self):
        pairs = [
            ("sample-rates.json", "backend-sample-rates.json"),
            ("sample-rates-history.json", "backend-sample-rates-history.json"),
        ]
        for source, copy in pairs:
            with open(os.path.join(ROOT, "tests", "fixtures", "rates", source), "rb") as handle:
                expected = handle.read()
            with open(os.path.join(ROOT, "atlas", "tests", "fixtures", "rates", copy), "rb") as handle:
                self.assertEqual(handle.read(), expected, f"{copy} is out of date: copy tests/fixtures/rates/{source}")

if __name__ == "__main__":
    unittest.main()
