"""Permanent full-market daily bars; archive records never depend on Actions cache retention.

Only this module writes archive CSVs. Daily imports append; backfill inserts missing
dates with an audit event; a cache-proven adjustment may replace matching dates.
All existing dates and unlisted symbols survive every operation.
"""
import argparse
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import kline_integrity as ki
import universe_cache as uc

BRANCH = "kline-archive"
FIELDS = ["Date", "Open", "High", "Low", "Close", "Volume"]
LOG = "docs/report/kline-corrections.json"
CONFLICTS = "state/conflicts.json"
PROGRESS = "state/backfill.json"
SYMBOL = re.compile(r"^[A-Z0-9^][A-Z0-9.^=_-]{0,39}$")
README = """# Permanent full-market daily K-lines

This orphan branch is independent of main, generated and GitHub Pages.
Never delete symbols, squash history, force-push, or expire records.

Layout: tw/<YAHOO_SYMBOL>/<YEAR>.csv and us/<YAHOO_SYMBOL>/<YEAR>.csv.
Date,Open,High,Low,Close,Volume; Yahoo split-adjusted OHLC, NOT dividend-adjusted.
Prices have four decimals; volume is integer shares or empty when unknown.
Ordinary daily updates only append completed sessions accepted by universe-full.
Backfill inserts missing dates with audit records, never replaces disagreements.
Only a cache-proven history adjustment may correct existing dates; every change
has old/new values in docs/report/kline-corrections.json. Dates are never removed.
state/ retains resumable backfill progress and deduplicated conflicts.
manifest.json reports the actual symbols, market date bounds, rows and byte size.

Run the verification/publishing tools from main: scripts/kline_archive.py and
scripts/kline_archive_git.py. See docs/KLINE_ARCHIVE.md on main for operations.
"""


def digest(value):
    return hashlib.sha256(value).hexdigest()


def atomic(path, content):
    path = Path(path)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    uc.write_atomic(str(path), content)


def json_write(path, value):
    atomic(path, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")


def json_read(path, default):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def market_of(symbol):
    if not SYMBOL.fullmatch(symbol) or symbol in (".", ".."):
        raise ValueError(f"Unsafe symbol: {symbol!r}")
    return "tw" if symbol.endswith((".TW", ".TWO")) else "us"


def normalize(row):
    """Validate before rounding; missing volume stays empty, never becomes zero."""
    day = str(row[0])[:10]
    if date.fromisoformat(day).isoformat() != day or len(row) != 6:
        raise ValueError("Invalid bar date/column count")
    values = []
    for value in row[1:5]:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError("Nonpositive/nonfinite OHLC")
        values.append(number)
    if not all(math.isfinite(float(v)) for v in values):
        raise ValueError("OHLC exceeds the cache's finite numeric range")
    o, h, l, c = values
    if not l <= min(o, c) <= max(o, c) <= h:
        raise ValueError("Invalid OHLC bounds")
    # Match universe_cache._price's existing Python round(..., 4) exactly; backfill
    # and daily cache must not manufacture a conflict at a binary half-way value.
    prices = [f"{round(float(v), 4):.4f}" for v in values]
    if any(Decimal(v) <= 0 for v in prices):
        raise ValueError("OHLC cannot be represented as positive four-decimal prices")
    volume = row[5]
    if volume is None or str(volume).lower() in ("", "nan", "<na>", "none"):
        volume = ""
    else:
        number = Decimal(str(volume))
        if not number.is_finite() or number < 0:
            raise ValueError("Invalid volume")
        volume = str(int(number.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)))
    return (day, *prices, volume)


