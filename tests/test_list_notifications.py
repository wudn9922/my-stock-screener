"""Per-list 均線／畫線 notification settings for the breakout alert service."""

import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from breakout_alert.list_notifications import (  # noqa: E402
    DEFAULT_SETTINGS,
    config_list_key,
    drawing_allowed_tickers,
    filter_drawing_watchlist,
    filter_ma_configs,
    parse_settings,
)
from breakout_alert.drawing_alerts import DrawingWatchlist, WatchEntry  # noqa: E402

LIST_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def custom(**lists):
    return parse_settings({"version": 1, "mode": "custom", "lists": lists})


def config(ticker, group, market="TW"):
    return {"ticker": ticker, "group_id": group, "raw_group_id": group, "market": market, "ma_list": [20]}


class SettingsTest(unittest.TestCase):
    def test_default_and_invalid_documents_mean_all_lists(self):
        for raw in (None, {}, {"version": 2, "mode": "custom", "lists": {}}, {"version": 1, "mode": "all"},
                    {"version": 1, "mode": "custom", "lists": {"group:nope": {"ma": True}}},
                    {"version": 1, "mode": "custom", "lists": {"group:tw_g1": {"ma": "yes"}}},
                    {"version": 1, "mode": "custom", "lists": []}):
            self.assertTrue(parse_settings(raw).all_lists, raw)
        self.assertTrue(DEFAULT_SETTINGS.enabled("group:tw_g1", "ma"))
        self.assertTrue(DEFAULT_SETTINGS.enabled("watchlist:x", "drawing"))

    def test_both_one_or_none_per_list(self):
        settings = custom(**{"group:tw_g1": {"ma": True, "drawing": True}, "group:us_g1": {"ma": False, "drawing": True},
                             f"watchlist:{LIST_ID.upper()}": {"ma": True}, "group:custom_7": {"ma": False, "drawing": False}})
        self.assertFalse(settings.all_lists)
        self.assertTrue(settings.enabled("group:tw_g1", "ma") and settings.enabled("group:tw_g1", "drawing"))
        self.assertEqual((settings.enabled("group:us_g1", "ma"), settings.enabled("group:us_g1", "drawing")), (False, True))
        self.assertTrue(settings.enabled(f"watchlist:{LIST_ID}", "ma"))
        self.assertFalse(settings.enabled(f"watchlist:{LIST_ID}", "drawing"))
        self.assertFalse(settings.enabled("group:custom_7", "ma"))
        self.assertFalse(settings.enabled("group:tw_g2", "ma"))  # unlisted lists are off in custom mode
        self.assertEqual(settings.watchlists_with("ma"), {LIST_ID})
        self.assertEqual(DEFAULT_SETTINGS.watchlists_with("ma"), set())


class MovingAverageFilterTest(unittest.TestCase):
    def test_list_keys(self):
        self.assertEqual(config_list_key(config("2330.TW", "tw_g1")), "group:tw_g1")
        self.assertEqual(config_list_key(config("NVDA", f"watchlist_{LIST_ID}")), f"watchlist:{LIST_ID}")
        self.assertIsNone(config_list_key(config("^TWII", "admin_index")))

    def test_all_lists_keeps_everything_but_alerts_once_per_ticker(self):
        configs = [config("2330.TW", "tw_g1"), config("2330.TW", f"watchlist_{LIST_ID}"), config("^TWII", "admin_index")]
        self.assertEqual([c["group_id"] for c in filter_ma_configs(configs, DEFAULT_SETTINGS)], ["tw_g1", "admin_index"])

    def test_any_enabled_list_notifies_and_index_alerts_always_run(self):
        configs = [config("2330.TW", "tw_g1"), config("2330.TW", f"watchlist_{LIST_ID}"),
                   config("2317.TW", "tw_g2"), config("^TWII", "admin_index")]
        settings = custom(**{"group:tw_g1": {"ma": False}, f"watchlist:{LIST_ID}": {"ma": True}})
        self.assertEqual([(c["ticker"], c["group_id"]) for c in filter_ma_configs(configs, settings)],
                         [("2330.TW", f"watchlist_{LIST_ID}"), ("^TWII", "admin_index")])


