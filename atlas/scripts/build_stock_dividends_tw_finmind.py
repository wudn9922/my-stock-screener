"""
Taiwan stock dividend events from FinMind (TaiwanStockDividend, one request per stock) → the "stockDividends" block
of docs/report/financials/<code>.TW(O).json. The statements themselves are untouched: the Atlas financials page,
the P/E river and build_growth.py derive the retroactively restated EPS from the reported EPS and these events
(tw_stock_dividends.py has the rule). Stocks without a financials file are skipped; a stock is fetched again after
RECHECK_DAYS (a new resolution can appear any time).

  python atlas/scripts/build_stock_dividends_tw_finmind.py --budget 1200        resumable, oldest check first
  Options: --tickers 2330,1773, --limit N requests, --report-dir docs/report. Env: FINMIND_TOKEN (optional).
Exit codes: 0 (also when paused), 1 when FinMind refused the token.
"""
import argparse
import os
import sys
import time
from datetime import date, datetime, timezone

import tw_stock_dividends as div
from build_valuation_us import read_json, write_atomic
from finmind_client import (AuthError, FinMindError, Http, LimitReached, RateLimited, fetch_rows, request_interval)

START_DATE = "2010-01-01"
RECHECK_DAYS = 30


def due(document, today):
    """True when this stock's block is missing or older than RECHECK_DAYS."""
    stored = document.get("stockDividends")
    checked = div.day_of(stored.get("checked")) if isinstance(stored, dict) else None
    return checked is None or (today - checked).days >= RECHECK_DAYS


def apply_events(path, document, events, today):
    """Writes the block (only when events or the check day changed) → 'written' | 'unchanged'."""
    new = div.block(events, today.isoformat())
    old = document.get("stockDividends")
    if isinstance(old, dict) and old.get("events") == events and old.get("source") == div.SOURCE and old.get("checked") == new["checked"]:
        return "unchanged"
    document = dict(document)
    document["stockDividends"] = new
    write_atomic(path, document)
    return "written"


def run(args, http=None, sleep=time.sleep, clock=time.monotonic, log=print, today=None):
    today = today or datetime.now(timezone.utc).date()
    out_dir = args.out_dir or os.path.join(args.report_dir, "financials")
    token = (os.environ.get("FINMIND_TOKEN") or "").strip() or None
    wanted = set(args.tickers or ())
    work = []
    for name in sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []:
        stem = name[:-5] if name.endswith(".json") else None
        if not stem or not stem.upper().endswith((".TW", ".TWO")):
            continue
        code = stem.rsplit(".", 1)[0]
        if wanted and code not in wanted:
            continue
        path = os.path.join(out_dir, name)
        document = read_json(path)
        if not isinstance(document, dict):
            continue
        if wanted or due(document, today):
            stored = document.get("stockDividends")
            work.append((str(stored.get("checked")) if isinstance(stored, dict) else "", code, path, document))
    work.sort(key=lambda item: (item[0], item[1]))
    log(f"stock dividends: {len(work)} stocks to fetch, {'with' if token else 'without'} token, "
        f"one request every {request_interval(token):.1f}s, budget {args.budget:.0f}s")
    http = http or Http()
    counter = {"requests": 0, "limit": args.limit}
    deadline = clock() + args.budget
    interval = request_interval(token)
    written = 0
    for _, code, path, document in work:
        if counter["requests"]:
            if clock() + interval > deadline:
                log("PAUSED: time budget used up; run the same command again")
                break
            sleep(interval)
        try:
            rows = fetch_rows(http, div.DATASET, code, START_DATE, token, sleep, log, counter)
        except AuthError as error:
            log(f"::error::STOPPED: {error}")
            return 1
        except (RateLimited, LimitReached) as error:
            log(f"PAUSED: {error}")
            break
        except FinMindError as error:
            log(f"::warning::{code}: {error}; skipped")
            continue
        if apply_events(path, document, div.parse_events(rows), today) == "written":
            written += 1
    log(f"stock dividends: {written} files written, {counter['requests']} requests")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="FinMind stock dividends → financials/<code>.json stockDividends")
    parser.add_argument("--report-dir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs", "report"))
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--budget", type=float, default=600.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--tickers", type=lambda text: [p.strip().upper() for p in text.split(",") if p.strip()], default=None)
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))
