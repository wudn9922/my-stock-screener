#!/usr/bin/env python3
"""^TWII-only official share-volume repair; dry-run by default; never pushes Git.

Use with kline-archive-official-repair.yml. The publish job requires the exact
prepared archive SHA, non-force push, and a pre-publication backup artifact.
"""
from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, timezone
import gzip
import json
from pathlib import Path
import sys

if __package__:
    from . import kline_archive as archive
    from . import twii_volume as volume
    from .repair_kline_archive import git, outside, write_json
else:
    import kline_archive as archive
    import twii_volume as volume
    from repair_kline_archive import git, outside, write_json

SYMBOL = "^TWII"
REASON = "kline-archive/official-twii-volume"
BACKUP_NAME = "twii-volume-backup.json"
SOURCE_URL = "https://www.twse.com.tw/exchangeReport/FMTQIK?response=json"


def official_for_archive(fetch_json, rows, log=print):
    """Fetch months from four calibration months before START_DATE through the archive tip."""
    end = volume.month_start(rows[-1][0])
    begin = list(volume.months_back(volume.START_DATE))[0]
    count = (end.year - begin.year) * 12 + end.month - begin.month + 1
    if not 1 <= count <= 24:
        raise ValueError("TWII volume repair outside bounded official request window")
    official, errors = {}, []
    for offset in range(count):
        index = begin.year * 12 + begin.month - 1 + offset
        year, month = divmod(index, 12)
        day = date(year, month + 1, 1)
        url = volume.fmtqik_url(day)
        try:
            parsed = volume.parse_fmtqik(fetch_json(url), day)
            official.update(parsed)
        except Exception as exc:  # noqa: BLE001 - wrong-date/HTML/transport month is discarded
            error = f"{day:%Y-%m}: {type(exc).__name__}: {exc}"
            errors.append(error)
            log("⚠️ ^TWII FMTQIK skipped: " + error)
    return official, errors


def plan(rows, official):
    """Only zero/empty ^TWII Volume on/after 2026-11-05 may change."""
    existing = {r[0]: r[5] for r in rows}
    factor, stats = volume.calibrated_ratio(existing, official)
    proposed, changes = volume.fill_rows(rows, official, factor)
    targets = [r for r in rows if r[0] >= volume.START_DATE and r[5] in ("", "0")]
    skipped = {r[0]: ("unstable-or-insufficient-ratio" if factor is None else
                       "official-missing-or-close-mismatch")
               for r in targets if r[0] not in {c["date"] for c in changes}}
    return proposed, changes, skipped, stats


def make_backup(root, path, base, paths):
    root, path = Path(root).resolve(), outside(root, path)
    if path.exists():
        raise ValueError("Recovery backup already exists; refusing overwrite")
    if git(root, "rev-parse", "HEAD") != base:
        raise ValueError("Archive advanced before backup")
    files = []
    for relative in paths:
        source = root / relative
        if not source.is_file():
            raise ValueError("Missing required recovery input: " + relative)
        contents = source.read_bytes()
        files.append(dict(path=relative, sha256=archive.digest(contents),
                          gzipBase64=base64.b64encode(gzip.compress(contents, mtime=0)).decode()))
    backup = dict(version=1, operation="twii-volume", archiveCommit=base, files=files)
    write_json(path, backup, exclusive=True)
    return backup


