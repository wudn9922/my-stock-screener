"""
Taiwan stock dividend events from FinMind (TaiwanStockDividend, one request per stock) → the "stockDividends" block
of docs/report/financials/<code>.TW(O).json, and capital reductions (TaiwanStockCapitalReductionReferencePrice,
減資恢復買賣參考價) → the "capitalReductions" block. The statements themselves are untouched: the Atlas financials page,
the P/E river and build_growth.py derive the retroactively restated EPS from the reported EPS and these events
(tw_stock_dividends.py / tw_capital_reductions.py have the rules). Stocks without a financials file are skipped; a
stock is fetched again after RECHECK_DAYS (a new resolution can appear any time).

Capital reductions: FinMind serves that dataset without data_id ("Free" tier), so one request fetches every stock
(start_date START_DATE) when any file is due; an answer that is empty or spans fewer than MIN_ALL_DATES days is not
trusted as the whole market and the due stocks are fetched one request each instead. Unknown 減資原因 are logged.

  python atlas/scripts/build_stock_dividends_tw_finmind.py --budget 1200        resumable, oldest check first
  Options: --tickers 2330,1773, --limit N requests, --report-dir docs/report. Env: FINMIND_TOKEN (optional).
Exit codes: 0 (also when paused), 1 when FinMind refused the token.
"""
import argparse
import os
import sys
import time
from datetime import date, datetime, timezone

import tw_capital_reductions as cap
import tw_stock_dividends as div
from build_valuation_us import read_json, write_atomic
from finmind_client import (AuthError, FinMindError, Http, LimitReached, RateLimited, fetch_rows, request_interval)

START_DATE = "2010-01-01"
RECHECK_DAYS = 30
# An all-market capital reduction answer must span at least this many distinct days to count as the whole history
MIN_ALL_DATES = 5


def due(document, today, name="stockDividends"):
    """True when this stock's block (stockDividends / capitalReductions) is missing or older than RECHECK_DAYS."""
    stored = document.get(name)
    checked = div.day_of(stored.get("checked")) if isinstance(stored, dict) else None
    return checked is None or (today - checked).days >= RECHECK_DAYS


def apply_block(path, document, name, new, source):
    """Writes block `name` (only when its events, source or check day changed) → ('written' | 'unchanged', document)."""
    old = document.get(name)
    if isinstance(old, dict) and old.get("events") == new["events"] and old.get("source") == source and old.get("checked") == new["checked"]:
        return "unchanged", document
    document = dict(document)
    document[name] = new
    write_atomic(path, document)
    return "written", document


def apply_events(path, document, events, today):
    """Writes the stockDividends block → 'written' | 'unchanged'."""
    return apply_block(path, document, "stockDividends", div.block(events, today.isoformat()), div.SOURCE)[0]


def fetch_all_reductions(http, token, sleep, log, counter):
    """One all-market request → {stock_id: rows}, or None when the answer cannot be trusted as the whole market
    (empty, or fewer than MIN_ALL_DATES distinct days: e.g. a single-day answer). RateLimited / AuthError /
    LimitReached propagate; any other FinMind error → None (per-stock fallback)."""
    try:
        rows = fetch_rows(http, cap.DATASET, None, START_DATE, token, sleep, log, counter)
    except FinMindError as error:
        log(f"capital reductions: all-market request failed ({error}); one request per stock instead")
        return None
    days = {str(row.get("date"))[:10] for row in rows if isinstance(row, dict) and row.get("date")}
    if len(days) < MIN_ALL_DATES:
        log(f"capital reductions: all-market answer has {len(rows)} rows on {len(days)} days; one request per stock instead")
        return None
    return cap.group_by_stock(rows)


def run(args, http=None, sleep=time.sleep, clock=time.monotonic, log=print, today=None):
    today = today or datetime.now(timezone.utc).date()
    out_dir = args.out_dir or os.path.join(args.report_dir, "financials")
    token = (os.environ.get("FINMIND_TOKEN") or "").strip() or None
    wanted = set(args.tickers or ())
    work, reduction_work = [], []
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
            work.append((str(stored.get("checked")) if isinstance(stored, dict) else "", code, path))
        if wanted or due(document, today, "capitalReductions"):
            stored = document.get("capitalReductions")
            reduction_work.append((str(stored.get("checked")) if isinstance(stored, dict) else "", code, path))
    work.sort(key=lambda item: (item[0], item[1]))
    reduction_work.sort(key=lambda item: (item[0], item[1]))
    log(f"stock dividends: {len(work)} stocks to fetch, capital reductions: {len(reduction_work)} due, "
        f"{'with' if token else 'without'} token, one request every {request_interval(token):.1f}s, budget {args.budget:.0f}s")
    http = http or Http()
    counter = {"requests": 0, "limit": args.limit}
    deadline = clock() + args.budget
    interval = request_interval(token)
    written = {"stockDividends": 0, "capitalReductions": 0}

    def pace():
        """Sleeps between requests; False when the budget is used up."""
        if counter["requests"]:
            if clock() + interval > deadline:
                log("PAUSED: time budget used up; run the same command again")
                return False
            sleep(interval)
        return True

    def store(path, name, new, source):
        document = read_json(path)
        if isinstance(document, dict) and apply_block(path, document, name, new, source)[0] == "written":
            written[name] += 1

    def store_reductions(code, path, rows):
        events = cap.parse_events(rows, log)
        store(path, "capitalReductions", cap.block(events, today.isoformat()), cap.SOURCE)

    try:
        # 1. capital reductions: one all-market request, else one per due stock
        grouped = fetch_all_reductions(http, token, sleep, log, counter) if reduction_work else {}
        paused = False
        if grouped is not None:
            for _, code, path in reduction_work:
                store_reductions(code, path, grouped.get(code.upper(), []))
        else:
            for _, code, path in reduction_work:
                if not pace():
                    paused = True
                    break
                try:
                    rows = fetch_rows(http, cap.DATASET, code, START_DATE, token, sleep, log, counter)
                except FinMindError as error:
                    log(f"::warning::{code} capital reductions: {error}; skipped")
                    continue
                store_reductions(code, path, rows)
        # 2. stock dividends, one request per stock
        for _, code, path in [] if paused else work:
            if not pace():
                break
            try:
                rows = fetch_rows(http, div.DATASET, code, START_DATE, token, sleep, log, counter)
            except FinMindError as error:
                log(f"::warning::{code}: {error}; skipped")
                continue
            store(path, "stockDividends", div.block(div.parse_events(rows), today.isoformat()), div.SOURCE)
    except AuthError as error:
        log(f"::error::STOPPED: {error}")
        return 1
    except (RateLimited, LimitReached) as error:
        log(f"PAUSED: {error}")
    log(f"stock dividends: {written['stockDividends']} files written, capital reductions: "
        f"{written['capitalReductions']} files written, {counter['requests']} requests")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="FinMind stock dividends / capital reductions → financials/<code>.json")
    parser.add_argument("--report-dir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs", "report"))
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--budget", type=float, default=600.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--tickers", type=lambda text: [p.strip().upper() for p in text.split(",") if p.strip()], default=None)
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))
