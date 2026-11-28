"""Read-only audits and explicitly scoped isolated-price-scale corrections."""
from collections import defaultdict
from pathlib import Path

import pandas as pd

import alpaca_bars as alpaca
import kline_integrity as k
from scripts import kline_archive as archive
from scripts.repair_us_bars_alpaca import archive_frame, read_main, basis_witness_reason


def supported(symbol):
    return bool(alpaca.alpaca_symbol(symbol) or symbol.endswith((".TW", ".TWO")))


def stores(root, archive_dir, symbols=None):
    """Yield independent permanent copies, including cross-year archive neighbors."""
    root, archive_dir = Path(root), Path(archive_dir)
    for path in sorted((root / "data").rglob("*.csv")):
        symbol = path.stem
        if supported(symbol) and (symbols is None or symbol in symbols):
            yield symbol, path.relative_to(root).as_posix(), read_main(path).sort_index(), root / archive.LOG
    known = {p.parent.name for p in archive.archive_files(archive_dir) if p.parent.parent.name in ("tw", "us")}
    for symbol in sorted(known if symbols is None else known & set(symbols)):
        if supported(symbol):
            yield symbol, "archive", archive_frame(archive.symbol_rows(archive_dir, symbol)), archive_dir / archive.LOG


def adjustment(destination):
    return alpaca.ARCHIVE_ADJUSTMENT if destination == "archive" else alpaca.MAIN_ADJUSTMENT


def candidates(frame):
    """Yield inclusive run bounds, leaving row validation to the planner."""
    yield from k.scale_run_bounds(frame["Close"].tolist())


def _blocked(journal):
    grouped = defaultdict(set)
    for row in k.read_log(journal)["quarantined"]:
        grouped[row["symbol"]].add(row["date"])
    return grouped


def _prefetch(targets):
    groups = defaultdict(set)
    for symbol, day, basis in targets:
        groups[basis, day[:4]].add((symbol, day))
    for (basis, _), pairs in sorted(groups.items()):
        if not alpaca.enabled():
            continue
        days = [day for _, day in pairs]
        if alpaca.prefetch({symbol for symbol, _ in pairs}, min(days), max(days), adjustment=basis) is None:
            raise RuntimeError("Alpaca batch fetch failed; refusing incomplete audit")


def _check_source(item):
    symbol, day, destination = item["symbol"], item["date"], item["destination"]
    basis = adjustment(destination)
    try:
        if alpaca.alpaca_symbol(symbol):
            result = alpaca.alpaca_bar(symbol, day, adjustment=basis)
        else:
            result = k.official_bar(symbol, day)
    except Exception as exc:
        return "source-error:" + type(exc).__name__
    if result is None:
        return "no-source-bar"
    values, url, fetched = result
    item.update(new=k.bar_values(values), sourceUrl=url, fetchedAt=fetched)
    failure = k.scale_source_reason(item["previous"], item["following"], values)
    if not failure and alpaca.alpaca_symbol(symbol):
        other_basis = alpaca.ARCHIVE_ADJUSTMENT if basis == alpaca.MAIN_ADJUSTMENT else alpaca.MAIN_ADJUSTMENT
        other = alpaca.alpaca_bar(symbol, day, adjustment=other_basis)
        if other is None or k.violation(other[0]):
            failure = "no-valid-alternate-basis"
        elif abs(float(values["Close"]) / float(other[0]["Close"]) - 1) > .01:
            refs = pd.DataFrame([item["previous"], item["following"]],
                                index=pd.to_datetime(item["neighborDates"]))
            failure = basis_witness_reason(symbol, day, refs, set(), basis)
    if not failure and destination == "archive":
        try:
            archive.normalize([day] + [values.get(f) for f in k.FIELDS])
        except (ValueError, ArithmeticError):
            failure = "archive-price-or-volume-not-representable"
    return failure


def _reject_run(items, reason):
    for item in items:
        if not item["reason"]:
            item["reason"] = reason


def _reject_conflicts(runs, *, multi_only=False):
    """Compare ranges in the original frame, not just calendar adjacency."""
    conflicts = set()
    for i, (start, end, items) in enumerate(runs):
        for j in range(i+1, len(runs)):
            next_start, next_end, other = runs[j]
            if next_start > end + 1:
                break
            if multi_only and start == end and next_start == next_end:
                continue
            if not multi_only and (any(item["reason"] for item in items)
                                   or any(item["reason"] for item in other)):
                continue
            conflicts.update((i, j))
    for i in conflicts:
        _reject_run(runs[i][2], "adjacent-corrections-ambiguous")


