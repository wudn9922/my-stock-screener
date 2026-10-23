"""Offline checks for account consent and the scheduled/private/public boundary."""

import ast
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from breakout_alert.account_permissions import (
    DEFAULT_ADMIN_LINE_ID,
    CloudStorageDenied,
    is_public_report_owner,
    require_cloud_storage,
    storage_mode,
)
from breakout_alert.supabase_store import SupabaseStore

USER = "U11111111111111111111111111111111"
OTHER = "U22222222222222222222222222222222"
REST = "https://offline.invalid/rest/v1"
ROOT = Path(__file__).resolve().parents[1]


def response(body, status=200):
    return Mock(status_code=status, json=Mock(return_value=body), text=json.dumps(body))


def main_functions(*names, **namespace):
    """Run actual reporting functions without importing the network-heavy script."""
    source = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    nodes = [
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec"), namespace)
    return namespace


class StorageConsentTests(unittest.TestCase):
    def setUp(self):
        owner = patch.dict(os.environ, {"ADMIN_LINE_USER_ID": DEFAULT_ADMIN_LINE_ID})
        owner.start()
        self.addCleanup(owner.stop)

    def test_default_owner_only_and_no_automatic_enrollment(self):
        for user, expected in [(DEFAULT_ADMIN_LINE_ID, "cloud"), (USER, "none")]:
            with self.subTest(user=user):
                request = Mock(return_value=response([]))
                self.assertEqual(storage_mode(user, REST, request=request), expected)
                request.assert_called_once_with(
                    REST + "/atlas_storage_access",
                    headers={},
                    params={
                        "select": "line_user_id,storage_mode",
                        "line_user_id": f"eq.{user}",
                        "limit": "2",
                    },
                    timeout=15,
                )

    def test_explicit_policy_overrides_owner_default(self):
        for user in [USER, DEFAULT_ADMIN_LINE_ID]:
            for mode in ["none", "device", "cloud"]:
                with self.subTest(user=user, mode=mode):
                    request = Mock(
                        return_value=response(
                            [{"line_user_id": user, "storage_mode": mode}]
                        )
                    )
                    self.assertEqual(storage_mode(user, REST, request=request), mode)

    def test_schema_bootstrap_only_allows_owner(self):
        for code in ["42P01", "PGRST205"]:
            request = Mock(return_value=response({"code": code}, 404))
            self.assertEqual(
                storage_mode(DEFAULT_ADMIN_LINE_ID, REST, request=request), "cloud"
            )
            self.assertEqual(storage_mode(USER, REST, request=request), "none")

    def test_errors_and_malformed_or_foreign_policy_fail_closed(self):
        malformed = [
            None,
            {},
            {"storage_mode": "cloud"},
            [{"line_user_id": OTHER, "storage_mode": "cloud"}],
            [{"line_user_id": USER, "storage_mode": "cloud"}] * 2,
            [{"line_user_id": USER, "storage_mode": []}],
            [{"line_user_id": USER, "storage_mode": "unknown"}],
        ]
        for body in malformed:
            with self.subTest(body=body):
                self.assertEqual(
                    storage_mode(USER, REST, request=Mock(return_value=response(body))),
                    "none",
                )
        for status in [401, 403, 429, 500]:
            self.assertEqual(
                storage_mode(
                    DEFAULT_ADMIN_LINE_ID,
                    REST,
                    request=Mock(return_value=response({}, status)),
                ),
                "none",
            )
        for error in [
            requests.Timeout(),
            requests.ConnectionError(),
            ValueError("invalid JSON"),
        ]:
            self.assertEqual(
                storage_mode(
                    DEFAULT_ADMIN_LINE_ID, REST, request=Mock(side_effect=error)
                ),
                "none",
            )

    def test_invalid_identity_or_insecure_url_sends_no_request(self):
        request = Mock()
        for user in [None, "", "U-test", USER + "\n", {"id": USER}]:
            self.assertEqual(storage_mode(user, REST, request=request), "none")
        self.assertEqual(
            storage_mode(USER, "http://offline.invalid", request=request), "none"
        )
        request.assert_not_called()

    def test_denied_modes_cannot_be_used_as_cloud(self):
        for mode in ["device", "none"]:
            with self.assertRaises(CloudStorageDenied):
                require_cloud_storage(
                    USER,
                    REST,
                    request=Mock(
                        return_value=response(
                            [{"line_user_id": USER, "storage_mode": mode}]
                        )
                    ),
                )
        require_cloud_storage(
            USER,
            REST,
            request=Mock(
                return_value=response([{"line_user_id": USER, "storage_mode": "cloud"}])
            ),
        )

    def test_configured_owner_is_exact_and_valid(self):
        with patch.dict(os.environ, {"ADMIN_LINE_USER_ID": USER}):
            self.assertTrue(is_public_report_owner(USER))
            self.assertFalse(is_public_report_owner(DEFAULT_ADMIN_LINE_ID))
            self.assertFalse(is_public_report_owner(USER + "\n"))
        with patch.dict(os.environ, {"ADMIN_LINE_USER_ID": "not-a-line-id"}):
            self.assertFalse(is_public_report_owner("not-a-line-id"))


class ScheduledStoreConsentTests(unittest.TestCase):
    def make_store(self):
        store = SupabaseStore.__new__(SupabaseStore)
        store.user_id = USER
        store.rest_url = REST
        store.session = Mock(headers={"apikey": "offline-test-only"})
        store.session.request.return_value = response([])
        return store

    def test_none_and_device_do_not_touch_private_tables(self):
        for mode in ["none", "device"]:
            store = self.make_store()
            store.session.get.return_value = response(
                [{"line_user_id": USER, "storage_mode": mode}]
            )
            for method in ["GET", "POST", "PATCH", "DELETE"]:
                with self.assertRaises(CloudStorageDenied):
                    store._request(
                        method, "breakout_alert_state", json_body={"line_user_id": USER}
                    )
            store.session.request.assert_not_called()

    def test_every_operation_rechecks_revocation(self):
        store = self.make_store()
        store.session.get.side_effect = [
            response([{"line_user_id": USER, "storage_mode": "cloud"}]),
            response([{"line_user_id": USER, "storage_mode": "none"}]),
        ]
        self.assertEqual(store._request("GET", "stocks"), [])
        with self.assertRaises(CloudStorageDenied):
            store._request(
                "POST", "breakout_alert_state", json_body={"line_user_id": USER}
            )
        self.assertEqual(store.session.get.call_count, 2)
        self.assertEqual(store.session.request.call_count, 1)

    def test_groups_and_stocks_filter_in_query_and_response(self):
        store = self.make_store()
        own = {"id": 1, "line_user_id": USER}
        foreign = {"id": 2, "line_user_id": OTHER}
        store._request = Mock(return_value=[foreign, own, None])
        for read in [store.get_groups, store.get_user_stocks]:
            self.assertEqual(read(), [own])
            self.assertEqual(
                store._request.call_args.kwargs["params"]["line_user_id"], f"eq.{USER}"
            )


class PublicReportingPrivacyTests(unittest.TestCase):
    def setUp(self):
        owner = patch.dict(os.environ, {"ADMIN_LINE_USER_ID": DEFAULT_ADMIN_LINE_ID})
        owner.start()
        self.addCleanup(owner.stop)

    def test_writer_cannot_publish_another_accounts_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = main_functions(
                "write_report_json",
                REPORT_DIR=directory,
                is_public_report_owner=is_public_report_owner,
                os=os,
                write_json_atomic=Mock(),
            )["write_report_json"]
            for user in [None, USER, "U-test"]:
                with self.assertRaises(PermissionError):
                    writer({"private": True}, source_user_id=user)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_owner_writer_retains_atomic_output(self):
        with tempfile.TemporaryDirectory() as directory:
            write = Mock(
                side_effect=lambda path, value: Path(path).write_text(json.dumps(value))
            )
            writer = main_functions(
                "write_report_json",
                REPORT_DIR=directory,
                is_public_report_owner=is_public_report_owner,
                os=os,
                write_json_atomic=write,
            )["write_report_json"]
            writer({"date": "2026-10-23"}, source_user_id=DEFAULT_ADMIN_LINE_ID)
            write.assert_called_once()
            self.assertEqual(
                json.loads((Path(directory) / "latest.json").read_text()),
                {"date": "2026-10-23"},
            )

    def test_public_entrypoint_stops_before_any_foreign_account_read_or_output(self):
        loader = Mock(side_effect=AssertionError("must not read private settings"))
        main = main_functions(
            "main",
            os=os,
            is_public_report_owner=is_public_report_owner,
            load_configs_from_supabase=loader,
        )["main"]
        with patch.dict(
            os.environ,
            {"LINE_USER_ID": DEFAULT_ADMIN_LINE_ID, "SUPABASE_USER_ID": USER},
        ):
            main()
        loader.assert_not_called()

    def test_ungranted_config_loader_never_reads_groups_stocks_or_indices(self):
        request = Mock(side_effect=AssertionError("must not read private tables"))
        namespace = main_functions(
            "load_configs_from_supabase",
            requests=Mock(get=request),
            get_supabase_settings=lambda: (REST, "offline-test-only"),
            get_default_group_metadata=lambda: {"us_g1": {"ma_list": [20]}},
            get_default_index_configs=lambda: [{"ticker": "^GSPC", "ma1": 20}],
            storage_mode=lambda *args, **kwargs: "device",
        )
        configs, indices, metadata = namespace["load_configs_from_supabase"](USER)
        self.assertTrue(all(not tickers for tickers in configs.values()))
        self.assertEqual(metadata["us_g1"]["ma_list"], [20])
        self.assertEqual(indices[0]["ticker"], "^GSPC")
        request.assert_not_called()

    def test_foreign_fixed_group_cannot_change_owner_defaults(self):
        from breakout_alert.supabase_store import (
            fixed_parameter_map,
            get_ma_list,
            parameter_ticker_key,
        )

        request = Mock(
            side_effect=[
                response(
                    [
                        {
                            "id": 1,
                            "name": "美股-權值精選",
                            "line_user_id": USER,
                            "ma1": 20,
                        },
                        {
                            "id": 2,
                            "name": "美股-權值精選",
                            "line_user_id": OTHER,
                            "ma1": 999,
                        },
                    ]
                ),
                response(
                    [
                        {"ticker": "NVDA", "group_id": "us_g1", "line_user_id": USER},
                        {
                            "ticker": "AAPL",
                            "group_id": "us_g1",
                            "line_user_id": OTHER,
                            "ma1": 999,
                        },
                    ]
                ),
                response([]),
            ]
        )
        namespace = main_functions(
            "load_configs_from_supabase",
            requests=Mock(get=request),
            get_supabase_settings=lambda: (REST, "offline-test-only"),
            get_default_group_metadata=lambda: {
                "us_g1": {"ma_list": [20], "market": "us"}
            },
            get_default_index_configs=lambda: [],
            storage_mode=lambda *args, **kwargs: "cloud",
            normalize_group_name=lambda value: value,
            get_group_ma_list=get_ma_list,
            fixed_parameter_map=fixed_parameter_map,
            parameter_ticker_key=parameter_ticker_key,
            FIXED_STOCK_GROUP_ID="ungrouped",
            normalize_custom_ticker=lambda value: value,
            get_stock_ma_list=get_ma_list,
        )
        configs, _, metadata = namespace["load_configs_from_supabase"](USER)
        self.assertEqual(configs["us_g1"], {"NVDA": [20]})
        self.assertEqual(metadata["us_g1"]["ma_list"], [20])
        for call in request.call_args_list[:2]:
            self.assertEqual(call.kwargs["params"]["line_user_id"], f"eq.{USER}")


if __name__ == "__main__":
    unittest.main()
