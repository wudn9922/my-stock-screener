#!/usr/bin/env python3
"""Isolated, allowlisted generated-branch storage. Requires only Python and git.

The caller's checkout/index is never committed or reset. Publishing replays only
the owner's changed blobs onto each freshly fetched tip (an index-only rebase).
Same-path concurrent edits fail instead of silently overwriting newer output.
"""
import argparse
from contextlib import contextmanager
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".github/generated-paths.json"
RELAY = ".github/workflows/generated-pages-relay.yml"
RELAY_BODY = b"""# Storage-branch relay; implementation always comes from main.
name: Generated Pages relay
on:
  push:
    branches: [generated]
permissions:
  contents: read
  pages: write
  id-token: write
jobs:
  deploy:
    uses: wudn9922/my-stock-screener/.github/workflows/generated-pages.yml@main
"""


def git(*args, data=None, env=None):
    return subprocess.check_output(["git", *args], input=data, env=env)


def matches(path, patterns):
    # '*' is one path component; '**' explicitly includes descendants.
    return any(fnmatch.fnmatchcase(path, p) and
               ("**" in p or path.count("/") == p.count("/")) for p in patterns)


class Storage:
    def __init__(self, config=CONFIG):
        self.config = json.loads(Path(config).read_text())
        if self.config["branch"] != "generated":
            raise ValueError("Storage branch must be exactly generated")
        self.patterns = sorted({p for v in self.config["owners"].values() for p in v})
        self.append_only = self.config.get("append_only", [])
        if any(p.startswith("data/") for p in self.patterns):
            raise ValueError("Owner-retained K-line history must stay on main")
        self.state = Path(git("rev-parse", "--absolute-git-dir").decode().strip()) / "generated-state.json"

    def moved(self, path):
        return matches(path, self.patterns)

    def entries(self, ref):
        entries = {}
        for row in git("ls-tree", "-rz", ref).split(b"\0"):
            if not row:
                continue
            meta, name = row.split(b"\t", 1)
            mode, kind, sha = meta.decode().split()
            if kind != "blob" or mode not in ("100644", "100755"):
                raise ValueError("Unsupported tree entry: " + name.decode())
            entries[name.decode()] = (mode, sha)
        return entries

    def validate(self, entries):
        bad = [p for p in entries if p != RELAY and not self.moved(p)]
        if bad:
            raise ValueError("Records/code are forbidden on generated: " + repr(bad))
        if RELAY not in entries or git("cat-file", "blob", entries[RELAY][1]) != RELAY_BODY:
            raise ValueError("Missing or changed main-only Pages relay")

    def fetch(self, branch="generated"):
        git("fetch", "--no-tags", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
        return git("rev-parse", f"refs/remotes/origin/{branch}").decode().strip()

    @contextmanager
    def index(self, base=None):
        with tempfile.TemporaryDirectory(prefix="generated-index-") as folder:
            env = dict(os.environ, GIT_INDEX_FILE=folder + "/index")
            git("read-tree", base or "--empty", env=env)
            yield env

    def tree(self, entries):
        with self.index() as env:
            self.update_index(entries, env)
            return git("write-tree", env=env).decode().strip()

    @staticmethod
    def write_blobs(entries, destination, prefix=""):
        """Read blobs in one git process; avoid thousands of per-file processes."""
        ordered = sorted(entries.items())
        with tempfile.TemporaryFile() as requests, tempfile.TemporaryFile() as responses:
            requests.write(b"".join(e[1].encode() + b"\n" for _, e in ordered))
            requests.seek(0)
            subprocess.run(["git", "cat-file", "--batch"], stdin=requests,
                           stdout=responses, check=True)
            responses.seek(0)
            for path, (mode, expected) in ordered:
                sha, kind, size = responses.readline().decode().split()
                assert sha == expected and kind == "blob"
                body = responses.read(int(size))
                assert responses.read(1) == b"\n"
                target = Path(destination) / path.removeprefix(prefix)
                if target.is_symlink():
                    raise ValueError("Refusing symlink: " + str(target))
                if target.is_file() and target.read_bytes() == body:
                    if target.stat().st_mode & 0o777 != int(mode, 8) & 0o777:
                        target.chmod(int(mode, 8) & 0o777)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
                target.chmod(int(mode, 8) & 0o777)

    @staticmethod
    def update_index(entries, env):
        rows = []
        for path, entry in sorted(entries.items()):
            mode, sha = entry or ("0", "0" * 40)
            rows.append(f"{mode} {sha}\t{path}".encode() + b"\0")
        git("update-index", "-z", "--index-info", data=b"".join(rows), env=env)

    @staticmethod
    def commit(tree, message, parent=None):
        env = dict(os.environ)
        for role in ("AUTHOR", "COMMITTER"):
            env.setdefault(f"GIT_{role}_NAME", "github-actions[bot]")
            env.setdefault(f"GIT_{role}_EMAIL", "github-actions[bot]@users.noreply.github.com")
        return git("commit-tree", tree, *(["-p", parent] if parent else []),
                   data=(message + "\n").encode(), env=env).decode().strip()

    def seed(self, ref):
        source = self.entries(ref)
        entries = {p: e for p, e in source.items() if self.moved(p)}
        entries[RELAY] = ("100644", git("hash-object", "-w", "--stdin", data=RELAY_BODY).decode().strip())
        self.validate(entries)
        if not any(p.startswith("docs/atlas/") for p in entries) or "docs/report/latest.json" not in entries:
            raise ValueError("Seed must contain current outputs, not an empty checkout")
        commit = self.commit(self.tree(entries), f"Seed generated outputs from {ref}")
        # Prove every selected blob/mode exists before exposing the orphan commit.
        assert self.entries(commit) == entries
        return commit

    def restore(self, ref=None):
        ref = ref or self.fetch()
        entries = self.entries(ref)
        self.validate(entries)
        # Remove stale main copies, including files deleted on generated.
        paths = set(git("ls-files", "-z", "--", "docs", "data").decode().split("\0"))
        for folder in (Path("docs"), Path("data")):
            if folder.exists():
                paths.update(p.as_posix() for p in folder.rglob("*") if p.is_file())
        for path in paths:
            if matches(path, self.append_only) and Path(path).exists():
                if path not in entries or git("hash-object", "--", path).decode().strip() != entries[path][1]:
                    raise ValueError("Refusing to delete or rewrite an archived snapshot: " + path)
        for path in paths:
            if self.moved(path) and path not in entries:
                Path(path).unlink(missing_ok=True)
        self.write_blobs({p: e for p, e in entries.items() if p != RELAY}, ".")
        self.state.write_text(json.dumps({"generated": ref, "main": git("rev-parse", "HEAD").decode().strip()}))
        print(f"Restored generated inputs at {ref}")

    def changed(self, base, patterns):
        old = {p: e for p, e in self.entries(base).items() if matches(p, patterns)}
        paths = set(old)
        for pattern in patterns:
            glob = pattern + "/*" if pattern.endswith("**") else pattern
            paths.update(p.as_posix() for p in Path(".").glob(glob) if p.is_file())
        new = {}
        for path in paths:
            file = Path(path)
            if file.is_symlink():
                raise ValueError("Refusing symlink: " + path)
            if file.is_file():
                sha = git("hash-object", "-w", "--", path).decode().strip()
                new[path] = ("100755" if file.stat().st_mode & 0o111 else "100644", sha)
        return {p: new.get(p) for p in old.keys() | new.keys() if old.get(p) != new.get(p)}, old

    def publish_one(self, branch, base, patterns, message):
        delta, original = self.changed(base, patterns)
        for path, entry in delta.items():
            if branch == "main" and matches(path, ["data/*.csv"]) and entry is None:
                raise ValueError("Refusing to delete owner-retained K-line history: " + path)
            if matches(path, self.append_only) and (entry is None or path in original):
                raise ValueError("Archived snapshots are append-only: " + path)
        if not delta:
            print(f"No {branch} changes")
            return False
        for attempt in range(5):
            head = self.fetch(branch)
            current = self.entries(head)
            if branch == "generated":
                self.validate(current)
            conflicts = [p for p, e in delta.items()
                         if current.get(p) != original.get(p) and current.get(p) != e]
            if conflicts:
                raise ValueError("Concurrent same-path change; rerun producer: " + repr(conflicts))
            with self.index(head) as env:
                self.update_index(delta, env)
                tree = git("write-tree", env=env).decode().strip()
            if tree == git("rev-parse", head + "^{tree}").decode().strip():
                return False
            commit = self.commit(tree, message, head)
            result = subprocess.run(["git", "push", "origin", f"{commit}:refs/heads/{branch}"], check=False)
            if result.returncode == 0:
                return True
            # Fetch and replay this exact owner's patch on the new parent, including
            # after a concurrent daily squash. Never force a producer push.
            time.sleep(attempt + 1)
        raise RuntimeError(f"Push to {branch} failed after five fetch/rebase retries")

    def publish(self, owner):
        state = json.loads(self.state.read_text())  # no restore => fail closed
        # Publish durable K-line / financial records first; never put them on generated.
        records = self.config["records"].get(owner, [])
        if records:
            self.publish_one("main", state["main"], records, f"Update {owner} records")
        self.publish_one("generated", state["generated"], self.config["owners"][owner],
                         f"Regenerate {owner} outputs")

    def squash(self):
        for attempt in range(5):
            head = self.fetch()
            self.validate(self.entries(head))
            tree = git("rev-parse", head + "^{tree}").decode().strip()
            commit = self.commit(tree, "Daily generated snapshot (current tree unchanged)")
            assert git("rev-parse", commit + "^{tree}").decode().strip() == tree
            result = subprocess.run(["git", "push", f"--force-with-lease=refs/heads/generated:{head}",
                                     "origin", f"{commit}:refs/heads/generated"], check=False)
            if result.returncode == 0:
                return
            time.sleep(attempt + 1)
        raise RuntimeError("Squash lost its lease five times; no other branch was touched")

    def rollback_branch(self, name):
        if not name.startswith("rollback/generated-"):
            raise ValueError("Rollback branch must start with rollback/generated-")
        main = self.fetch("main")
        generated = self.fetch()
        overlay = self.entries(generated)
        self.validate(overlay)
        entries = {p: e for p, e in self.entries(main).items() if not self.moved(p)}
        entries.update({p: e for p, e in overlay.items() if p != RELAY})
        commit = self.commit(self.tree(entries), "Restore latest generated outputs for branch Pages rollback", main)
        # Create a reviewable local branch only. No production ref is updated.
        git("update-ref", f"refs/heads/{name}", commit, "0" * 40)
        print(f"Created local {name} at {commit}; push it and open a rollback PR")
        return commit

    def assemble(self, main, generated, dest):
        dest = Path(dest).resolve()
        if dest.exists():
            raise ValueError("Assembly destination must not exist")
        entries = {p: e for p, e in self.entries(main).items() if p.startswith("docs/")}
        if generated:
            overlay = self.entries(generated)
            self.validate(overlay)
            entries = {p: e for p, e in entries.items() if not self.moved(p)}
            entries.update({p: e for p, e in overlay.items() if p.startswith("docs/")})
        dest.mkdir(parents=True)
        self.write_blobs(entries, dest, "docs/")
        size = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
        if size >= 1_000_000_000:
            raise ValueError(f"Pages artifact {size} bytes exceeds the 1 GB limit")
        report = f"Pages: {len(entries)} files, {size:,} bytes ({size / 2**20:.2f} MiB); limit 1,000,000,000 bytes"
        print(report)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
                f.write(report + "\n")
        return entries


def digest_tree(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(folder).rglob("*") if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    sub = parser.add_subparsers(dest="action", required=True)
    seed = sub.add_parser("seed")
    seed.add_argument("--ref", default="origin/main")
    seed.add_argument("--push", action="store_true")
    sub.add_parser("restore")
    publish = sub.add_parser("publish")
    publish.add_argument("owner")
    sub.add_parser("squash")
    rollback = sub.add_parser("rollback-branch")
    rollback.add_argument("--branch", required=True)
    assemble = sub.add_parser("assemble")
    assemble.add_argument("--main", default="origin/main")
    assemble.add_argument("--generated")
    assemble.add_argument("--dest", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--ref", default="origin/main")
    verify.add_argument("--dest", type=Path)
    args = parser.parse_args()
    store = Storage(args.config)
    if args.action in ("restore", "publish", "squash") and os.environ.get("GENERATED_BRANCH_MODE") != "1":
        print("GENERATED_BRANCH_MODE is not 1; legacy behavior remains active")
        return
    if args.action == "seed":
        if args.push:
            store.fetch("main")
            if args.ref != "origin/main":
                raise ValueError("Remote seeding must use freshly fetched origin/main")
        commit = store.seed(args.ref)
        print("Verified orphan seed: " + commit)
        if args.push:
            # Empty expected value means create-only; never overwrite an existing branch.
            git("push", "--force-with-lease=refs/heads/generated:", "origin", f"{commit}:refs/heads/generated")
    elif args.action == "restore":
        store.restore()
    elif args.action == "publish":
        store.publish(args.owner)
    elif args.action == "squash":
        store.squash()
    elif args.action == "rollback-branch":
        store.rollback_branch(args.branch)
    elif args.action == "assemble":
        store.assemble(args.main, args.generated, args.dest)
    elif args.action == "verify":
        with tempfile.TemporaryDirectory(prefix="verify-pages-") as temp:
            baseline = Path(temp) / "main"
            assembled = args.dest or Path(temp) / "assembled"
            seed = store.seed(args.ref)
            store.assemble(args.ref, None, baseline)
            store.assemble(args.ref, seed, assembled)
            assert digest_tree(baseline) == digest_tree(assembled), "Pages bytes/paths differ"
            print("PASS: seeded overlay is byte-identical to main docs/ (all paths + SHA-256)")


if __name__ == "__main__":
    main()
