import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import build_themes  # noqa: E402
import report_theme_momentum  # noqa: E402
from test_themes import geometric, mini_config  # noqa: E402
from themes_synth import business_days  # noqa: E402

TODAY = datetime(2026, 10, 9, 22, 30, tzinfo=timezone.utc)


class MissingPriceTest(unittest.TestCase):
    def test_carry_cap_uses_sessions_without_backfill_or_future_data(self):
        calendar = business_days("2026-10-09", 10)
        bars = [(calendar[1], 100.0), (calendar[7], 160.0), ("2026-10-12", 999.0)]
        values, observed = build_themes.align_with_observations(bars, calendar, calendar[-1])
        self.assertEqual(values, [None, 100, 100, 100, 100, None, None, 160, 160, 160])
        self.assertEqual(observed, [False, True, False, False, False, False, False, True, False, False])

    def test_missing_and_recovery_days_use_only_other_real_members(self):
        calendar = business_days("2026-10-09", 6)
        halted = [(calendar[0], 100), (calendar[1], 110), (calendar[4], 150), (calendar[5], 165)]
        values, observed = build_themes.align_with_observations(halted, calendar, calendar[-1])
        other = [100 * 1.02**index for index in range(6)]
        daily = build_themes.equal_weight_daily_returns([values, other], 6, [observed, [True] * 6])
        self.assertEqual([count for _, count in daily], [2, 1, 1, 1, 2])
        for (actual, _), expected in zip(daily, [.06, .02, .02, .02, .06]):
            self.assertAlmostEqual(actual, expected)
        series = build_themes.equal_weight_series([values, other], 6, [observed, [True] * 6])
        self.assertAlmostEqual(series[-1], 100 * 1.06**2 * 1.02**3)
        # The 110 -> 150 multi-session change is observable as a span, but never a daily return.
        self.assertIsNone(build_themes.period_return(values[:5], 1, observed[:5]))
        self.assertAlmostEqual(build_themes.period_return(values[:5], 3, observed[:5]), (150 / 110 - 1) * 100)

    def test_an_entire_missing_theme_day_is_marked_unavailable(self):
        values = [100, 100, 120, 121]
        observed = [True, False, True, True]
        dates = business_days("2026-10-09", 4)
        quality = build_themes.series_data_quality([values], [observed], dates)["dataQuality"]
        self.assertEqual(quality["seriesReturnContributors"], [0, 0, 1])
        self.assertEqual(quality["seriesUnavailableDates"], dates[1:3])
        self.assertEqual(build_themes.equal_weight_series([values], 4, [observed])[:3], [100, 100, 100])

    def test_systemic_interior_outage_is_not_removed_from_the_calendar(self):
        calendar = business_days("2026-10-09", 260)
        missing = calendar[-3]
        config = mini_config({"t": ["A", "B", "C"]})
        prices = {"SPY": geometric(calendar, 400, .001)}
        prices.update({symbol: [bar for bar in geometric(calendar, 100, .01) if bar[0] != missing]
                       for symbol in ["A", "B", "C"]})
        payload = build_themes.build_payload(prices, config, TODAY)
        self.assertIn(missing, payload["dates"])
        theme = payload["themes"][0]
        self.assertEqual(theme["dataQuality"]["seriesUnavailableDates"], calendar[-3:-1])
        self.assertEqual(theme["series"][-4:-1], [theme["series"][-4]] * 3)
        self.assertAlmostEqual(theme["series"][-1] / theme["series"][-2] - 1, .01, places=4)

    def test_recovery_is_omitted_from_payload_daily_mean_and_series(self):
        calendar = business_days("2026-10-09", 260)
        config = mini_config({"t": ["A", "B", "C"]})
        prices = {"SPY": geometric(calendar, 400, 0), "A": geometric(calendar, 100, 0),
                  "B": geometric(calendar, 100, .01), "C": geometric(calendar, 100, .01)}
        prices["A"] = prices["A"][:-2] + [(calendar[-1], 200)]
        theme = build_themes.build_payload(prices, config, TODAY)["themes"][0]
        a = next(member for member in theme["constituents"] if member["symbol"] == "A")
        self.assertIsNone(a["returns"]["d1"])
        self.assertEqual(theme["returns"]["d1"], 1.0)
        self.assertAlmostEqual(theme["series"][-1] / theme["series"][-2] - 1, .01, places=4)
        self.assertEqual(theme["dataQuality"]["seriesReturnContributors"][-2:], [2, 2])

    def test_latest_carried_close_has_its_real_date_and_no_invented_metrics(self):
        calendar = business_days("2026-10-09", 260)
        config = mini_config({"t": ["A", "B", "C"]})
        prices = {"SPY": geometric(calendar, 400, 0), "A": geometric(calendar, 100, .1)[:-1],
                  "B": geometric(calendar, 100, .01), "C": geometric(calendar, 100, .01)}
        theme = build_themes.build_payload(prices, config, TODAY)["themes"][0]
        a = next(member for member in theme["constituents"] if member["symbol"] == "A")
        self.assertEqual(a["priceAsOf"], calendar[-2])
        self.assertTrue(all(value is None for value in a["returns"].values()))
        self.assertIsNone(a["aboveMa50"])
        self.assertIsNone(a["fromHi52"])
        self.assertEqual(theme["returns"]["d1"], 1.0)

    def test_calendar_day_freshness_still_rejects_a_friday_close_on_tuesday(self):
        calendar = business_days("2026-10-13", 260)
        config = mini_config({"t": ["A", "B", "C", "OLD"]})
        prices = {symbol: geometric(calendar, 100, .001) for symbol in ["SPY", "A", "B", "C"]}
        # Three-session carry allowance does not override the old four-calendar-day age rejection.
        prices["OLD"] = [(day, value) for day, value in prices["A"] if day <= "2026-10-09"]
        prices["OLD"].append(("2026-10-14", 999))  # future data cannot rescue a stale as-of close
        payload = build_themes.build_payload(prices, config, TODAY)
        self.assertEqual(payload["skipped"]["stale"], ["OLD"])


