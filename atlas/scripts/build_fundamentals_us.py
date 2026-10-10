"""
Builds public/valuation/us-growth.json (quarterly EPS / revenue growth) from Yahoo Finance
(git-ignored; built in GitHub Actions, seeded from the last deployment like us.json).

  python scripts/build_fundamentals_us.py
  Env: ATLAS_REPORT_DIR (default ../docs/report): universe.json / latest.json / themes.json,
       ATLAS_DATA_DIR (default ../data): CSV fallback for the symbol list,
       ATLAS_VALUATION_OUT_DIR (default public/valuation),
       YAHOO_GROWTH_BUDGET_SECONDS (default 600): time allowed for requests per run.

- Symbols: US rows of docs/report/universe.json + US report groups (latest.json) + theme
  constituents (themes.json), whichever exist; without universe.json, every US CSV in data/ too.
- fundamentals-timeseries quarterlyDilutedEPS / quarterlyBasicEPS / quarterlyTotalRevenue, one
  request per symbol; only points in USD (or without a currency) are kept, so foreign-currency
  reporters (most ADRs) get no record rather than a wrong one.
- Yahoo returns only the last few quarters, so each run merges into the previous file by period
  end and keeps up to 12 quarters; YoY compares with the quarter that ended 365 ± 25 days earlier.
- A symbol is re-fetched when it has no record, when its latest quarter is over 100 days old and
  it was checked more than 3 days ago (the next report is due), or when it was checked more than
  30 days ago.

Eight consecutive failed requests stop the run: the file is still written (records already in it
are kept) and the script exits 1 so the workflow shows a warning.
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from build_valuation_us import Yahoo, finite_number, read_json, write_atomic

TIMESERIES_URL = (
    "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"
)
SERIES_KINDS = {
    "quarterlyDilutedEPS": "diluted",
    "quarterlyBasicEPS": "basic",
    "quarterlyTotalRevenue": "revenue",
}
SOURCE = "Yahoo Finance"
REQUEST_DELAY = 0.12
MAX_CONSECUTIVE_FAILURES = 8
DEFAULT_BUDGET_SECONDS = 600
HISTORY_YEARS = 3
KEEP_QUARTERS = 12
YOY_QUARTERS = 4
YOY_DAYS = 365
YOY_TOLERANCE_DAYS = 25
# Latest quarter older than this → a new report is due, re-check every RECHECK_DAYS
REPORT_DUE_DAYS = 100
RECHECK_DAYS = 3
MAX_CHECK_AGE_DAYS = 30
# Records of symbols no longer in any source are kept this long after their last check
KEEP_UNLISTED_DAYS = 60
TW_SUFFIXES = (".TW", ".TWO")


def parse_day(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def round4(value):
    return round(float(value), 4)


def clean_revenue(value):
    value = finite_number(value)
    if value is None:
        return None
    return int(value) if value.is_integer() else round4(value)


def clean_eps(value):
    value = finite_number(value)
    return None if value is None else round4(value)


def us_symbol(value):
    if not isinstance(value, str):
        return None
    symbol = value.strip().upper()
    if not symbol or symbol.startswith("^") or symbol.endswith(TW_SUFFIXES):
        return None
    if len(symbol) > 12 or not all(ch.isalnum() or ch in ".-" for ch in symbol):
        return None
    return symbol


# ---------------------------------------------------------------------------------------------
# Yahoo payload
# ---------------------------------------------------------------------------------------------
def parse_growth(payload):
    """timeseries → {'diluted'|'basic'|'revenue': {period_end: value}} (quarterly, USD or no currency)."""
    parsed = {"diluted": {}, "basic": {}, "revenue": {}}
    timeseries = (payload or {}).get("timeseries") if isinstance(payload, dict) else None
    results = (timeseries or {}).get("result") if isinstance(timeseries, dict) else None
    for series in results or []:
        if not isinstance(series, dict):
            continue
        kind = ((series.get("meta") or {}).get("type") or [None])[0]
        if kind not in SERIES_KINDS:
            continue
        target = parsed[SERIES_KINDS[kind]]
        for point in series.get(kind) or []:
            if not isinstance(point, dict):
                continue
            period_end = point.get("asOfDate")
            value = finite_number((point.get("reportedValue") or {}).get("raw"))
            if parse_day(period_end) is None or value is None:
                continue
            if point.get("periodType") not in (None, "3M"):
                continue
            if point.get("currencyCode") not in (None, "", "USD"):
                continue
            target[period_end] = value
    return parsed


def quarters_from_parsed(parsed):
    """→ ([[period_end, eps, revenue], ...] ascending, basis). Diluted EPS, basic where missing."""
    diluted, basic, revenue = parsed["diluted"], parsed["basic"], parsed["revenue"]
    basis = "diluted" if diluted else "basic"
    rows = []
    for period_end in sorted(set(diluted) | set(basic) | set(revenue)):
        eps = diluted.get(period_end)
        if eps is None:
            eps = basic.get(period_end)
        rows.append([period_end, clean_eps(eps), clean_revenue(revenue.get(period_end))])
    return rows, basis


# ---------------------------------------------------------------------------------------------
# Merge, YoY, records
# ---------------------------------------------------------------------------------------------
def merge_quarters(previous, new, keep=KEEP_QUARTERS):
    """Merge by period end (new values win, a missing new value keeps the old one); last `keep`."""
    merged = {}
    for rows in (previous or [], new or []):
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 3 or parse_day(row[0]) is None:
                continue
            eps, revenue = clean_eps(row[1]), clean_revenue(row[2])
            current = merged.get(row[0], [row[0], None, None])
            merged[row[0]] = [
                row[0],
                eps if eps is not None else current[1],
                revenue if revenue is not None else current[2],
            ]
    rows = [merged[key] for key in sorted(merged) if merged[key][1] is not None or merged[key][2] is not None]
    return rows[-keep:]


def year_ago_index(quarters, index):
    """Index of the quarter that ended 365 ± 25 days before quarters[index], closest to 365."""
    ended = parse_day(quarters[index][0])
    best = None
    for other in range(index):
        gap = (ended - parse_day(quarters[other][0])).days
        if abs(gap - YOY_DAYS) <= YOY_TOLERANCE_DAYS:
            if best is None or abs(gap - YOY_DAYS) < best[0]:
                best = (abs(gap - YOY_DAYS), other)
    return None if best is None else best[1]


def growth_pct(current, base):
    if current is None or base is None or base <= 0:
        return None
    return round((current / base - 1) * 100, 1)


def build_growth_record(quarters, basis, checked_at):
    """Record for us-growth.json, or None when there is no quarterly data at all."""
    quarters = merge_quarters([], quarters)
    if not quarters:
        return None
    eps_yoy, rev_yoy = [], []
    for offset in range(min(YOY_QUARTERS, len(quarters))):
        index = len(quarters) - 1 - offset
        previous = year_ago_index(quarters, index)
        if previous is None:
            eps_yoy.append(None)
            rev_yoy.append(None)
            continue
        eps_yoy.append(growth_pct(quarters[index][1], quarters[previous][1]))
        rev_yoy.append(growth_pct(quarters[index][2], quarters[previous][2]))
    latest_previous = year_ago_index(quarters, len(quarters) - 1)
    latest_eps = quarters[-1][1]
    base_eps = quarters[latest_previous][1] if latest_previous is not None else None
    return {
        "q": quarters,
        "epsYoY": eps_yoy,
        "revYoY": rev_yoy,
        "epsTurn": bool(latest_eps is not None and latest_eps > 0 and base_eps is not None and base_eps <= 0),
        "basis": basis,
        "latest": quarters[-1][0],
        "checkedAt": checked_at,
    }


def needs_refresh(record, today, miss_checked=None):
    if not record:
        if miss_checked is None:
            return True
        checked = parse_day(miss_checked)
        return checked is None or (today - checked).days > RECHECK_DAYS
    latest, checked = parse_day(record.get("latest")), parse_day(record.get("checkedAt"))
    if latest is None or checked is None:
        return True
    if (today - checked).days > MAX_CHECK_AGE_DAYS:
        return True
    return (today - latest).days > REPORT_DUE_DAYS and (today - checked).days > RECHECK_DAYS


# ---------------------------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------------------------
def symbols_from_universe(document):
    fields = (document or {}).get("fields") or []
    column = fields.index("symbol") if "symbol" in fields else 0
    rows = (((document or {}).get("markets") or {}).get("US") or {}).get("rows") or []
    return [row[column] for row in rows if isinstance(row, list) and len(row) > column]


def symbols_from_report(document):
    symbols = []
    for group in (document or {}).get("groups") or []:
        if not isinstance(group, dict) or str(group.get("market") or "").upper() == "TW":
            continue
        for item in group.get("items") or []:
            if isinstance(item, dict):
                symbols.append(item.get("symbol"))
    return symbols


def symbols_from_themes(document):
    symbols = []
    for theme in (document or {}).get("themes") or []:
        if not isinstance(theme, dict):
            continue
        for item in theme.get("constituents") or []:
            if isinstance(item, dict):
                symbols.append(item.get("symbol"))
    return symbols


def symbols_from_csv(data_dir):
    try:
        names = os.listdir(data_dir)
    except OSError:
        return []
    return [name[:-4] for name in names if name.endswith(".csv")]


def collect_symbols(report_dir, data_dir):
    universe = symbols_from_universe(read_json(os.path.join(report_dir, "universe.json")))
    candidates = (
        universe
        + symbols_from_report(read_json(os.path.join(report_dir, "latest.json")))
        + symbols_from_themes(read_json(os.path.join(report_dir, "themes.json")))
    )
    # Without universe.json (not generated yet) the report groups alone are a small list:
    # the US CSVs (S&P 500 + custom stocks) stand in for it
    if not any(map(us_symbol, universe)):
        candidates += symbols_from_csv(data_dir)
    return sorted({symbol for symbol in map(us_symbol, candidates) if symbol})


# ---------------------------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------------------------
def run_refresh(symbols, previous_doc, fetch, now, budget_seconds,
                delay=REQUEST_DELAY, sleep=time.sleep, clock=time.monotonic):
    """
    fetch(symbol) → timeseries payload (raises on failure).
    Returns (document, stats); stats["failed"] is True when consecutive failures stopped the run.
    """
    today = now.date()
    today_iso = today.isoformat()
    previous_doc = previous_doc if isinstance(previous_doc, dict) else {}
    same_source = str(previous_doc.get("source") or "").startswith(SOURCE)
    previous_items = (previous_doc.get("items") or {}) if same_source else {}
    previous_misses = (previous_doc.get("misses") or {}) if same_source else {}
    listed = set(symbols)

    items = {}
    for symbol, record in previous_items.items():
        if not isinstance(record, dict) or not isinstance(record.get("q"), list):
            continue
        checked = parse_day(record.get("checkedAt"))
        if symbol in listed or (checked and (today - checked).days <= KEEP_UNLISTED_DAYS):
            items[symbol] = record
    misses = {symbol: value for symbol, value in previous_misses.items() if symbol in listed}

    pending = [
        symbol for symbol in symbols
        if needs_refresh(items.get(symbol), today, misses.get(symbol))
    ]
    # Never fetched first, then the oldest check
    pending.sort(key=lambda symbol: (
        symbol in items or symbol in misses,
        (items.get(symbol) or {}).get("checkedAt") or misses.get(symbol) or "",
        symbol,
    ))

    deadline = clock() + budget_seconds
    failures = 0
    requested = 0
    updated = 0
    stop_reason = None
    failed = False
    for symbol in pending:
        if clock() > deadline:
            stop_reason = "time budget"
            break
        try:
            payload = fetch(symbol)
            failures = 0
        except Exception as error:  # noqa: BLE001 - keep the previous record
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                stop_reason = f"{failures} consecutive failures ({error})"
                failed = True
                break
            continue
        finally:
            requested += 1
            sleep(delay)
        quarters, basis = quarters_from_parsed(parse_growth(payload))
        previous = items.get(symbol) or {}
        record = build_growth_record(
            merge_quarters(previous.get("q"), quarters),
            basis if quarters else previous.get("basis") or basis,
            today_iso,
        )
        if record is None:
            misses[symbol] = today_iso
            continue
        items[symbol] = record
        misses.pop(symbol, None)
        updated += 1

    document = {
        "version": 1,
        "market": "US",
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "source": SOURCE,
        "notes": (
            "fundamentals-timeseries quarterlyDilutedEPS (basic where missing) and "
            "quarterlyTotalRevenue, USD only. q = [periodEnd, eps, revenue] ascending, merged "
            "across runs (up to 12 quarters). epsYoY / revYoY: index 0 = latest quarter, vs the "
            "quarter ended 365 +/- 25 days earlier; null when that base is missing or <= 0."
        ),
        "items": dict(sorted(items.items())),
        "misses": dict(sorted(misses.items())),
    }
    stats = {
        "symbols": len(symbols),
        "pending": len(pending),
        "requested": requested,
        "updated": updated,
        "records": len(items),
        "misses": len(misses),
        "stopped": stop_reason,
        "failed": failed,
    }
    return document, stats


def main():
    report_dir = os.environ.get("ATLAS_REPORT_DIR") or "../docs/report"
    data_dir = os.environ.get("ATLAS_DATA_DIR") or "../data"
    out_dir = os.environ.get("ATLAS_VALUATION_OUT_DIR") or "public/valuation"
    budget = float(os.environ.get("YAHOO_GROWTH_BUDGET_SECONDS") or DEFAULT_BUDGET_SECONDS)
    out_path = os.path.join(out_dir, "us-growth.json")

    symbols = collect_symbols(report_dir, data_dir)
    if not symbols:
        raise RuntimeError("no US symbols found")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    period2 = int(now.timestamp())
    period1 = int((now - timedelta(days=HISTORY_YEARS * 365)).timestamp())
    yahoo = Yahoo()

    def fetch(symbol):
        return yahoo.get_json(
            TIMESERIES_URL.format(symbol=symbol),
            {
                "symbol": symbol,
                "type": ",".join(SERIES_KINDS),
                "period1": period1,
                "period2": period2,
            },
        )

    document, stats = run_refresh(symbols, read_json(out_path), fetch, now, budget)
    stats["bytes"] = write_atomic(out_path, document)
    print({"market": "US", **stats})
    if stats["failed"]:
        raise RuntimeError(f"growth refresh stopped early: {stats['stopped']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"::warning::US growth data not fully rebuilt (previous records kept): {error}")
        sys.exit(1)
