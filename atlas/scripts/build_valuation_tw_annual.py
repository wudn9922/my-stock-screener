"""
Builds public/valuation/tw-annual.json: the latest full fiscal-year EPS (TWD) of Taiwan stocks from
Yahoo Finance, for scripts/build-valuation.ts to fill tw.json's epsAnnual.

Why: the TWSE / TPEx open data (t187ap14_L, mopsfin_t187ap14_O) only hold the latest quarter's
year-to-date EPS, so a full-year value exists there only between the Q4 filing (March) and the Q1
filing (May). Without this file tw.json has epsAnnual = null for every stock for most of the year and
the Atlas 「本益比」 (annual EPS) field stays empty. An official Q4 value still takes precedence for the
same fiscal year (see buildTaiwanRecords).

  python scripts/build_valuation_tw_annual.py
  Env: ATLAS_DIRECTORY_PATH (default public/symbols/directory.json),
       ATLAS_VALUATION_OUT_DIR (default public/valuation),
       YAHOO_TW_ANNUAL_BUDGET_SECONDS (default 600): time allowed for requests per run.

One fundamentals-timeseries request (annualBasicEPS, fallback annualDilutedEPS, TWD only) per symbol,
only when the stored fiscal year is missing or ended more than ~13 months ago (symbols without data are
retried weekly); the rest keep the previous run's value (the workflow seeds public/valuation from the
last deployment), so the first runs fill the file over a few workflow runs.

A failure keeps the previous tw-annual.json and exits 1 so the workflow shows a warning.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from build_valuation_us import (
    ANNUAL_DELAY,
    MAX_CONSECUTIVE_FAILURES,
    SOURCE,
    TIMESERIES_URL,
    Yahoo,
    needs_annual,
    parse_annual,
    read_json,
    write_atomic,
)

MIN_TW_SYMBOLS = 500
CURRENCY = "TWD"
# 基本每股盈餘 is what the exchanges publish, so basic EPS comes first
PREFER = ("annualBasicEPS", "annualDilutedEPS")


def tw_symbols_from_directory(document):
    items = (document or {}).get("items") or []
    return [
        item[0]
        for item in items
        if isinstance(item, list)
        and len(item) >= 3
        and isinstance(item[0], str)
        and item[2] in ("TWSE", "TPEx")
    ]


def build_entry(annual, previous, today_iso):
    """tw-annual.json entry after a request: the new value, or the previous one when none was found."""
    previous = previous or {}
    entry = {
        "epsAnnual": previous.get("epsAnnual"),
        "fiscalYear": previous.get("fiscalYear"),
    }
    if previous.get("annualPeriodEnd"):
        entry["annualPeriodEnd"] = previous["annualPeriodEnd"]
    if annual is not None:
        eps, period_end = annual
        entry["epsAnnual"] = round(eps, 2)
        entry["fiscalYear"] = int(period_end[:4])
        entry["annualPeriodEnd"] = period_end
    entry["annualCheckedAt"] = today_iso
    return entry


def main():
    directory_path = os.environ.get("ATLAS_DIRECTORY_PATH") or "public/symbols/directory.json"
    out_dir = os.environ.get("ATLAS_VALUATION_OUT_DIR") or "public/valuation"
    budget = float(os.environ.get("YAHOO_TW_ANNUAL_BUDGET_SECONDS") or 600)
    out_path = os.path.join(out_dir, "tw-annual.json")

    symbols = tw_symbols_from_directory(read_json(directory_path))
    if len(symbols) < MIN_TW_SYMBOLS:
        raise RuntimeError(f"directory has only {len(symbols)} Taiwan symbols")
    previous_items = (read_json(out_path) or {}).get("items") or {}

    now = datetime.now(timezone.utc)
    today = now.date()
    today_iso = today.isoformat()
    period2 = int(now.timestamp())
    period1 = int((now - timedelta(days=5 * 365)).timestamp())
    yahoo = Yahoo()

    pending = [symbol for symbol in symbols if needs_annual(previous_items.get(symbol), today)]
    updated = {}
    deadline = time.monotonic() + budget
    failures = 0
    stop_reason = None
    for symbol in pending:
        if time.monotonic() > deadline:
            stop_reason = "time budget"
            break
        try:
            payload = yahoo.get_json(
                TIMESERIES_URL.format(symbol=symbol),
                {
                    "symbol": symbol,
                    "type": ",".join(PREFER),
                    "period1": period1,
                    "period2": period2,
                },
            )
            updated[symbol] = build_entry(
                parse_annual(payload, currency=CURRENCY, prefer=PREFER),
                previous_items.get(symbol),
                today_iso,
            )
            failures = 0
        except Exception as error:  # noqa: BLE001
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                stop_reason = f"{failures} consecutive failures ({error})"
                break
        time.sleep(ANNUAL_DELAY)

    listed = set(symbols)
    items = {symbol: entry for symbol, entry in previous_items.items() if symbol in listed}
    items.update(updated)
    items = dict(sorted(items.items()))
    document = {
        "version": 1,
        "market": "TW",
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "source": f"{SOURCE} (fundamentals-timeseries annualBasicEPS, fallback annualDilutedEPS, TWD)",
        "notes": (
            "Latest full fiscal-year EPS per Taiwan stock, re-checked after the period is over ~13 months "
            "old; merged into tw.json epsAnnual (annualSource) when no exchange Q4 statement for that or "
            "a later year is stored."
        ),
        "items": items,
    }
    size = write_atomic(out_path, document)
    print(json.dumps({
        "market": "TW-annual",
        "records": len(items),
        "withAnnual": sum(1 for item in items.values() if item.get("epsAnnual") is not None),
        "requested": len(updated),
        "pending": len(pending) - len(updated),
        "stopped": stop_reason,
        "bytes": size,
    }))
    if stop_reason and stop_reason != "time budget":
        print(f"::warning::Taiwan annual EPS refresh stopped early: {stop_reason}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"::warning::Taiwan annual EPS not rebuilt (previous file kept): {error}")
        sys.exit(1)
