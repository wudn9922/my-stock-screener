import unittest
from types import SimpleNamespace as NS
from pathlib import Path
from unittest.mock import Mock
from sinopac_sync.core import convert, read_positions, synchronize, SyncError

OWNER = "U" + "0" * 32
ACCOUNT = "20000000-0000-4000-8000-000000000001"
CAT = {"2330": "2330.TW", "6488": "6488.TWO"}


def row(code="2330", quantity=1, price="100", direction="Buy", cond="Cash"):
    return dict(
        code=code, quantity=quantity, price=price, direction=direction, cond=cond
    )


class Tests(unittest.TestCase):
    def test_merge_decimal(self):
        value, skips = convert(
            [row(price="0.1")], [row(quantity=250, price="0.2")], CAT
        )
        self.assertEqual(
            value, [{"symbol": "2330.TW", "quantity": "1250", "averageCost": "0.12"}]
        )
        self.assertEqual(skips, 0)

    def test_short_margin_otc(self):
        value, skips = convert(
            [
                row(direction="Sell"),
                row(cond="ShortSelling"),
                row(code="6488", cond="MarginTrading"),
            ],
            [],
            CAT,
        )
        self.assertEqual(skips, 2)
        self.assertEqual(value[0]["symbol"], "6488.TWO")

    def test_empty(self):
        self.assertEqual(convert([], [], CAT), ([], 0))

    def test_bad_symbol_does_not_drop(self):
        with self.assertRaises(SyncError):
            convert([row(code="9999")], [], CAT)

    def test_invalid_numerics(self):
        for value in ["NaN", "Infinity", "-1"]:
            with self.subTest(value=value), self.assertRaises(SyncError):
                convert([row(price=value)], [], CAT)

    def test_login_failure_logout(self):
        api = Mock()
        api.login.side_effect = RuntimeError("SENSITIVE")
        with self.assertRaisesRegex(SyncError, "登入失敗"):
            read_positions(lambda: api, NS(Common=1, Share=2), "fake", "fake")
        api.logout.assert_called_once()

    def test_success_logout_units(self):
        api = Mock()
        api.list_positions.side_effect = [[], []]
        self.assertEqual(
            read_positions(lambda: api, NS(Common=1, Share=2), "fake", "fake"), ([], [])
        )
        self.assertEqual(
            [c.kwargs["unit"] for c in api.list_positions.call_args_list], [1, 2]
        )
        api.logout.assert_called_once()
        self.assertFalse(api.login.call_args.kwargs["subscribe_trade"])

    def test_timeout_and_permission_logout(self):
        for error, code in [
            (TimeoutError("SECRET"), "timeout"),
            (PermissionError("SECRET"), "permission"),
        ]:
            api = Mock()
            api.list_positions.side_effect = error
            with self.assertRaises(SyncError) as e:
                read_positions(lambda: api, NS(Common=1, Share=2), "fake", "fake")
            self.assertEqual(e.exception.code, code)
            api.logout.assert_called_once()

    def sync(self, repo, fetch, caller=OWNER, date="2026-11-09"):
        return synchronize(
            repo,
            OWNER,
            ACCOUNT,
            caller,
            date,
            {
                "validFrom": "2026-01-01",
                "validTo": "2026-12-31",
                "markets": {"TW": {"sessions": {"2026-11-09": {}}}},
            },
            fetch,
            CAT,
            "2026-11-09T09:00:00+08:00",
        )

    def test_unchanged_no_write(self):
        repo = Mock()
        repo.claim.return_value = True
        repo.list.return_value = {
            "accounts": [
                {
                    "account": {
                        "id": ACCOUNT,
                        "name": "永豐",
                        "broker": "sinopac",
                        "kind": "tw-broker",
                        "currency": "TWD",
                    },
                    "revision": 4,
                    "holdings": [
                        {
                            "symbol": "2330.TW",
                            "quantity": "1000",
                            "averageCost": "100.00",
                            "source": "api",
                        }
                    ],
                }
            ]
        }
        self.sync(repo, lambda: ([row()], []))
        repo.replace.assert_not_called()
        repo.finish.assert_called_once()

    def test_changed_and_empty_write_cas(self):
        for positions in [([row()], []), ([], [])]:
            repo = Mock()
            repo.claim.return_value = True
            repo.list.return_value = {"accounts": []}
            self.sync(repo, lambda: positions)
            self.assertIsNone(repo.replace.call_args.args[-1])
            repo.replace.assert_called_once()

    def test_owner_before_claim(self):
        repo = Mock()
        fetch = Mock()
        with self.assertRaisesRegex(SyncError, "權限"):
            self.sync(repo, fetch, caller="other")
        repo.claim.assert_not_called()
        fetch.assert_not_called()

    def test_closed_no_login(self):
        repo = Mock()
        fetch = Mock()
        self.sync(repo, fetch, date="2026-11-08")
        fetch.assert_not_called()

    def test_rate_no_login(self):
        repo = Mock()
        repo.claim.return_value = False
        fetch = Mock()
        with self.assertRaisesRegex(SyncError, "60 秒"):
            self.sync(repo, fetch)
        fetch.assert_not_called()

    def test_conflict_not_retry(self):
        repo = Mock()
        repo.claim.return_value = True
        repo.list.return_value = {"accounts": []}
        repo.replace.side_effect = SyncError("conflict")
        with self.assertRaisesRegex(SyncError, "更新"):
            self.sync(repo, lambda: ([row()], []))
        repo.replace.assert_called_once()

    def test_no_trading_surface(self):
        source = "\n".join(p.read_text() for p in Path("sinopac_sync").glob("*.py"))
        for name in [
            "place_order",
            "cancel_order",
            "update_order",
            "activate_ca",
            "api.contracts",
        ]:
            self.assertNotIn(name, source)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sinopac_sync import app

        cls.module = app

    def test_anonymous_and_nonowner_never_connect(self):
        from unittest.mock import patch

        config = {k: "fake" for k in self.module.CONFIG}
        config.update(
            SINOPAC_SHARED_SECRET="x" * 48,
            SINOPAC_OWNER_ID=OWNER,
            SINOPAC_ACCOUNT_ID=ACCOUNT,
        )
        with patch.dict(self.module.CONFIG, config), patch.object(
            self.module, "positions"
        ) as connect:
            client = self.module.app.test_client()
            self.assertEqual(
                client.post(
                    "/sync", json={"ownerId": OWNER, "accountId": ACCOUNT}
                ).status_code,
                403,
            )
            self.assertEqual(
                client.post(
                    "/sync",
                    headers={"X-Sinopac-Secret": "x" * 48},
                    json={"ownerId": "other", "accountId": ACCOUNT},
                ).status_code,
                403,
            )
            connect.assert_not_called()


