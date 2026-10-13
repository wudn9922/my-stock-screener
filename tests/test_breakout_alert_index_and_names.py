"""
大盤指數提醒（突破 0.3%／跌破 0.5%）與台股中文名稱補齊的單元測試。

不連網、不需要 Supabase 或 Discord：
  python -m unittest tests.test_breakout_alert_index_and_names -v
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")
os.environ.setdefault("SUPABASE_USER_ID", "U-test")
os.environ.setdefault(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1/test",
)

from breakout_alert import tw_names  # noqa: E402
from breakout_alert.breakout_engine import (  # noqa: E402
    BreakoutEngine,
    determine_price_side,
    get_config_ratios,
)
from breakout_alert.discord_notifier import (  # noqa: E402
    DiscordNotifier,
    format_ratio_percent,
)
from breakout_alert.price_provider import PriceQuote  # noqa: E402
from breakout_alert.supabase_store import SupabaseStore  # noqa: E402


INDEX_RATIOS = (0.003, 0.005)


class ThresholdTests(unittest.TestCase):
    def test_stock_defaults_unchanged(self):
        self.assertEqual(determine_price_side(101.0, 100.0), "above")
        self.assertEqual(determine_price_side(100.99, 100.0), "equal")
        self.assertEqual(determine_price_side(98.5, 100.0), "below")
        self.assertEqual(determine_price_side(98.51, 100.0), "equal")

    def test_index_thresholds(self):
        up, down = INDEX_RATIOS
        self.assertEqual(determine_price_side(100.3, 100.0, up, down), "above")
        self.assertEqual(determine_price_side(100.29, 100.0, up, down), "equal")
        self.assertEqual(determine_price_side(99.5, 100.0, up, down), "below")
        self.assertEqual(determine_price_side(99.51, 100.0, up, down), "equal")

    def test_config_ratios(self):
        self.assertEqual(get_config_ratios({}), (0.01, 0.015))
        self.assertEqual(
            get_config_ratios(
                {"breakout_up_ratio": 0.003, "breakout_down_ratio": 0.005}
            ),
            INDEX_RATIOS,
        )
        # 無效值退回預設
        self.assertEqual(
            get_config_ratios(
                {"breakout_up_ratio": -1, "breakout_down_ratio": "x"}
            ),
            (0.01, 0.015),
        )

    def test_percent_text(self):
        self.assertEqual(format_ratio_percent(0.01), "1%")
        self.assertEqual(format_ratio_percent(0.015), "1.5%")
        self.assertEqual(format_ratio_percent(0.003), "0.3%")
        self.assertEqual(format_ratio_percent(0.005), "0.5%")


class FakeResponse:
    def __init__(self, status_code=200, content=b"", payload=None):
        self.status_code = status_code
        self.content = content
        self._payload = payload

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, twse_ok=True, tpex_ok=True):
        self.twse_ok = twse_ok
        self.tpex_ok = tpex_ok
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        if "STOCK_DAY_ALL" in url:
            if not self.twse_ok:
                return FakeResponse(503)
            csv_text = (
                "﻿證券代號,證券名稱,成交股數\n"
                "2330,台積電,1000\n"
                "0050,元大台灣50,500\n"
            )
            return FakeResponse(content=csv_text.encode("utf-8"))
        if not self.tpex_ok:
            return FakeResponse(503)
        return FakeResponse(
            payload=[
                {"SecuritiesCompanyCode": "6488", "CompanyName": "環球晶"},
                {"SecuritiesCompanyCode": "bad"},
            ]
        )


def quote(ticker, market="TW", name="", source="yahoo_1m"):
    return PriceQuote(
        ticker=ticker,
        market=market,
        price=100.0,
        source=source,
        quoted_at="2026-10-13T00:00:00+00:00",
        display_name=name,
    )


class TwNamesTests(unittest.TestCase):
    def setUp(self):
        tw_names._names.clear()
        tw_names._next_refresh_at = 0.0

    def test_fill_missing_names(self):
        http = FakeHttp()
        quotes = {
            "2330.TW": quote("2330.TW"),
            "0050.TW": quote("0050.TW"),
            "6488.TWO": quote("6488.TWO"),
            "2317.TW": quote("2317.TW", name="鴻海"),
            "^TWII": quote("^TWII"),
            "AAPL": quote("AAPL", market="US"),
        }
        filled = tw_names.fill_missing_names(http, quotes)
        self.assertEqual(filled["2330.TW"].display_name, "台積電")
        self.assertEqual(filled["0050.TW"].display_name, "元大台灣50")
        self.assertEqual(filled["6488.TWO"].display_name, "環球晶")
        self.assertEqual(filled["2317.TW"].display_name, "鴻海")  # 不覆蓋
        self.assertEqual(filled["^TWII"].display_name, "")
        self.assertEqual(filled["AAPL"].display_name, "")
        # 原物件不被修改
        self.assertEqual(quotes["2330.TW"].display_name, "")

    def test_no_fetch_when_nothing_missing(self):
        http = FakeHttp()
        quotes = {"2317.TW": quote("2317.TW", name="鴻海")}
        tw_names.fill_missing_names(http, quotes)
        self.assertEqual(http.calls, 0)

    def test_index_only_does_not_fetch(self):
        http = FakeHttp()
        tw_names.fill_missing_names(http, {"^TWII": quote("^TWII")})
        self.assertEqual(http.calls, 0)

    def test_cache_prevents_refetch(self):
        http = FakeHttp()
        tw_names.fill_missing_names(http, {"2330.TW": quote("2330.TW")})
        first = http.calls
        tw_names.fill_missing_names(http, {"2330.TW": quote("2330.TW")})
        self.assertEqual(http.calls, first)

    def test_one_source_down_keeps_other(self):
        http = FakeHttp(tpex_ok=False)
        filled = tw_names.fill_missing_names(
            http,
            {"2330.TW": quote("2330.TW"), "6488.TWO": quote("6488.TWO")},
        )
        self.assertEqual(filled["2330.TW"].display_name, "台積電")
        self.assertEqual(filled["6488.TWO"].display_name, "")

    def test_all_down_does_not_raise(self):
        http = FakeHttp(twse_ok=False, tpex_ok=False)
        quotes = {"2330.TW": quote("2330.TW")}
        filled = tw_names.fill_missing_names(http, quotes)
        self.assertEqual(filled["2330.TW"].display_name, "")


class IndexConfigTests(unittest.TestCase):
    def make_store(self, rows=None, error=None):
        store = SupabaseStore.__new__(SupabaseStore)
        store.user_id = "U-test"
        store.index_configs_loaded = False
        calls = []

        def fake_request(method, table, **kwargs):
            calls.append((method, table, kwargs))
            if error and table == "index_configs":
                raise error
            if table == "index_configs":
                return rows
            return None

        store._request = fake_request
        store.calls = calls
        return store

    ROWS = [
        {"ticker": "^TWII", "name": "台灣加權指數",
         "ma1": 23, "ma2": 29, "ma3": 62, "ma4": 0},
        {"ticker": "^GSPC", "name": "美國標普500",
         "ma1": 23, "ma2": 60, "ma3": None, "ma4": None},
        {"ticker": "^RUT", "name": "羅素2000",
         "ma1": 23, "ma2": 56, "ma3": 0, "ma4": 0},
        {"ticker": "^N225", "name": "日本日經225",
         "ma1": 24, "ma2": None, "ma3": None, "ma4": None},
        {"ticker": "^IXIC", "name": "美國那斯達克",
         "ma1": None, "ma2": None, "ma3": None, "ma4": None},
    ]

    def test_index_configs(self):
        store = self.make_store(rows=self.ROWS)
        configs = store.get_index_monitor_configs()
        by_ticker = {c["ticker"]: c for c in configs}
        self.assertTrue(store.index_configs_loaded)
        # 日經沒有交易時段支援、那斯達克沒有均線 → 不納入
        self.assertEqual(set(by_ticker), {"^TWII", "^GSPC", "^RUT"})
        self.assertEqual(by_ticker["^TWII"]["market"], "TW")
        self.assertEqual(by_ticker["^TWII"]["ma_list"], [23, 29, 62])
        self.assertEqual(by_ticker["^GSPC"]["market"], "US")
        self.assertEqual(by_ticker["^RUT"]["ma_list"], [23, 56])
        for config in configs:
            self.assertEqual(config["group_id"], "admin_index")
            self.assertEqual(config["group_name"], "大盤指數")
            self.assertEqual(config["breakout_up_ratio"], 0.003)
            self.assertEqual(config["breakout_down_ratio"], 0.005)
        self.assertEqual(by_ticker["^TWII"]["display_name"], "台灣加權指數")

    def test_load_failure_returns_empty_and_flags(self):
        store = self.make_store(error=RuntimeError("boom"))
        self.assertEqual(store.get_index_monitor_configs(), [])
        self.assertFalse(store.index_configs_loaded)

    def prune(self, store, state_rows, configs):
        deleted = []

        def fake_request(method, table, **kwargs):
            if method == "GET":
                return state_rows
            deleted.append(kwargs["params"]["id"])
            return None

        store._request = fake_request
        store.prune_orphan_states(configs)
        return deleted

    def state_rows(self):
        return [
            {"id": i, "group_id": g, "ticker": t, "ma_period": m}
            for i, (g, t, m) in enumerate(
                [
                    ("tw_g1", "2330.TW", 5),
                    ("tw_g1", "2330.TW", 10),
                    ("tw_g1", "2317.TW", 5),
                    ("admin_index", "^TWII", 23),
                    ("admin_index", "^GSPC", 23),
                    ("tw_g1", "9999.TW", 5),  # 孤兒
                ],
                start=1,
            )
        ]

    def stock_configs(self):
        return [
            {"group_id": "tw_g1", "ticker": "2330.TW", "ma_list": [5, 10]},
            {"group_id": "tw_g1", "ticker": "2317.TW", "ma_list": [5]},
        ]

    def test_prune_keeps_index_rows_when_not_loaded(self):
        store = self.make_store(rows=[])
        store.index_configs_loaded = False
        deleted = self.prune(store, self.state_rows(), self.stock_configs())
        self.assertEqual(deleted, ["in.(6)"])  # 只刪真正的孤兒

    def test_empty_stock_configs_never_prune_even_with_index_configs(self):
        store = self.make_store(rows=[])
        store.index_configs_loaded = True
        configs = [
            {"group_id": "admin_index", "ticker": "^TWII", "ma_list": [23]},
        ]
        deleted = self.prune(store, self.state_rows(), configs)
        self.assertEqual(deleted, [])

    def test_guards_are_separate_for_stocks_and_indices(self):
        store = self.make_store(rows=[])
        store.index_configs_loaded = True
        rows = [
            {"id": i, "group_id": "tw_g1", "ticker": f"{i}.TW", "ma_period": 5}
            for i in range(1, 7)
        ] + [
            {"id": 100 + i, "group_id": "admin_index",
             "ticker": f"^X{i}", "ma_period": 23}
            for i in range(1, 21)
        ]
        # 6 檔股票有 4 檔孤兒（67%）：股票端應略過；
        # 若與 20 列指數合併計算只有 15%，會被誤刪。
        configs = [
            {"group_id": "tw_g1", "ticker": "1.TW", "ma_list": [5]},
            {"group_id": "tw_g1", "ticker": "2.TW", "ma_list": [5]},
            {"group_id": "admin_index", "ticker": "^X1", "ma_list": [23]},
        ]
        # 另外 19 個指數狀態列也是孤兒（95%）→ 指數端也略過
        deleted = self.prune(store, rows, configs)
        self.assertEqual(deleted, [])

    def test_none_index_body_is_load_failure(self):
        store = self.make_store(rows=None)
        self.assertEqual(store.get_index_monitor_configs(), [])
        self.assertFalse(store.index_configs_loaded)

    def test_prune_keeps_index_rows_in_configs(self):
        store = self.make_store(rows=[])
        store.index_configs_loaded = True
        configs = self.stock_configs() + [
            {"group_id": "admin_index", "ticker": "^TWII", "ma_list": [23]},
            {"group_id": "admin_index", "ticker": "^GSPC", "ma_list": [23]},
        ]
        deleted = self.prune(store, self.state_rows(), configs)
        self.assertEqual(deleted, ["in.(6)"])


class DiscordQueryImageTests(unittest.TestCase):
    """Dockerfile.discord-query 只複製少數檔案，supabase_store 不能依賴其他模組。"""

    def test_store_imports_with_discord_query_files_only(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        copied = [
            "__init__.py",
            "config.py",
            "supabase_store.py",
            "cache_query.py",
            "discord_app.py",
        ]
        dockerfile = (root / "Dockerfile.discord-query").read_text(
            encoding="utf-8-sig"
        )
        for name in copied:
            self.assertIn(f"breakout_alert/{name}", dockerfile)

        with tempfile.TemporaryDirectory() as tmp:
            package = pathlib.Path(tmp) / "breakout_alert"
            package.mkdir()
            for name in copied:
                shutil.copy(root / "breakout_alert" / name, package / name)

            result = subprocess.run(
                [
                    sys.executable, "-I", "-c",
                    "import sys; sys.path.insert(0, sys.argv[1]);"
                    "import breakout_alert.supabase_store",
                    tmp,
                ],
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr)


class FakeStore:
    def __init__(self, previous_side="below"):
        self.states = {}
        self.previous_side = previous_side
        self.upserts = []

    def get_states_for_stock(self, group_id, ticker):
        return [
            {"ma_period": 23, "previous_side": self.previous_side}
        ]

    def upsert_alert_state(self, **kwargs):
        self.upserts.append(kwargs)

    def update_regular_volume_cache(self, **kwargs):
        pass


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send_breakout(self, **kwargs):
        self.sent.append(kwargs)
        return True


def analysis(ma=100.0):
    return types.SimpleNamespace(
        ma_values={"23": ma},
        volume_ratio=None,
        volume_source=None,
        current_session_date=None,
        previous_session_date=None,
        volume_cutoff_minute=None,
        current_cumulative_volume=None,
        previous_cumulative_volume=None,
    )


def decision():
    return types.SimpleNamespace(
        session_type="regular",
        session_date="2026-10-13",
        slot_key="k",
        interval_minutes=5,
    )


def index_config():
    return {
        "stock_id": None,
        "group_id": "admin_index",
        "raw_group_id": "admin_index",
        "group_name": "大盤指數",
        "ticker": "^TWII",
        "market": "TW",
        "ma_list": [23],
        "display_name": "台灣加權指數",
        "breakout_up_ratio": 0.003,
        "breakout_down_ratio": 0.005,
    }


class EngineIndexTests(unittest.TestCase):
    def run_engine(self, price, previous_side, config=None):
        store = FakeStore(previous_side)
        notifier = FakeNotifier()
        engine = BreakoutEngine(store=store, notifier=notifier)
        results = engine.process_config(
            config=config or index_config(),
            quote=PriceQuote(
                ticker="^TWII", market="TW", price=price,
                source="yahoo_1m", quoted_at="t", display_name="",
            ),
            analysis=analysis(),
            decision=decision(),
        )
        return results, store, notifier

    def test_breakout_up_at_0_3_percent(self):
        results, store, notifier = self.run_engine(100.3, "below")
        self.assertEqual(results[0].action, "alert_sent")
        sent = notifier.sent[0]
        self.assertEqual(sent["direction"], "breakout_up")
        self.assertEqual(sent["display_name"], "台灣加權指數")  # 指數名稱
        self.assertEqual((sent["up_ratio"], sent["down_ratio"]), INDEX_RATIOS)

    def test_no_breakout_below_0_3_percent(self):
        results, _, notifier = self.run_engine(100.29, "below")
        self.assertEqual(results[0].action, "unchanged")
        self.assertEqual(notifier.sent, [])

    def test_breakdown_at_0_5_percent(self):
        results, _, notifier = self.run_engine(99.5, "above")
        self.assertEqual(results[0].action, "alert_sent")
        self.assertEqual(notifier.sent[0]["direction"], "breakout_down")

    def test_no_breakdown_above_0_5_percent(self):
        results, _, notifier = self.run_engine(99.6, "above")
        self.assertEqual(results[0].action, "unchanged")

    def test_stock_config_keeps_old_thresholds(self):
        config = index_config()
        for key in ("breakout_up_ratio", "breakout_down_ratio", "display_name"):
            config.pop(key)
        results, _, _ = self.run_engine(100.5, "below", config)
        self.assertEqual(results[0].action, "unchanged")  # 0.5% < 1%

    def test_state_metadata_records_ratios(self):
        _, store, _ = self.run_engine(100.3, "below")
        meta = store.upserts[0]["metadata"]
        self.assertEqual(meta["breakout_up_ratio"], 0.003)
        self.assertEqual(meta["breakout_down_ratio"], 0.005)
        self.assertEqual(meta["display_name"], "台灣加權指數")


class NotifierTextTests(unittest.TestCase):
    def capture(self, direction, **ratios):
        notifier = DiscordNotifier()
        payloads = []

        class Resp:
            status_code = 204

        notifier.session.post = lambda url, json=None, timeout=None: (
            payloads.append(json) or Resp()
        )
        notifier.send_breakout(
            ticker="^TWII", display_name="台灣加權指數",
            group_name="大盤指數", ma_period=23, ma_value=100.0,
            ma_values={"23": 100.0}, price=100.4, volume_ratio=None,
            direction=direction, **ratios,
        )
        return payloads[0]["content"]

    def test_index_text(self):
        up = self.capture("breakout_up", up_ratio=0.003, down_ratio=0.005)
        down = self.capture("breakout_down", up_ratio=0.003, down_ratio=0.005)
        self.assertIn("向上突破 0.3%", up)
        self.assertIn("向下跌破 0.5%", down)
        self.assertIn("^TWII 台灣加權指數｜大盤指數", up)

    def test_stock_text_unchanged(self):
        self.assertIn("向上突破 1%", self.capture("breakout_up"))
        self.assertIn("向下跌破 1.5%", self.capture("breakout_down"))


if __name__ == "__main__":
    unittest.main()
