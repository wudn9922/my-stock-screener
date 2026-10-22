"""Measure working-tree storage and a reproducible one-session git growth replay.

Previous snapshot: remove the latest observed session from real daily input histories,
recompute institutional summaries/estimates using those inputs, and rewind the 21-day
volume feature list (volume's aggregate estimates remain unchanged, explicitly noted).
This is an estimate of pack growth, not a forecast of provider corrections or backfills.
No generated replay data is published to the website.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zlib
from research_io import read, write
from research_store import MarketStore, INDEX
from research_metrics import institutional_summary, cost_estimate


def command(root, *args, data=None):
    return subprocess.check_output(["git", "-C", str(root), *args], input=data)


def previous(root):
    for module in ("volume", "institutions", "sec", "turnover"):
        store = MarketStore(root, module)
        for market, records in store.records.items():
            last = max(
                (r.get("asOf") for r in records.values() if r.get("asOf")), default=None
            )
            if not last:
                continue
            if module == "turnover":
                store.records[market].pop(last, None)
            for key, value in records.items():
                if module == "volume":
                    features = [r for r in value["features"] if r["date"] < last]
                    if features and value["asOf"] == last:
                        value.update(
                            features=features,
                            latest=features[-1],
                            asOf=features[-1]["date"],
                        )
                elif module == "institutions":
                    rows = [r for r in value["rows"] if r["date"] < last]
                    if not rows:
                        continue
                    days = sorted(
                        {
                            r["date"]
                            for v in records.values()
                            for r in v["rows"]
                            if r["date"] < last
                        }
                    )
                    prices = [
                        dict(
                            date=r["date"],
                            close=r.get("close"),
                            volume=r.get("market", {}).get("volume"),
                            amount=r.get("market", {}).get("amount"),
                            action=None,
                        )
                        for r in rows
                    ]
                    value.update(
                        rows=rows,
                        asOf=rows[-1]["date"],
                        summary=institutional_summary(rows, days),
                        costs={
                            role: {
                                str(w): cost_estimate(rows, prices, w, role, days)
                                for w in (5, 20, 60)
                            }
                            for role in ("foreign", "trust", "dealer")
                        },
                    )
                elif module == "sec":
                    value["events"] = [e for e in value["events"] if e["date"] < last]
        path = root / (INDEX[module] + ".json")
        index = read(path)
        index["turnoverStorage" if module == "turnover" else "storage"] = store.save()
        write(path, index)
    for name in ("alerts", "sec-radar"):
        path = root / (name + ".json")
        value = read(path)
        last = max((e["date"] for e in value["events"]), default=None)
        value["events"] = [e for e in value["events"] if e["date"] < last]
        write(path, value)
    path = root / "world-assets.json"
    world = read(path)
    for series in world["series"].values():
        if series.get("frequency") == "daily" and len(series.get("history", [])) > 1:
            series["history"].pop()
            series.update(
                asOf=series["history"][-1][0], latest=series["history"][-1][1]
            )
    write(path, world)


def audit(root, baseline=None):
    files = sorted(root.rglob("*.json"))
    sizes = {p.relative_to(root).as_posix(): p.stat().st_size for p in files}
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        data = repo / "research"
        shutil.copytree(root, data)
        previous(data)
        command(repo, "init", "-q")
        command(repo, "config", "user.name", "Storage audit")
        command(repo, "config", "user.email", "audit@example.invalid")
        command(repo, "add", ".")
        command(repo, "commit", "-qm", "Previous observed session replay")
        before = command(repo, "rev-parse", "HEAD").strip()
        command(repo, "gc", "--quiet")
        shutil.copytree(root, data, dirs_exist_ok=True)
        command(repo, "add", ".")
        loose = sum(
            len(
                zlib.compress(
                    b"blob "
                    + str(len(p.read_bytes())).encode()
                    + b"\0"
                    + p.read_bytes()
                )
            )
            for p in data.rglob("*.json")
        )
        command(repo, "commit", "-qm", "Latest actual snapshots")
        packed = command(
            repo,
            "pack-objects",
            "--revs",
            "--stdout",
            "--thin",
            data=b"HEAD\n^" + before + b"\n",
        )
        result = {
            "method": "one-session-replay-v1",
            "files": len(files),
            "bytes": sum(sizes.values()),
            "fileBytes": sizes,
            "baseline": baseline,
            "unchangedRunGitBytes": 0,
            "scheduledCommitsPerTradingWeek": 10,
            "expectedCommitsPerTradingDayMaximum": 2,
            "replayThinPackBytes": len(packed),
            "allSnapshotsCompressedBlobBytesBeforeDeltaPacking": loose,
            "expectedDailyPackedGrowthBytesRange": [len(packed), len(packed) * 2],
            "limitations": "Planning estimate from actual latest-session removal; 21 volume features rewound but volume aggregate metrics held fixed. Institutional estimates recomputed from archived official rows; this can also change fallback/adjustment flags. Provider corrections/backfills can exceed this estimate. Server packing/retention is GitHub-controlled; loose new blobs are larger. Existing PR history is retained, not force-rewritten.",
        }
        return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "docs/report/research",
    )
    p.add_argument("--baseline", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    result = audit(args.root, read(args.baseline) if args.baseline else None)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "fileBytes"}, indent=2))
