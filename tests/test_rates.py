import contextlib
import io
import json
import math
import os
import random
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

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


def build(series=None, origin=None, prices=None, previous_rates=None, previous_history=None, today=None):
    base_series, base_prices = market()
    series = base_series if series is None else series
    prices = base_prices if prices is None else prices
    origin = rates_synth.origins_all(series) if origin is None else origin
    return build_rates.build_payloads(
        series, origin, prices, rates_config.ETFS, previous_rates, previous_history,
        today or rates_synth.TODAY, rates_synth.NOW,
    )


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

    def test_csv_first(self):
        calls = []

        def http_get(url, params, timeout):
            calls.append(url)
            return 200, self.CSV.format(id=params["id"])

        series, origin, _ = self.run_load(http_get, SECRET)
        self.assertEqual(origin, {"DGS10": "csv", "DGS2": "csv"})
        self.assertEqual(series["DGS10"][-1], ("2024-01-03", 4.0))
        self.assertTrue(all("fredgraph.csv" in url for url in calls))

    def test_api_fallback_and_key_never_printed(self):
        def http_get(url, params, timeout):
            if "fredgraph" in url:
                if params["id"] == "DGS10":
                    return 503, "unavailable"
                raise ConnectionError("Max retries exceeded with url: /graph/fredgraph.csv?id=DGS2")
            # requests puts the full URL (including api_key) into exception messages
            if params["series_id"] == "DGS2":
                raise ConnectionError(f"Max retries exceeded with url: /fred/series/observations?api_key={params['api_key']}")
            return 200, json.dumps({"observations": [{"date": "2024-01-02", "value": "3.9"}]})

        previous = {"DGS2": [("2023-12-29", 4.2)]}
        series, origin, output = self.run_load(http_get, SECRET, previous)
        self.assertEqual(origin["DGS10"], "api")
        self.assertEqual(series["DGS10"], [("2024-01-02", 3.9)])
        self.assertEqual(origin["DGS2"], "previous")
        self.assertEqual(series["DGS2"], [("2023-12-29", 4.2)])
        self.assertNotIn(SECRET, output)
        self.assertNotIn("api_key", output)
        self.assertIn("DGS10", output)
        self.assertIn("503", output)

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
        self.assertEqual(list(targets), ["now", "1w", "1m", "3m", "1y", "2y"])
        self.assertEqual(targets["1w"], date(2026, 10, 2))
        self.assertEqual(targets["1m"], date(2026, 9, 9))
        self.assertEqual(targets["2y"], date(2024, 10, 9))


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


# --------------------------------------------------------------------------- returns and risk


def geometric_series(start, days, annual):
    first = date.fromisoformat(start)
    return ra.Series([((first + timedelta(days=i)).isoformat(), 100 * (1 + annual) ** (i / 365.25)) for i in range(days)])


