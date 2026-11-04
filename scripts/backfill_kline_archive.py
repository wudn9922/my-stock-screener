"""Resumable, budgeted Yahoo archive backfill. Produces a transport, never pushes Git."""
import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_universe_full as bu
import kline_archive as archive
import universe_cache as uc
import kline_instruments as instruments
import archive_tpex_index


def start_date(today, years):
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # February 29
        return today.replace(year=today.year - years, day=28)


class PeriodNetwork(bu.Network):
    def __init__(self, start, end):
        super().__init__()
        self.start, self.end = start, end

    def chart(self, symbol, _range):
        return self.chart_period(symbol, self.start, self.end)


def backfill(archive_dir, cache_dir, payload_dir, years=2, budget=1800, markets="TW,US",
             now=None, net=None, clock=time.monotonic, sleep=time.sleep, listing=None,
             calendar_years=False, include_instruments=False, segment='all', max_symbols=None,
             only_symbols=None, report_dir=None, scope_getter=instruments.get_text):
    if not 1 <= years <= 100 or not 30 <= budget <= 2400:
        raise ValueError("years must be 1..100 and budget 30..2400 seconds")
    now = now or datetime.now(timezone.utc)
    progress = archive.json_read(Path(archive_dir) / archive.PROGRESS, {"version": 1, "symbols": {}})
    target = date(now.year - years, 1, 1) if calendar_years else start_date(now.date(), years)
    if progress.get("years") == years and progress.get("targetStart"):
        target = min(target, date.fromisoformat(progress["targetStart"]))
    args = bu.parse_args(["--cache-dir", str(cache_dir), "--budget", str(budget), "--markets", markets])
    net = net or PeriodNetwork(datetime.combine(target, datetime.min.time(), timezone.utc), now)
    job = bu.Run(args, net=net, now=now, clock=clock, sleep=sleep)
    if listing is None:
        if include_instruments and segment in ('etfs', 'indices'):
            listing = {market: {} for market in args.markets}
        else:
            job.load_lists()
            listing = job.listing
            if any(not listing.get(market) for market in args.markets):
                raise ValueError("Full-market source list unavailable; no partial list presented as complete")
    plan = {"version": 1, "mode": "backfill", "symbols": [],
            "progress": {"version": 1, "years": years, "targetStart": target.isoformat(), "symbols": {}}}
    candidates = sorted({symbol for market in args.markets for symbol in listing.get(market, {})})
    extra = {}
    if include_instruments:
        print('Archive backfill: resolve public ETF/index scope', flush=True)
        previous = archive.json_read(Path(archive_dir) / instruments.INSTRUMENTS_STATE, {}).get('symbols', {})
        extra, warnings = instruments.scope(reports=report_dir, previous=previous, getter=scope_getter)
        extra = {s: item for s, item in extra.items() if ('TW' if archive.market_of(s) == 'tw' else 'US') in args.markets}
        known = archive.json_read(Path(archive_dir) / 'manifest.json', {}).get('markets', {})
        old_symbols = {s for market in known.values() for s in market.get('symbols', []) if ('TW' if archive.market_of(s) == 'tw' else 'US') in args.markets}
        candidates = sorted(set(candidates) | set(extra) | old_symbols)
        plan['instruments'] = extra
        plan['scopeWarnings'] = warnings
        print(f'Archive backfill: public instruments={len(extra)} warnings={len(warnings)}', flush=True)
    if segment not in ('all', 'stocks', 'etfs', 'indices'):
        raise ValueError('Invalid backfill segment')
    kind = lambda s: extra.get(s, {}).get('kind', 'stock')
    if segment != 'all':
        selected_kind = {'stocks': 'stock', 'etfs': 'etf', 'indices': 'index'}[segment]
        candidates = [s for s in candidates if kind(s) == selected_kind]
    if only_symbols:
        requested = {s.strip().upper() for s in only_symbols.split(',') if s.strip()}
        if requested - set(candidates):
            raise ValueError('Requested symbols must belong to the public archive scope')
        candidates = [s for s in candidates if s in requested]
    if max_symbols is not None and not 1 <= max_symbols <= 10000:
        raise ValueError('max_symbols must be 1..10000')
    attempted = 0
    failed = 0
    for symbol in candidates:
        old = progress["symbols"].get(symbol, {})
        if old.get("status") == "done" and old.get("targetStart", "9999") <= target.isoformat():
            continue
        if old.get("retryAfter", "") > now.date().isoformat():
            continue
        if job.time_left() < 30:
            break
        if max_symbols is not None and attempted >= max_symbols:
            break
        official_otc = include_instruments and symbol == '^TWOII'
        if not official_otc and not job.yahoo_ready and not job.connect_yahoo():
            raise RuntimeError("Yahoo unavailable; archive and saved progress untouched")
        try:
            attempted += 1
            if official_otc:
                frame = archive_tpex_index.history(target, now, job.net, time_left=job.time_left, sleep=sleep)
            else:
                frame = job.fetch_chart(symbol, bu.FULL_RANGE, completed_market='TW' if archive.market_of(symbol) == 'tw' else 'US') if symbol in extra else job.fetch_chart(symbol, bu.FULL_RANGE)
            # Empty/rejected source frames can have a plain Index without .date.
            if not frame.empty:
                frame = frame[frame.index.date >= target]
            if frame.empty:
                raise ValueError(f"no data from source: Yahoo returned no valid completed sessions for {symbol} in the requested period")
            rows = archive.frame_rows(frame)
            relative = f"{archive.market_of(symbol)}/{symbol}.csv"
            content = archive.csv_text(rows)
            archive.atomic(Path(payload_dir) / relative, content)
            plan["symbols"].append({"symbol": symbol, "file": relative, "sha256": archive.digest(content.encode()), "fetchedAt": now.isoformat()})
            plan["progress"]["symbols"][symbol] = {
                "status": "done", "targetStart": target.isoformat(), "firstSourceDate": rows[0][0],
                "lastSourceDate": rows[-1][0], "completedAt": now.isoformat(),
                "coverage": "source-starts-later" if rows[0][0] > (target.isoformat()) else "requested-period",
            }
            failed = 0
        except Exception as exc:
            plan["progress"]["symbols"][symbol] = dict(old)
            uc.record_failure({"symbols": plan["progress"]["symbols"]}, symbol, now.date().isoformat(), str(exc))
            plan["progress"]["symbols"][symbol].update({"status": "retry", "targetStart": target.isoformat(), "error": str(exc)[:200]})
            failed += 1
            if isinstance(exc, bu.RateLimited) or failed >= bu.MAX_CONSECUTIVE_ERRORS:
                break
        finally:
            sleep(bu.REQUEST_DELAY)
        # A terminated runner can still publish the last complete checkpoint artifact.
        archive.json_write(Path(payload_dir) / "operations.json", plan)
    archive.json_write(Path(payload_dir) / "operations.json", plan)
    print(json.dumps({"candidates": len(candidates), "fetched": len(plan["symbols"]),
                      "checkpointed": len(plan["progress"]["symbols"]), "targetStart": target.isoformat()}))
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", required=True)
    parser.add_argument("--cache-dir", default=".cache/universe-full")
    parser.add_argument("--payload-dir", default=".cache/kline-backfill-payload")
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--budget", type=float, default=1800)
    parser.add_argument("--markets", default="TW,US")
    parser.add_argument('--calendar-years', action='store_true', help='January 1 of year now-years, through the current year')
    parser.add_argument('--include-instruments', action='store_true')
    parser.add_argument('--segment', choices=['all', 'stocks', 'etfs', 'indices'], default='all')
    parser.add_argument('--max-symbols', type=int)
    parser.add_argument('--symbols', help='Optional comma-separated subset of the public scope')
    parser.add_argument('--report-dir')
    parser.add_argument('--refresh-public-reports', action='store_true')
    args = parser.parse_args()
    if args.refresh_public_reports:
        args.report_dir = args.report_dir or '.cache/archive-public-reports'
        failures = instruments.restore_public_reports(args.report_dir)
        print('Archive public reference files unavailable:', ','.join(failures) or 'none', flush=True)
    backfill(args.archive_dir, args.cache_dir, args.payload_dir, args.years, args.budget, args.markets,
             calendar_years=args.calendar_years, include_instruments=args.include_instruments,
             segment=args.segment, max_symbols=args.max_symbols, only_symbols=args.symbols, report_dir=args.report_dir)


if __name__ == "__main__":
    main()
