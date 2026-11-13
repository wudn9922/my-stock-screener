#!/usr/bin/env python3
"""^TWII-only official share-volume repair; dry-run by default; never pushes Git.

Use with kline-archive-official-repair.yml. The publish job requires the exact
prepared archive SHA, non-force push, and a pre-publication backup artifact.
"""
from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import gzip
import io
import json
from pathlib import Path
import sys
import subprocess

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
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII"


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


def plan(rows, official, yahoo_v8=()):
    """Try same-unit raw Yahoo v8 first; only residual zeros use calibrated FMTQIK."""
    yahoo_v8 = list(yahoo_v8)
    first, direct = volume.fill_archive_from_yahoo(rows, yahoo_v8)
    # FMTQIK is a fallback ONLY when raw v8 has no positive volume.
    # A v8 price mismatch is a rejection, never a license to prefer FMTQIK.
    yahoo_has_volume = set()
    for row in yahoo_v8:
        try:
            if Decimal(str(row[5])).is_finite() and Decimal(str(row[5])) > 0:
                yahoo_has_volume.add(str(row[0])[:10])
        except (IndexError, InvalidOperation, ValueError, TypeError):
            continue
    factor, stats = volume.calibrated_ratio({r[0]: r[5] for r in rows}, official)
    fallback_official = {day: item for day, item in official.items()
                         if day not in yahoo_has_volume}
    proposed, fallback = volume.fill_rows(first, fallback_official, factor)
    changes = sorted(direct + [{**item, "source": "fmtqik"} for item in fallback],
                     key=lambda item: item["date"])
    targets = [r for r in rows if r[0] >= volume.START_DATE and volume._missing_or_zero(r[5])]
    changed_days = {c["date"] for c in changes}
    skipped = {r[0]: ("yahoo-v8-unavailable-or-close-mismatch; "
                       + ("fmtqik-unstable-or-insufficient-ratio" if factor is None else
                          "fmtqik-missing-or-close-mismatch"))
               for r in targets if r[0] not in changed_days}
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
    if any(c["source"] == "fmtqik" for c in changes) and (
            stats["reason"] != "stable" or stats["overlap"] < volume.MIN_OVERLAP):
        raise ValueError("FMTQIK calibration is not verified")
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
    direct = {c["date"]: {"close": c["yahooClose"], "volume": c["new"]}
              for c in changes if c["source"] == "yahoo-v8"}
    event = dict(
        symbol=SYMBOL, date=changed[0], dates=changed, status="corrected",
        old={"sha256": old_sha},
        new={"path": year, "sha256": after_sha, "rowsChanged": len(changed)},
        reason=REASON, sourceUrl=YAHOO_URL if direct else SOURCE_URL, officialBasis=evidence,
        volumeSources={c["date"]: c["source"] for c in changes}, yahooV8=direct,
        backupFile=BACKUP_NAME, fetchedAt=datetime.now(timezone.utc).isoformat(),
    )
    event["id"] = archive.digest(json.dumps(event, sort_keys=True).encode())
    event["loggedAt"] = datetime.now(timezone.utc).isoformat()
    archive.add_events(root, [event])
    archive.atomic(path, after)
    archive.verify(root, base=base, refresh_manifest=True)
    return True



def get_yahoo_v8(range_="2y", log=print):
    """Fetch *unmodified* Yahoo v8 Chart rows; never use merged/cache/FMTQIK bars."""
    if __package__:
        from .build_universe_full import Network
        from .universe_sources import parse_yahoo_chart
    else:
        from build_universe_full import Network
        from universe_sources import parse_yahoo_chart
    rows, _, _ = parse_yahoo_chart(Network().chart(SYMBOL, range_), SYMBOL)
    for row in rows[-20:]:
        log(f"Yahoo raw {SYMBOL} {row[0]} volume={row[5]}")
    return rows


def diagnose_yahoo(log=print):
    """A live source probe: failure is explicitly unknown, never a claim of zero."""
    try:
        rows = get_yahoo_v8("1mo", log)
        return dict(status="verified", observed=len(rows[-20:]),
                    zeroDates=[r[0] for r in rows[-20:] if r[5] == 0])
    except Exception as exc:  # noqa: BLE001
        log(f"Yahoo ^TWII live probe unavailable: {type(exc).__name__}: {exc}")
        return dict(status="unavailable", observed=0, zeroDates=[])


