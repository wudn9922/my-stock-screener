"""Permanent full-market daily bars; archive records never depend on Actions cache retention.

Only this module writes archive CSVs. Daily imports append; backfill inserts missing
dates with an audit event; a cache-proven adjustment may replace matching dates.
All existing dates and unlisted symbols survive every operation.
"""
import argparse
import csv
from contextlib import contextmanager
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
import tempfile
import time

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
has per-year old/new SHA-256 audits in docs/report/kline-corrections.json.
Dates are never removed.
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


def frame_rows(frame):
    """Serialize source observations, not the calculation frame's zero fallback."""
    source = uc._volume_source_frame(frame)
    return [normalize((day.strftime("%Y-%m-%d"), *row))
            for day, row in zip(source.index, source.itertuples(index=False, name=None))]


def read_rows(path, strict=False):
    with Path(path).open(newline="", encoding="utf-8") as stream:
        return _read_rows(stream, path, strict)


def _read_rows(stream, path, strict=False):
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
                if e["status"] == "corrected" and str(e.get("reason", "")).startswith("kline-archive/adjustment-year:")]}
    for record in incoming["events"]:
        validate_adjustment_audit(record)
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


def adjustment_comparison(witness):
    """Keep the cache witness explicit and finite in every compact receipt."""
    try:
        day = witness["date"]
        old, new = float(witness["oldClose"]), float(witness["newClose"])
        worst = float(witness.get("worst", round(abs(new / old - 1), 6)))
        if (date.fromisoformat(day).isoformat() != day or not all(math.isfinite(v) and v > 0 for v in (old, new, worst))
                or abs(new / old - 1) <= uc.PRICE_TOLERANCE or worst <= uc.PRICE_TOLERANCE):
            raise ValueError("Not a proven price adjustment")
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("Adjustment lacks a cache comparison witness") from exc
    return {"date": day, "oldClose": old, "newClose": new, "worst": worst}


def validate_adjustment_audit(record):
    try:
        symbol, year = record["symbol"], str(record["year"])
        if (not re.fullmatch(r"\d{4}", year)
                or record["new"]["path"] != f"{market_of(symbol)}/{symbol}/{year}.csv"
                or date.fromisoformat(record["date"]).isoformat() != record["date"]
                or not record["date"].startswith(year + "-")
                or record["status"] != "corrected" or not record["adjustmentId"]
                or record["reason"] != "kline-archive/adjustment-year:" + record["adjustmentId"]
                or type(record["new"]["rowsChanged"]) is not int or record["new"]["rowsChanged"] <= 0
                or not all(re.fullmatch(r"[0-9a-f]{64}", record[key]["sha256"]) for key in ("old", "new"))
                or record["old"]["sha256"] == record["new"]["sha256"]
                or set(record["comparison"]) != {"date", "oldClose", "newClose", "worst"}):
            raise ValueError("Invalid year/hash/adjustment metadata")
        adjustment_comparison(record["comparison"])
        identity = digest(json.dumps({k: v for k, v in record.items() if k not in ("id", "loggedAt")}, sort_keys=True).encode())
        if record["id"] != identity:
            raise ValueError("Audit content does not match its identifier")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid adjustment-year audit") from exc


class _AdjustmentBatch:
    """Coalesce only unpublished work; committed audit events are never rewritten.

    A baseline may append before an adjustment, and the current cache may append
    after it. The receipt must cover that transaction's actual old -> final file,
    rather than an intermediate file that was never committed. Disk snapshots
    bound memory even if many symbols need adjustments in the same artifact.
    """
    def __init__(self, root, scratch):
        self.root, self.scratch = Path(root), Path(scratch)
        self.watched, self.snapshots, self.pending, self.events = set(), {}, {}, []
        self.applied_daily = {}

    def record(self, path, symbol, adjustment):
        if symbol not in self.watched:
            return
        name = path.relative_to(self.root).as_posix()
        if name not in self.snapshots:
            snapshot = self.scratch / name
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                shutil.copyfile(path, snapshot)
            self.snapshots[name] = snapshot
        if adjustment:
            previous = self.pending.get(name)
            if previous and previous["adjustmentId"] != adjustment["adjustmentId"]:
                self.finish(name)
                shutil.copyfile(path, self.snapshots[name])
            self.pending[name] = adjustment

    def finish(self, name):
        record = self.pending.pop(name, None)
        if not record:
            return
        snapshot, path = self.snapshots[name], self.root / name
        before = read_rows(snapshot, strict=True) if snapshot.exists() else []
        after = {r[0]: r for r in read_rows(path, strict=True)}
        changed = [r[0] for r in before if after.get(r[0]) != r]
        if not changed:
            return  # A newly created file has no historical rows to correct.
        record = dict(record, date=min(changed), old={"sha256": digest(snapshot.read_bytes())},
                      new=dict(record["new"], sha256=digest(path.read_bytes()), rowsChanged=len(changed)))
        record["id"] = digest(json.dumps({k: v for k, v in record.items() if k not in ("id", "loggedAt")}, sort_keys=True).encode())
        self.events.append(record)

    def commit(self):
        for name in list(self.pending):
            self.finish(name)
        add_events(self.root, self.events)


