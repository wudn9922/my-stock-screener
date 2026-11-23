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
        self.run_sql("truncate public.atlas_statement_links, public.atlas_statement_trades, public.atlas_statement_messages, public.atlas_statement_snapshots;")

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

    def link(self, market="US", owner=OWNER, broker="cathay"):
        self.call(f"insert into public.atlas_statement_links(line_user_id,account_id,broker,market) values ('{owner}','{ACCOUNT}','{broker}','{market}');")

    def trade(self, key, side, quantity, price, amount, day="2026-11-19", symbol="UHS", owner=OWNER, broker="cathay", market="US"):
        currency = "TWD" if market == "TW" else "USD"
        self.call(
            "insert into public.atlas_statement_trades(line_user_id,broker,market,trade_key,trade_date,symbol,side,quantity,price,amount,currency,message_id) "
            f"values ('{owner}','{broker}','{market}','{key}','{day}','{symbol}','{side}',{quantity},{price},{amount},'{currency}','m1') on conflict do nothing;"
        )

    def snapshot(self, day, positions):
        body = json.dumps(positions)
        self.call("insert into public.atlas_statement_snapshots(line_user_id,broker,market,report_date,positions,message_id) "
                  f"values ('{OWNER}','sinopac','TW','{day}','{body}'::jsonb,'m1') on conflict do nothing;")

    def apply(self, owner=OWNER, role="service_role", success=True, broker="cathay", market="US"):
        result = self.call(f"select public.atlas_statement_apply('{owner}','{broker}','{market}');", role=role, success=success)
        return json.loads(result) if success else result

    def current(self):
        document = self.holdings("list", {})["document"]
        entry = next(e for e in document["accounts"] if e["account"]["id"] == ACCOUNT)
        return entry, {h["symbol"]: h for h in entry["holdings"]}

    def test_browsers_cannot_reach_tables_or_function(self):
        for role in ("anon", "authenticated"):
            self.assertIn("permission denied", self.apply(role=role, success=False))
            for table in ("links", "trades", "messages", "snapshots"):
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
        self.assertEqual(self.apply(), {"status": "ok", "applied": 3, "snapshots": 0, "missingCost": 0})
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

    def test_snapshot_sets_quantities_and_supersedes_older_items(self):
        manual = {"id": str(uuid.UUID(int=9, version=4)), "accountId": ACCOUNT, "symbol": "2330.TW", "market": "TW",
                  "quantity": "100", "averageCost": "500", "costCurrency": "TWD", "source": "manual"}
        sold = dict(manual, id=str(uuid.UUID(int=8, version=4)), symbol="2603.TW")
        self.account(kind="tw-broker", holdings=[manual, sold])
        self.link(market="TW", broker="sinopac")
        tw = {"broker": "sinopac", "market": "TW"}
        # 031863 bought today; the inventory also lists 2308 (no known cost) and a 2330 stock dividend; 2603 is gone.
        self.trade("2026-11-23:X-21JF:1", "buy", 14000, 0.11, 1542, day="2026-11-23", symbol="031863.TW", **tw)
        self.trade("2026-11-23:X-21JG:2", "sell", 300, 50, 14950, day="2026-11-23", symbol="2308.TW", **tw)  # covered by the snapshot
        self.snapshot("2026-11-23", {"031863.TW": "14000", "2308.TW": "30", "2330.TW": "110"})
        self.assertEqual(self.apply(**tw), {"status": "ok", "applied": 2, "snapshots": 1, "missingCost": 1})
        _, rows = self.current()
        self.assertEqual({k: float(v["quantity"]) for k, v in rows.items()}, {"031863.TW": 14000, "2308.TW": 30, "2330.TW": 110})
        self.assertAlmostEqual(float(rows["031863.TW"]["averageCost"]), 1542 / 14000, places=10)
        self.assertEqual(float(rows["2308.TW"]["averageCost"]), 0)
        self.assertAlmostEqual(float(rows["2330.TW"]["averageCost"]), 50000 / 110, places=10)
        self.assertEqual((rows["2330.TW"]["id"], rows["2330.TW"]["source"]), (manual["id"], "manual"))
        # A late, re-sent older report is already reflected in the newer snapshot.
        self.trade("2026-11-20:Y-1:1", "buy", 1000, 10, 10020, day="2026-11-20", symbol="2884.TW", **tw)
        self.snapshot("2026-11-20", {"2884.TW": "1000"})
        self.assertEqual(self.apply(**tw)["status"], "ok")
        self.assertEqual(set(self.current()[1]), {"031863.TW", "2308.TW", "2330.TW"})
        self.assertEqual(self.call("select count(*) from public.atlas_statement_snapshots where applied_at is null;"), "0")

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