def recent_yahoo_backfill(root, base, now=None, yahoo_v8=None, log=print):
    """Daily publisher: correct only the last 10 completed XTAI sessions, Yahoo v8.

    A new event is anchored to the *pre-publish Git baseline*, so a correction
    and normal append in the same year have one exact auditable hash transition.
    No network failure is interpreted as a zero. Never force-push or edit OHLC.
    """
    if not base:
        return []
    import market_sessions as sessions
    import universe_cache as uc
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Timezone-aware clock required")
    today = now.astimezone(timezone.utc).date()
    try:
        recent = sorted(day for day in sessions.exchange_sessions(
            "tw", (today - timedelta(days=45)).isoformat(), today.isoformat())
            if uc.session_done(day, "TW", now))[-10:]
    except Exception as exc:  # noqa: BLE001 - optional repair must not halt other archive updates
        log(f"^TWII daily backfill skipped: calendar unavailable ({type(exc).__name__})")
        return []
    recent = {day for day in recent if day >= volume.START_DATE}
    if not recent:
        return []
    root = Path(root)
    years = sorted({day[:4] for day in recent})
    targets = {}
    for year in years:
        path = root / f"tw/^TWII/{year}.csv"
        if not path.is_file():
            continue
        rows = archive.read_rows(path, strict=True)
        if any(r[0] in recent and r[5] in ("", "0") for r in rows):
            targets[year] = (path, rows)
    if not targets:
        return []
    if yahoo_v8 is None:
        try:
            yahoo_v8 = get_yahoo_v8("1mo", log)
        except Exception as exc:  # noqa: BLE001 - read-only Yahoo failure keeps archive
            log(f"^TWII daily Yahoo v8 backfill unavailable: {type(exc).__name__}: {exc}")
            return []
    all_changes = []
    for year, (path, rows) in targets.items():
        proposed, changes = volume.fill_archive_from_yahoo(rows, yahoo_v8, recent)
        if not changes:
            continue
        relative = f"tw/^TWII/{year}.csv"
        try:
            baseline_bytes = subprocess.check_output(
                ["git", "-C", str(root), "show", f"{base}:{relative}"])
        except subprocess.CalledProcessError:
            # A first-ever annual file has no old commit to correct. Leave its
            # newly appended zeros to the next run, after a baseline exists.
            log(f"^TWII daily backfill skipped: no baseline file for {year}")
            continue
        # A normal append may have preceded this repair; verify will check the
        # union is append-only except exactly these old 0->positive changes.
        old = archive._read_rows(io.StringIO(baseline_bytes.decode()),
                                 relative, strict=True)
        before = {r[0]: r for r in old}
        if any(c["date"] not in before or before[c["date"]][5] not in ("", "0")
               for c in changes):
            raise ValueError("Daily TWII repair may not alter newly created/nonzero rows")
        if any(r[:5] != before[r[0]][:5] for r in proposed if r[0] in before):
            raise ValueError("Daily TWII repair may not change baseline OHLC")
        content = archive.csv_text(proposed)
        event = dict(
            symbol=SYMBOL, date=changes[0]["date"],
            dates=[c["date"] for c in changes], status="corrected",
            old={"sha256": archive.digest(baseline_bytes)},
            new={"path": relative, "sha256": archive.digest(content.encode()),
                 "rowsChanged": len(changes)},
            reason=REASON, sourceUrl=YAHOO_URL, mode="daily",
            volumeSources={c["date"]: "yahoo-v8" for c in changes},
            yahooV8={c["date"]: {"close": c["yahooClose"], "volume": c["new"]}
                     for c in changes},
            fetchedAt=now.isoformat())
        event["id"] = archive.digest(json.dumps(event, sort_keys=True).encode())
        event["loggedAt"] = datetime.now(timezone.utc).isoformat()
        archive.add_events(root, [event])
        archive.atomic(path, content)
        for c in changes:
            log(f"^TWII daily {c['date']} Volume {c['old']} → {c['new']} (來源 yahoo-v8)")
        all_changes.extend(changes)
    return all_changes


def run(root, backup_path, apply_changes=False, fetch_json=None, offline=False,
        yahoo_v8=None):
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
    probe = dict(status="offline") if offline else dict(status="unavailable")
    if yahoo_v8 is None and not offline:
        try:
            yahoo_v8 = get_yahoo_v8("2y")
            probe = dict(status="verified", observed=len(yahoo_v8),
                         zeroDates=[r[0] for r in yahoo_v8[-20:] if r[5] == 0])
        except Exception as exc:  # noqa: BLE001
            print(f"^TWII Yahoo v8 unavailable: {type(exc).__name__}: {exc}")
    official, errors = official_for_archive(fetch_json, original)
    proposed, changes, skipped, stats = plan(original, official, yahoo_v8 or ())
    print(f"^TWII Yahoo live probe: {probe}")
    print(f"^TWII official calibration: {stats}; officialErrors={len(errors)}")
    for item in changes:
        print(f"{item['date']} ^TWII Volume {item['old']} → {item['new']} (來源 {item['source']})")
    for day, reason in sorted(skipped.items()):
        print(f"{day} ^TWII Volume unchanged: {reason}")
    if stats["reason"] != "stable":
        print("^TWII FMTQIK calibration refused; Yahoo v8 verified direct-volume fixes remain eligible")
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