def csv_text(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(FIELDS)
    writer.writerows(rows)
    return stream.getvalue()


def read_rows(path, strict=False):
    with Path(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        if next(reader, None) != FIELDS:
            raise ValueError(f"Invalid CSV header: {path}")
        result = []
        for raw in reader:
            try:
                row = normalize(raw)
                if strict and tuple(raw) != row:
                    raise ValueError("Noncanonical price/volume formatting")
                if result and row[0] <= result[-1][0]:
                    raise ValueError("Dates are not strictly increasing")
                result.append(row)
            except (ValueError, InvalidOperation) as exc:
                raise ValueError(f"{path}: {raw[:1]}: {exc}") from exc
    if strict and not result:
        raise ValueError(f"Empty archive CSV: {path}")
    return result


def archive_files(root):
    root = Path(root)
    result = []
    for market in ("tw", "us"):
        for path in sorted((root / market).rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Archive symlink forbidden: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if len(relative.parts) != 3 or path.suffix != ".csv" or not re.fullmatch(r"\d{4}", path.stem):
                raise ValueError(f"Invalid archive path: {relative}")
            if market_of(relative.parts[1]) != market:
                raise ValueError(f"Wrong market: {relative}")
            result.append(path)
    return result


def symbol_rows(root, symbol, years=None):
    rows = []
    for path in sorted((Path(root) / market_of(symbol) / symbol).glob("*.csv")):
        if years is not None and path.stem not in years:
            continue
        part = read_rows(path, strict=True)
        if any(row[0][:4] != path.stem for row in part):
            raise ValueError(f"CSV year mismatch: {path}")
        rows.extend(part)
    return rows


def latest_rows(root, symbol, count):
    rows = []
    for path in sorted((Path(root) / market_of(symbol) / symbol).glob("*.csv"), reverse=True):
        part = read_rows(path, strict=True)
        if any(row[0][:4] != path.stem for row in part):
            raise ValueError(f"CSV year mismatch: {path}")
        rows = part + rows
        if len(rows) >= count:
            break
    return rows[-count:]


def first_date(root, symbol):
    paths = sorted((Path(root) / market_of(symbol) / symbol).glob("*.csv"))
    return read_rows(paths[0], strict=True)[0][0] if paths else None


def values(row):
    return {field: (None if value == "" else float(value)) for field, value in zip(FIELDS[1:], row[1:])}


def event(symbol, row, old, status, reason, fetched_at=None):
    data = dict(symbol=symbol, date=row[0], status=status,
                old=values(old) if old else None, new=values(row),
                sourceUrl="https://query1.finance.yahoo.com/v8/finance/chart/" + symbol,
                fetchedAt=fetched_at, reason=reason)
    data["id"] = digest(json.dumps(data, sort_keys=True).encode())
    data["loggedAt"] = ki.utcnow()
    return data


def add_events(root, events):
    if not events:
        return
    path = Path(root) / LOG
    current = ki.read_log(path)
    # Same schema and union mechanism as permanent data/*.csv correction records.
    merged = ki.merge_logs(current, {"version": 1, "events": events})
    json_write(path, merged)
    ki._load_log.cache_clear()


def merge_main_log(root, destination=None):
    source = Path(root) / LOG
    if not source.exists():
        return
    destination = Path(destination or ki.LOG_PATH)
    incoming = {"version": 1, "events": [e for e in ki.read_log(source)["events"]
                if e["status"] == "corrected" and str(e.get("reason", "")).startswith("kline-archive/adjustment:")]}
    current = ki.read_log(destination)
    known = {e["id"]: e for e in current["events"]}
    if not incoming["events"] or all(known.get(e["id"]) == e for e in incoming["events"]):
        return False
    merged = ki.merge_logs(current, incoming)
    if merged == current:
        return False
    # Preserve the existing main journal's human-readable format; no formatting-only pushes.
    atomic(destination, json.dumps(merged, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    ki._load_log.cache_clear()
    return True


def conflict(root, symbol, row, old, reason):
    path = Path(root) / CONFLICTS
    state = json_read(path, {"version": 1, "conflicts": {}})
    value = dict(symbol=symbol, date=row[0], old=list(old) if old else None,
                 incoming=list(row), reason=reason)
    key = digest(json.dumps(value, sort_keys=True).encode())
    if key not in state["conflicts"]:
        state["conflicts"][key] = {**value, "firstObservedAt": ki.utcnow()}
        json_write(path, state)


def put_symbol(root, symbol, incoming, mode="daily", correction=None, fetched_at=None):
    """Plan first; all writes retain the union of existing dates. No deletion API exists."""
    if mode not in ("daily", "backfill"):
        raise ValueError("Invalid import mode")
    incoming = [normalize(row) for row in incoming]
    if any(b[0] <= a[0] for a, b in zip(incoming, incoming[1:])):
        raise ValueError("Incoming dates must be sorted and unique")
    years = {r[0][:4] for r in incoming}
    paths = sorted((Path(root) / market_of(symbol) / symbol).glob("*.csv"))
    if paths:
        years.add(paths[-1].stem)  # global last date, even for a backfill of an old year
    current = symbol_rows(root, symbol, None if correction else years)
    by_date = {row[0]: row for row in current}
    last = current[-1][0] if current else None
    if correction:
        witness = correction.get("comparison", {})
        old_close, new_close = witness.get("oldClose"), witness.get("newClose")
        source = {row[0]: row for row in incoming}
        if (not correction.get("id") or not old_close or not new_close
                or abs(new_close / old_close - 1) <= uc.PRICE_TOLERANCE
                or witness.get("date") not in source
                or abs(float(source[witness["date"]][4]) / new_close - 1) > 0.0001):
            raise ValueError("Adjustment lacks a cache comparison witness")
        # A fresh max download must cover every archived earlier session. A short or
        # truncated provider response never mixes old/new split bases in one symbol.
        if current and any(row[0] not in source for row in current if row[0] <= incoming[-1][0]):
            conflict(root, symbol, incoming[0], None, "adjustment-source-does-not-cover-retained-history")
            return {"added": 0, "corrected": 0, "conflicts": 1, "blocked": True}
    counts = {"added": 0, "corrected": 0, "conflicts": 0, "blocked": False}
    changes = set()
    past_insertions = {}
    events = []
    for row in incoming:
        old = by_date.get(row[0])
        if old == row:
            continue
        if old:
            if correction:
                by_date[row[0]] = row
                changes.add(row[0][:4])
                counts["corrected"] += 1
                events.append(event(symbol, row, old, "corrected",
                                    "kline-archive/adjustment:" + correction["id"], fetched_at))
            else:
                conflict(root, symbol, row, old, "existing-row-disagrees")
                counts["conflicts"] += 1
        elif not last or row[0] > last or mode == "backfill":
            by_date[row[0]] = row
            changes.add(row[0][:4])
            counts["added"] += 1
            if last and row[0] <= last:
                past_insertions.setdefault(row[0][:4], []).append(row[0])
        # Daily cache windows do not fill past holes. Only explicit backfill does.
    planned = {}
    for year in sorted(changes):
        rows = [by_date[day] for day in sorted(by_date) if day.startswith(year + "-")]
        path = Path(root) / market_of(symbol) / symbol / (year + ".csv")
        planned[path] = csv_text(rows)
        if year in past_insertions:
            record = dict(symbol=symbol, date=min(past_insertions[year]), status="backfilled",
                          old={"sha256": digest(path.read_bytes())} if path.exists() else None,
                          new={"path": path.relative_to(root).as_posix(), "sha256": digest(planned[path].encode()), "rowsAdded": len(past_insertions[year])},
                          sourceUrl="https://query1.finance.yahoo.com/v8/finance/chart/" + symbol,
                          fetchedAt=fetched_at, reason="kline-archive/backfill-year")
            record.update(id=digest(json.dumps(record, sort_keys=True).encode()), loggedAt=ki.utcnow())
            events.append(record)
    add_events(root, events)
    for path, content in planned.items():
        atomic(path, content)
    return counts


def update_manifest(root, write=True):
    root = Path(root)
    markets = {key: {"symbols": [], "firstDate": None, "lastDate": None, "rows": 0, "csvBytes": 0} for key in ("tw", "us")}
    for path in archive_files(root):
        rows = read_rows(path, strict=True)
        if any(row[0][:4] != path.stem for row in rows):
            raise ValueError(f"CSV year mismatch: {path}")
        market, symbol, _ = path.relative_to(root).parts
        part = markets[market]
        part["symbols"].append(symbol)
        part["firstDate"] = min(part["firstDate"] or rows[0][0], rows[0][0])
        part["lastDate"] = max(part["lastDate"] or rows[-1][0], rows[-1][0])
        part["rows"] += len(rows)
        part["csvBytes"] += path.stat().st_size
    for part in markets.values():
        part["symbols"] = sorted(set(part["symbols"]))
        part["symbolCount"] = len(part["symbols"])
    manifest = {"version": 1, "priceBasis": "Yahoo split-adjusted; not dividend-adjusted",
                "markets": markets, "csvBytes": sum(p["csvBytes"] for p in markets.values()), "totalBytes": 0}
    # Fixed point includes the manifest's own byte count, plus README/audit/progress.
    other = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()
                and ".git" not in p.relative_to(root).parts and p.name != "manifest.json")
    for _ in range(10):
        size = other + len((json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())
        if manifest["totalBytes"] == size:
            break
        manifest["totalBytes"] = size
    if write:
        json_write(root / "manifest.json", manifest)
    return manifest


def initialize(root):
    root = Path(root)
    if any(root.glob("tw/*/*.csv")) or any(root.glob("us/*/*.csv")):
        raise ValueError("Initialization refuses an existing archive")
    atomic(root / "README.md", README)
    json_write(root / LOG, {"version": 1, "quarantined": [], "events": []})
    return update_manifest(root)


def stage_cache(cache, archive, output, now=None, previous=None):
    """Immutable transport: only accepted, complete cache entries; never current sessions."""
    now = now or datetime.now(timezone.utc)
    cache, output = Path(cache), Path(output)
    plan = {"version": 1, "mode": "daily", "symbols": [], "skipped": []}
    for symbol, entry in sorted(uc.load_state(str(cache))["symbols"].items()):
        try:
            market_of(symbol)
        except ValueError as exc:
            plan["skipped"].append({"symbol": symbol, "reason": str(exc)})
            continue
        if not uc.is_complete(entry):
            continue
        path = Path(uc.bars_path(str(cache), symbol))
        if not path.exists():
            continue
        try:
            rows = read_rows(path)
            if not rows or rows[-1][0] != entry["last"]:
                raise ValueError("Cache CSV/state checkpoint disagree")
            receipt_path = cache / "archive-adjustments" / (symbol + ".json")
            receipt = json_read(receipt_path, None)
            if receipt and not pending_adjustment(archive, cache, symbol):
                receipt = None
            if receipt:
                full = receipt_path.with_suffix(".csv")
                if digest(full.read_bytes()) != receipt["sha256"]:
                    raise ValueError("Adjustment payload digest mismatch")
                fresh = read_rows(full, strict=True)
                by_date = {r[0]: r for r in fresh}
                by_date.update({r[0]: r for r in rows if r[0] > fresh[-1][0]})
                rows = [by_date[day] for day in sorted(by_date)]
            if any(not uc.session_done(row[0], "TW" if market_of(symbol) == "tw" else "US", now) for row in rows):
                raise ValueError("Cache includes an unfinished session")
            existing = symbol_rows(archive, symbol, {r[0][:4] for r in rows})
            known = {r[0]: r for r in existing}
            # Changed overlaps are retained so the publisher can report conflicts.
            rows = rows if receipt else [r for r in rows if known.get(r[0]) != r]
            if not rows:
                continue
            relative = f"{market_of(symbol)}/{symbol}.csv"
            content = csv_text(rows)
            atomic(output / relative, content)
            plan["symbols"].append({"symbol": symbol, "file": relative, "sha256": digest(content.encode()), "correction": receipt})
        except (ValueError, InvalidOperation, KeyError, OSError) as exc:
            plan["skipped"].append({"symbol": symbol, "reason": str(exc)})
    if previous and (Path(previous) / "operations.json").exists():
        if (output / "baseline").exists():
            raise ValueError("Baseline transport destination must be fresh")
        shutil.copytree(previous, output / "baseline")
        plan["baseline"] = True
    json_write(output / "operations.json", plan)
    return plan


def apply_operations(archive, payload, main_log=None, now=None):
    archive, payload = Path(archive), Path(payload)
    plan = json_read(payload / "operations.json", None)
    if not plan or plan.get("version") != 1 or plan.get("mode") not in ("daily", "backfill"):
        raise ValueError("Invalid archive transport")
    prepared = []
    now = now or datetime.now(timezone.utc)
    for item in plan["symbols"]:
        expected = f"{market_of(item['symbol'])}/{item['symbol']}.csv"
        if item["file"] != expected:
            raise ValueError("Unsafe payload path")
        path = payload / expected
        if path.is_symlink() or digest(path.read_bytes()) != item["sha256"]:
            raise ValueError("Transport CSV digest mismatch")
        rows = read_rows(path, strict=True)
        market = "TW" if market_of(item["symbol"]) == "tw" else "US"
        if any(not uc.session_done(row[0], market, now) for row in rows):
            raise ValueError("Archive transport includes an unfinished session")
        prepared.append((item, rows))
    totals = {"added": 0, "corrected": 0, "conflicts": 0, "blocked": 0}
    if plan.get("baseline"):
        # Retain already-accepted bars BEFORE rolling eviction / unlisted pruning,
        # then apply the latest source and any proven correction. No old date is lost.
        baseline = apply_operations(archive, payload / "baseline", now=now)
        for key in totals:
            totals[key] += baseline[key]
    for item, rows in prepared:
        result = put_symbol(archive, item["symbol"], rows, plan["mode"], item.get("correction"), item.get("fetchedAt"))
        for key in totals:
            totals[key] += result[key]
    if "progress" in plan:
        # The serialized publisher is the sole progress writer. Never remove old entries.
        progress = json_read(archive / PROGRESS, {"version": 1, "symbols": {}})
        for symbol, entry in plan["progress"]["symbols"].items():
            current = progress["symbols"].get(symbol, {})
            if current.get("status") == "done" and current.get("targetStart", "9999") <= entry.get("targetStart", ""):
                continue
            progress["symbols"][symbol] = entry
        progress.update({k: v for k, v in plan["progress"].items() if k != "symbols"})
        json_write(archive / PROGRESS, progress)
    update_manifest(archive)
    if main_log:
        merge_main_log(archive, main_log)
    return totals


def stage_adjustment(cache, symbol, frame, comparison, now):
    market_of(symbol)
    rows = [normalize((day.strftime("%Y-%m-%d"), *row)) for day, row in zip(frame.index, frame.itertuples(index=False, name=None))]
    content = csv_text(rows)
    receipt = {"version": 1, "symbol": symbol, "comparison": comparison,
               "sha256": digest(content.encode()), "fetchedAt": now.isoformat(),
               "sourceUrl": "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol}
    receipt["id"] = digest(json.dumps(receipt, sort_keys=True).encode())
    path = Path(cache) / "archive-adjustments" / (symbol + ".csv")
    atomic(path, content)
    json_write(path.with_suffix(".json"), receipt)


def pending_adjustment(root, cache, symbol):
    path = Path(cache) / "archive-adjustments" / (symbol + ".csv")
    if not path.exists():
        return path.with_suffix(".json").exists()
    try:
        rows = read_rows(path, strict=True)
    except (ValueError, InvalidOperation):
        return True
    source = {row[0]: row for row in rows}
    current = symbol_rows(root, symbol)
    return any(source.get(row[0]) != row for row in current if row[0] <= rows[-1][0])


def bootstrap(archive, cache, now=None):
    import pandas as pd
    now = now or datetime.now(timezone.utc)
    state = uc.load_state(str(cache))
    verify(archive)
    symbols = sorted({p.parent.name for p in archive_files(archive)})
    seeded = 0
    for symbol in symbols:
        target = Path(uc.bars_path(str(cache), symbol))
        if target.exists() and uc.is_complete(state["symbols"].get(symbol)):
            continue
        rows = latest_rows(archive, symbol, uc.KEEP_BARS)
        rows = [r for r in rows if uc.session_done(r[0], "TW" if market_of(symbol) == "tw" else "US", now)]
        if not rows:
            continue
        frame = pd.DataFrame([[float(v) if v else float("nan") for v in row[1:]] for row in rows],
                             index=pd.to_datetime([row[0] for row in rows]), columns=uc.PRICE_COLUMNS)
        uc.write_bars(str(target), frame, now=now)
        uc.record_frame(state, symbol, frame, now.date().isoformat(), full=True, verified=True)
        state["symbols"][symbol]["seededFromArchive"] = True
        seeded += 1
    uc.save_state(str(cache), state)
    return seeded


def _git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def verify(root, base=None, summary=None):
    root = Path(root)
    allowed = {"README.md", "manifest.json", LOG, CONFLICTS, PROGRESS}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            raise ValueError(f"Archive symlink forbidden: {relative}")
        if path.is_file() and relative.as_posix() not in allowed and relative.parts[0] not in ("tw", "us"):
            raise ValueError(f"Unexpected archive file: {relative}")
    if not (root / "README.md").is_file() or not (root / LOG).is_file():
        raise ValueError("Archive not initialized")
    files = archive_files(root)
    current = {}
    row_count = 0
    object_format = _git(root, "rev-parse", "--show-object-format") if base else "sha1"
    for path in files:
        rows = read_rows(path, strict=True)
        if any(r[0][:4] != path.stem for r in rows):
            raise ValueError(f"Year/date mismatch: {path}")
        row_count += len(rows)
        body = path.read_bytes()
        header = b"blob " + str(len(body)).encode() + b"\0"
        current[path.relative_to(root).as_posix()] = {
            "blob": hashlib.new(object_format, header + body).hexdigest(), "sha256": digest(body)}
    log = ki.read_log(root / LOG)
    if len({e["id"] for e in log["events"]}) != len(log["events"]):
        raise ValueError("Duplicate correction event identifiers")
    if base:
        old_tree = {}
        for item in _git(root, "ls-tree", "-rz", base, "--", "tw", "us").split("\0"):
            if not item:
                continue
            metadata, name = item.split("\t", 1)
            if not re.fullmatch(r"(?:tw|us)/[^/]+/\d{4}\.csv", name):
                raise ValueError(f"Invalid baseline archive path: {name}")
            old_tree[name] = metadata.split()[2]
        if any(name not in current for name in old_tree):
            raise ValueError("Archive file deleted")
        old_log = json.loads(_git(root, "show", f"{base}:{LOG}"))
        old_ids = {e["id"]: e for e in old_log["events"]}
        new_ids = {e["id"]: e for e in log["events"]}
        if any(new_ids.get(key) != value for key, value in old_ids.items()):
            raise ValueError("Correction history was removed or rewritten")
        audit = [e for e in log["events"] if e["id"] not in old_ids]
        old_last = {}
        changed = {}
        row_audit, file_audit = {}, {}
        for name in old_tree:
            symbol = name.split("/")[1]
            old_last[symbol] = max(old_last.get(symbol, name), name)
        for name, metadata in current.items():
            if old_tree.get(name) != metadata["blob"]:
                changed.setdefault(name.split("/")[1], []).append(name)
        for e in audit:
            row_audit.setdefault((e["symbol"], e["date"]), []).append(e)
            if e["status"] == "backfilled" and e.get("reason") == "kline-archive/backfill-year":
                file_audit.setdefault(e["new"]["path"], []).append(e)

        def baseline(name):
            blob = subprocess.check_output(["git", "-C", str(root), "show", f"{base}:{name}"])
            data = [normalize(r) for r in list(csv.reader(io.StringIO(blob.decode())))[1:]]
            return data, digest(blob)

        # Only metadata is kept for the whole market. OHLC rows are compared one
        # symbol/year at a time, so permanent history cannot exhaust runner RAM.
        for symbol, names in changed.items():
            last_name = old_last.get(symbol)
            last_rows, last_hash = baseline(last_name) if last_name else ([], None)
            last = last_rows[-1][0] if last_rows else ""
            for name in names:
                old_rows, old_hash = (last_rows, last_hash) if name == last_name else baseline(name) if name in old_tree else ([], None)
                rows = read_rows(root / name, strict=True)
                old = {r[0]: r for r in old_rows}
                if not old.keys() <= {r[0] for r in rows}:
                    raise ValueError(f"Archive dates deleted: {symbol}")
                file_state = old_hash
                for e in file_audit.get(name, []):
                    before = e.get("old")
                    if (before.get("sha256") if before else None) == file_state:
                        file_state = e["new"]["sha256"]
                backfilled = file_state != old_hash and file_state == current[name]["sha256"]
                for row in rows:
                    day = row[0]
                    before = old.get(day)
                    if before == row or (before is None and (day > last or backfilled)):
                        continue
                    state = values(before) if before else None
                    for e in row_audit.get((symbol, day), []):
                        if (e.get("old") == state and e["status"] == "corrected"
                                and str(e.get("reason", "")).startswith("kline-archive/adjustment:")):
                            state = e["new"]
                    if state != values(row):
                        raise ValueError(f"Unlogged historical modification: {symbol} {day}")
    stored = json_read(root / "manifest.json", None)
    # Verification is read-only: compute expected manifest in memory, restoring no files.
    actual_bytes = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts)
    csv_bytes = sum(p.stat().st_size for p in files)
    if not stored or stored != update_manifest(root, write=False) or stored["totalBytes"] != actual_bytes or stored["csvBytes"] != csv_bytes:
        raise ValueError("Archive manifest size does not match files")
    result = {"files": len(files), "rows": row_count, "totalBytes": actual_bytes, "csvBytes": csv_bytes}
    text = "### Permanent K-line archive\n\n" + "\n".join(f"- {k}: {v:,}" for k, v in result.items()) + "\n"
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(text)
    print(json.dumps(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["init", "stage", "apply", "bootstrap", "verify"])
    parser.add_argument("--archive-dir", required=True)
    parser.add_argument("--cache-dir", default=".cache/universe-full")
    parser.add_argument("--payload-dir", default=".cache/kline-payload")
    parser.add_argument("--previous-payload")
    parser.add_argument("--base")
    parser.add_argument("--main-log")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    args = parser.parse_args()
    if args.operation == "init":
        initialize(args.archive_dir)
    elif args.operation == "stage":
        plan = stage_cache(args.cache_dir, args.archive_dir, args.payload_dir, previous=args.previous_payload)
        print(json.dumps({"staged": len(plan["symbols"]), "skipped": plan["skipped"]}))
    elif args.operation == "apply":
        print(json.dumps(apply_operations(args.archive_dir, args.payload_dir, args.main_log)))
    elif args.operation == "bootstrap":
        print(json.dumps({"seeded": bootstrap(args.archive_dir, args.cache_dir)}))
    else:
        verify(args.archive_dir, args.base, args.summary)


if __name__ == "__main__":
    main()