class MomentumAuditTest(unittest.TestCase):
    def test_benchmark_denominator_gives_exact_effective_coefficient(self):
        self.assertEqual(build_themes.MOMENTUM_WEIGHTS, {"w1": .2, "m1": .25, "m3": .3, "rs3m": .25})
        for benchmark in [-30, 0, 20]:
            coefficient, common = report_theme_momentum.effective_m3_terms(benchmark)
            for m3 in [-10, 5, 40]:
                returns = {"w1": 3, "m1": 7, "m3": m3}
                rs = build_themes.relative_strength(m3, benchmark)
                score = build_themes.momentum_score(returns, rs)
                self.assertAlmostEqual(score, .2 * 3 + .25 * 7 + coefficient * m3 + common)
            self.assertEqual(sorted([-10, 5, 40], key=lambda m: build_themes.relative_strength(m, benchmark)), [-10, 5, 40])
        self.assertAlmostEqual(report_theme_momentum.effective_m3_terms(0)[0], .55)
        self.assertNotAlmostEqual(report_theme_momentum.effective_m3_terms(20)[0], .55)

    def test_report_separates_policy_and_diagnostics_from_oos_evidence(self):
        report = report_theme_momentum.render_report()
        self.assertIn("未提供逐日原始價格", report)
        self.assertIn("不是樣本外績效驗證", report)
        self.assertIn("0.30 + 0.25/(1+b/100)", report)
        self.assertIn("保留現有三個月動能偏重", report)
        calendar = business_days("2026-10-09", 260)
        prices = {symbol: geometric(calendar, 100, .001) for symbol in ["SPY", "A", "B", "C"]}
        prices["A"] = prices["A"][:-4] + prices["A"][-3:]
        measured = report_theme_momentum.render_report(prices, mini_config({"t": ["A", "B", "C"]}))
        self.assertIn("輸入資料日期：2026-10-09", measured)
        self.assertIn("| t | 3/3 | 1 | 0 | 0 |", measured)
        self.assertIn("下市股", measured)


if __name__ == "__main__":
    unittest.main()