class ReturnRiskTest(unittest.TestCase):
    def test_cagr_on_geometric_series(self):
        series = geometric_series("2015-01-01", 4000, 0.10)
        returns = ra.period_returns(series)
        self.assertAlmostEqual(returns["3y"]["annualized"], 10.0, places=6)
        self.assertAlmostEqual(returns["5y"]["annualized"], 10.0, places=6)
        self.assertAlmostEqual(returns["inception"]["annualized"], 10.0, places=6)
        end = date.fromisoformat(series.dates[-1])
        start = ra.shift_years(end, -3)
        self.assertAlmostEqual(returns["3y"]["total"], (1.1 ** ((end - start).days / 365.25) - 1) * 100, places=6)
        self.assertNotIn("annualized", returns["1y"])
        self.assertAlmostEqual(returns["10y"]["annualized"], 10.0, places=6)  # 4000 days ~ 10.95 years
        self.assertIsNone(ra.period_returns(geometric_series("2015-01-01", 3000, 0.10))["10y"])
        self.assertEqual(returns["inception"]["start"], "2015-01-01")

    def test_window_start_grace(self):
        series = geometric_series("2023-10-15", 365 * 3, 0.05)  # 3y window starts a few days before data
        end = date.fromisoformat(series.dates[-1])
        self.assertLessEqual((date(2023, 10, 15) - ra.shift_years(end, -3)).days, ra.WINDOW_GRACE_DAYS)
        self.assertIsNotNone(ra.total_return(series, ra.shift_years(end, -3)))
        late = geometric_series("2023-11-15", 365 * 3 - 31, 0.05)
        self.assertIsNone(ra.total_return(late, ra.shift_years(end, -3)))

    def test_short_history_inception_not_annualized(self):
        returns = ra.period_returns(geometric_series("2026-01-01", 200, 0.10))
        self.assertIsNone(returns["inception"]["annualized"])
        self.assertIsNone(returns["1y"])

    def test_vol_of_alternating_log_returns(self):
        days = daily_calendar(date(2025, 1, 1), 301)  # 300 returns: equal +/- count, mean exactly 0
        a = 0.01
        logs = [a if i % 2 == 0 else -a for i in range(len(days) - 1)]
        values = [100.0]
        for step in logs:
            values.append(values[-1] * math.exp(step))
        series = ra.Series(list(zip(days, values)))
        start = date.fromisoformat(days[0])
        n = len(logs)
        expected = math.sqrt(n / (n - 1)) * a * math.sqrt(252) * 100
        self.assertAlmostEqual(ra.ann_vol(series, start), expected, places=9)

    def test_max_drawdown_peak_trough_recovery(self):
        days = daily_calendar(date(2026, 1, 5), 8)
        series = ra.Series(list(zip(days, [100, 120, 90, 60, 80, 119, 130, 110])))
        mdd = ra.max_drawdown(series)
        self.assertEqual(mdd, {"value": -50.0, "peak": days[1], "trough": days[3], "recovered": days[6]})
        self.assertAlmostEqual(ra.current_drawdown(series), (110 / 130 - 1) * 100, places=9)
        unrecovered = ra.Series(list(zip(days[:5], [100, 120, 90, 60, 80])))
        self.assertIsNone(ra.max_drawdown(unrecovered)["recovered"])
        rising = ra.Series(list(zip(days[:3], [1, 2, 3])))
        self.assertEqual(ra.max_drawdown(rising), {"value": 0.0, "peak": None, "trough": None, "recovered": None})

    def test_bad_point_filter(self):
        days = daily_calendar(date(2026, 1, 5), 12)
        values = [100, 101, 250, 102, 103, 104, 30, 31, 32, 33, 34, 35]
        kept, dropped = ra.filter_bad_points(list(zip(days, values)), 0.25)
        self.assertEqual(dropped, [days[2]])  # isolated spike removed
        # a persistent level change (3+ consistent points) is kept, not erased
        self.assertEqual([d for d, _ in kept], days[:2] + days[3:])
        kept, dropped = ra.filter_bad_points(list(zip(days[:5], [100, 101, 102, 103, 300])), 0.25)
        self.assertEqual(dropped, [days[4]])
        kept, dropped = ra.filter_bad_points(list(zip(days[:4], [100, 150, 101, 102])), 0.6)
        self.assertEqual(dropped, [])


# --------------------------------------------------------------------------- empirical duration


def regression_market(duration, seed, days=900):
    """True yields every weekday; FRED drops bond holidays, the ETF drops stock holidays."""
    rng = random.Random(seed)
    calendar = daily_calendar(date(2023, 1, 2), days)
    yields, price = [], []
    y, p = 4.0, 100.0
    for index, day in enumerate(calendar):
        if index:
            dy = rng.gauss(0, 0.06)
            y += dy
            p *= 1 - duration * dy / 100 + rng.gauss(0, 0.0005)
        yields.append((day, round(y, 4)))
        price.append((day, p))
    bond_holidays = set(calendar[7::23])   # bond market closed, ETF trades
    stock_holidays = set(calendar[13::41])  # ETF closed, FRED has a value
    fred = [(d, v) for d, v in yields if d not in bond_holidays]
    etf = [(d, v) for d, v in price if d not in stock_holidays]
    return fred, etf


