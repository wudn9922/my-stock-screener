"""Exercise atlas_statement_sync.sql on a disposable native PostgreSQL cluster.

Run: ATLAS_PG_BIN=/path/to/postgres/bin python supabase/tests/test_statement_sync_sql.py -v
Skips when PostgreSQL binaries are unavailable (same fixture as the storage-policy suite).
"""

import json
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_account_storage_permissions import OWNER, USER, _PostgresFixture  # noqa: E402

MIGRATION = Path(__file__).resolve().parents[1] / "manual/atlas_statement_sync.sql"
ACCOUNT = str(uuid.UUID(int=1, version=4))


class StatementSyncSqlTests(_PostgresFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.run_sql(MIGRATION.read_text())

    def setUp(self):
        super().setUp()
        self.run_sql("truncate public.atlas_statement_links, public.atlas_statement_trades, public.atlas_statement_messages;")

    def call(self, sql, role="service_role", success=True):
        result = self.run_sql(f"set role {role}; {sql}", success=success)
        return result.splitlines()[-1] if success else result

    def holdings(self, operation, payload, revision=None, owner=OWNER):
        body = json.dumps(payload).replace("'", "''")
        rev = "null" if revision is None else str(revision)
        return json.loads(self.call(f"select public.atlas_holdings_action('{operation}','{owner}','{body}'::jsonb,{rev});"))

    def account(self, kind="us-broker", holdings=(), revision=None):
        currency = {"us-broker": "USD", "tw-broker": "TWD"}[kind]
        definition = {"id": ACCOUNT, "name": "國泰美股", "kind": kind, "currency": currency, "broker": "cathay"}
        return self.holdings("replaceAccount", {"account": definition, "holdings": list(holdings)}, revision)

    def link(self, market="US", owner=OWNER):
        self.call(f"insert into public.atlas_statement_links(line_user_id,account_id,broker,market) values ('{owner}','{ACCOUNT}','cathay','{market}');")

    def trade(self, key, side, quantity, price, amount, day="2026-11-19", symbol="UHS", owner=OWNER):
        self.call(
            "insert into public.atlas_statement_trades(line_user_id,broker,market,trade_key,trade_date,symbol,side,quantity,price,amount,currency,message_id) "
            f"values ('{owner}','cathay','US','{key}','{day}','{symbol}','{side}',{quantity},{price},{amount},'USD','m1') on conflict do nothing;"
        )

    def apply(self, owner=OWNER, role="service_role", success=True):
        result = self.call(f"select public.atlas_statement_apply('{owner}','cathay','US');", role=role, success=success)
        return json.loads(result) if success else result

    def current(self):
        document = self.holdings("list", {})["document"]
        entry = next(e for e in document["accounts"] if e["account"]["id"] == ACCOUNT)
        return entry, {h["symbol"]: h for h in entry["holdings"]}

    def test_browsers_cannot_reach_tables_or_function(self):
        for role in ("anon", "authenticated"):
            self.assertIn("permission denied", self.apply(role=role, success=False))
            for table in ("links", "trades", "messages"):
                self.assertIn("permission denied", self.call(f"select count(*) from public.atlas_statement_{table};", role=role, success=False))

    def test_unlinked_or_missing_account_keeps_trades_pending(self):
        self.trade("2026-11-19:00000001", "buy", 2, 100, 200.3)
        self.assertEqual(self.apply()["status"], "no-account")
        self.link()
        self.assertEqual(self.apply()["status"], "no-account")  # linked, but the account was never created
        self.assertEqual(self.call("select count(*) from public.atlas_statement_trades where applied_at is null;"), "1")

    def test_buys_and_sells_update_weighted_cost_once(self):
        self.account()
        self.link()
        self.trade("2026-11-18:00000001", "buy", 4, 60, 240.4, day="2026-11-18")
        self.trade("2026-11-18:00000002", "buy", 2, 70, 140.2, day="2026-11-18")
        self.trade("2026-11-19:00000003", "sell", 3, 80, 239.8)
        self.assertEqual(self.apply(), {"status": "ok", "applied": 3})
        entry, rows = self.current()
        self.assertEqual(entry["revision"], 2)  # creating the account was revision 1
        self.assertEqual(float(rows["UHS"]["quantity"]), 3)
        self.assertAlmostEqual(float(rows["UHS"]["averageCost"]), 380.6 / 6, places=10)  # sell keeps the average
        self.assertEqual(rows["UHS"]["source"], "api")
        # A second run, or a re-sent e-mail with the same trade keys, changes nothing.
        self.trade("2026-11-19:00000003", "sell", 3, 80, 239.8)
        self.assertEqual(self.apply(), {"status": "ok", "applied": 0})
        self.assertEqual(self.current()[0]["revision"], 2)

    def test_selling_everything_removes_the_holding_and_keeps_manual_rows(self):
        manual = {"id": str(uuid.UUID(int=9, version=4)), "accountId": ACCOUNT, "symbol": "AAPL", "market": "US",
                  "quantity": "5", "averageCost": "150", "costCurrency": "USD", "source": "manual"}
        self.account(holdings=[manual])
        self.link()
        self.trade("2026-11-18:00000001", "buy", 1, 180, 180.15, day="2026-11-18")
        self.trade("2026-11-19:00000002", "sell", 1, 181.3152, 181.16)
        self.trade("2026-11-19:00000003", "buy", 1, 200, 200.2, symbol="AAPL")
        self.assertEqual(self.apply()["status"], "ok")
        _, rows = self.current()
        self.assertEqual(set(rows), {"AAPL"})
        self.assertEqual(rows["AAPL"]["id"], manual["id"])
        self.assertEqual(rows["AAPL"]["source"], "manual")
        self.assertAlmostEqual(float(rows["AAPL"]["averageCost"]), (750 + 200.2) / 6, places=10)

    def test_oversell_writes_nothing(self):
        self.account()
        self.link()
        self.trade("2026-11-19:00000001", "sell", 1, 181, 181.16)
        result = self.apply()
        self.assertEqual(result["status"], "oversell")
        self.assertEqual(result["symbol"], "UHS")
        self.assertEqual(self.current()[0]["revision"], 1)
        self.assertEqual(self.call("select count(*) from public.atlas_statement_trades where applied_at is null;"), "1")

    def test_market_mismatch_and_revoked_cloud_grant_are_refused(self):
        self.account(kind="tw-broker")
        self.link()
        self.trade("2026-11-19:00000001", "buy", 1, 10, 10)
        self.assertEqual(self.apply()["status"], "no-account")
        self.link(owner=USER)  # a non-owner without a cloud grant
        self.trade("2026-11-19:00000001", "buy", 1, 10, 10, owner=USER)
        self.assertEqual(self.apply(owner=USER)["status"], "forbidden")


if __name__ == "__main__":
    unittest.main()
