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
    """Scan closes cheaply; validate all OHLCV only for candidate triples."""
    closes = frame["Close"].tolist()
    for i in range(1, len(closes) - 1):
        if k.isolated_scale(closes[i-1], closes[i], closes[i+1]):
            yield i


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


def plan_scale(root, archive_dir):
    pending = []
    targets = set()
    blocked = {}
    for symbol, destination, frame, journal in stores(root, archive_dir):
        if journal not in blocked:
            blocked[journal] = _blocked(journal)
        excluded = blocked[journal][symbol]
        for i in candidates(frame):
            dates = [str(d.date()) for d in frame.index[i-1:i+2]]
            previous, current, following = [k.bar_values(row) for _, row in frame.iloc[i-1:i+2].iterrows()]
            day = dates[1]
            item = dict(symbol=symbol, destination=destination, date=day, old=current, new=None,
                        sourceUrl=None, fetchedAt=None, reason=None,
                        previous=previous, following=following, neighborDates=[dates[0], dates[2]])
            if any(d in excluded for d in dates):
                item["reason"] = "quarantined-original-or-neighbor"
            elif any(k.violation(row) for row in (previous, current, following)):
                item["reason"] = "invalid-original-or-neighbor"
            elif not k.session_complete(dates[2], symbol):
                item["reason"] = "unfinished-neighbor-session"
            elif alpaca.alpaca_symbol(symbol):
                if not alpaca.enabled():
                    item["reason"] = "alpaca-disabled"
                else:
                    # Both bases are fetched to detect small corporate-action scale differences.
                    for basis in (alpaca.MAIN_ADJUSTMENT, alpaca.ARCHIVE_ADJUSTMENT):
                        for d in dates:
                            targets.add((symbol, d, basis))
            pending.append(item)
    _prefetch(targets)
    for item in pending:
        if item["reason"]:
            continue
        symbol, day, destination = item["symbol"], item["date"], item["destination"]
        basis = adjustment(destination)
        try:
            if alpaca.alpaca_symbol(symbol):
                result = alpaca.alpaca_bar(symbol, day, adjustment=basis)
            else:
                result = k.official_bar(symbol, day)
        except Exception as exc:
            item["reason"] = "source-error:" + type(exc).__name__
            continue
        if result is None:
            item["reason"] = "no-source-bar"
            continue
        values, url, fetched = result
        item.update(new=k.bar_values(values), sourceUrl=url, fetchedAt=fetched)
        failure = k.scale_replacement_reason(item["previous"], item["old"], item["following"], values)
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
        item["reason"] = failure
    # Do not apply adjacent accepted candidates: their witnesses would change.
    accepted = {(i["symbol"], i["destination"], i["date"]) for i in pending if not i["reason"]}
    for item in pending:
        if not item["reason"] and any((item["symbol"], item["destination"], d) in accepted for d in item["neighborDates"]):
            item["reason"] = "adjacent-corrections-ambiguous"
    return dict(mode="scale-outliers", candidates=len(pending),
                operations=[i for i in pending if not i["reason"]],
                skipped=[i for i in pending if i["reason"]])


def apply_scale(root, archive_dir, report):
    root, archive_dir = Path(root), Path(archive_dir)
    archive_ops = defaultdict(list)
    main_ops = []
    for item in report["operations"]:
        symbol, day, destination = item["symbol"], item["date"], item["destination"]
        if destination == "archive":
            archive_ops[symbol].append(dict(date=day,
                old=list(archive.normalize([day] + [item["old"][f] for f in k.FIELDS])),
                new=list(archive.normalize([day] + [item["new"][f] for f in k.FIELDS])),
                sourceUrl=item["sourceUrl"], fetchedAt=item["fetchedAt"]))
        else:
            path = root / destination
            frame = read_main(path).sort_index()
            pos = frame.index.get_loc(pd.Timestamp(day))
            if pos <= 0 or pos >= len(frame) - 1 or k.bar_values(frame.iloc[pos]) != item["old"]:
                raise ValueError("Main scale repair changed after planning")
            prev, current, next_ = [frame.iloc[j] for j in (pos-1, pos, pos+1)]
            if k.scale_replacement_reason(prev, current, next_, item["new"]):
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
    lines += ["", "Old/new fields: Open/High/Low/Close/Volume.", "",
              "| Symbol | Date | Destination | Old | New | Source | Result |",
              "|---|---|---|---|---|---|---|"]
    for item in sorted(report["operations"] + report["skipped"], key=lambda i: (i["symbol"], i["date"], i["destination"])):
        source = item.get("sourceUrl") or "-"
        if source != "-":
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(source)
            mode = parse_qs(parsed.query).get("adjustment", ["official"])[0]
            source = f"[{parsed.hostname} ({mode})]({source})"
        reason = item.get("reason") or "eligible"
        if "differencePct" in item:
            reason += f" ({max(item['differencePct'].values()):.4g}%)"
        lines.append(f"| {item['symbol']} | {item['date']} | {item['destination']} | {bar(item.get('old'))} | "
                     f"{bar(item.get('new'))} | {source} | {reason} |")
    return "\n".join(lines) + "\n"