def plan_scale(root, archive_dir):
    all_runs = []
    targets = set()
    blocked = {}
    for symbol, destination, frame, journal in stores(root, archive_dir):
        if journal not in blocked:
            blocked[journal] = _blocked(journal)
        excluded = blocked[journal][symbol]
        runs = []
        for start, end in candidates(frame):
            dates = [str(d.date()) for d in frame.index[start-1:end+2]]
            previous, *currents, following = [k.bar_values(row) for _, row in frame.iloc[start-1:end+2].iterrows()]
            reason = None
            if any(d in excluded for d in dates):
                reason = "quarantined-original-or-neighbor"
            else:
                reason = k.scale_run_original_reason(previous, currents, following)
            if not reason and not k.session_complete(dates[-1], symbol):
                reason = "unfinished-neighbor-session"
            if not reason and alpaca.alpaca_symbol(symbol):
                if not alpaca.enabled():
                    reason = "alpaca-disabled"
                else:
                    for basis in (alpaca.MAIN_ADJUSTMENT, alpaca.ARCHIVE_ADJUSTMENT):
                        for d in dates:
                            targets.add((symbol, d, basis))
            items = [dict(symbol=symbol, destination=destination, date=day, old=current, new=None,
                          sourceUrl=None, fetchedAt=None, reason=reason,
                          previous=previous, following=following, neighborDates=[dates[0], dates[-1]],
                          runStart=dates[1], runEnd=dates[-2])
                     for day, current in zip(dates[1:-1], currents)]
            runs.append((start, end, items))
        # Multi-bar structural conflicts are ambiguous even before source checks.
        # For singletons retain the previous behavior: only independently
        # eligible adjacent hypotheses conflict (e.g. the APH split boundary).
        _reject_conflicts(runs, multi_only=True)
        all_runs.append(runs)
    _prefetch(targets)
    for runs in all_runs:
        for _, _, items in runs:
            if any(item["reason"] for item in items):
                continue
            for item in items:
                item["reason"] = _check_source(item)
            failed = next((item for item in items if item["reason"]), None)
            if failed:
                _reject_run(items, "run-rejected:" + failed["date"] + ":" + failed["reason"])
        _reject_conflicts(runs)
    pending = [item for runs in all_runs for _, _, items in runs for item in items]
    return dict(mode="scale-outliers", candidates=len(pending),
                operations=[i for i in pending if not i["reason"]],
                skipped=[i for i in pending if i["reason"]])


def apply_scale(root, archive_dir, report):
    root, archive_dir = Path(root), Path(archive_dir)
    archive_ops = defaultdict(list)
    main_runs = defaultdict(list)
    for item in report["operations"]:
        symbol, day, destination = item["symbol"], item["date"], item["destination"]
        run = (item["runStart"], item["runEnd"])
        if destination == "archive":
            archive_ops[symbol].append(dict(date=day, runStart=run[0], runEnd=run[1],
                old=list(archive.normalize([day] + [item["old"][f] for f in k.FIELDS])),
                new=list(archive.normalize([day] + [item["new"][f] for f in k.FIELDS])),
                sourceUrl=item["sourceUrl"], fetchedAt=item["fetchedAt"]))
        else:
            main_runs[(root / destination, symbol)].append(item)
    main_ops = []
    for (path, symbol), items in main_runs.items():
        frame = read_main(path).sort_index()
        dates = [str(d.date()) for d in frame.index]
        groups = k.validate_scale_run_groups(dates, items)
        blocked = k.quarantined_dates(symbol, root / archive.LOG)
        for start, end, group in groups:
            previous, *currents, following = [k.bar_values(row) for _, row in frame.iloc[start-1:end+2].iterrows()]
            if (any(d in blocked for d in dates[start-1:end+2])
                    or k.scale_run_original_reason(previous, currents, following)):
                raise ValueError("Main scale repair no longer passes guards")
            for current, item in zip(currents, group):
                if current != item["old"]:
                    raise ValueError("Main scale repair changed after planning")
                if k.scale_source_reason(previous, following, item["new"]):
                    raise ValueError("Main scale repair no longer passes guards")
                main_ops.append((path, item))
    for symbol, entries in archive_ops.items():
        archive._validate_scale_changes(symbol, archive.symbol_rows(archive_dir, symbol), entries)
    for symbol, entries in archive_ops.items():
        archive.correct_scale_rows(archive_dir, symbol, entries)
    if archive_ops:
        archive.update_manifest(archive_dir)
    with k.batched_journal():
        for path, item in main_ops:
            k.correct_bar(path, item["symbol"], item["date"], item["new"], item["sourceUrl"],
                          item["fetchedAt"], root / archive.LOG, reason=archive.SCALE_REPAIR_REASON)


