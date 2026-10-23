"""Fetch/replay an auditable financial-sector rebuild without removing any stored period.

Fetch raw inputs with --fetch; replay/rebuild with --apply after reviewing the report.
Raw responses stay in the ignored cache; receipts and every changed cell are reported.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import shutil

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "atlas/scripts"), str(ROOT)]
import build_financials_tw_finmind as ftw
from finmind_client import Http, fetch_rows, request_interval
import build_fundamentals_tw_finmind as fmo
import build_growth as growth


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    os.replace(temp, path)


def cohort(report):
    universe = read(report / "universe.json")
    fields = universe["fields"]
    industry = fields.index("industry") if "industry" in fields else None
    members = set()
    for row in universe["markets"]["TW"]["rows"]:
        if industry is not None and any(word in str(row[industry] or "") for word in ("金融", "保險", "證券")):
            members.add(row[0])
    for path in (report / "financials").glob("*.json"):
        doc = read(path)
        if doc.get("market") == "TW" and "financial-sector-format" in doc.get("quality", {}).get("issues", []):
            members.add(doc["symbol"])
    return sorted(members)


def fetch(report, cache):
    members = cohort(report)
    token = os.environ.get("FINMIND_TOKEN") or None
    counter = {"requests": 0, "limit": 450}
    http = Http()
    for i, symbol in enumerate(members, 1):
        for dataset, name in ftw.DATASETS.items():
            path = cache / symbol / (dataset + ".json")
            if path.exists():
                continue
            started = time.monotonic()
            rows = fetch_rows(http, name, symbol.split(".")[0], "2015-01-01", token, time.sleep, print, counter)
            write(path, {"sourceUrl": "https://api.finmindtrade.com/api/v4/data?dataset=" + name
                       + "&data_id=" + symbol.split(".")[0] + "&start_date=2015-01-01",
                       "fetchedAt": datetime.now(timezone.utc).isoformat(), "data": rows})
            time.sleep(max(0, request_interval(token) - (time.monotonic() - started)))
        print(f"Fetched {i}/{len(members)} {symbol}; requests {counter['requests']}", flush=True)


def changed_cells(symbol, before, after):
    changes = []
    for frequency in ('annual', 'quarterly'):
        a, b = before.get(frequency, {}), after.get(frequency, {})
        old = {end: i for i, end in enumerate(a.get('end', []))}
        new = {end: i for i, end in enumerate(b.get('end', []))}
        if not old.keys() <= new.keys():
            raise ValueError(f'Refusing to remove {symbol} {frequency} periods')
        for end, i in new.items():
            for field in set(a.get('values', {})) | set(b.get('values', {})):
                previous = a.get('values', {}).get(field, [None] * len(old))[old[end]] if end in old else None
                current = b.get('values', {}).get(field, [None] * len(new))[i]
                if previous != current:
                    changes.append(dict(symbol=symbol, period=frequency, end=end,
                                        field=field, old=previous, new=current))
    return changes


def rebuild(report, cache, apply=False):
    members = cohort(report)
    missing = [str(cache / s / (name + '.json')) for s in members for name in ftw.DATASETS
               if not (cache / s / (name + '.json')).exists()]
    if missing:
        raise ValueError(f'{len(missing)} source inputs missing; run --fetch first; no files changed')
    now = datetime.now(timezone.utc).replace(microsecond=0)
    state = read(report / 'fundamentals' / ftw.STATE_FILE)
    fundamentals_path = report / 'fundamentals' / fmo.FILE_NAME
    store = fmo.load_store(read(fundamentals_path))
    previous_store = {code: dict(values) for code, values in store['data'].items()}
    official = fmo.official_annual_eps(read(report / 'fundamentals' / growth.TW_FILE))
    rebuilt = cache / 'rebuilt'
    rebuilt.mkdir(exist_ok=True)
    entries, changes = [], []
    for symbol in members:
        source = report / 'financials' / (symbol + '.json')
        before = read(source)
        rows, receipts = {}, []
        for name in ftw.DATASETS:
            path = cache / symbol / (name + '.json')
            raw = read(path)
            rows[name] = raw['data']
            receipts.append(dict(dataset=ftw.DATASETS[name], sourceUrl=raw['sourceUrl'],
                                 fetchedAt=raw['fetchedAt'], rows=len(raw['data']),
                                 cachedInputSha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        code = symbol.split('.')[0]
        parsed = ftw.parse_stock(code, rows)
        annual, quarterly, flags = ftw.derive(parsed, state['modes'], official.get(code))
        a, q = ftw.periods_of(annual, quarterly)
        shutil.copyfile(source, rebuilt / source.name)
        outcome = ftw.write_stock(str(rebuilt), symbol, a, q, flags, now, now.date()) if a or q else 'empty-source'
        if outcome == 'corrupt':
            raise ValueError('Cannot safely rebuild ' + symbol)
        after = read(rebuilt / source.name)
        baseline = cache / 'originals' / source.name
        delta = changed_cells(symbol, read(baseline) if baseline.exists() else before, after)
        changes.extend(delta)
        entries.append(dict(symbol=symbol, outcome=outcome, changedValues=len(delta),
                            beforeSha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                            afterSha256=hashlib.sha256((rebuilt / source.name).read_bytes()).hexdigest(),
                            beforePeriods={f:len(before[f]['end']) for f in ('annual','quarterly')},
                            afterPeriods={f:len(after[f]['end']) for f in ('annual','quarterly')},
                            qualityBefore=before['quality'], qualityAfter=after['quality'], sources=receipts))
        core = fmo.parse_rows(rows['pl'], code)['entries']
        if core:
            fmo.merge_stock(store, code, core, now.date())
        if apply:
            backup = cache / 'originals' / source.name
            if not backup.exists():
                backup.parent.mkdir(exist_ok=True)
                shutil.copyfile(source, backup)
            if source.read_bytes() != (rebuilt / source.name).read_bytes():
                # Write-ahead evidence survives an interrupted multi-file rebuild.
                journal = cache / 'apply-journal.json'
                write(journal, dict(version=1, generatedAt=now.isoformat(), changes=changes,
                                    entries=entries, pendingSymbol=symbol))
                temp = source.with_suffix('.json.tmp')
                try:
                    temp.write_bytes((rebuilt / source.name).read_bytes())
                    os.replace(temp, source)
                finally:
                    temp.unlink(missing_ok=True)
    history_changes = []
    for code, periods in store['data'].items():
        for period, values in periods.items():
            old = previous_store.get(code, {}).get(period)
            for i, field in enumerate(['revenue','epsBasic','netIncome','revenueKind']):
                previous = old[i] if old is not None and i < len(old) else None
                current = values[i] if i < len(values) else None
                if previous != current:
                    history_changes.append(dict(symbol=next((s for s in members if s.startswith(code+'.')),code),
                                                period=period, field=field, old=previous, new=current))
    before_growth = read(report / 'growth.json')
    growth_baseline = cache / 'originals/growth.json'
    if not growth_baseline.exists():
        write(growth_baseline, before_growth)
    before_growth = read(growth_baseline)
    growth_summary = None
    if apply:
        ftw.write_atomic(str(fundamentals_path), fmo.dump_store(store))
        growth_summary = growth.run(str(report / 'financials'), str(report / 'growth.json'), now,
                                   fundamentals_dir=str(report / 'fundamentals'), universe_path=str(report / 'universe.json'))
    after_growth = read(report / 'growth.json')
    old_growth = {row[0]: row for row in before_growth['rows']}
    growth_changes = []
    for row in after_growth['rows']:
        previous = old_growth.get(row[0])
        if previous is not None:
            for field, old, new in zip(after_growth['fields'], previous, row):
                if old != new:
                    growth_changes.append(dict(symbol=row[0], period='growth-snapshot', field=field, old=old, new=new))
    receipt = dict(version=1, generatedAt=now.isoformat(), applied=apply, symbols=len(members),
                   changedSymbols=sum(bool(e['changedValues']) for e in entries), changedValues=len(changes),
                   removedFiles=0, removedPeriods=0, entries=entries, changes=changes,
                   fundamentalsHistoryChanges=history_changes, growthChanges=growth_changes,
                   growthBuild=growth_summary,
                   scope='official financial-industry membership union existing financial-sector-format flags; all three current FinMind datasets from 2015')
    target = report / 'tw-financial-sector-corrections.json' if apply else cache / 'preview.json'
    if apply and target.exists():
        previous = read(target)
        receipt['previousRuns'] = previous.get('previousRuns', []) + [{
            k: previous.get(k) for k in ('generatedAt','applied','changedSymbols','changedValues','changes','growthChanges')}]
    write(target, receipt)
    print(json.dumps({k:receipt[k] for k in ('applied','symbols','changedSymbols','changedValues','removedFiles','removedPeriods')}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "docs/report")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache/task-a-financials")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    if args.fetch:
        fetch(args.report_dir, args.cache)
    elif args.apply or args.preview:
        rebuild(args.report_dir, args.cache, args.apply)
    else:
        print(json.dumps(cohort(args.report_dir)))


if __name__ == "__main__":
    main()
