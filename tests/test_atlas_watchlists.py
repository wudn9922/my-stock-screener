"""Atlas list monitoring integration; no Supabase/Discord requests are sent."""
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from breakout_alert.supabase_store import SupabaseStore

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


if __name__ == '__main__':
    unittest.main()