def audit_filled(root, archive_dir):
    root, archive_dir = Path(root), Path(archive_dir)
    if not alpaca.enabled():
        raise RuntimeError("Alpaca credentials are not configured")
    keys = set()
    for journal in (root / archive.LOG, archive_dir / archive.LOG):
        for event in k.read_log(journal)["events"]:
            if event.get("reason") == "missing-bar-filled-alpaca" and alpaca.alpaca_symbol(event["symbol"]):
                keys.add((event["symbol"], event["date"]))
    dates = defaultdict(set)
    for symbol, day in keys:
        dates[symbol].add(day)
    copies = []
    targets = set()
    for symbol, destination, frame, journal in stores(root, archive_dir, dates):
        for day in sorted(dates[symbol]):
            stamp = pd.Timestamp(day)
            if stamp not in frame.index:
                continue
            old = k.bar_values(frame.loc[stamp])
            basis = adjustment(destination)
            copies.append(dict(symbol=symbol, destination=destination, date=day, old=old, adjustment=basis))
            targets.add((symbol, day, basis))
    _prefetch(targets)
    differences, checked = [], 0
    for item in copies:
        result = alpaca.alpaca_bar(item["symbol"], item["date"], adjustment=item["adjustment"])
        item.update(new=None, sourceUrl=None, reason=None)
        if result is None:
            item["reason"] = "no-source-bar"
        else:
            values, url, _ = result
            item.update(new=k.bar_values(values), sourceUrl=url)
            if k.violation(values) or k.violation(item["old"]):
                item["reason"] = "invalid-bar"
            else:
                checked += 1
                delta = {f: abs(float(item["old"][f]) / float(values[f]) - 1) * 100 for f in k.FIELDS[:4]}
                item["differencePct"] = delta
                if max(delta.values()) > 1:
                    item["reason"] = "price-difference-over-1pct"
        if item["reason"]:
            differences.append(item)
    seen = {(i["symbol"], i["date"]) for i in copies}
    for symbol, day in sorted(keys - seen):
        differences.append(dict(symbol=symbol, date=day, destination="-", old=None, new=None,
                                sourceUrl=None, reason="no-permanent-copy"))
    return dict(mode="audit-filled", candidates=len(keys), checked=checked,
                operations=[], skipped=differences, apply=False)


def markdown(report):
    def bar(value):
        if value is None:
            return "-"
        return "/".join("-" if value.get(f) is None else format(value[f], ".8g") for f in k.FIELDS)
    lines = ["# " + report["mode"], "",
             f"Mode: {'apply to isolated local checkout' if report.get('apply') else 'read-only'}; "
             f"candidates: {report['candidates']}; eligible: {len(report['operations'])}; "
             f"reported/skipped: {len(report['skipped'])}"]
    if "checked" in report:
        lines.append(f"Compared permanent copies: {report['checked']}; raw main / split archive.")
    scale_mode = report["mode"] == "scale-outliers"
    lines += ["", "Old/new fields: Open/High/Low/Close/Volume.", "",
              "| Symbol | Date | Destination | Old | New | Source | Result |" + (" Run |" if scale_mode else ""),
              "|---|---|---|---|---|---|---|" + ("---|" if scale_mode else "")]
    def order(item):
        if scale_mode:
            return (item["symbol"], item["destination"], item["runStart"], item["runEnd"], item["date"])
        return (item["symbol"], item["date"], item["destination"])
    for item in sorted(report["operations"] + report["skipped"], key=order):
        source = item.get("sourceUrl") or "-"
        if source != "-":
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(source)
            mode = parse_qs(parsed.query).get("adjustment", ["official"])[0]
            source = f"[{parsed.hostname} ({mode})]({source})"
        reason = item.get("reason") or "eligible"
        if "differencePct" in item:
            reason += f" ({max(item['differencePct'].values()):.4g}%)"
        run = f" run {item['runStart']}..{item['runEnd']} |" if scale_mode else ""
        lines.append(f"| {item['symbol']} | {item['date']} | {item['destination']} | {bar(item.get('old'))} | "
                     f"{bar(item.get('new'))} | {source} | {reason} |" + run)
    return "\n".join(lines) + "\n"
