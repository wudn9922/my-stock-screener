"""Fill rejected US daily bars only in their existing permanent storage scopes."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import alpaca_bars as alpaca
import kline_integrity as k
from scripts import kline_archive as archive


def read_main(path):
    frame = pd.read_csv(path, index_col=0, parse_dates=True, **k.ROUND_TRIP)
    if frame.index.has_duplicates:
        raise ValueError("Duplicate permanent CSV dates")
    return frame


def archive_frame(rows):
    return pd.DataFrame([dict(zip(k.FIELDS, r[1:])) for r in rows],
                        index=pd.to_datetime([r[0] for r in rows]), columns=k.FIELDS).apply(pd.to_numeric, errors="coerce")


def neighbor_reason(frame, day, values, blocked):
    """Require the nearest usable close on each available side, never new repairs."""
    stamp = pd.Timestamp(day)
    if stamp in frame.index:
        row = frame.loc[stamp]
        if k.violation(row) or day in blocked:
            return "existing-invalid-or-quarantined"
        if abs(float(values["Close"]) / float(row["Close"]) - 1) > .03 + 1e-12:
            return "existing-close-mismatch-3pct"
        return None
    rows = [(d, row) for d, row in frame.sort_index().iterrows()
            if str(d.date()) not in blocked and not k.violation(row)]
    before = [row for d, row in rows if d < stamp]
    after = [row for d, row in rows if d > stamp]
    refs = before[-1:] + after[:1]
    if not refs:
        return "no-valid-reference-close"
    if any(abs(float(values["Close"]) / float(row["Close"]) - 1) > .25 + 1e-12 for row in refs):
        return "neighbor-close-mismatch-25pct"
    return None


def basis_witness_reason(symbol, day, frame, blocked, basis):
    stamp = pd.Timestamp(day)
    rows = [(d, row) for d, row in frame.sort_index().iterrows()
            if d != stamp and str(d.date()) not in blocked and not k.violation(row)]
    refs = [(d, row) for d, row in rows if d < stamp][-1:] + [(d, row) for d, row in rows if d > stamp][:1]
    if not refs:
        return "no-basis-witness"
    for d, row in refs:
        result = alpaca.alpaca_bar(symbol, str(d.date()), adjustment=basis)
        if result is None or k.violation(result[0]) or abs(float(row["Close"]) / float(result[0]["Close"]) - 1) > .03 + 1e-12:
            return "destination-basis-unconfirmed"
    return None


def plan(root, archive_dir):
    root, archive_dir = Path(root), Path(archive_dir)
    journal = root / archive.LOG
    if not alpaca.enabled():
        raise RuntimeError("Alpaca credentials are not configured")
    if not journal.is_file() or not (archive_dir / "manifest.json").is_file():
        raise ValueError("Both permanent data checkouts are required")
    events = {}
    for event in k.read_log(journal)["events"]:
        symbol, day = event["symbol"], event["date"]
        if event["status"] == "rejected" and day >= "2016-01-01" and alpaca.alpaca_symbol(symbol):
            events[symbol, day] = event
    groups = defaultdict(list)
    for symbol, day in events:
        groups[day[:4]].append((symbol, day))
    for entries in groups.values():
        days = [d for _, d in entries]
        for adjustment in (alpaca.MAIN_ADJUSTMENT, alpaca.ARCHIVE_ADJUSTMENT):
            if alpaca.prefetch({s for s, _ in entries}, min(days), max(days), adjustment=adjustment) is None:
                raise RuntimeError("Alpaca batch fetch failed; refusing an incomplete repair")
    main_paths = defaultdict(list)
    for path in (root / "data").rglob("*.csv"):
        if alpaca.alpaca_symbol(path.stem):
            main_paths[path.stem].append(path)
    main_frames, archives, blocked_main, blocked_archive = {}, {}, {}, {}
    operations, skipped = [], []
    for (symbol, day), event in sorted(events.items()):
        if symbol not in main_frames:
            main_frames[symbol] = [(p, read_main(p)) for p in sorted(main_paths[symbol])]
            archives[symbol] = archive_frame(archive.symbol_rows(archive_dir, symbol))
            blocked_main[symbol] = k.quarantined_dates(symbol, journal)
            blocked_archive[symbol] = k.quarantined_dates(symbol, archive_dir / archive.LOG)
        copies = [(str(p.relative_to(root)), f, blocked_main[symbol]) for p, f in main_frames[symbol]]
        af = archives[symbol]
        # Do not invent archive membership for a symbol that was never retained.
        if not af.empty:
            copies.append(("archive", af, blocked_archive[symbol]))
        stamp = pd.Timestamp(day)
        missing = [name for name, f, _ in copies if stamp not in f.index]
        reason = None
        if not copies:
            reason = "no-existing-permanent-symbol"
        elif not missing:
            reason = "already-present-in-all-copies"
        pending = []
        if not reason:
            sources = {basis: alpaca.alpaca_bar(symbol, day, adjustment=basis)
                       for basis in (alpaca.MAIN_ADJUSTMENT, alpaca.ARCHIVE_ADJUSTMENT)}
            # The rejected Yahoo download was split-adjusted; compare that
            # observation with the same basis, not the main raw destination.
            old = event.get("old") or {}
            try:
                old_close = float(old.get("Close"))
            except (TypeError, ValueError):
                old_close = math.nan
            split = sources[alpaca.ARCHIVE_ADJUSTMENT]
            if math.isfinite(old_close) and old_close > 0:
                if split is None or k.violation(split[0]):
                    reason = "no-valid-split-reference"
                elif abs(float(split[0]["Close"]) / old_close - 1) > .03 + 1e-12:
                    reason = "rejected-close-mismatch-3pct"
            for name, frame, blocked in copies:
                if reason:
                    break
                basis = alpaca.ARCHIVE_ADJUSTMENT if name == "archive" else alpaca.MAIN_ADJUSTMENT
                result = sources[basis]
                if result is None:
                    reason = name + ":no-alpaca-bar"
                    break
                values, url, fetched = result
                failure = k.violation(values) or neighbor_reason(frame, day, values, blocked)
                # When raw/split disagree materially, require same-date
                # independent witnesses for the destination basis. A 25% price
                # continuity check alone cannot distinguish a 5:4 split.
                other = sources[alpaca.ARCHIVE_ADJUSTMENT if basis == alpaca.MAIN_ADJUSTMENT else alpaca.MAIN_ADJUSTMENT]
                if not failure and name in missing and other and not k.violation(other[0]) and abs(float(values["Close"]) / float(other[0]["Close"]) - 1) > .01:
                    failure = basis_witness_reason(symbol, day, frame, blocked, basis)
                if not failure and name == "archive" and name in missing:
                    try:
                        archive.normalize([day] + [values.get(f) for f in k.FIELDS])
                    except (ValueError, ArithmeticError):
                        failure = "archive-price-or-volume-not-representable"
                if failure:
                    reason = name + ":" + failure
                    break
                if name in missing:
                    pending.append(dict(symbol=symbol, date=day, destinations=[name],
                                        adjustment=basis, values=k.bar_values(values),
                                        sourceUrl=url, fetchedAt=fetched))
        if reason:
            skipped.append(dict(symbol=symbol, date=day, reason=reason))
        else:
            operations.extend(pending)
    return dict(candidates=len(events), operations=operations, skipped=skipped)


def apply_plan(root, archive_dir, report):
    root, archive_dir = Path(root), Path(archive_dir)
    journal = root / archive.LOG
    by_symbol = defaultdict(list)
    for op in report["operations"]:
        if "archive" in op["destinations"]:
            by_symbol[op["symbol"]].append(op)
    # Use the archive writer, not the main CSV writer: canonical four decimals,
    # append-only annual SHA audit, permanent archive journal and manifest.
    archive_events = []
    for symbol, ops in by_symbol.items():
        existing = {r[0] for r in archive.symbol_rows(archive_dir, symbol)}
        if any(op["date"] in existing for op in ops):
            raise ValueError("Archive changed after planning; refusing to overwrite")
        rows = [archive.normalize([op["date"]] + [op["values"].get(f) for f in k.FIELDS])
                for op in ops]
        counts = archive.put_symbol(archive_dir, symbol, sorted(rows), mode="backfill",
                                    source_url=alpaca.URL, fetched_at=k.utcnow(), _events=archive_events)
        if counts["added"] != len(rows) or counts["corrected"] or counts["conflicts"]:
            raise ValueError("Unexpected archive repair result")
        for op, row in zip(ops, rows):
            event = dict(symbol=symbol, date=op["date"], status="corrected", old=None,
                         new=archive.values(row), sourceUrl=op["sourceUrl"],
                         fetchedAt=op["fetchedAt"], reason="missing-bar-filled-alpaca")
            event["id"] = archive.digest(json.dumps(event, sort_keys=True).encode())
            event["loggedAt"] = k.utcnow()
            archive_events.append(event)
    archive.add_events(archive_dir, archive_events)
    if by_symbol:
        archive.update_manifest(archive_dir)
    with k.batched_journal():
        for op in report["operations"]:
            symbol, day, values = op["symbol"], op["date"], op["values"]
            for target in op["destinations"]:
                if target != "archive":
                    k.fill_missing_bar(root / target, symbol, day, values,
                                       op["sourceUrl"], op["fetchedAt"], journal)
            # Archive-only repairs belong to the archive journal. Do not
            # mislabel split prices as main raw prices in the main journal.


def run(root, archive_dir, apply=False):
    report = plan(root, archive_dir)
    report["apply"] = apply
    if apply:
        apply_plan(root, archive_dir, report)
    return report


def markdown(report):
    from collections import Counter
    counts = Counter(item["reason"] for item in report["skipped"])
    lines = ["# Rejected US daily bar repair", "",
             f"Mode: {'apply to local checkout' if report['apply'] else 'dry-run'}",
             f"Candidates: {report['candidates']}; eligible: {len(report['operations'])}; skipped: {len(report['skipped'])}",
             "", "| Skip reason | Count |", "|---|---:|"]
    lines.extend(f"| {reason} | {count} |" for reason, count in sorted(counts.items()))
    lines.extend(["", "Full per-symbol and destination details are in the artifact."])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path, help="Optional JSON artifact outside permanent data")
    args = parser.parse_args()
    report = run(ROOT, args.archive_dir, args.apply)
    print(markdown(report))
    if args.report:
        args.report.write_text(json.dumps(report, allow_nan=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
