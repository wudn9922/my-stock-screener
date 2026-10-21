"""Audit daily CSVs against their original providers and fill source-confirmed gaps.

Run with --apply to write; otherwise only produce an audit. Raw responses and
byte-for-byte originals are retained under .cache/kline-backfill/.
"""
import argparse
import concurrent.futures
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def valid(value, column):
    try:
        number = float(value)
        return math.isfinite(number) and (number >= 0 if column == "Volume" else number > 0)
    except (TypeError, ValueError):
        return False


def cutoff(symbol, now):
    taiwan = symbol.endswith((".TW", ".TWO")) or symbol == "^TWOII"
    local = now.astimezone(ZoneInfo("Asia/Taipei" if taiwan else "America/New_York"))
    close = local.replace(hour=13 if taiwan else 16, minute=50 if taiwan else 20, second=0, microsecond=0)
    return (local if local >= close else local - timedelta(days=1)).date().isoformat()


def reconcile(local, source):
    """Preserve good local cells; insert only complete, actual provider bars."""
    result = {r["Date"]: dict(r) for r in local}
    added, repaired = [], []
    for row in source:
        day = row["Date"]
        if datetime.fromisoformat(day).weekday() >= 5:
            continue
        if not all(valid(row.get(c), c) for c in COLUMNS):
            continue
        if day not in result:
            result[day] = dict(row)
            added.append(day)
        else:
            for col in COLUMNS:
                if not valid(result[day].get(col), col):
                    result[day][col] = row[col]
                    repaired.append({"date": day, "column": col})
    return [result[d] for d in sorted(result)], added, repaired


def yahoo(symbol, start, end, cache):
    begin = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp()) - 86400
    finish = int((datetime.fromisoformat(end).replace(tzinfo=timezone.utc) + timedelta(days=2)).timestamp())
    url = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(symbol, safe="")
    url += "?" + urllib.parse.urlencode(dict(period1=begin, period2=finish, interval="1d", events="history"))
    target = cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
    if target.exists():
        payload = json.loads(target.read_text())
    else:
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(request, timeout=35) as response:
                    raw = response.read()
                payload = json.loads(raw)
                if payload.get("chart", {}).get("error"):
                    raise ValueError(str(payload["chart"]["error"]))
                target.write_bytes(raw)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
    chart = payload["chart"]["result"][0]
    zone = ZoneInfo(chart["meta"]["exchangeTimezoneName"])
    quote = chart["indicators"]["quote"][0]
    rows = []
    for i, stamp in enumerate(chart.get("timestamp", [])):
        day = datetime.fromtimestamp(stamp, zone).date().isoformat()
        if start <= day <= end:
            rows.append({"Date": day, **{c: quote[c.lower()][i] for c in COLUMNS}})
    return rows, str(target.relative_to(ROOT)), url


def tpex(start, end, cache):
    sys.path.insert(0, str(ROOT))
    import main
    import pandas as pd

    rows = []
    for month in pd.period_range(start[:7], end[:7], freq="M"):
        target = cache / f"tpex-{month}.csv"
        if target.exists():
            frame = pd.read_csv(target, index_col=0, parse_dates=True)
        else:
            def fetch(url, params, kind):
                url += "?" + urllib.parse.urlencode(params)
                raw_target = cache / f"tpex-{month}-{kind}.json"
                if raw_target.exists():
                    return json.loads(raw_target.read_text())
                request = urllib.request.Request(url, headers=main.TPEX_HTTP_HEADERS)
                with urllib.request.urlopen(request, timeout=20) as response:
                    raw = response.read()
                payload = json.loads(raw.decode("utf-8-sig"))
                raw_target.write_bytes(raw)
                return payload

            # The configured new index URL currently returns a 404 HTML page.
            # Use the project's original official legacy endpoint directly.
            payload = fetch(main.TPEX_INDEX_HISTORY_LEGACY_URL,
                            {"l": "zh-tw", "d": f"{month.year - 1911}/{month.month:02d}", "o": "json"}, "index")
            frame = main.parse_tpex_index_payload(payload)
            payload = fetch(main.TPEX_TRADING_INDEX_URL,
                            {"date": f"{month.year}/{month.month:02d}/01", "response": "json"}, "volume")
            volume = main.parse_tpex_trading_payload(payload)
            frame = frame[(frame.index.year == month.year) & (frame.index.month == month.month)].copy()
            if frame.empty:
                raise ValueError(f"TPEx returned no index bars for {month}")
            frame["Volume"] = volume.reindex(frame.index)
            frame.to_csv(target)
            time.sleep(main.TPEX_REQUEST_DELAY)
        for day, row in frame.iterrows():
            day = day.strftime("%Y-%m-%d")
            if start <= day <= end:
                rows.append({"Date": day, **{c: row[c] for c in COLUMNS}})
    return rows, str(cache.relative_to(ROOT)) + "/tpex-*", main.TPEX_INDEX_HISTORY_LEGACY_URL


