"""Create-only orphan initialization and validated, non-force archive publication.

Run from the code checkout. Every pull/rebase is confined to kline-archive;
the optional main correction-journal publication uses the existing index-only
records publisher and never rebases the caller's main checkout.
"""
import argparse
import base64
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import kline_archive as archive


def auth_env():
    env = dict(os.environ)
    token = env.get("GITHUB_TOKEN")
    if token:
        server = env.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        if not server.startswith("https://") or any(c.isspace() for c in server):
            raise ValueError("Invalid GitHub server URL")
        index = int(env.get("GIT_CONFIG_COUNT", "0"))
        env["GIT_CONFIG_COUNT"] = str(index + 2)
        env[f"GIT_CONFIG_KEY_{index}"] = f"http.{server}/.extraheader"
        env[f"GIT_CONFIG_VALUE_{index}"] = ""  # reset checkout's existing Authorization header
        env[f"GIT_CONFIG_KEY_{index + 1}"] = f"http.{server}/.extraheader"
        encoded = base64.b64encode(("x-access-token:" + token).encode()).decode()
        env[f"GIT_CONFIG_VALUE_{index + 1}"] = "AUTHORIZATION: basic " + encoded
    return env


def git(path, *args, check=True):
    return subprocess.run(["git", "-C", str(path), *args], check=check,
                          env=auth_env(), capture_output=True, text=True)


def assert_archive(path):
    top = git(path, "rev-parse", "--show-toplevel").stdout.strip()
    branch = git(path, "symbolic-ref", "--short", "HEAD").stdout.strip()
    if Path(top).resolve() != Path(path).resolve() or branch != archive.BRANCH:
        raise ValueError("Refusing to commit/rebase anything except the dedicated kline-archive checkout")
    for p in Path(path).rglob("*"):
        rel = p.relative_to(path)
        if ".git" in rel.parts or not p.is_file():
            continue
        name = rel.as_posix()
        if name not in ("README.md", "manifest.json", archive.LOG, archive.CONFLICTS, archive.PROGRESS) and not name.startswith(("tw/", "us/")):
            raise ValueError("Unexpected file in permanent archive: " + name)


def initialize(path, remote):
    path = Path(path)
    if path.exists() and any(path.iterdir()):
        raise ValueError("Orphan initialization requires a new empty directory")
    # init has no main parent, code tree, generated owners, Pages relay or workflows.
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "--initial-branch=" + archive.BRANCH)
    git(path, "remote", "add", "origin", remote)
    archive.initialize(path)


def publish(path, payload=None, summary=None, sleep=time.sleep, create_only=False):
    path = Path(path)
    assert_archive(path)
    git(path, "config", "user.name", "github-actions[bot]")
    git(path, "config", "user.email", "github-actions[bot]@users.noreply.github.com")
    head = git(path, "rev-parse", "--verify", "HEAD", check=False)
    base = head.stdout.strip() if head.returncode == 0 else None
    if payload:
        with archive.timed("publish.apply_operations"):
            archive.apply_operations(path, payload, rebuild_manifest=False)
    archive.verify(path, base, summary, refresh_manifest=bool(payload))
    with archive.timed("publish.stage"):
        git(path, "add", "-A", "--", ".")
    changed = git(path, "diff", "--cached", "--quiet", check=False)
    if changed.returncode == 0:
        print("Archive unchanged; no commit or push")
        return False
    if changed.returncode != 1:
        raise RuntimeError("Unable to inspect archive index")
    with archive.timed("publish.commit"):
        git(path, "commit", "-m", "Retain full-market daily K-lines")
    for attempt in range(3):
        with archive.timed(f"publish.push (attempt {attempt + 1})"):
            result = git(path, "push", "origin", f"HEAD:refs/heads/{archive.BRANCH}", check=False)
        if result.returncode == 0:
            print("Published permanent archive:", git(path, "rev-parse", "HEAD").stdout.strip())
            return True
        if create_only:
            raise RuntimeError("Create-only archive push rejected; existing remote branch was not modified")
        if attempt == 2:
            raise RuntimeError("Archive push rejected after three non-force attempts")
        # Never resolve conflicts with ours/theirs, discard dates, or force a push.
        rebased = git(path, "pull", "--rebase", "origin", archive.BRANCH, check=False)
        if rebased.returncode:
            git(path, "rebase", "--abort", check=False)
            raise RuntimeError("Archive rebase conflict; remote unchanged, retry the producer against the new tip")
        remote_base = git(path, "rev-parse", "FETCH_HEAD").stdout.strip()
        archive.verify(path, remote_base, summary, refresh_manifest=True)
        git(path, "add", "--", "manifest.json")
        if git(path, "diff", "--cached", "--quiet", check=False).returncode == 1:
            git(path, "commit", "-m", "Update archive size after concurrent append")
        sleep(attempt + 1)
    return False


def publish_main_journal(path, sleep=time.sleep, main_root=None):
    """Merge only actual archive corrections into the existing permanent main log."""
    import generated_branch as generated
    store = generated.Storage()
    for attempt in range(5):
        head = store.fetch("main")
        text = generated.git("show", f"{head}:{archive.LOG}")
        target = Path(main_root or ROOT) / archive.LOG
        archive.atomic(target, text.decode())
        archive.merge_main_log(path, target)
        try:
            return store.publish_one("main", head, [archive.LOG], "Record permanent K-line archive adjustments")
        except ValueError as exc:
            if "Concurrent same-path change" not in str(exc):
                raise
            sleep(attempt + 1)
    raise RuntimeError("Correction journal publication raced repeatedly; archive retains every event for replay")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["initialize", "publish", "journal"])
    parser.add_argument("--archive-dir", required=True)
    parser.add_argument("--payload-dir")
    parser.add_argument("--remote")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    args = parser.parse_args()
    if args.operation == "initialize":
        if not args.remote:
            parser.error("--remote is required for create-only initialization")
        initialize(args.archive_dir, args.remote)
        publish(args.archive_dir, summary=args.summary, create_only=True)
    elif args.operation == "publish":
        publish(args.archive_dir, args.payload_dir, args.summary)
    else:
        publish_main_journal(args.archive_dir)


if __name__ == "__main__":
    main()