class Boundaries(unittest.TestCase):
    def test_quota_uses_status_code_without_error_text(self):
        error = RuntimeError("PRIVATE")
        error.status_code = 429
        api = Mock()
        api.list_positions.side_effect = error
        with self.assertRaises(SyncError) as caught:
            read_positions(lambda: api, NS(Common=1, Share=2), "fake", "fake")
        self.assertEqual(caught.exception.code, "quota")
        self.assertNotIn("PRIVATE", str(caught.exception))
        api.logout.assert_called_once()

    def test_second_query_failure_and_logout_failure_prevent_writes(self):
        api = Mock()
        api.list_positions.side_effect = [[], TimeoutError("PRIVATE")]
        with self.assertRaisesRegex(SyncError, "逾時"):
            read_positions(lambda: api, NS(Common=1, Share=2), "fake", "fake")
        api.logout.assert_called_once()
        api = Mock()
        api.list_positions.side_effect = [[], []]
        api.logout.side_effect = RuntimeError("PRIVATE")
        with self.assertRaisesRegex(SyncError, "暫時"):
            read_positions(lambda: api, NS(Common=1, Share=2), "fake", "fake")

    def test_stale_calendar_and_wrong_account_never_connect(self):
        t = Tests()
        repo = Mock()
        repo.claim.return_value = True
        repo.list.return_value = {
            "accounts": [{"account": {"id": ACCOUNT, "broker": "other"}}]
        }
        fetch = Mock()
        for date, code in [("2027-11-09", "calendar"), ("2026-11-09", "forbidden")]:
            with self.assertRaises(SyncError) as caught:
                t.sync(repo, fetch, date=date)
            self.assertEqual(caught.exception.code, code)
        fetch.assert_not_called()

    def test_existing_empty_portfolio_clears_only_at_expected_revision(self):
        repo = Mock()
        repo.claim.return_value = True
        repo.list.return_value = {
            "accounts": [
                {
                    "account": {
                        "id": ACCOUNT,
                        "broker": "sinopac",
                        "kind": "tw-broker",
                        "currency": "TWD",
                    },
                    "revision": 7,
                    "holdings": [
                        {
                            "symbol": "2330.TW",
                            "quantity": "1000",
                            "averageCost": "100",
                            "source": "api",
                        }
                    ],
                }
            ]
        }
        Tests().sync(repo, lambda: ([], []))
        self.assertEqual(repo.replace.call_args.args[2:], ([], 7))

    def test_repository_uses_existing_rpc_and_sanitizes_transport_errors(self):
        from sinopac_sync.repository import Repository
        from unittest.mock import patch

        repo = Repository("https://fake.test", "fake-key")
        with patch("sinopac_sync.repository.requests.post") as post:
            post.return_value.ok = True
            post.return_value.json.return_value = {"status": "conflict"}
            with self.assertRaises(SyncError) as caught:
                repo.replace(OWNER, {"id": ACCOUNT}, [], 9)
            self.assertEqual(caught.exception.code, "conflict")
            self.assertEqual(post.call_args.kwargs["json"]["expected_revision"], 9)
            post.side_effect = RuntimeError("PRIVATE")
            with self.assertRaisesRegex(SyncError, "暫時"):
                repo.list(OWNER)

    def test_void_finish_rpc_accepts_204_no_content(self):
        from sinopac_sync.repository import Repository
        from unittest.mock import patch

        repo = Repository("https://fake.test", "fake-key")
        with patch("sinopac_sync.repository.requests.post") as post:
            post.return_value.ok = True
            post.return_value.status_code = 204
            post.return_value.content = b""
            post.return_value.json.side_effect = ValueError("no body")
            repo.finish(OWNER, ACCOUNT, "ok", 0, True)
            self.assertEqual(post.call_args.args[0], "https://fake.test/rest/v1/rpc/atlas_sinopac_finish")

    def test_http_authenticated_rate_limited_request_never_imports_sdk(self):
        from sinopac_sync import app
        from unittest.mock import patch

        config = {k: "fake" for k in app.CONFIG}
        config.update(
            SINOPAC_SHARED_SECRET="x" * 48,
            SINOPAC_OWNER_ID=OWNER,
            SINOPAC_ACCOUNT_ID=ACCOUNT,
        )
        repo = Mock()
        repo.claim.return_value = False
        with patch.dict(app.CONFIG, config), patch.object(
            app.json, "load", return_value={}
        ), patch("builtins.open", Mock()), patch.object(
            app, "Repository", return_value=repo
        ), patch.object(
            app, "positions"
        ) as connect:
            result = app.app.test_client().post(
                "/sync",
                headers={"X-Sinopac-Secret": "x" * 48},
                json={"ownerId": OWNER, "accountId": ACCOUNT},
            )
            self.assertEqual(result.status_code, 429)
            self.assertEqual(result.json["code"], "rate")
            connect.assert_not_called()