def apply(root, rows, proposed, changes, stats, backup_path):
    """Preflight ALL changes; write only the ^TWII year CSV plus audit/derived indexes."""
    root = Path(root).resolve()
    if not changes:
        return False
    if git(root, "status", "--porcelain"):
        raise ValueError("Repair requires a clean isolated kline-archive checkout")
    if archive.json_read(root / "manifest.json", {}).get("version") != 3:
        raise ValueError("Volume repair requires v3 archive indexes")
    if stats["reason"] != "stable" or stats["overlap"] < volume.MIN_OVERLAP:
        raise ValueError("Calibration is not verified")
    if any(r[0][:4] != "2026" for r in rows):
        raise ValueError("Volume repair expected one 2026 annual file")
    year = "tw/^TWII/2026.csv"
    path = root / year
    old = archive.read_rows(path, strict=True)
    if old != rows:
        raise ValueError("Archive changed since volume planning")
    if len(old) != len(proposed) or [r[:5] for r in old] != [r[:5] for r in proposed]:
        raise ValueError("Refusing date/OHLC modification")
    changed = [new[0] for prev, new in zip(old, proposed) if prev != new]
    if changed != [r["date"] for r in changes] or any(
        prev[5] not in ("", "0") or int(new[5]) <= 0 or prev[0] < volume.START_DATE
        for prev, new in zip(old, proposed) if prev != new
    ):
        raise ValueError("Refusing non-zero or out-of-scope update")
    base = git(root, "rev-parse", "HEAD")
    index = "index/" + archive.instrument_url_path(SYMBOL) + ".json"
    backup = make_backup(root, backup_path, base, [year, index, archive.LOG, "manifest.json"])
    print(f"Recovery backup saved before local write: {backup_path} ({len(backup['files'])} files)")
    old_sha = archive.digest(path.read_bytes())
    after = archive.csv_text(proposed)
    after_sha = archive.digest(after.encode())
    evidence = dict(stats)
    event = dict(
        symbol=SYMBOL, date=changed[0], dates=changed, status="corrected",
        old={"sha256": old_sha},
        new={"path": year, "sha256": after_sha, "rowsChanged": len(changed)},
        reason=REASON, sourceUrl=SOURCE_URL, officialBasis=evidence,
        backupFile=BACKUP_NAME, fetchedAt=datetime.now(timezone.utc).isoformat(),
    )
    event["id"] = archive.digest(json.dumps(event, sort_keys=True).encode())
    event["loggedAt"] = datetime.now(timezone.utc).isoformat()
    archive.add_events(root, [event])
    archive.atomic(path, after)
    archive.verify(root, base=base, refresh_manifest=True)
    return True


def diagnose_yahoo(log=print):
    """A live source probe: failure is explicitly unknown, never a claim of zero."""
    try:
        if __package__:
            from .build_universe_full import Network
            from .universe_sources import parse_yahoo_chart
        else:
            from build_universe_full import Network
            from universe_sources import parse_yahoo_chart
        client = Network()
        payload = client.chart(SYMBOL, "1mo")
        rows, _, _ = parse_yahoo_chart(payload, SYMBOL)
        recent = rows[-20:]
        for row in recent:
            log(f"Yahoo raw {SYMBOL} {row[0]} volume={row[5]}")
        zeros = [row[0] for row in recent if row[5] == 0]
        return dict(status="verified", observed=len(recent), zeroDates=zeros)
    except Exception as exc:  # noqa: BLE001 - avoid inaccurate attribution on transport failure
        log(f"Yahoo ^TWII live probe unavailable: {type(exc).__name__}: {exc}")
        return dict(status="unavailable", observed=0, zeroDates=[])


def run(root, backup_path, apply_changes=False, fetch_json=None, offline=False):
    root = Path(root).resolve()
    backup_path = outside(root, backup_path)
    if git(root, "status", "--porcelain"):
        raise ValueError("Volume repair requires a clean isolated archive checkout")
    base = git(root, "rev-parse", "HEAD")
    original = archive.symbol_rows(root, SYMBOL, {"2026"})
    if not original:
        raise ValueError("No archived ^TWII 2026 rows")
    if fetch_json is None:
        if offline:
            raise ValueError("Offline repair requires an injected official fixture")
        import requests
        session = requests.Session()
        session.headers["User-Agent"] = "Mozilla/5.0"
        def fetch_json(url):
            response = session.get(url, timeout=25, allow_redirects=False)
            response.raise_for_status()
            return response.json()
    probe = dict(status="offline") if offline else diagnose_yahoo()
    official, errors = official_for_archive(fetch_json, original)
    proposed, changes, skipped, stats = plan(original, official)
    print(f"^TWII Yahoo live probe: {probe}")
    print(f"^TWII official calibration: {stats}; officialErrors={len(errors)}")
    for item in changes:
        print(f"{item['date']} ^TWII Volume {item['old']} → {item['new']} | ratio={stats['ratio']:.12g} | overlap={stats['overlap']}")
    for day, reason in sorted(skipped.items()):
        print(f"{day} ^TWII Volume unchanged: {reason}")
    if stats["reason"] != "stable":
        print("^TWII repair refused: calibration not stable/insufficient; no guessed volumes")
    applied = apply(root, original, proposed, changes, stats, backup_path) if apply_changes else False
    print(f"^TWII repair mode={'apply' if apply_changes else 'dry-run'} changedRows={len(changes) if applied else 0}; archiveCommit={base}")
    return dict(probe=probe, calibration=stats, changes=changes, skipped=skipped,
                applied=applied, officialErrors=errors)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", required=True, type=Path)
    parser.add_argument("--backup", type=Path, default=Path(".cache/repair-output") / BACKUP_NAME)
    parser.add_argument("--apply", action="store_true", help="Manual maintainer workflow_dispatch only")
    args = parser.parse_args(argv)
    run(args.archive_dir, args.backup, args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