class EmpiricalDurationTest(unittest.TestCase):
    def test_daily_beta_recovers_duration_with_holiday_gaps(self):
        for duration, seed in ((16.5, 1), (26.0, 2), (49.5, 3)):
            fred, etf = regression_market(duration, seed)
            end = date.fromisoformat(fred[-1][0])
            result = ra.regress_beta(etf, fred, ra.shift_years(end, -1), "daily")
            self.assertLess(abs(result["durEmp"] - duration) / duration, 0.02, (duration, result))
            self.assertAlmostEqual(result["durEmp"], -100 * result["beta"], places=1)
            self.assertGreater(result["r2"], 0.9)
            self.assertGreater(result["n"], 230)

    def test_weekly_beta_recovers_duration(self):
        fred, etf = regression_market(24.0, 4)
        end = date.fromisoformat(fred[-1][0])
        result = ra.regress_beta(etf, fred, ra.shift_years(end, -3), "weekly")
        self.assertLess(abs(result["durEmp"] - 24.0) / 24.0, 0.02, result)
        self.assertGreater(result["n"], 150)

    def test_positional_alignment_would_fail(self):
        """Guards the test itself: zipping returns by position (ignoring holidays) gives a wrong answer."""
        fred, etf = regression_market(16.5, 1)
        dy = [b[1] - a[1] for a, b in zip(fred, fred[1:])]
        r = [b[1] / a[1] - 1 for a, b in zip(etf, etf[1:])]
        n = min(len(dy), len(r))
        beta = ra.ols(dy[:n][-250:], r[:n][-250:])[0]
        self.assertGreater(abs(-100 * beta - 16.5) / 16.5, 0.02)

    def test_aligned_changes_span_holidays(self):
        fred = [("2026-10-09", 4.0), ("2026-10-13", 4.1)]               # 10-12 bond holiday
        etf = [("2026-10-09", 100.0), ("2026-10-12", 99.0), ("2026-10-13", 98.0)]
        dy, r = ra.aligned_changes(etf, fred, date(2026, 1, 1), "daily")
        self.assertEqual(len(dy), 1)
        self.assertAlmostEqual(dy[0], 0.1)
        self.assertAlmostEqual(r[0], -0.02)

    def test_too_few_points(self):
        fred, etf = regression_market(16.5, 1, days=30)
        self.assertIsNone(ra.regress_beta(etf, fred, date(2023, 1, 1), "daily"))


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


RATES_KEYS = {"version", "generatedAt", "asOf", "sources", "missing", "tenors", "tenorYears", "realTenors",
              "curves", "policy", "inflation", "spreads", "etfs", "notes"}
ETF_KEYS = {"symbol", "name", "issuer", "index", "inception", "leverage", "expenseRatio", "expenseNote",
            "effectiveDuration", "maturityRange", "holdings", "staticAsOf", "staticSource", "verified",
            "model", "price", "returns", "risk", "sensitivity"}
SPREAD_KEYS = {"key", "long", "short", "valueBp", "chg1mBp", "inverted", "invertedDays", "lastInversion", "pct5y"}


class PayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rates, cls.history = build()

    def test_contract_keys(self):
        rates, history = self.rates, self.history
        self.assertEqual(set(rates), RATES_KEYS)
        self.assertNotIn("tmf", rates)
        self.assertEqual(rates["version"], 1)
        self.assertEqual(rates["tenors"], ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"])
        self.assertEqual(rates["tenorYears"], [0.0833, 0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30])
        self.assertEqual(rates["realTenors"], ["5Y", "7Y", "10Y", "20Y", "30Y"])
        self.assertEqual(set(rates["sources"]), {"rates", "etf", "etfAsOf"})
        self.assertEqual(rates["sources"]["rates"], "FRED fredgraph.csv")
        self.assertEqual(rates["sources"]["etf"], "Yahoo Finance adjusted close")
        self.assertEqual(rates["missing"], [])
        self.assertEqual([c["key"] for c in rates["curves"]["nominal"]], ["now", "1w", "1m", "3m", "1y", "2y"])
        for curve in rates["curves"]["nominal"]:
            self.assertEqual(set(curve), {"key", "date", "values"})
            self.assertEqual(len(curve["values"]), 11)
        for curve in rates["curves"]["real"]:
            self.assertEqual(len(curve["values"]), 5)
        self.assertEqual(set(rates["policy"]), {"lower", "upper", "date"})
        self.assertEqual(set(rates["inflation"]), {"5Y", "10Y", "5y5y", "20Y", "30Y"})
        self.assertTrue(rates["inflation"]["20Y"]["derived"])
        self.assertNotIn("derived", rates["inflation"]["10Y"])
        self.assertEqual([s["key"] for s in rates["spreads"]], ["10y2y", "10y3m", "30y5y", "30y10y", "2y3m"])
        for spread in rates["spreads"]:
            self.assertEqual(set(spread), SPREAD_KEYS)
            self.assertIsInstance(spread["valueBp"], int)
        self.assertEqual([e["symbol"] for e in rates["etfs"]], ["TLT", "EDV", "GOVZ", "ZROZ", "TMF"])
        for etf in rates["etfs"]:
            self.assertEqual(set(etf), ETF_KEYS, etf["symbol"])
            self.assertFalse(etf["verified"])
            self.assertEqual(set(etf["model"]), {"duration", "convexity", "proxy"})
            self.assertEqual(set(etf["price"]), {"asOf", "close", "adjClose"})
            self.assertEqual(set(etf["returns"]), {"1y", "3y", "5y", "10y", "inception"})
            self.assertEqual(set(etf["risk"]), {"vol1y", "vol3y", "vol5y", "maxDrawdown", "mdd5y", "currentDrawdown"})
            self.assertEqual(set(etf["risk"]["maxDrawdown"]), {"value", "peak", "trough", "recovered"})
            self.assertEqual(set(etf["sensitivity"]), {"dgs20", "dgs30"})
            for block in etf["sensitivity"].values():
                self.assertEqual(set(block), {"daily1y", "weekly3y"})
                for fit in block.values():
                    self.assertEqual(set(fit), {"beta", "durEmp", "r2", "n"})
        self.assertEqual(set(history), {"version", "generatedAt", "asOf", "weekly", "daily", "etf"})
        self.assertEqual(set(history["weekly"]["series"]), set(build_rates.HISTORY_IDS))
        self.assertEqual(set(history["etf"]["series"]), {"TLT", "EDV", "GOVZ", "ZROZ", "TMF"})
        for block in ("weekly", "daily", "etf"):
            for values in history[block]["series"].values():
                self.assertEqual(len(values), len(history[block]["dates"]))

    def test_values_and_units(self):
        rates, history = self.rates, self.history
        series, prices = market()
        last10 = series["DGS10"][-1]
        self.assertEqual(rates["asOf"], last10[0])
        now = rates["curves"]["nominal"][0]
        self.assertEqual(now["date"], last10[0])
        self.assertEqual(now["values"][8], last10[1])
        spread = rates["spreads"][0]
        self.assertEqual(spread["valueBp"], round((series["DGS10"][-1][1] - series["DGS2"][-1][1]) * 100))
        # the synthetic 10Y-2Y inversion: 2022-07-05..2024-09-04, deepest -100bp in the middle
        last = spread["lastInversion"]
        self.assertEqual(last["start"][:7], "2022-07")
        self.assertEqual(last["end"][:7], "2024-09")
        self.assertTrue(-102 <= last["minBp"] <= -98, last)
        self.assertFalse(spread["inverted"])
        self.assertEqual(rates["policy"], {"lower": 4.0, "upper": 4.25, "date": series["DFEDTARU"][-1][0]})
        infl20 = rates["inflation"]["20Y"]
        self.assertAlmostEqual(infl20["value"], round(series["DGS20"][-1][1] - series["DFII20"][-1][1], 3))
        tlt, tmf = rates["etfs"][0], rates["etfs"][4]
        self.assertEqual(tmf["leverage"], 3)
        self.assertEqual(tlt["price"]["adjClose"], round(prices["TLT"][-1][2], 4))
        self.assertEqual(tlt["price"]["close"], round(prices["TLT"][-1][1], 2))
        # synthetic durations come back from the regression (weekly: weaker noise impact, 3y of data)
        self.assertLess(abs(tlt["sensitivity"]["dgs20"]["weekly3y"]["durEmp"] - 16.5) / 16.5, 0.05)
        self.assertLess(abs(rates["etfs"][3]["sensitivity"]["dgs30"]["daily1y"]["durEmp"] - 27.0) / 27.0, 0.05)
        self.assertGreater(tlt["model"]["convexity"], 100)  # years², not the published /100 figure
        self.assertIsNone(tlt["returns"]["10y"])
        govz = rates["etfs"][2]
        self.assertEqual(govz["returns"]["inception"]["start"], "2020-09-22")
        self.assertIsNone(history["etf"]["series"]["GOVZ"][0])
        first_govz = next(v for v in history["etf"]["series"]["GOVZ"] if v is not None)
        self.assertEqual(first_govz, 100.0)
        self.assertEqual(history["weekly"]["dates"], ra.weekly_sample([d for d, _ in series["DGS10"]]))
        self.assertEqual(len(history["daily"]["dates"]), build_rates.DAILY_POINTS)

    def test_guard_missing_core_series(self):
        series, _ = market()
        origin = rates_synth.origins_all(series)
        origin["DGS30"] = "previous"
        with self.assertRaises(build_rates.InsufficientData):
            build(origin=origin)
        trimmed = {k: v for k, v in series.items() if k != "DGS2"}
        with self.assertRaises(build_rates.InsufficientData):
            build(series=trimmed, origin=rates_synth.origins_all(trimmed))

    def test_guard_too_few_tenors(self):
        series, _ = market()
        origin = rates_synth.origins_all(series)
        for key in ("DGS1MO", "DGS3MO", "DGS6MO", "DGS1"):
            origin[key] = None
        with self.assertRaises(build_rates.InsufficientData):
            build(origin=origin)
        origin["DGS1"] = "csv"  # 8 of 11 fresh is enough; the rest is reported as missing
        rates, _ = build(origin=origin)
        self.assertEqual(rates["missing"], ["DGS1MO", "DGS3MO", "DGS6MO"])
        self.assertEqual(rates["sources"]["rates"], "FRED fredgraph.csv")

    def test_guard_stale(self):
        with self.assertRaises(build_rates.InsufficientData):
            build(today=rates_synth.TODAY + timedelta(days=11))
        build(today=rates_synth.TODAY + timedelta(days=10))

    def test_mixed_sources(self):
        series, _ = market()
        origin = rates_synth.origins_all(series)
        origin["T5YIFR"] = "api"
        rates, _ = build(origin=origin)
        self.assertEqual(rates["sources"]["rates"], "mixed")

    def test_etf_failure_keeps_previous_blocks(self):
        rates, history = self.rates, self.history
        again, again_history = build(prices={}, previous_rates=rates, previous_history=history)
        self.assertEqual(again["sources"]["etf"], rates["sources"]["etf"])
        self.assertEqual(again["sources"]["etfAsOf"], rates["sources"]["etfAsOf"])
        self.assertEqual(again_history["etf"], history["etf"])
        for old, new in zip(rates["etfs"], again["etfs"]):
            for key in ("price", "returns", "risk", "sensitivity"):
                self.assertEqual(old[key], new[key])
            self.assertEqual(old["model"], new["model"])  # model comes from today's curve, same inputs

    def test_partial_etf_failure_reuses_one_symbol(self):
        _, prices = market()
        partial = {k: v for k, v in prices.items() if k != "EDV"}
        again, again_history = build(prices=partial, previous_rates=self.rates, previous_history=self.history)
        self.assertEqual(again["etfs"][1]["returns"], self.rates["etfs"][1]["returns"])
        self.assertEqual(again_history["etf"]["series"]["EDV"], self.history["etf"]["series"]["EDV"])

    def test_short_partial_download_keeps_reused_history(self):
        # Only GOVZ came back, and only its last 300 days: the other funds keep their whole weekly
        # history from the previous file (inception = 100) instead of being cut to GOVZ's span.
        _, prices = market()
        partial = {"GOVZ": prices["GOVZ"][-300:]}
        _, again_history = build(prices=partial, previous_rates=self.rates, previous_history=self.history)
        old, new = self.history["etf"], again_history["etf"]
        self.assertEqual(new["dates"][0], old["dates"][0])
        for symbol in ("TLT", "EDV", "ZROZ", "TMF"):
            old_values = dict(zip(old["dates"], old["series"][symbol]))
            new_values = dict(zip(new["dates"], new["series"][symbol]))
            self.assertEqual(new_values[new["dates"][0]], 100)
            self.assertEqual({d: v for d, v in new_values.items() if d in old_values}, old_values)
        govz = [v for v in new["series"]["GOVZ"] if v is not None]
        self.assertEqual(govz[0], 100)
        self.assertEqual(len(govz), len(ra.weekly_sample([d for d, _, _ in partial["GOVZ"]])))

    def test_etf_failure_without_previous(self):
        rates, history = build(prices={})
        self.assertIsNone(rates["sources"]["etf"])
        self.assertIsNone(rates["sources"]["etfAsOf"])
        self.assertIsNone(rates["etfs"][0]["price"])
        self.assertEqual(history["etf"]["dates"], [])
        self.assertIsNotNone(rates["etfs"][0]["model"])

    def test_payload_is_strict_json(self):
        for payload in (self.rates, self.history):
            text = json.dumps(payload, allow_nan=False)
            self.assertNotIn("-0.0,", text)
            self.assertNotIn("-0.0]", text)

    def test_price_spike_is_dropped_before_metrics(self):
        _, prices = market()
        spiked = dict(prices)
        points = list(prices["TLT"])
        day, close, adj = points[-300]
        points[-300] = (day, close * 0.5, adj * 0.5)  # one bad print, -50%
        spiked["TLT"] = points
        rates, _ = build(prices=spiked)
        clean, got = self.rates["etfs"][0], rates["etfs"][0]
        self.assertEqual(got["returns"], clean["returns"])
        self.assertEqual(got["risk"]["maxDrawdown"], clean["risk"]["maxDrawdown"])
        # the dropped day turns two daily returns into one two-day return: vol moves a hair, not ~50%
        self.assertLess(abs(got["risk"]["vol5y"] - clean["risk"]["vol5y"]), 0.1)

    def test_model_block_uses_todays_long_yields(self):
        series, _ = market()
        y20, y30 = series["DGS20"][-1][1], series["DGS30"][-1][1]
        for etf, config in zip(self.rates["etfs"], rates_config.ETFS):
            self.assertEqual(etf["model"], ra.model_duration_convexity(config["modelProxy"], y20, y30))
        self.assertEqual(self.rates["etfs"][0]["model"], self.rates["etfs"][4]["model"])  # TMF: unlevered index


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
    def fred_http(self, broken=()):
        series, _ = market()

        def http_get(url, params, timeout):
            if "fredgraph" not in url:
                return 500, f"error for api_key={params.get('api_key')}"
            key = params["id"]
            if key in broken or key not in series:
                return 404, ""
            rows = ["observation_date," + key] + [f"{d},{v}" for d, v in series[key]]
            return 200, "\n".join(rows) + "\n"

        return http_get

    def downloader_for(self, prices):
        def downloader(symbols):
            return {s: prices[s] for s in symbols if s in prices}

        return downloader

    def run_build(self, out_dir, http_get, prices):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = build_rates.run(
                out_dir, http_get=http_get, price_source=self.downloader_for(prices), api_key=SECRET,
                today=rates_synth.TODAY, now=rates_synth.NOW, sleep=lambda _s: None,
            )
        return code, out.getvalue()

    def test_writes_both_files_and_keeps_old_on_failure(self):
        _, prices = market()
        with tempfile.TemporaryDirectory() as tmp:
            code, output = self.run_build(tmp, self.fred_http(), prices)
            self.assertEqual(code, 0, output)
            self.assertNotIn(SECRET, output)
            names = sorted(os.listdir(tmp))
            self.assertEqual(names, ["rates-history.json", "rates.json"])
            with open(os.path.join(tmp, "rates.json"), encoding="utf-8") as file:
                first = file.read()
            # DGS10 broken: nothing is written, previous files stay byte-identical
            code, output = self.run_build(tmp, self.fred_http(broken={"DGS10"}), prices)
            self.assertEqual(code, 1)
            self.assertNotIn(SECRET, output)
            with open(os.path.join(tmp, "rates.json"), encoding="utf-8") as file:
                self.assertEqual(file.read(), first)
            self.assertEqual(sorted(os.listdir(tmp)), names)  # no stray .tmp files

    def test_minor_series_falls_back_to_previous_file(self):
        _, prices = market()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_build(tmp, self.fred_http(), prices)[0], 0)
            code, output = self.run_build(tmp, self.fred_http(broken={"T5YIFR"}), {})
            self.assertEqual(code, 0, output)
            with open(os.path.join(tmp, "rates.json"), encoding="utf-8") as file:
                rates = json.load(file)
            self.assertEqual(rates["missing"], ["T5YIFR"])
            self.assertIsNotNone(rates["inflation"]["5y5y"]["value"])
            self.assertEqual(rates["sources"]["etf"], "Yahoo Finance adjusted close")  # kept from old file

    def test_extract_etf_points_from_dataframe(self):
        try:
            import pandas as pd
        except ImportError:  # pragma: no cover - pandas is in requirements.txt
            self.skipTest("pandas not installed")
        index = pd.to_datetime(["2026-10-07", "2026-10-08", "2026-10-09"])
        columns = pd.MultiIndex.from_product([["TLT", "TMF"], ["Close", "Adj Close"]])
        frame = pd.DataFrame(
            [[90.0, 88.0, 40.0, 39.0], [float("nan"), float("nan"), 41.0, 40.0], [91.0, 89.0, 42.0, 41.0]],
            index=index, columns=columns,
        )
        self.assertEqual(build_rates.extract_etf_points(frame, "TLT"), [("2026-10-07", 90.0, 88.0), ("2026-10-09", 91.0, 89.0)])
        swapped = frame.swaplevel(axis=1)
        self.assertEqual(build_rates.extract_etf_points(swapped, "TMF")[-1], ("2026-10-09", 42.0, 41.0))
        self.assertEqual(build_rates.extract_etf_points(frame, "EDV"), [])

    def test_drop_open_session(self):
        prices = {"TLT": [("2026-10-08", 1.0, 1.0), ("2026-10-09", 2.0, 2.0)]}
        during = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)  # 11:00 New York
        after = datetime(2026, 10, 9, 21, 0, tzinfo=timezone.utc)   # 17:00 New York
        self.assertEqual(build_rates.drop_open_session(prices, during)["TLT"], prices["TLT"][:1])
        self.assertEqual(build_rates.drop_open_session(prices, after)["TLT"], prices["TLT"])

    def test_download_uses_injected_downloader(self):
        calls = []

        def downloader(symbols):
            calls.append(list(symbols))
            raise RuntimeError("network down")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            prices = build_rates.download_etf_prices(["TLT", "TMF"], downloader=downloader, sleep=lambda _s: None)
        self.assertEqual(prices, {})
        self.assertGreaterEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
