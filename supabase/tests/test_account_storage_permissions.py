"""Exercise the manual storage-policy migration in a disposable native PostgreSQL cluster.

Run: ATLAS_PG_BIN=/path/to/postgres/bin python supabase/tests/test_account_storage_permissions.py -v
Requires PostgreSQL 15+ server/client binaries. No database URL, secrets or production database
are accepted: this suite creates its own trust-authenticated cluster under a temporary directory.
"""

import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

OWNER = "Ua0037dbd5037b64fc76804c60520ad76"
USER = "U11111111111111111111111111111111"
OTHER = "U22222222222222222222222222222222"
TABLES = (
    "groups",
    "stocks",
    "chart_drawings",
    "atlas_watchlists",
    "breakout_alert_state",
)
MIGRATION = (
    Path(__file__).resolve().parents[1] / "manual/account_storage_permissions.sql"
)


class _PostgresFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("ATLAS_PG_BIN")
        cls.bin = (
            Path(configured)
            if configured
            else Path(shutil.which("initdb") or "/missing").parent
        )
        if not (cls.bin / "initdb").is_file() or not (cls.bin / "psql").is_file():
            if configured:
                raise RuntimeError("ATLAS_PG_BIN must contain both initdb and psql")
            raise unittest.SkipTest(
                "PostgreSQL binaries unavailable; set ATLAS_PG_BIN to run SQL integration tests"
            )
        cls.temporary = tempfile.TemporaryDirectory(prefix="atlas-policy-pg-")
        cls.root = Path(cls.temporary.name)
        cls.data = cls.root / "data"
        cls.socket = cls.root / "socket"
        cls.socket.mkdir()
        cls.log = cls.root / "postgres.log"
        cls.port = "55439"  # Socket lives in a fresh private directory; no TCP listener is used.
        subprocess.run(
            [
                str(cls.bin / "initdb"),
                "-D",
                str(cls.data),
                "-A",
                "trust",
                "--locale=C",
                "-E",
                "UTF8",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                str(cls.bin / "pg_ctl"),
                "-D",
                str(cls.data),
                "-l",
                str(cls.log),
                "-o",
                f"-k {cls.socket} -h '' -p {cls.port}",
                "start",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        cls.psql = [
            str(cls.bin / "psql"),
            "-X",
            "-h",
            str(cls.socket),
            "-p",
            cls.port,
            "-U",
            getpass.getuser(),
            "-d",
            "postgres",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
        ]
        fixture = """
            create role anon;
            create role authenticated;
            create role service_role bypassrls;
        """
        for table in TABLES:
            fixture += f"""
                create table public.{table} (id serial primary key, line_user_id text not null, value text);
                grant all on public.{table} to anon, authenticated, service_role;
            """
        fixture += """
            create table public.index_configs (ticker text primary key, value text);
            grant all on public.index_configs to anon, authenticated, service_role;
            grant usage, select on all sequences in schema public to service_role;
        """
        cls.run_sql(fixture)
        cls.run_sql(MIGRATION.read_text())
        cls.run_sql((MIGRATION.parent / 'line_login_handoff.sql').read_text())

    @classmethod
    def tearDownClass(cls):
        subprocess.run(
            [str(cls.bin / "pg_ctl"), "-D", str(cls.data), "-m", "immediate", "stop"],
            check=True,
            capture_output=True,
            text=True,
        )
        cls.temporary.cleanup()

    @classmethod
    def run_sql(cls, text, success=True):
        result = subprocess.run(cls.psql, input=text, capture_output=True, text=True)
        if success and result.returncode:
            raise AssertionError(result.stderr)
        if not success and not result.returncode:
            raise AssertionError("SQL unexpectedly succeeded: " + text)
        return result.stdout.strip() if success else result.stderr

    def setUp(self):
        self.run_sql('truncate atlas_private.line_login_handoffs;')
        self.run_sql(
            "truncate public.atlas_storage_access, public.index_configs, "
            + ", ".join("public." + table for table in TABLES)
            + " restart identity;"
        )

    def grant(self, mode, user=USER):
        return self.run_sql(
            f"insert into public.atlas_storage_access(line_user_id,storage_mode,updated_by) values ('{user}','{mode}','{OWNER}') on conflict(line_user_id) do update set storage_mode=excluded.storage_mode,updated_by=excluded.updated_by;"
        )

class AccountStorageSqlTests(_PostgresFixture):
    def test_owner_default_and_explicit_modes(self):
        for table in TABLES:
            self.run_sql(
                f"set role service_role; insert into public.{table}(line_user_id,value) values ('{OWNER}','owner');"
            )
        self.grant("device", OWNER)
        error = self.run_sql(
            f"set role service_role; insert into public.stocks(line_user_id) values ('{OWNER}');",
            success=False,
        )
        self.assertIn("Supabase persistence is not granted", error)
        self.assertEqual(self.run_sql("select count(*) from public.stocks;"), "1")

    def test_non_owner_default_cannot_write_any_personal_table(self):
        for table in TABLES:
            with self.subTest(table=table):
                error = self.run_sql(
                    f"set role service_role; insert into public.{table}(line_user_id) values ('{USER}');",
                    success=False,
                )
                self.assertIn("Supabase persistence is not granted", error)

    def test_none_and_device_block_insert_update_delete_without_deleting_existing_data(
        self,
    ):
        for mode in ("none", "device"):
            with self.subTest(mode=mode):
                self.grant("cloud")
                for table in TABLES:
                    self.run_sql(
                        f"set role service_role; insert into public.{table}(line_user_id,value) values ('{USER}','retained');"
                    )
                self.grant(mode)
                for table in TABLES:
                    for operation in (
                        f"insert into public.{table}(line_user_id) values ('{USER}')",
                        f"update public.{table} set value='changed' where line_user_id='{USER}'",
                        f"delete from public.{table} where line_user_id='{USER}'",
                    ):
                        error = self.run_sql(
                            "set role service_role; " + operation, success=False
                        )
                        self.assertIn("Supabase persistence is not granted", error)
                    self.assertEqual(
                        self.run_sql(
                            f"select count(*) from public.{table} where value='retained';"
                        ),
                        "1",
                    )
                self.run_sql(
                    "truncate " + ", ".join("public." + table for table in TABLES) + ";"
                )

    def test_cloud_account_can_insert_update_delete_but_cannot_transfer_row_ownership(
        self,
    ):
        self.grant("cloud")
        self.grant("cloud", OTHER)
        for table in TABLES:
            self.run_sql(
                f"set role service_role; insert into public.{table}(line_user_id,value) values ('{USER}','a'); update public.{table} set value='b' where line_user_id='{USER}';"
            )
            error = self.run_sql(
                f"set role service_role; update public.{table} set line_user_id='{OTHER}' where line_user_id='{USER}';",
                success=False,
            )
            self.assertIn("Personal data account cannot be changed", error)
            self.run_sql(
                f"set role service_role; delete from public.{table} where line_user_id='{USER}';"
            )
            self.assertEqual(self.run_sql(f"select count(*) from public.{table};"), "0")

    def test_direct_anon_and_authenticated_rest_paths_are_closed(self):
        for role in ("anon", "authenticated"):
            for table in (*TABLES, "atlas_storage_access"):
                for operation in (
                    f"select * from public.{table}",
                    f"delete from public.{table}",
                ):
                    error = self.run_sql(f"set role {role}; {operation}", success=False)
                    self.assertIn("permission denied", error)
            # Public market/index reads keep their pre-existing privilege.
            self.run_sql(f"set role {role}; select * from public.index_configs;")
            error = self.run_sql(
                f"set role {role}; insert into public.index_configs values ('^GSPC','x');",
                success=False,
            )
            self.assertIn("permission denied", error)

    def test_index_configuration_mutations_follow_owner_storage_policy(self):
        self.run_sql(
            "set role service_role; insert into public.index_configs values ('^GSPC','owner');"
        )
        self.grant("none", OWNER)
        for operation in (
            "update public.index_configs set value='changed'",
            "delete from public.index_configs",
            "insert into public.index_configs values ('^TWII','new')",
        ):
            error = self.run_sql("set role service_role; " + operation, success=False)
            self.assertIn("Supabase persistence is not granted", error)
        self.assertEqual(
            self.run_sql("select value from public.index_configs;"), "owner"
        )

    def test_policy_metadata_is_owner_marked_bounded_and_validated(self):
        error = self.run_sql(
            f"set role service_role; insert into public.atlas_storage_access(line_user_id,storage_mode,updated_by) values ('{USER}','cloud','{USER}');",
            success=False,
        )
        self.assertIn("owner does not match", error)
        self.grant("device")
        error = self.run_sql(
            f"update public.atlas_storage_access set line_user_id='{OTHER}' where line_user_id='{USER}';",
            success=False,
        )
        self.assertIn("Storage policy account cannot be changed", error)
        for field, value in (
            ("storage_mode", "paid"),
            ("line_user_id", "not-a-line-id"),
            ("label", "x" * 81),
        ):
            statement = f"insert into public.atlas_storage_access(line_user_id,storage_mode,label,updated_by) values ('{OTHER}','none','','{OWNER}');"
            error = self.run_sql(
                statement
                + f"update public.atlas_storage_access set {field}='{value}' where line_user_id='{OTHER}';",
                success=False,
            )
            self.assertIn("ERROR", error)
            self.run_sql(
                f"delete from public.atlas_storage_access where line_user_id='{OTHER}';"
            )
        self.run_sql(
            f"insert into public.atlas_storage_access(line_user_id,storage_mode,updated_by) select 'U' || lpad(to_hex(n),32,'0'),'device','{OWNER}' from generate_series(1,1999) n;"
        )
        self.assertEqual(
            self.run_sql("select count(*) from public.atlas_storage_access;"), "2000"
        )
        error = self.run_sql(
            f"insert into public.atlas_storage_access(line_user_id,storage_mode,updated_by) values ('{OTHER}','none','{OWNER}');",
            success=False,
        )
        self.assertIn("account limit reached", error)

    def test_policy_version_is_server_assigned(self):
        self.grant("device")
        first = self.run_sql(
            f"select updated_at from public.atlas_storage_access where line_user_id='{USER}';"
        )
        self.run_sql(
            f"update public.atlas_storage_access set updated_at='2000-01-01',storage_mode='cloud' where line_user_id='{USER}';"
        )
        second = self.run_sql(
            f"select updated_at from public.atlas_storage_access where line_user_id='{USER}';"
        )
        self.assertNotEqual(first, second)
        self.assertNotIn("2000-01-01", second)

    def test_reapplying_migration_preserves_existing_rows_and_grants(self):
        self.grant("cloud")
        self.run_sql(
            f"insert into public.stocks(line_user_id,value) values ('{USER}','preserved');"
        )
        self.run_sql(MIGRATION.read_text())
        self.assertEqual(self.run_sql("select value from public.stocks;"), "preserved")
        self.assertEqual(
            self.run_sql("select storage_mode from public.atlas_storage_access;"),
            "cloud",
        )

    def test_writer_waiting_for_revocation_cannot_use_old_permission(self):
        self.grant("cloud")
        # Hold the exact same per-user advisory lock as a policy update, then issue a write
        # from an independent connection. It must wait, reread policy after commit, and fail.
        revoker = subprocess.Popen(
            self.psql,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        writer = None
        try:
            revoker.stdin.write(
                f"begin; update public.atlas_storage_access set storage_mode='device' where line_user_id='{USER}'; select 'locked';\n"
            )
            revoker.stdin.flush()
            self.assertEqual(revoker.stdout.readline().strip(), "BEGIN")
            self.assertEqual(revoker.stdout.readline().strip(), "UPDATE 1")
            self.assertEqual(revoker.stdout.readline().strip(), "locked")
            writer = subprocess.Popen(
                self.psql,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            writer.stdin.write(
                f"set role service_role; insert into public.stocks(line_user_id) values ('{USER}');\n"
            )
            writer.stdin.flush()
            time.sleep(0.1)
            self.assertIsNone(writer.poll(), "writer did not wait for revoke lock")
            revoker.stdin.write("commit;\n")
            revoker.stdin.flush()
            revoker.communicate(timeout=5)
            _, stderr = writer.communicate(timeout=5)
            self.assertNotEqual(writer.returncode, 0)
            self.assertIn("Supabase persistence is not granted", stderr)
            self.assertEqual(self.run_sql("select count(*) from public.stocks;"), "0")
        finally:
            for process in (writer, revoker):
                if process and process.poll() is None:
                    process.kill()
                    process.communicate()


class LineLoginSqlTests(_PostgresFixture):
    def handoff(self, operation, intent='12345678-1234-4234-8234-123456789abc', read='a' * 64,
                write='b' * 64, bucket='c' * 64, ciphertext=None, network='d' * 64, code='e' * 64):
        quoted = lambda value: 'null' if value is None else "'" + value.replace("'", "''") + "'"
        sql = "set role service_role; select public.atlas_line_login_handoff(" + ','.join(
            quoted(v) for v in (operation, intent, read, write, bucket, ciphertext, network, code)) + ');'
        return json.loads(self.run_sql(sql).splitlines()[-1])

    def test_login_one_use_proofs_and_no_personal_rows(self):
        self.assertEqual(self.handoff('begin')['status'], 'pending')
        self.assertEqual(self.handoff('poll', read='d' * 64)['status'], 'missing')
        self.assertEqual(self.handoff('complete', write='d' * 64, ciphertext='encrypted')['status'], 'missing')
        self.assertEqual(self.handoff('complete', ciphertext='encrypted')['status'], 'complete')
        self.assertEqual(self.handoff('complete', ciphertext='replacement')['status'], 'used')
        self.assertEqual(self.handoff('poll'), dict(status='consumed', ciphertext='encrypted'))
        self.assertEqual(self.handoff('poll')['status'], 'missing')
        self.assertEqual(self.run_sql('select count(*) from atlas_private.line_login_handoffs;'), '0')
        self.assertEqual(self.run_sql('select count(*) from public.atlas_storage_access;'), '0')
        for table in TABLES:
            self.assertEqual(self.run_sql(f'select count(*) from public.{table};'), '0')

    def test_login_expiry_cancel_and_cleanup(self):
        self.handoff('begin')
        self.run_sql("update atlas_private.line_login_handoffs set expires_at=clock_timestamp()-interval '1 second';")
        self.assertEqual(self.handoff('complete', ciphertext='encrypted')['status'], 'missing')
        self.handoff('begin')
        self.assertEqual(self.handoff('cancel')['status'], 'consumed')
        self.assertEqual(self.handoff('complete', ciphertext='encrypted')['status'], 'missing')

    def test_login_capacity_and_caller_limit(self):
        for number in range(6):
            result = self.handoff('begin', intent=f'12345678-1234-4234-8234-{number:012d}')
            self.assertEqual(result['status'], 'pending' if number < 5 else 'busy')
        self.run_sql('truncate atlas_private.line_login_handoffs;')
        self.run_sql("""insert into atlas_private.line_login_handoffs(id,read_hash,write_hash,bucket)
          select md5(n::text)::uuid, repeat('a',64), repeat('b',64), md5(n::text)||md5(n::text)
          from generate_series(1,512) n;""")
        self.assertEqual(self.handoff('begin')['status'], 'busy')
        self.assertEqual(self.run_sql('select count(*) from atlas_private.line_login_handoffs;'), '512')

    def test_login_anon_authenticated_cannot_call_or_read_and_rpc_is_invoker(self):
        for role in ('anon', 'authenticated'):
            self.assertIn('permission denied', self.run_sql(f"set role {role}; select public.atlas_line_login_handoff('poll','12345678-1234-4234-8234-123456789abc');", success=False))
            self.assertIn('permission denied', self.run_sql(f'set role {role}; select * from atlas_private.line_login_handoffs;', success=False))
        self.assertEqual(self.run_sql("select prosecdef from pg_proc where oid='public.atlas_line_login_handoff(text,uuid,text,text,text,text,text,text)'::regprocedure;"), 'f')
        self.assertEqual(self.run_sql("select relrowsecurity from pg_class where oid='atlas_private.line_login_handoffs'::regclass;"), 't')

    def test_reverse_code_is_required_wrong_guesses_are_counted_and_fifth_deletes(self):
        self.handoff('begin')
        self.handoff('complete', ciphertext='encrypted')
        for _ in range(8):
            self.assertEqual(self.handoff('poll', code=None), dict(status='code_required'))
        self.assertEqual(self.run_sql('select failed_attempts from atlas_private.line_login_handoffs;'), '0')
        # The read proof remains necessary; its failure cannot consume another user's attempts.
        self.assertEqual(self.handoff('poll', read='f' * 64, code='f' * 64)['status'], 'missing')
        for remaining in (4, 3, 2, 1):
            self.assertEqual(self.handoff('poll', code='f' * 64), dict(status='wrong_code', attemptsRemaining=remaining))
        self.assertEqual(self.handoff('poll', code='f' * 64), dict(status='locked'))
        self.assertEqual(self.run_sql('select count(*) from atlas_private.line_login_handoffs;'), '0')
        self.assertEqual(self.handoff('poll')['status'], 'missing')

    def test_correct_code_after_wrong_code_is_one_use_and_cancel_never_returns_payload(self):
        self.handoff('begin')
        self.handoff('complete', ciphertext='encrypted')
        self.assertEqual(self.handoff('poll', code='f' * 64)['status'], 'wrong_code')
        self.assertEqual(self.handoff('poll'), dict(status='consumed', ciphertext='encrypted'))
        self.assertEqual(self.handoff('poll')['status'], 'missing')
        self.handoff('begin')
        self.handoff('complete', ciphertext='encrypted')
        self.assertEqual(self.handoff('cancel'), dict(status='consumed'))

    def test_different_or_missing_network_cannot_complete_and_never_changes_payload(self):
        self.handoff('begin')
        for network in ('f' * 64, None):
            self.assertEqual(self.handoff('complete', network=network, ciphertext='encrypted'), dict(status='network_mismatch'))
            self.assertEqual(self.handoff('poll', code=None), dict(status='pending'))
            self.assertEqual(self.run_sql('select ciphertext is null and code_hash is null from atlas_private.line_login_handoffs;'), 't')
        self.assertEqual(self.handoff('complete', ciphertext='encrypted')['status'], 'complete')
        self.assertEqual(self.handoff('poll')['ciphertext'], 'encrypted')

    def test_concurrent_wrong_guesses_cannot_exceed_five_or_release_identity(self):
        from concurrent.futures import ThreadPoolExecutor
        self.handoff('begin')
        self.handoff('complete', ciphertext='encrypted')
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda _: self.handoff('poll', code='f' * 64), range(8)))
        self.assertEqual(sorted(r['status'] for r in results), ['locked'] + ['missing'] * 3 + ['wrong_code'] * 4)
        self.assertTrue(all('ciphertext' not in r for r in results))
        self.assertEqual(self.handoff('poll')['status'], 'missing')

    def test_manual_upgrade_is_idempotent_and_legacy_inflight_rows_fail_closed(self):
        migration = (MIGRATION.parent / 'line_login_handoff.sql').read_text()
        # Recreate the actual prior shape, including its RPC overload and an in-flight row.
        self.run_sql("""alter table atlas_private.line_login_handoffs
          drop column network_hash, drop column code_hash, drop column failed_attempts;
          create function public.atlas_line_login_handoff(text,uuid,text,text,text,text)
          returns jsonb language sql as 'select ''{}''::jsonb';
          insert into atlas_private.line_login_handoffs(id,read_hash,write_hash,bucket,ciphertext)
          values ('12345678-1234-4234-8234-123456789abc',repeat('a',64),repeat('b',64),repeat('c',64),'old-encrypted');""")
        try:
            self.run_sql(migration)
            self.run_sql(migration)
            self.assertEqual(self.run_sql("select to_regprocedure('public.atlas_line_login_handoff(text,uuid,text,text,text,text)') is null;"), 't')
            self.assertEqual(self.handoff('poll')['status'], 'restart_required')
            self.assertEqual(self.run_sql('select count(*) from atlas_private.line_login_handoffs;'), '0')
            self.handoff('begin')
            self.run_sql('update atlas_private.line_login_handoffs set network_hash=null;')
            self.assertEqual(self.handoff('complete', ciphertext='encrypted')['status'], 'restart_required')
        finally:
            self.run_sql(migration)

    def test_login_concurrent_consumers_get_exactly_one_identity(self):
        from concurrent.futures import ThreadPoolExecutor
        self.handoff('begin')
        self.handoff('complete', ciphertext='encrypted')
        with ThreadPoolExecutor(2) as pool:
            statuses = list(pool.map(lambda _: self.handoff('poll')['status'], range(2)))
        self.assertEqual(sorted(statuses), ['consumed', 'missing'])


if __name__ == "__main__":
    unittest.main()
