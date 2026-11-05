"""Immutable forward observations. Existing bytes/keys are never replaced."""
import csv
import io
import hashlib
import json
import argparse
import os
from pathlib import Path
import subprocess

FIELDS = ("asOf", "market", "generatedAt", "params_sha256", "warning_complete", "warning_level", "vol60_weak", "leader_lag",
          "breadth_divergence", "avg60", "median756", "leader_success", "market_success",
          "breakout_count_per_1000", "count_p30")
PATH = "docs/report/market-warnings-log.csv"


def read_rows(body):
    if not body:
        return []
    reader = csv.DictReader(io.StringIO(body.decode("utf-8")))
    if tuple(reader.fieldnames or ()) != FIELDS or not body.endswith(b"\n"):
        raise ValueError("Invalid immutable market warnings log")
    rows = list(reader)
    keys = [(r["asOf"], r["market"]) for r in rows]
    if len(set(keys)) != len(keys) or any(set(r) != set(FIELDS) for r in rows):
        raise ValueError("Duplicate or malformed market warnings observation")
    return rows


def encode(rows, header=False):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, FIELDS, lineterminator="\n")
    if header:
        writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def require_append(original, proposed):
    read_rows(original)
    read_rows(proposed)
    if not proposed.startswith(original):
        raise ValueError("Forward market warnings log is append-only; refusing rewrite/truncation")


def merge(remote, local):
    rows = read_rows(remote)
    known = {(r["asOf"], r["market"]): r for r in rows}
    added = []
    for row in read_rows(local):
        key = row["asOf"], row["market"]
        if key in known:
            # Concurrent producers may observe the same date at different times.
            # The first published observation wins, exactly like an ordinary rerun.
            continue
        else:
            known[key] = row
            added.append(row)
    return remote + encode(added, header=not remote) if added else remote


def append_observations(path, payload):
    path = Path(path)
    old = path.read_bytes() if path.exists() else b""
    known = {(r["asOf"], r["market"]) for r in read_rows(old)}
    additions = []
    for market, data in payload["markets"].items():
        latest = data["latest"]
        if (data["asOf"], market) in known:
            continue
        row = {field: latest.get(field) for field in FIELDS}
        row.update(asOf=data["asOf"], market=market, generatedAt=payload["generatedAt"])
        row["params_sha256"] = hashlib.sha256(json.dumps(payload.get("params", {}), sort_keys=True).encode()).hexdigest()
        additions.append(row)
    if additions:
        path.parent.mkdir(parents=True, exist_ok=True)
        # One append + fsync. No replace, truncate or historical recomputation.
        with path.open("ab") as file:
            file.write(encode(additions, header=not old))
            file.flush()
            os.fsync(file.fileno())


def verify_against(base="HEAD", path=PATH):
    subprocess.check_call(["git", "cat-file", "-e", base])
    tracked = subprocess.check_output(["git", "ls-tree", "-z", base, "--", path])
    baseline = subprocess.check_output(["git", "show", f"{base}:{path}"]) if tracked else b""
    proposed = Path(path).read_bytes() if Path(path).exists() else b""
    require_append(baseline, proposed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-against", default="HEAD")
    args = parser.parse_args()
    verify_against(args.verify_against)