class AdapterTests(unittest.TestCase):
    def test_actual_adapter_only_calls_fake_sdk_and_suppresses_its_output(self):
        import sys, io, contextlib
        from unittest.mock import patch
        from sinopac_sync import app

        api = Mock()
        api.list_positions.side_effect = [[], []]
        api.login.side_effect = lambda **kwargs: print("PRIVATE broker account / key")
        sdk = NS(Shioaji=Mock(return_value=api), Unit=NS(Common=1, Share=2))
        capture = io.StringIO()
        with patch.dict(sys.modules, {"shioaji": sdk}), contextlib.redirect_stdout(
            capture
        ):
            self.assertEqual(app.positions(), ([], []))
        self.assertEqual(capture.getvalue(), "")
        sdk.Shioaji.assert_called_once_with(simulation=False)
        api.logout.assert_called_once()

    def test_logout_false_rejects_success_but_does_not_hide_login_failure(self):
        api = Mock()
        api.list_positions.side_effect = [[], []]
        api.logout.return_value = False
        with self.assertRaisesRegex(SyncError, "暫時"):
            read_positions(lambda: api, NS(Common=1, Share=2), "fake", "fake")
        api.login.side_effect = RuntimeError("PRIVATE")
        api.logout.side_effect = RuntimeError("PRIVATE logout")
        with self.assertRaisesRegex(SyncError, "登入"):
            read_positions(lambda: api, NS(Common=1, Share=2), "fake", "fake")

    def test_cross_caller_sixty_second_claim_is_shared(self):
        repo = Mock()
        clock = [0]
        last = [-60]

        def claim(*_):
            if clock[0] - last[0] < 60:
                return False
            last[0] = clock[0]
            return True

        repo.claim.side_effect = claim
        repo.list.return_value = {"accounts": []}
        fetch = Mock(return_value=([], []))
        t = Tests()
        t.sync(repo, fetch)
        clock[0] = 59
        with self.assertRaisesRegex(SyncError, "60 秒"):
            t.sync(repo, fetch)
        clock[0] = 60
        t.sync(repo, fetch)
        self.assertEqual(fetch.call_count, 2)


