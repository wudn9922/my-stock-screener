"""Atlas list monitoring integration; no Supabase/Discord requests are sent."""
import os
import ast
from pathlib import Path
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from breakout_alert.supabase_store import SupabaseStore, fixed_parameter_map

LIST_ID = 'd93f0aa6-c873-4f97-8cf9-45a7b55b6439'


class WatchlistMonitorTests(unittest.TestCase):
    def setUp(self):
        self.store = SupabaseStore.__new__(SupabaseStore)
        self.store.user_id = 'U-test'
        self.store._request = Mock()
        self.enabled = patch.dict(os.environ, {'ATLAS_WATCHLIST_SYNC_ENABLED': '1'})
        self.enabled.start()
        self.addCleanup(self.enabled.stop)

    def row(self, **changes):
        definition = {
            'id': LIST_ID, 'name': '成長股', 'market': 'US',
            'kind': 'rules', 'monitorBreakouts': True, 'maList': [20, 50],
            'history': [{'date': datetime.now(timezone.utc).date().isoformat(), 'members': [{'symbol': 'NVDA'}, {'symbol': 'AAPL'}]}],
        }
        definition.update(changes)
        return {'id': LIST_ID, 'line_user_id': 'U-test', 'definition': definition}

    def test_opt_in_default_leaves_existing_monitor_unchanged(self):
        with patch.dict(os.environ, {'ATLAS_WATCHLIST_SYNC_ENABLED': '0'}):
            self.assertEqual(self.store.get_watchlist_monitor_configs(), [])
        self.store._request.assert_not_called()
        self.assertFalse(self.store.watchlists_loaded)

    def test_enabled_list_reuses_existing_engine_configuration(self):
        self.store._request.return_value = [self.row()]
        configs = self.store.get_watchlist_monitor_configs()
        self.assertEqual([config['ticker'] for config in configs], ['NVDA', 'AAPL'])
        self.assertEqual(configs[0]['group_id'], f'watchlist_{LIST_ID}')
        self.assertEqual(configs[0]['ma_list'], [20, 50])
        self.assertTrue(self.store.watchlists_loaded)
        self.assertEqual(self.store._request.call_args.kwargs['params']['line_user_id'], 'eq.U-test')

    def test_disabled_and_other_users_lists_cannot_send_alerts(self):
        other = self.row()
        other['line_user_id'] = 'U-other'
        self.store._request.return_value = [self.row(monitorBreakouts=False), other]
        self.assertEqual(self.store.get_watchlist_monitor_configs(), [])

    def test_manual_lists_monitor_fixed_members(self):
        self.store._request.return_value = [self.row(kind='manual', symbols=['NVDA', 'NVDA'], history=[])]
        configs = self.store.get_watchlist_monitor_configs()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]['ticker'], 'NVDA')

    def test_unavailable_stale_or_invalid_data_preserves_baselines(self):
        for row in [
            self.row(history=[]),
            self.row(history=[{'date': '2000-01-01', 'members': [{'symbol': 'NVDA'}]}]),
            self.row(maList=[True]),
            self.row(maList=[20, 20]),
            self.row(monitorBreakouts='true'),
            self.row(market='TW'),
        ]:
            self.store._request.return_value = [row]
            self.assertEqual(self.store.get_watchlist_monitor_configs(), [])
            self.assertFalse(self.store.watchlists_loaded)
        self.store._request.side_effect = RuntimeError('table not deployed')
        self.assertEqual(self.store.get_watchlist_monitor_configs(), [])
        self.assertFalse(self.store.watchlists_loaded)

    def test_cleanup_excludes_watchlist_rows_when_the_table_was_not_loaded(self):
        self.store.watchlists_loaded = False
        self.store.index_configs_loaded = False
        self.store._request.return_value = [
            {'id': 1, 'group_id': 'us_g1', 'ticker': 'NVDA', 'ma_period': 20},
            {'id': 2, 'group_id': f'watchlist_{LIST_ID}', 'ticker': 'AAPL', 'ma_period': 20},
        ]
        self.store._delete_orphan_rows = Mock(return_value=0)
        self.store.prune_orphan_states([{'group_id': 'us_g1', 'ticker': 'NVDA', 'ma_list': [20]}])
        self.assertEqual(self.store._delete_orphan_rows.call_count, 1)
        self.assertEqual([row['id'] for row in self.store._delete_orphan_rows.call_args.args[0]], [1])

    def test_fixed_parameters_override_only_current_members_and_stay_dormant_otherwise(self):
        self.store._request.return_value = [self.row()]
        configs = self.store.get_watchlist_monitor_configs({'NVDA': [13, 34], 'MSFT': [200]})
        self.assertEqual([(row['ticker'], row['ma_list']) for row in configs], [('NVDA', [13, 34]), ('AAPL', [20, 50])])
        self.store._request.return_value = [self.row(history=[{'date': datetime.now(timezone.utc).date().isoformat(), 'members': [{'symbol': 'AAPL'}]}])]
        self.assertEqual([row['ticker'] for row in self.store.get_watchlist_monitor_configs({'NVDA': [13, 34]})], ['AAPL'])

    def test_fixed_parameters_are_user_scoped_and_not_standalone_monitors(self):
        fixed = {'id': 1, 'ticker': 'NVDA', 'line_user_id': 'U-test', 'group_id': 'ungrouped', 'ma1': 13, 'ma2': 34}
        other = dict(fixed, ticker='AAPL', line_user_id='U-other')
        self.assertEqual(fixed_parameter_map([fixed, other], 'U-test'), {'NVDA': [13, 34]})
        self.store.get_groups = Mock(return_value=[])
        self.store.get_user_stocks = Mock(return_value=[fixed, other])
        self.store.get_index_monitor_configs = Mock(return_value=[])
        self.store._request.return_value = []
        self.assertEqual(self.store.get_monitor_configs(), [])
        grouped = dict(fixed, id=3, group_id='us_g1', ma1=20, ma2=50)
        self.store.get_user_stocks.return_value = [fixed, grouped]
        configs = self.store.get_monitor_configs()
        self.assertEqual([(row['group_id'], row['ma_list']) for row in configs], [('us_g1', [13, 34])])

    def test_daily_report_applies_fixed_parameters_without_adding_dormant_stocks(self):
        # Execute the actual loader without importing data downloaders or sending requests.
        source = ast.parse((Path(__file__).resolve().parents[1] / 'main.py').read_text())
        loader = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'load_configs_from_supabase')
        fixed = {'ticker': 'NVDA', 'line_user_id': 'U-test', 'group_id': 'ungrouped', 'ma1': 13, 'ma2': 34}
        grouped = dict(fixed, group_id='us_g1', ma1=20, ma2=50)
        dormant = dict(fixed, ticker='MSFT')
        replies = [[], [fixed, grouped, dormant], []]
        request = Mock(side_effect=[Mock(status_code=200, json=Mock(return_value=reply)) for reply in replies])
        from breakout_alert.supabase_store import get_ma_list, parameter_ticker_key, FIXED_STOCK_GROUP_ID
        namespace = {
            'requests': Mock(get=request), 'get_supabase_settings': lambda: ('https://test.invalid/rest/v1', 'test'),
            'get_default_group_metadata': lambda: {'us_g1': {'market': 'us', 'ma_list': [20]}},
            'get_default_index_configs': lambda: [], 'fixed_parameter_map': fixed_parameter_map,
            'parameter_ticker_key': parameter_ticker_key, 'FIXED_STOCK_GROUP_ID': FIXED_STOCK_GROUP_ID,
            'normalize_custom_ticker': lambda ticker: ticker, 'get_stock_ma_list': get_ma_list,
        }
        exec(compile(ast.Module(body=[loader], type_ignores=[]), 'main.py', 'exec'), namespace)
        configs, _, _ = namespace['load_configs_from_supabase']('U-test')
        self.assertEqual(configs['us_g1'], {'NVDA': [13, 34]})
        self.assertNotIn('ungrouped', configs)
        self.assertFalse(any('MSFT' in group for group in configs.values()))


if __name__ == '__main__':
    unittest.main()
