"""Real git integration tests: isolated bare origin, no network or production writes."""
import importlib.util
import ast
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("generated_branch", REPO / "scripts/generated_branch.py")
gb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gb)


class GeneratedStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.before = Path.cwd()
        self.addCleanup(os.chdir, self.before)
        self.origin = Path(self.temp.name) / "origin.git"
        self.work = Path(self.temp.name) / "work"
        self.git("init", "--bare", str(self.origin))
        self.git("init", "-b", "main", str(self.work))
        os.chdir(self.work)
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.git("remote", "add", "origin", str(self.origin))
        (self.work / ".github").mkdir()
        self.config = self.work / ".github/generated-paths.json"
        self.config.write_bytes((REPO / ".github/generated-paths.json").read_bytes())
        self.files = {
            "code.py": b"keep code\n",
            "docs/index.html": b"daily\x00html\n",
            "docs/wsj.png": b"image\x00\x01",
            "docs/atlas/index.html": b"atlas\n",
            "docs/atlas/assets/old.js": b"old bundle",
            "docs/report/latest.json": b'{"latest":1}',
            "docs/report/universe.json": b'{"universe":1}',
            "docs/report/breakouts-archive/2026-09-01.json": b'{"asOf":"2026-09-01"}',
            "docs/report/series/index.json": b'{"series":1}',
            "docs/report/rates.json": b'{"rates":1}',
            "docs/report/growth.json": b'{"growth":1}',
            "docs/report/research/volume.json": b'{"volume":1}',
            "docs/report/research/sec/US.json": b'{"sec":1}',
            "data/AAPL.csv": b"Date,Close\n2026-10-20,1\n",
            "docs/report/financials/index.json": b'{"history":1}',
            "docs/report/financials/AAPL.json": b'{"eps":1}',
            "docs/report/fundamentals/us-market-snapshot.json": b'{"frozen":1}',
            "docs/report/q4-8k/answer-set.json": b'{"ref":"95184ade"}',
            "docs/report/q4-eps-acceptance-ledger.json": b'{"accepted":1}',
            "docs/handoff/review.md": b"keep review",
            "docs/unknown.json": b"conservative default",
        }
        for p, body in self.files.items():
            self.write(p, body)
        self.git("add", ".")
        self.git("commit", "-m", "main history")
        self.base = self.git("rev-parse", "HEAD")
        self.git("push", "origin", "HEAD:main")
        self.store = gb.Storage(self.config)
        self.seed = self.store.seed("HEAD")
        self.git("push", "origin", self.seed + ":refs/heads/generated")
        self.store.restore()

    @staticmethod
    def git(*args):
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL).decode().strip()

    @staticmethod
    def write(path, body):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def remote(self, branch):
        return self.store.fetch(branch)

    def body(self, ref, path):
        return subprocess.check_output(["git", "show", f"{ref}:{path}"])

    def test_seed_orphan_all_outputs_no_records_and_byte_identical_pages(self):
        self.assertEqual(self.git("rev-list", "--count", self.seed), "1")
        seed_paths = self.store.entries(self.seed)
        self.assertIn(gb.RELAY, seed_paths)
        for path in self.files:
            self.assertEqual(path in seed_paths, self.store.moved(path), path)
        with tempfile.TemporaryDirectory() as t:
            a, b = Path(t) / "a", Path(t) / "b"
            self.store.assemble(self.base, None, a)
            self.store.assemble(self.base, self.seed, b)
            self.assertEqual(gb.digest_tree(a), gb.digest_tree(b))

    def test_world_candle_records_stay_on_main_and_report_is_overwritten_on_generated(self):
        self.write("data/world/CMD-WTI.csv", b"Date,Open,High,Low,Close,Volume\n2026-10-21,1,3,0,2,100\n")
        self.write("docs/report/research/world-assets.json", b'{"version":1}')
        self.store.publish("research")
        main, generated = self.remote("main"), self.remote("generated")
        self.assertIn("data/world/CMD-WTI.csv", self.store.entries(main))
        self.assertNotIn("data/world/CMD-WTI.csv", self.store.entries(generated))
        self.assertEqual(self.body(generated, "docs/report/research/world-assets.json"), b'{"version":1}')

    def test_publish_only_owned_changes_preserves_main_index_and_records(self):
        self.write("docs/report/latest.json", b"new latest")
        self.write("docs/report/financials/AAPL.json", b"must not leak")
        self.write("docs/report/research/sec/US.json", b"other owner's edit")
        self.git("add", "code.py")
        index_before = self.git("write-tree")
        self.store.publish("daily")
        tip = self.remote("generated")
        self.assertEqual(self.body(tip, "docs/report/latest.json"), b"new latest")
        self.assertEqual(self.body(tip, "docs/report/research/sec/US.json"), self.files["docs/report/research/sec/US.json"])
        self.assertEqual(self.remote("main"), self.base)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.base)
        self.assertEqual(self.git("write-tree"), index_before)

    def test_pr2_removes_only_outputs_and_new_csv_stays_on_main(self):
        # Simulate PR2: remove only moved paths from main, keep all old history.
        for path in self.store.entries(self.base):
            if self.store.moved(path):
                self.git("rm", "--", path)
        self.git("commit", "-m", "PR2 remove current generated copies")
        self.git("push", "origin", "HEAD:main")
        self.store.restore()
        Path("docs/atlas/assets/old.js").unlink()
        self.write("docs/atlas/assets/new.js", b"new bundle")
        self.store.publish("atlas")
        self.write("data/NEW.csv", b"Date,Close\n2026-10-22,2")
        self.store.publish("daily")
        generated = self.remote("generated")
        self.assertNotIn("docs/atlas/assets/old.js", self.store.entries(generated))
        self.assertIn("docs/atlas/assets/new.js", self.store.entries(generated))
        self.assertNotIn("data/NEW.csv", self.store.entries(generated))
        main = self.remote("main")
        self.assertEqual(self.body(main, "data/NEW.csv"), b"Date,Close\n2026-10-22,2")
        self.assertEqual(self.body(main, "data/AAPL.csv"), self.files["data/AAPL.csv"])
        self.assertEqual(self.body(self.base, "data/AAPL.csv"), self.files["data/AAPL.csv"])
        with tempfile.TemporaryDirectory() as t:
            target = Path(t) / "site"
            self.store.assemble("HEAD", generated, target)
            self.assertEqual((target / "report/financials/AAPL.json").read_bytes(), b'{"eps":1}')
            self.assertEqual((target / "index.html").read_bytes(), self.files["docs/index.html"])

    def test_daily_preserves_main_csvs_during_restore_and_publishes_k_line_history(self):
        new = self.files["data/AAPL.csv"] + b"2026-10-22,2\n"
        self.write("data/AAPL.csv", new)
        self.store.restore()
        self.assertEqual(Path("data/AAPL.csv").read_bytes(), new)
        self.store.publish("daily")
        main = self.remote("main")
        self.assertEqual(self.body(main, "data/AAPL.csv"), new)
        self.assertEqual(self.body(self.base, "data/AAPL.csv"), self.files["data/AAPL.csv"])
        self.assertEqual(self.git("merge-base", self.base, main), self.base)
        self.assertNotIn("data/AAPL.csv", self.store.entries(self.remote("generated")))

    def test_daily_refuses_to_publish_csv_deletions(self):
        Path("data/AAPL.csv").unlink()
        with self.assertRaisesRegex(ValueError, "Refusing to delete owner-retained K-line"):
            self.store.publish("daily")
        self.assertEqual(self.remote("main"), self.base)

    def test_legacy_main_publisher_keeps_committing_csvs_with_switch_off(self):
        function = next(n for n in ast.parse((REPO / "main.py").read_text(encoding="utf-8-sig")).body
                        if isinstance(n, ast.FunctionDef) and n.name == "push_report_to_github")

        def run_git_command(command):
            # Use the isolated repo identity, without touching the user's global config.
            if command[:2] == ["git", "config"]:
                return 0
            return subprocess.run(command, capture_output=True).returncode

        namespace = {"os": os, "subprocess": subprocess, "run_git_command": run_git_command}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "main.py", "exec"), namespace)
        self.git("branch", "--set-upstream-to=origin/main", "main")
        self.write("data/AAPL.csv", b"updated retained K-line")
        with patch.dict(os.environ, {"GENERATED_BRANCH_MODE": ""}):
            self.assertTrue(namespace["push_report_to_github"]())
        main = self.remote("main")
        self.assertEqual(self.body(main, "data/AAPL.csv"), b"updated retained K-line")
        self.assertEqual(self.body(self.base, "data/AAPL.csv"), self.files["data/AAPL.csv"])
        self.assertEqual(self.remote("generated"), self.seed)

    def test_legacy_universe_workflow_commits_archives_with_switch_off(self):
        snapshot = "docs/report/breakouts-archive/2026-10-22.json"
        self.write(snapshot, b"immutable full-market snapshot")
        self.write("docs/report/breakouts.json", b"current full-market snapshot")
        self.git("branch", "--set-upstream-to=origin/main", "main")
        workflow = (REPO / ".github/workflows/universe-full.yml").read_text()
        block = workflow.split("      - name: Commit and Push changes\n", 1)[1]
        script = textwrap.dedent(block.split("        run: |\n", 1)[1])
        with patch.dict(os.environ, {"GENERATED_BRANCH_MODE": ""}):
            subprocess.run(["bash", "-e", "-c", script], check=True, capture_output=True)
        self.assertEqual(self.body(self.remote("main"), snapshot), b"immutable full-market snapshot")
        self.assertEqual(self.remote("generated"), self.seed)

    def test_archive_snapshots_are_append_only_and_survive_daily_squash(self):
        old = "docs/report/breakouts-archive/2026-09-01.json"
        new = "docs/report/breakouts-archive/2026-10-22.json"
        self.write(new, b"new immutable snapshot")
        self.store.publish("universe")
        self.store.squash()
        tip = self.remote("generated")
        self.assertEqual(self.body(tip, old), self.files[old])
        self.assertEqual(self.body(tip, new), b"new immutable snapshot")
        for content in (b"rewritten old snapshot", None):
            with self.subTest(content=content):
                if content is None:
                    Path(old).unlink()
                else:
                    self.write(old, content)
                with self.assertRaisesRegex(ValueError, "append-only"):
                    self.store.publish("universe")
                self.assertEqual(self.remote("generated"), tip)

    def test_restore_refuses_to_remove_or_rewrite_local_archives(self):
        old = "docs/report/breakouts-archive/2026-09-01.json"
        mtime = Path(old).stat().st_mtime_ns
        self.store.restore()
        self.assertEqual(Path(old).stat().st_mtime_ns, mtime)
        self.write(old, b"different archive bytes")
        with self.assertRaisesRegex(ValueError, "Refusing to delete or rewrite an archived"):
            self.store.restore()
        self.assertEqual(Path(old).read_bytes(), b"different archive bytes")
        self.write(old, self.files[old])
        new = "docs/report/breakouts-archive/2026-10-22.json"
        self.write(new, b"not on generated yet")
        with self.assertRaisesRegex(ValueError, "Refusing to delete or rewrite an archived"):
            self.store.restore()
        self.assertTrue(Path(new).is_file())

    def test_financials_split_to_main_and_generated(self):
        self.write("docs/report/financials/AAPL.json", b"new permanent eps")
        self.write("docs/report/growth.json", b"new derived growth")
        self.store.publish("financials")
        main = self.remote("main")
        generated = self.remote("generated")
        self.assertEqual(self.body(main, "docs/report/financials/AAPL.json"), b"new permanent eps")
        self.assertEqual(self.body(main, "docs/report/growth.json"), self.files["docs/report/growth.json"])
        self.assertEqual(self.body(generated, "docs/report/growth.json"), b"new derived growth")
        self.assertNotIn("docs/report/financials/AAPL.json", self.store.entries(generated))
        self.assertEqual(self.git("merge-base", self.base, main), self.base)

    def test_fetch_rebase_retry_keeps_other_owner_after_push_race(self):
        self.write("docs/report/latest.json", b"daily update")
        original_run = subprocess.run
        raced = False

        def race(command, **kwargs):
            nonlocal raced
            if command[:3] == ["git", "push", "origin"] and not raced:
                raced = True
                entries = self.store.entries(self.seed)
                sha = gb.git(
                    "hash-object", "-w", "--stdin", data=b"concurrent research").decode().strip()
                entries["docs/report/research/volume.json"] = ("100644", sha)
                concurrent = self.store.commit(self.store.tree(entries), "other owner", self.seed)
                original_run(["git", "push", "origin", concurrent + ":refs/heads/generated"], check=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return original_run(command, **kwargs)

        with patch.object(gb.subprocess, "run", side_effect=race), patch.object(gb.time, "sleep"):
            self.store.publish("daily")
        self.assertTrue(raced)
        tip = self.remote("generated")
        self.assertEqual(self.body(tip, "docs/report/latest.json"), b"daily update")
        self.assertEqual(self.body(tip, "docs/report/research/volume.json"), b"concurrent research")

    def test_same_path_conflict_refuses_to_overwrite(self):
        self.write("docs/report/latest.json", b"first run")
        self.store.publish("daily")
        self.write("docs/report/latest.json", b"stale run different result")
        with self.assertRaisesRegex(ValueError, "Concurrent same-path"):
            self.store.publish("daily")

    def test_squash_tree_unchanged_main_history_untouched_and_producer_rebases(self):
        self.write("docs/report/research/volume.json", b"before squash")
        self.store.publish("research")
        old = self.remote("generated")
        self.store.squash()
        tip = self.remote("generated")
        self.assertEqual(self.git("rev-list", "--count", tip), "1")
        self.assertEqual(self.store.entries(old), self.store.entries(tip))
        self.assertEqual(self.remote("main"), self.base)
        self.write("docs/report/latest.json", b"after squash")
        self.store.publish("daily")
        self.assertEqual(self.body(self.remote("generated"), "docs/report/research/volume.json"), b"before squash")

    def test_squash_retries_lease_without_losing_a_racing_update(self):
        original_run = subprocess.run
        raced = False

        def race(command, **kwargs):
            nonlocal raced
            if command[:2] == ["git", "push"] and any("--force-with-lease=" in a for a in command) and not raced:
                raced = True
                entries = self.store.entries(self.seed)
                sha = gb.git("hash-object", "-w", "--stdin", data=b"won race").decode().strip()
                entries["docs/report/latest.json"] = ("100644", sha)
                concurrent = self.store.commit(self.store.tree(entries), "racing writer", self.seed)
                original_run(["git", "push", "origin", concurrent + ":refs/heads/generated"],
                             check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return original_run(command, **kwargs)

        with patch.object(gb.subprocess, "run", side_effect=race), patch.object(gb.time, "sleep"):
            self.store.squash()
        tip = self.remote("generated")
        self.assertTrue(raced)
        self.assertEqual(self.git("rev-list", "--count", tip), "1")
        self.assertEqual(self.body(tip, "docs/report/latest.json"), b"won race")
        self.assertEqual(self.remote("main"), self.base)

    def test_rollback_creates_review_branch_with_latest_outputs_no_production_write(self):
        self.write("docs/report/latest.json", b"latest rollback payload")
        self.store.publish("daily")
        generated_before = self.remote("generated")
        commit = self.store.rollback_branch("rollback/generated-test")
        self.assertEqual(self.body(commit, "docs/report/latest.json"), b"latest rollback payload")
        self.assertEqual(self.body(commit, "docs/report/financials/AAPL.json"), self.files["docs/report/financials/AAPL.json"])
        self.assertEqual(self.body(commit, "data/AAPL.csv"), self.files["data/AAPL.csv"])
        self.assertNotIn(gb.RELAY, self.store.entries(commit))
        self.assertEqual(self.git("merge-base", commit, self.base), self.base)
        self.assertEqual(self.remote("main"), self.base)
        self.assertEqual(self.remote("generated"), generated_before)

    def test_seed_create_only_push_cannot_replace_existing_generated(self):
        with patch("sys.argv", ["generated_branch.py", "--config", str(self.config), "seed", "--push"]):
            with self.assertRaises(subprocess.CalledProcessError):
                gb.main()
        self.assertEqual(self.remote("generated"), self.seed)
        self.assertEqual(self.remote("main"), self.base)

    def test_unsafe_generated_tree_refused_before_restore_or_deploy(self):
        entries = self.store.entries(self.seed)
        entries["docs/report/financials/AAPL.json"] = self.store.entries(self.base)["docs/report/financials/AAPL.json"]
        bad = self.store.commit(self.store.tree(entries), "unsafe snapshot", self.seed)
        with self.assertRaisesRegex(ValueError, "Records/code"):
            self.store.restore(bad)
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError, "Records/code"):
                self.store.assemble(self.base, bad, Path(t) / "bad")

    def test_switch_unset_is_noop(self):
        for command in ("restore", "squash", "publish"):
            args = ["generated_branch.py", "--config", str(self.config), command]
            if command == "publish":
                args.append("daily")
            with patch.dict(os.environ, {"GENERATED_BRANCH_MODE": ""}), patch("sys.argv", args):
                gb.main()
        self.assertEqual(self.remote("generated"), self.seed)
        self.assertEqual(self.remote("main"), self.base)


class InventoryTests(unittest.TestCase):
    def test_main_tracks_only_frozen_pngs_and_ignore_rules_protect_records(self):
        import csv
        config = json.loads((REPO / ".github/generated-paths.json").read_text())
        owners = [p for paths in config["owners"].values() for p in paths]
        frozen = {"docs/wsj.png", "docs/barrons.png"}
        tracked = set(subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=REPO).decode().split("\0")) - {""}
        self.assertEqual({p for p in tracked if gb.matches(p, owners)}, frozen)
        with (REPO / "operations/generated-path-inventory.tsv").open() as f:
            records = {r["path"] for r in csv.DictReader(f, delimiter="\t")
                       if r["classification"] == "record"}
        self.assertLessEqual(records, tracked)
        records.update(p for p in tracked if p.startswith(("data/", "tests/fixtures/", "atlas/tests/fixtures/")))
        records.update(p.replace("**", "future/record.json").replace("*", "future")
                       for paths in config["records"].values() for p in paths)
        records.update({"data/world/FUTURE.csv", "docs/report/fundamentals/q4-8k-state.json"})
        outputs = {p.replace("**", "future/output.json").replace("*", "future") for p in owners} - frozen
        candidates = sorted(records | outputs | frozen)
        result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin", "-z"],
                                cwd=REPO, input=("\0".join(candidates) + "\0").encode(),
                                capture_output=True, check=False)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        ignored = set(result.stdout.decode().split("\0")) - {""}
        self.assertLessEqual(outputs, ignored)
        self.assertFalse(ignored & (records | frozen), ignored & (records | frozen))

    def test_all_inventory_classifications_agree_with_allowlist(self):
        import csv
        store = gb.Storage(REPO / ".github/generated-paths.json")
        with (REPO / "operations/generated-path-inventory.tsv").open() as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        self.assertEqual(len(rows), len({r["path"] for r in rows}))
        for row in rows:
            expected = "regenerated" if store.moved(row["path"]) else "record"
            self.assertEqual(row["classification"], expected, row["path"])
            self.assertTrue(row["reason"])
            if row["path"].startswith("data/"):
                self.assertEqual(row["reason"], "owner-retained K-line history")
        baseline = json.loads((REPO / "operations/generated-baseline.json").read_text())
        self.assertEqual(len(rows), baseline["inventory_files"])
        self.assertEqual(sum(r["classification"] == "regenerated" for r in rows), baseline["regenerated_files"])
        self.assertEqual(sum(r["classification"] == "record" for r in rows), baseline["record_files"])

    def test_main_embedded_publisher_cannot_push_when_switch_is_enabled(self):
        # Execute the actual publisher without importing main's network clients.
        import ast
        tree = ast.parse((REPO / "main.py").read_text(encoding="utf-8-sig"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "push_report_to_github")
        sentinel = RuntimeError("legacy git path reached")
        namespace = {"os": os, "run_git_command": lambda *a: (_ for _ in ()).throw(sentinel)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "main.py", "exec"), namespace)
        with patch.dict(os.environ, {"GENERATED_BRANCH_MODE": "1"}):
            self.assertTrue(namespace["push_report_to_github"]())
        with patch.dict(os.environ, {"GENERATED_BRANCH_MODE": ""}):
            with self.assertRaisesRegex(RuntimeError, "legacy git path"):
                namespace["push_report_to_github"]()


if __name__ == "__main__":
    unittest.main()