class PolicyTests(unittest.TestCase):
    def test_revoked_storage_claim_rejects_before_broker_or_private_reads(self):
        repo = Mock()
        repo.claim.side_effect = SyncError("forbidden")
        fetch = Mock()
        with self.assertRaisesRegex(SyncError, "權限"):
            Tests().sync(repo, fetch)
        fetch.assert_not_called()
        repo.list.assert_not_called()
        repo.replace.assert_not_called()

    def test_revision_write_wrapper_preserves_revocation_and_expected_revision(self):
        from sinopac_sync.repository import Repository
        from unittest.mock import patch

        repo = Repository("https://fake.test", "fake-key")
        with patch.object(repo, "rpc", return_value={"status": "forbidden"}) as rpc:
            with self.assertRaisesRegex(SyncError, "權限"):
                repo.replace(OWNER, {"id": ACCOUNT}, [], 8)
            self.assertEqual(rpc.call_args.args[0], "atlas_sinopac_replace")
            self.assertEqual(rpc.call_args.args[1]["expected_revision"], 8)

    def test_claim_result_maps_cross_instance_rate_and_revoked_policy(self):
        from sinopac_sync.repository import Repository
        from unittest.mock import patch

        repo = Repository("https://fake.test", "fake-key")
        for status, expected in [("ok", True), ("rate", False)]:
            with patch.object(repo, "rpc", return_value={"status": status}):
                self.assertEqual(repo.claim(OWNER, ACCOUNT), expected)
        with patch.object(repo, "rpc", return_value={"status": "forbidden"}):
            with self.assertRaisesRegex(SyncError, "權限"):
                repo.claim(OWNER, ACCOUNT)


if __name__ == "__main__":
    unittest.main()