def put_symbol(root, symbol, incoming, mode="daily", correction=None, fetched_at=None, _audit=None):
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
        witness = adjustment_comparison(correction.get("comparison", {}))
        new_close = witness["newClose"]
        source = {row[0]: row for row in incoming}
        if (not correction.get("id")
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
    corrected_dates = {}
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
                corrected_dates.setdefault(row[0][:4], []).append(row[0])
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
        adjustment_event = None
        if year in corrected_dates:
            record = dict(symbol=symbol, year=int(year), date=min(corrected_dates[year]), status="corrected",
                          old={"sha256": digest(path.read_bytes())},
                          new={"path": path.relative_to(root).as_posix(), "sha256": digest(planned[path].encode()),
                               "rowsChanged": len(corrected_dates[year])},
                          comparison=witness, adjustmentId=correction["id"],
                          sourceUrl=correction.get("sourceUrl") or "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol,
                          fetchedAt=fetched_at or correction.get("fetchedAt"),
                          reason="kline-archive/adjustment-year:" + correction["id"])
            record.update(id=digest(json.dumps(record, sort_keys=True).encode()), loggedAt=ki.utcnow())
            adjustment_event = record
            if _audit is None:
                events.append(record)
        if year in past_insertions:
            record = dict(symbol=symbol, date=min(past_insertions[year]), status="backfilled",
                          old={"sha256": digest(path.read_bytes())} if path.exists() else None,
                          new={"path": path.relative_to(root).as_posix(), "sha256": digest(planned[path].encode()), "rowsAdded": len(past_insertions[year])},
                          sourceUrl="https://query1.finance.yahoo.com/v8/finance/chart/" + symbol,
                          fetchedAt=fetched_at, reason="kline-archive/backfill-year")
            record.update(id=digest(json.dumps(record, sort_keys=True).encode()), loggedAt=ki.utcnow())
            events.append(record)
        if _audit is not None:
            _audit.record(path, symbol, adjustment_event)
    add_events(root, events)
    for path, content in planned.items():
        atomic(path, content)
    return counts


def update_manifest(root, write=True):
    root = Path(root)
    stats = {}
    for path in archive_files(root):
        rows = read_rows(path, strict=True)
        if any(row[0][:4] != path.stem for row in rows):
            raise ValueError(f"CSV year mismatch: {path}")
        stats[path.relative_to(root).as_posix()] = _row_stats(rows, path.stat().st_size)
    other = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()
                and ".git" not in p.relative_to(root).parts and p.name != "manifest.json")
    manifest = _manifest(stats, other)
    if write:
        json_write(root / "manifest.json", manifest)
    return manifest


def _row_stats(rows, size):
    return {"firstDate": rows[0][0], "lastDate": rows[-1][0], "rows": len(rows), "csvBytes": size}


