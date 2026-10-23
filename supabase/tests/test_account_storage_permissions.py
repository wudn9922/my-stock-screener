"""Exercise the manual storage-policy migration in a disposable native PostgreSQL cluster.

Run: ATLAS_PG_BIN=/path/to/postgres/bin python supabase/tests/test_account_storage_permissions.py -v
Requires PostgreSQL 15+ server/client binaries. No database URL, secrets or production database
are accepted: this suite creates its own trust-authenticated cluster under a temporary directory.
"""

import getpass
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


class AccountStorageSqlTests(unittest.TestCase):
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
        self.run_sql(
            "truncate public.atlas_storage_access, public.index_configs, "
            + ", ".join("public." + table for table in TABLES)
            + " restart identity;"
        )

    def grant(self, mode, user=USER):
        return self.run_sql(
            f"insert into public.atlas_storage_access(line_user_id,storage_mode,updated_by) values ('{user}','{mode}','{OWNER}') on conflict(line_user_id) do update set storage_mode=excluded.storage_mode,updated_by=excluded.updated_by;"
        )

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


if __name__ == "__main__":
    unittest.main()