class DrawingFilterTest(unittest.TestCase):
    stocks = [{"ticker": "2330", "group_id": "tw_g1"}, {"ticker": "AAPL", "group_id": "ungrouped"},
              {"ticker": "NVDA", "group_id": "us_g1"}]
    rows = [{"id": LIST_ID, "definition": {"kind": "manual", "symbols": ["TSLA"]}},
            {"id": "11111111-1111-4111-8111-111111111111",
             "definition": {"kind": "rules", "history": [{"members": [{"symbol": "META"}]}]}}]

    def test_all_lists_allows_every_drawing(self):
        self.assertIsNone(drawing_allowed_tickers(DEFAULT_SETTINGS, self.stocks, self.rows))
        watchlist = DrawingWatchlist(entries=[], row_count=0, valid_tickers=set())
        self.assertIs(filter_drawing_watchlist(watchlist, None), watchlist)

    def test_custom_allows_members_of_enabled_lists_only(self):
        settings = custom(**{"group:tw_g1": {"drawing": True}, "group:us_g1": {"ma": True},
                             f"watchlist:{LIST_ID}": {"drawing": True},
                             "watchlist:11111111-1111-4111-8111-111111111111": {"drawing": True}})
        allowed = drawing_allowed_tickers(settings, self.stocks, self.rows)
        self.assertEqual(allowed, {"2330.TW", "TSLA", "META"})
        entries = [WatchEntry(ticker=t, symbol=s, market="TW", lines=[1, 2]) for t, s in
                   (("2330.TW", "2330.TW"), ("NVDA", "NVDA"), ("AAPL", "AAPL"), ("TSLA", "TSLA"))]
        watchlist = DrawingWatchlist(entries=entries, row_count=4, valid_tickers={"2330.TW", "NVDA", "AAPL", "TSLA"})
        filtered = filter_drawing_watchlist(watchlist, allowed)
        self.assertEqual([e.symbol for e in filtered.entries], ["2330.TW", "TSLA"])
        self.assertEqual(filtered.line_count, 4)
        self.assertEqual(filtered.valid_tickers, watchlist.valid_tickers)


class StoreSettingsTest(unittest.TestCase):
    def store(self, response):
        from breakout_alert import supabase_store

        store = supabase_store.SupabaseStore.__new__(supabase_store.SupabaseStore)
        store.user_id = "U" + "0" * 32
        calls = []

        def request(method, table, **kwargs):
            calls.append(table)
            if isinstance(response, Exception):
                raise response
            return response

        store._request = request
        return store, calls

    def test_reads_one_row_and_fails_open_when_missing(self):
        from breakout_alert.supabase_store import SupabaseRequestError

        store, calls = self.store([{"line_user_id": "U" + "0" * 32,
                                    "settings": {"version": 1, "mode": "custom", "lists": {"group:tw_g1": {"ma": True}}}}])
        settings = store.get_list_notification_settings()
        self.assertFalse(settings.all_lists)
        self.assertEqual(calls, ["atlas_alert_settings"])
        for response in ([], [{"line_user_id": "Uother", "settings": {"version": 1, "mode": "custom", "lists": {}}}],
                         SupabaseRequestError("missing table", status_code=404)):
            self.assertTrue(self.store(response)[0].get_list_notification_settings().all_lists)

    def test_custom_ma_enables_a_watchlist_whose_own_switch_is_off(self):
        from breakout_alert import supabase_store

        definition = {"id": LIST_ID, "name": "觀察", "market": "US", "kind": "manual", "symbols": ["NVDA"],
                      "maList": [20], "monitorBreakouts": False}
        store, _ = self.store([{"id": LIST_ID, "line_user_id": "U" + "0" * 32, "definition": definition}])
        previous = os.environ.get("ATLAS_WATCHLIST_SYNC_ENABLED")
        os.environ["ATLAS_WATCHLIST_SYNC_ENABLED"] = "1"
        try:
            self.assertEqual(store.get_watchlist_monitor_configs(), [])
            configs = store.get_watchlist_monitor_configs(enabled_ids={LIST_ID})
            self.assertEqual([c["ticker"] for c in configs], ["NVDA"])
            self.assertEqual(len(store.watchlist_rows), 1)
        finally:
            if previous is None:
                os.environ.pop("ATLAS_WATCHLIST_SYNC_ENABLED", None)
            else:
                os.environ["ATLAS_WATCHLIST_SYNC_ENABLED"] = previous


if __name__ == "__main__":
    unittest.main()