def _manifest(stats, other):
    """Build the exact manifest from already validated rows, without another CSV pass."""
    markets = {key: {"symbols": [], "firstDate": None, "lastDate": None, "rows": 0, "csvBytes": 0} for key in ("tw", "us")}
    for name, stat in stats.items():
        market, symbol, _ = name.split("/")
        part = markets[market]
        part["symbols"].append(symbol)
        part["firstDate"] = min(part["firstDate"] or stat["firstDate"], stat["firstDate"])
        part["lastDate"] = max(part["lastDate"] or stat["lastDate"], stat["lastDate"])
        part["rows"] += stat["rows"]
        part["csvBytes"] += stat["csvBytes"]
    for part in markets.values():
        part["symbols"] = sorted(set(part["symbols"]))
        part["symbolCount"] = len(part["symbols"])
    manifest = {"version": 1, "priceBasis": "Yahoo split-adjusted; not dividend-adjusted",
                "markets": markets, "csvBytes": sum(p["csvBytes"] for p in markets.values()), "totalBytes": 0}
    # Fixed point includes the manifest's own byte count, plus README/audit/progress.
    for _ in range(10):
        size = other + len((json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())
        if manifest["totalBytes"] == size:
            break
        manifest["totalBytes"] = size
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


def apply_operations(archive, payload, main_log=None, now=None, rebuild_manifest=True):
    with tempfile.TemporaryDirectory(prefix="kline-audit-") as scratch:
        audit = _AdjustmentBatch(archive, scratch)
        totals = _apply_operations(archive, payload, now, audit)
        audit.commit()
    if rebuild_manifest:
        update_manifest(archive)
    if main_log:
        merge_main_log(archive, main_log)
    return totals


def _apply_operations(archive, payload, now, audit):
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
    audit.watched.update(item["symbol"] for item, _ in prepared if item.get("correction"))
    totals = {"added": 0, "corrected": 0, "conflicts": 0, "blocked": 0}
    if plan.get("baseline"):
        # Retain already-accepted bars BEFORE rolling eviction / unlisted pruning,
        # then apply the latest source and any proven correction. No old date is lost.
        baseline = _apply_operations(archive, payload / "baseline", now, audit)
        for key in totals:
            totals[key] += baseline[key]
    for item, rows in prepared:
        # The final snapshot often repeats the retained pre-rollover baseline.
        # Skip only an identical, successfully accepted daily operation. Any
        # intervening correction/conflict invalidates this symbol's checkpoint.
        daily = plan["mode"] == "daily" and not item.get("correction")
        if daily and audit.applied_daily.get(item["symbol"]) == item["sha256"]:
            continue
        result = put_symbol(archive, item["symbol"], rows, plan["mode"], item.get("correction"), item.get("fetchedAt"), _audit=audit)
        audit.applied_daily.pop(item["symbol"], None)
        if daily and not result["conflicts"] and not result["blocked"]:
            audit.applied_daily[item["symbol"]] = item["sha256"]
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
    return totals


def stage_adjustment(cache, symbol, frame, comparison, now):
    market_of(symbol)
    rows = frame_rows(frame)
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


@contextmanager
def timed(label):
    started = time.perf_counter()
    try:
        yield
    finally:
        print(f"K-line timing: {label}: {time.perf_counter() - started:.3f}s", flush=True)


def _blob_hash(body, object_format):
    return hashlib.new(object_format, b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()


@contextmanager
def _baseline_blobs(root, entries):
    """One Git batch, disk-backed framing; only one symbol/year lives in RAM.

    Temporary files avoid pipe deadlock on large responses. Identical object IDs
    are requested once, and offsets allow the latest year to be reused by a
    symbol's older changed years without another Git invocation or full scan.
    """
    objects = sorted(set(entries.values()))
    with tempfile.TemporaryFile() as requests, tempfile.TemporaryFile() as responses:
        offsets = {}
        if objects:
            requests.write(b"".join(sha.encode() + b"\n" for sha in objects))
            requests.seek(0)
            with timed(f"verify.baseline_git_batch ({len(objects)} blobs)"):
                subprocess.run(["git", "-C", str(root), "cat-file", "--batch"],
                               stdin=requests, stdout=responses, check=True)
            responses.seek(0)
            for expected in objects:
                header = responses.readline().decode().split()
                if len(header) != 3 or header[:2] != [expected, "blob"]:
                    raise ValueError("Invalid baseline blob response: " + repr(header))
                size = int(header[2])
                if size < 0:
                    raise ValueError("Invalid baseline blob size")
                offsets[expected] = (responses.tell(), size)
                responses.seek(size, 1)
                if responses.read(1) != b"\n":
                    raise ValueError("Truncated baseline blob")

        def read(name):
            offset, size = offsets[entries[name]]
            responses.seek(offset)
            body = responses.read(size)
            if len(body) != size:
                raise ValueError("Truncated baseline blob: " + name)
            return _read_rows(io.StringIO(body.decode()), name, strict=True), digest(body)

        yield read


def verify(root, base=None, summary=None, refresh_manifest=False):
    """Validate all CSVs once; baseline diffs use one batch, never per-file Git.

    Default verification remains read-only. The publisher alone requests a fresh
    manifest, computed from these validated rows after all history checks pass.
    """
    started = time.perf_counter()
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
    object_format = _git(root, "rev-parse", "--show-object-format") if base else "sha1"
    current = {}
    with timed("verify.current_blob_hashes"):
        for path in files:
            body = path.read_bytes()
            current[path.relative_to(root).as_posix()] = {
                "blob": _blob_hash(body, object_format), "sha256": digest(body)}
    log = ki.read_log(root / LOG)
    if len({e["id"] for e in log["events"]}) != len(log["events"]):
        raise ValueError("Duplicate correction event identifiers")
    for record in log["events"]:
        if str(record.get("reason", "")).startswith("kline-archive/adjustment-year:"):
            validate_adjustment_audit(record)
    old_tree, old_last, changed, file_audit = {}, {}, {}, {}
    if base:
        for item in _git(root, "ls-tree", "-rz", base, "--", "tw", "us").split("\0"):
            if not item:
                continue
            metadata, name = item.split("\t", 1)
            if not re.fullmatch(r"(?:tw|us)/[^/]+/\d{4}\.csv", name) or metadata.split()[1] != "blob":
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
        for name in old_tree:
            symbol = name.split("/")[1]
            old_last[symbol] = max(old_last.get(symbol, name), name)
        for name, metadata in current.items():
            if old_tree.get(name) != metadata["blob"]:
                changed.setdefault(name.split("/")[1], []).append(name)
        for e in audit:
            if ((e["status"] == "backfilled" and e.get("reason") == "kline-archive/backfill-year")
                    or (e["status"] == "corrected" and str(e.get("reason", "")).startswith("kline-archive/adjustment-year:"))):
                file_audit.setdefault(e["new"]["path"], []).append(e)
    needed = {name: old_tree[name] for names in changed.values() for name in names if name in old_tree}
    needed.update({old_last[symbol]: old_tree[old_last[symbol]] for symbol in changed if symbol in old_last})
    stats = {}
    active_symbol, last_name, last_rows, last_hash, last = None, None, [], None, ""
    with timed("verify.csv_rows_and_history"), _baseline_blobs(root, needed) as baseline:
        for path in files:
            name = path.relative_to(root).as_posix()
            body = path.read_bytes()
            if _blob_hash(body, object_format) != current[name]["blob"]:
                raise ValueError("Archive changed during verification: " + name)
            rows = _read_rows(io.StringIO(body.decode()), name, strict=True)
            if any(r[0][:4] != path.stem for r in rows):
                raise ValueError(f"Year/date mismatch: {path}")
            stats[name] = _row_stats(rows, len(body))
            symbol = name.split("/")[1]
            if symbol != active_symbol:
                active_symbol = symbol
                last_name = old_last.get(symbol) if symbol in changed else None
                last_rows, last_hash = baseline(last_name) if last_name else ([], None)
                last = last_rows[-1][0] if last_rows else ""
            # Byte-identical files need no baseline history diff; their schema,
            # dates, OHLC and manifest contribution are still checked above.
            if not base or name not in changed.get(symbol, []):
                continue
            old_rows, old_hash = (last_rows, last_hash) if name == last_name else baseline(name) if name in old_tree else ([], None)
            old = {r[0]: r for r in old_rows}
            if not old.keys() <= {r[0] for r in rows}:
                raise ValueError(f"Archive dates deleted: {symbol}")
            # Journal union ordering can differ for events with the same
            # clock timestamp. Follow exact hash edges, never log order.
            reachable = {(old_hash, False, False)}
            pending = list(reachable)
            while pending:
                state, adjusted, backfilled = pending.pop()
                for e in file_audit.get(name, []):
                    before = e.get("old")
                    if (before.get("sha256") if before else None) != state or e["symbol"] != symbol:
                        continue
                    is_adjustment = e["status"] == "corrected"
                    target = (e["new"]["sha256"], adjusted or is_adjustment, backfilled or not is_adjustment)
                    if target not in reachable:
                        reachable.add(target)
                        pending.append(target)
            covered = [r for r in reachable if r[0] == current[name]["sha256"] and r[0] != old_hash]
            adjusted = any(r[1] for r in covered)
            backfilled = any(r[2] for r in covered)
            for row in rows:
                day = row[0]
                before = old.get(day)
                if before == row or (before is None and (day > last or backfilled)) or (before is not None and adjusted):
                    continue
                raise ValueError(f"Unlogged historical modification: {symbol} {day}")
    stored = json_read(root / "manifest.json", None)
    sizes = {p.relative_to(root).as_posix(): p.stat().st_size for p in root.rglob("*")
             if p.is_file() and ".git" not in p.relative_to(root).parts}
    other = sum(size for name, size in sizes.items() if name != "manifest.json")
    expected = _manifest(stats, other)
    if refresh_manifest:
        json_write(root / "manifest.json", expected)
        stored = json_read(root / "manifest.json", None)
        sizes["manifest.json"] = (root / "manifest.json").stat().st_size
    actual_bytes = sum(sizes.values())
    csv_bytes = sum(stat["csvBytes"] for stat in stats.values())
    if not stored or stored != expected or stored["totalBytes"] != actual_bytes or stored["csvBytes"] != csv_bytes:
        raise ValueError("Archive manifest size does not match files")
    result = {"files": len(files), "rows": sum(stat["rows"] for stat in stats.values()),
              "totalBytes": actual_bytes, "csvBytes": csv_bytes}
    text = "### Permanent K-line archive\n\n" + "\n".join(f"- {k}: {v:,}" for k, v in result.items()) + "\n"
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(text)
    print(f"K-line timing: verify.total: {time.perf_counter() - started:.3f}s", flush=True)
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
