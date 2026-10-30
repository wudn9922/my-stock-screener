"""Offline tests for the provided OLS formula, MOF inputs and effective-date JP spreads."""
import json
import math
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import build_bond_research as br

MOF = '国債金利情報\n基準日,1年,10年,20年\nR8.10.27,1.0,2.830,3.0\nR8.10.28,1.0,2.867,3.0\n,,,,\n注意\n'
SEED = json.loads(br.SEED.read_text())


class BondResearchTests(unittest.TestCase):
    def test_mof_cp932_dates_and_null_rows(self):
        self.assertEqual(br.parse_mof(MOF.encode('cp932')), {'2026-10-27': 2.83, '2026-10-28': 2.867})
        for raw, expected in [('S64.1.7', '1989-01-07'), ('H1.1.8', '1989-01-08'), ('R1.5.1', '2019-05-01')]:
            self.assertEqual(br.iso_japanese(raw), expected)
        for body in ['', '基準日,10年\nR8.10.28,-\n', '基準日,20年\nR8.10.28,3.0']:
            with self.assertRaises(ValueError): br.parse_mof(body)

    def test_policy_uses_effective_dates_and_does_not_use_future_announcements(self):
        self.assertEqual(br.policy_on('2026-09-18'), ('2026-06-17', 1.))
        self.assertEqual(br.policy_on('2026-09-24'), ('2026-09-24', 1.25))
        self.assertEqual(br.policy_on('2024-07-31'), ('2024-03-21', .1))
        self.assertEqual(br.policy_on('2024-08-01'), ('2024-08-01', .25))
        self.assertIsNone(br.policy_on('2000-01-01'))

    def test_attachment_ols_residual_and_spread_degrees_of_freedom(self):
        rows = [{'date': f'2020-0{i+1}-01', 'pol': x, 'y10': y} for i, (x, y) in enumerate(zip(range(5), [2.1, 2.5, 3.5, 4., 5.3]))]
        p = br.fit_band(rows)
        self.assertAlmostEqual(p['a'], 1.9)
        self.assertAlmostEqual(p['b'], .79)
        self.assertAlmostEqual(p['sd'], math.sqrt(.207 / 3))
        self.assertAlmostEqual(p['spread_mean'], 1.48)
        self.assertAlmostEqual(p['spread_sd'], math.sqrt(.648 / 4))
        self.assertEqual((p['first'], p['last'], p['n']), ('2020-01-01', '2020-05-01', 5))
        for bad in [rows[:2], [dict(r, pol=1) for r in rows], [dict(r, y10=float('nan')) for r in rows]]:
            with self.assertRaises(ValueError): br.fit_band(bad)

    def test_fits_exclude_incomplete_current_month_and_use_actual_pre_target_rates(self):
        series = {'FEDFUNDS': [('1982-09-01', 3.)], 'DFEDTAR': [('1982-10-01', 4.), ('1982-10-02', 6.)],
                  'GS10': [('1982-09-01', 5.), ('1982-10-01', 6.), ('1982-11-01', 7.)]}
        rows = br.fit_rows('US', series, date(1982, 11, 20))
        self.assertEqual([r['pol'] for r in rows], [3., 5.])
        jp = {'IRSTCI01JPM156N': [('2006-06-01', .07)], 'IRLTLT01JPM156N': [('2006-06-01', 1.), ('2006-07-01', 2.), ('2006-08-01', 3.)]}
        rows = br.fit_rows('JP', jp, date(2006, 8, 20))
        self.assertAlmostEqual(rows[0]['pol'], .07)
        self.assertAlmostEqual(rows[1]['pol'], .25 * 18 / 31)
        self.assertEqual(len(rows), 2)

    def test_payload_aligns_policy_to_each_quote_and_rejects_future_observations(self):
        points = {'2026-09-18': 2.8, '2026-09-24': 2.9, '2026-10-30': 9.}
        data = br.japan_payload(points, None, date(2026, 10, 29), 'now', [])
        self.assertEqual(data['asOf'], '2026-09-24')
        self.assertAlmostEqual(data['spreadPp'], 1.65)
        self.assertEqual(data['daily']['series']['BOJ_TARGET'], [1., 1.25])
        self.assertIsNone(br.japan_payload({}, None, date(2026, 10, 29), 'now', []))

    def test_daily_run_updates_only_new_reports_and_keeps_attached_parameters_in_same_month(self):
        calls = []
        def get(url, *args):
            calls.append(url)
            return 200, MOF.encode('cp932')
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            original = out / 'rates.json'; original.write_text('unchanged')
            self.assertEqual(br.run(out, get=get, today=date(2026, 10, 29), now='2026-10-29T12:00:00Z'), 0)
            data = json.loads((out / 'jp-rates.json').read_text())
            self.assertEqual(data['asOf'], '2026-10-28')
            self.assertEqual(data['policy']['date'], '2026-09-24')
            self.assertAlmostEqual(data['spreadPp'], 1.617)
            self.assertEqual(json.loads((out / 'yield-band-params.json').read_text()), SEED)
            self.assertEqual(original.read_text(), 'unchanged')
            self.assertTrue(all(url.startswith(br.MOF_URL) for url in calls))

    def test_source_failure_retains_original_date_and_prices_instead_of_publishing_fabricated_rows(self):
        previous = br.japan_payload({'2026-10-28': 2.867}, None, date(2026, 10, 29), 'old', [])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp); (out / 'jp-rates.json').write_text(json.dumps(previous))
            get = lambda *args: (503, '')
            self.assertEqual(br.run(out, get=get, today=date(2026, 10, 29)), 0)
            kept = json.loads((out / 'jp-rates.json').read_text())
            self.assertEqual(kept['asOf'], '2026-10-28')
            self.assertEqual(kept['tenYear']['value'], 2.867)
            self.assertEqual(len(kept['missing']), 2)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(br.run(tmp, get=get, today=date(2026, 10, 29)), 1)
            self.assertFalse((Path(tmp) / 'jp-rates.json').exists())

    def test_partial_mof_input_never_regresses_a_retained_date(self):
        previous = br.japan_payload({'2026-10-28': 2.867}, None, date(2026, 10, 29), 'old', [])
        kept = br.japan_payload({'2026-09-30': 3.057}, previous, date(2026, 10, 29), 'new', ['current'])
        self.assertEqual(kept['asOf'], '2026-10-28')
        self.assertEqual(kept['daily']['series']['JGB10'], [3.057, 2.867])

    def test_failed_monthly_refit_retains_previous_coefficients_and_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(br.fred, 'make_fetchers', return_value=[('csv', lambda key: [])]):
                self.assertEqual(br.run(tmp, get=lambda *args: (200, MOF), today=date(2026, 11, 2)), 0)
            self.assertEqual(json.loads((Path(tmp) / 'yield-band-params.json').read_text()), SEED)

    def test_full_sample_requests_do_not_inherit_the_ui_history_cutoff(self):
        calls = []
        fetch = br.full_history_get(lambda *args: calls.append(args) or (200, ''))
        fetch(br.fred.FRED_CSV_URL, {'id': 'GS10', 'cosd': '2003-01-01'}, (10, 20))
        fetch(br.fred.FRED_API_URL, {'series_id': 'GS10', 'observation_start': '2003-01-01'}, (10, 20))
        self.assertEqual(calls[0][1]['cosd'], '1954-01-01')
        self.assertEqual(calls[1][1]['observation_start'], '1954-01-01')
        self.assertEqual(br.fred.HISTORY_START, '2003-01-01')

    def test_same_last_month_does_not_pretend_to_finish_a_new_month_update(self):
        dates = [f'{year}-{month:02d}-01' for year in range(1954, 2027) for month in range(1, 13) if (1954, 7) <= (year, month) <= (2026, 9)]
        policy = [(d, 1 + i % 5 * .3) for i, d in enumerate(dates)]
        yields = [(d, 2 + .75 * x + math.sin(i) * .1) for i, (d, x) in enumerate(policy)]
        series = {'FEDFUNDS': policy, 'DFEDTARU': policy, 'GS10': yields}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(br.fred, 'make_fetchers', return_value=[('csv', lambda key: series.get(key, []))]):
                br.run(tmp, get=lambda *args: (200, MOF), today=date(2026, 11, 2))
            self.assertEqual(json.loads((Path(tmp) / 'yield-band-params.json').read_text()), SEED)

    def test_each_country_can_refresh_independently_and_incomplete_model_retry_is_not_suppressed(self):
        dates = [f'{year}-{month:02d}-01' for year in range(1954, 2027) for month in range(1, 13) if (1954, 7) <= (year, month) <= (2026, 10)]
        policy = [(d, 1 + i % 5 * .3) for i, d in enumerate(dates)]
        yields = [(d, 2 + .75 * x + math.sin(i) * .1) for i, (d, x) in enumerate(policy)]
        series = {'FEDFUNDS': policy, 'DFEDTARU': policy, 'GS10': yields}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(br.fred, 'make_fetchers', return_value=[('csv', lambda key: series.get(key, []))]):
                br.run(tmp, get=lambda *args: (200, MOF), today=date(2026, 11, 2))
            p = json.loads((Path(tmp) / 'yield-band-params.json').read_text())
            self.assertEqual(p['US']['fittedAt'], '2026-11-02')
            self.assertEqual(p['US']['last'], '2026-10-01')
            self.assertEqual(p['JP'], SEED['JP'])
            ids = []
            with patch.object(br.fred, 'make_fetchers', return_value=[('csv', lambda key: ids.append(key) or [])]):
                br.run(tmp, get=lambda *args: (200, MOF), today=date(2026, 11, 3))
            self.assertEqual(set(ids), {'IRSTCI01JPM156N', 'IRLTLT01JPM156N'})


if __name__ == '__main__': unittest.main()