def process(path, args, now, cache, backup):
    symbol = path.stem
    original = path.read_bytes()
    local = list(csv.DictReader(io.StringIO(original.decode("utf-8-sig"))))
    end = cutoff(symbol, now)
    start = min((r["Date"] for r in local), default=(now.date() - timedelta(days=400)).isoformat())
    entry = {"symbol": symbol, "source": "TPEx" if symbol == "^TWOII" else "Yahoo", "start": start, "end": end, "before_rows": len(local)}
    try:
        source, evidence, url = (tpex(start, end, cache) if symbol == "^TWOII" else yahoo(symbol, start, end, cache))
        if not source:
            raise ValueError("Original provider returned no bars in requested interval")
        # Source dates, rather than weekdays or other stocks, define confirmed gaps.
        merged, added, repaired = reconcile(local, source)
        present = {r["Date"] for r in source}
        unavailable = [r["Date"] for r in local if r["Date"] <= end and r["Date"] not in present]
        invalid = [{"date": r["Date"], "column": c} for r in merged for c in COLUMNS if not valid(r.get(c), c)]
        incomplete = [r["Date"] for r in source if not all(valid(r.get(c), c) for c in COLUMNS)]
        entry.update(status="ok", source_rows=len(source), evidence=evidence, url=url, added_dates=added, repaired_cells=repaired, after_rows=len(merged), local_dates_absent_from_source=unavailable, unresolved_cells=invalid, incomplete_source_dates=incomplete)
        if args.apply and (added or repaired):
            (backup / path.name).write_bytes(original)
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=["Date"] + COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(merged)
            temporary = path.with_suffix(".csv.tmp")
            temporary.write_text(output.getvalue())
            temporary.replace(path)
            # Verify the written file has no remaining source-confirmed gaps/cells.
            check = list(csv.DictReader(path.open()))
            _, missing, cells = reconcile(check, source)
            if missing or cells or len(check) != len(merged):
                raise ValueError("Post-write verification failed")
        entry["remaining_confirmed_missing"] = 0 if args.apply else len(added)
    except Exception as exc:
        entry.update(status="error", error=f"{type(exc).__name__}: {exc}")
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--resume", action="store_true", help="Retain successful entries from the previous audit and retry unfinished files")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    run = now.strftime("%Y%m%dT%H%M%SZ")
    cache = ROOT / ".cache/kline-backfill/source"
    backup = ROOT / ".cache/kline-backfill" / run / "originals"
    cache.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=True)
    paths = sorted((ROOT / "data").glob("*.csv"))
    if args.symbols:
        paths = [p for p in paths if p.stem in args.symbols]
    entries = []
    report = ROOT / "docs/report/ohlcv-backfill.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    if args.resume and report.exists():
        previous = json.loads(report.read_text())
        if previous["applied"] != args.apply:
            raise ValueError("Cannot resume an audit with a different --apply mode")
        entries = [e for e in previous["symbols"] if e["status"] == "ok"]
        done = {e["symbol"] for e in entries}
        paths = [p for p in paths if p.stem not in done]
        backup = ROOT / previous["backup"]
    total = len(paths) + len(entries)
    if not paths:
        print("No unfinished matching CSV files.", flush=True)
        return 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        tasks = [pool.submit(process, p, args, now, cache, backup) for p in paths]
        for task in concurrent.futures.as_completed(tasks):
            entry = task.result()
            entries.append(entry)
            if len(entries) % 50 == 0 or entry["status"] == "error":
                print(f"{len(entries)}/{total} {entry['symbol']} {entry['status']}", flush=True)
            payload = dict(generated_at=now.isoformat(), applied=args.apply, backup=str(backup.relative_to(ROOT)), summary=dict(files=len(entries), errors=sum(e["status"] == "error" for e in entries), updated_files=sum(bool(e.get("added_dates") or e.get("repaired_cells")) for e in entries), added_rows=sum(len(e.get("added_dates", [])) for e in entries), repaired_cells=sum(len(e.get("repaired_cells", [])) for e in entries)), symbols=sorted(entries, key=lambda e:e["symbol"]))
            temp = report.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            temp.replace(report)
    print(json.dumps(payload["summary"], ensure_ascii=False), flush=True)
    return int(payload["summary"]["errors"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
