"""
Builds public/valuation/us-events.json from Yahoo Finance (git-ignored; built in GitHub Actions):
the next earnings date of each US stock, shown as the countdown badge on the screener.

  python scripts/build_events_us.py
  Env: ATLAS_VALUATION_OUT_DIR (default public/valuation)

- Stocks: the US rows of docs/report/universe.json, the US groups of docs/report/latest.json and the
  theme constituents of docs/report/themes.json (whichever exist); when none does, the US CSVs in data/.
- v7/finance/quote earningsTimestamp, earningsTimestampStart/End and isEarningsDateEstimate, 250 symbols
  per request. Yahoo's EPS / ADR logic is not needed here: only dates.
- Dates are US Eastern calendar dates, whatever time zone the runner uses. session: bmo (before the
  09:30 open) or amc (at or after the 16:00 close) for a confirmed time of day; null for estimates,
  for midnight (date only) and for times during the session.
- Taiwan is not covered: TWSE / TPEx publish no per-company earnings date in advance.

A failure keeps the previous us-events.json and exits 1 so the workflow shows a warning.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from build_valuation_us import QUOTE_URL, Yahoo, finite_number, read_json, write_atomic

QUOTE_FIELDS = (
    "symbol,quoteType,earningsTimestamp,earningsTimestampStart,earningsTimestampEnd,"
    "isEarningsDateEstimate"
)
QUOTE_BATCH = 250
QUOTE_DELAY = 0.5
MAX_CONSECUTIVE_FAILURES = 3
# Yahoo must answer for at least this share of the stocks, or the file is kept as it was
MIN_QUOTE_COVERAGE = 0.6
SOURCE = "Yahoo Finance"
EASTERN = ZoneInfo("America/New_York")
OPEN_MINUTES = 9 * 60 + 30
CLOSE_MINUTES = 16 * 60

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REPORT_DIR = os.path.join(REPO_ROOT, "docs", "report")
DATA_DIR = os.path.join(REPO_ROOT, "data")
US_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,14}")


def is_us_symbol(symbol):
    return (
        isinstance(symbol, str)
        and US_SYMBOL.fullmatch(symbol) is not None
        and not symbol.endswith((".TW", ".TWO"))
    )


def us_tickers(report_dir, data_dir):
    """Sorted US tickers from the report files; the US CSVs in data/ only when the report has none."""
    symbols = set()
    universe = read_json(os.path.join(report_dir, "universe.json")) or {}
    fields = universe.get("fields") if isinstance(universe.get("fields"), list) else []
    column = fields.index("symbol") if "symbol" in fields else 0
    for row in ((universe.get("markets") or {}).get("US") or {}).get("rows") or []:
        if isinstance(row, list) and len(row) > column:
            symbols.add(row[column])
    latest = read_json(os.path.join(report_dir, "latest.json")) or {}
    for group in latest.get("groups") or []:
        if isinstance(group, dict) and group.get("market") == "US":
            for item in group.get("items") or []:
                if isinstance(item, dict):
                    symbols.add(item.get("symbol"))
    themes = read_json(os.path.join(report_dir, "themes.json")) or {}
    for theme in themes.get("themes") or []:
        for member in (theme.get("constituents") or []) if isinstance(theme, dict) else []:
            if isinstance(member, dict):
                symbols.add(member.get("symbol"))
    if not any(is_us_symbol(symbol) for symbol in symbols):
        try:
            names = os.listdir(data_dir)
        except OSError:
            names = []
        for name in names:
            if name.endswith(".csv"):
                symbols.add(name[:-4])
    return sorted(symbol for symbol in symbols if is_us_symbol(symbol))


def parse_events(payload):
    """quoteResponse → {symbol: earnings fields} for every quote returned (the caller filters)."""
    result = {}
    for quote in ((payload or {}).get("quoteResponse") or {}).get("result") or []:
        symbol = quote.get("symbol")
        if not isinstance(symbol, str):
            continue
        quote_type = quote.get("quoteType")
        result[symbol] = {
            "quoteType": quote_type if isinstance(quote_type, str) else None,
            "at": finite_number(quote.get("earningsTimestamp")),
            "start": finite_number(quote.get("earningsTimestampStart")),
            "end": finite_number(quote.get("earningsTimestampEnd")),
            "estimate": quote.get("isEarningsDateEstimate") is True,
        }
    return result


def eastern_moment(timestamp):
    return datetime.fromtimestamp(timestamp, EASTERN)


def eastern_date(timestamp):
    return eastern_moment(timestamp).date().isoformat()


def session_of(moment, estimate):
    """bmo / amc for a confirmed time of day in US Eastern time; null when it is unknown."""
    if estimate:
        return None
    minutes = moment.hour * 60 + moment.minute
    if minutes == 0:
        # Midnight: Yahoo gave a date without a time of day
        return None
    if minutes < OPEN_MINUTES:
        return "bmo"
    if minutes >= CLOSE_MINUTES:
        return "amc"
    return None


def build_item(raw):
    """One earnings record for the file, or None when the quote has no usable date."""
    if raw.get("quoteType") not in (None, "EQUITY") or raw.get("at") is None:
        return None
    moment = eastern_moment(raw["at"])
    estimate = raw["estimate"]
    window = None
    if raw.get("start") is not None and raw.get("end") is not None:
        first, last = sorted((eastern_date(raw["start"]), eastern_date(raw["end"])))
        if first != last:
            window = [first, last]
    return {
        "earningsDate": moment.date().isoformat(),
        "earningsAt": int(raw["at"]),
        "window": window,
        "estimate": estimate,
        "session": session_of(moment, estimate),
    }


def fetch_quotes(yahoo, symbols):
    quotes = {}
    failures = 0
    for start in range(0, len(symbols), QUOTE_BATCH):
        batch = symbols[start:start + QUOTE_BATCH]
        try:
            payload = yahoo.get_json(QUOTE_URL, {"symbols": ",".join(batch), "fields": QUOTE_FIELDS})
            quotes.update(parse_events(payload))
            failures = 0
        except Exception as error:  # noqa: BLE001 - keep going; the coverage check decides
            failures += 1
            print(f"quote batch {start}: {error}", file=sys.stderr)
            if failures >= MAX_CONSECUTIVE_FAILURES:
                raise RuntimeError(f"quote requests keep failing: {error}") from error
        time.sleep(QUOTE_DELAY)
    return quotes


def main(report_dir=REPORT_DIR, data_dir=DATA_DIR, out_dir=None):
    out_dir = out_dir or os.environ.get("ATLAS_VALUATION_OUT_DIR") or "public/valuation"
    out_path = os.path.join(out_dir, "us-events.json")

    symbols = us_tickers(report_dir, data_dir)
    if not symbols:
        raise RuntimeError("no US tickers in docs/report or data/")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    quotes = fetch_quotes(Yahoo(), symbols)
    if len(quotes) < MIN_QUOTE_COVERAGE * len(symbols):
        raise RuntimeError(f"only {len(quotes)} of {len(symbols)} US quotes returned")

    items = {}
    for symbol, raw in sorted(quotes.items()):
        item = build_item(raw)
        if item is not None:
            items[symbol] = item
    if not items:
        raise RuntimeError("no US earnings dates in the quotes")

    document = {
        "version": 1,
        "market": "US",
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "source": f"{SOURCE} (v7 quote earningsTimestamp)",
        "notes": (
            "earningsDate and window are US Eastern calendar dates; session bmo / amc only for a "
            "confirmed time of day (null for estimates); window = Yahoo's estimated date range."
        ),
        "items": items,
    }
    size = write_atomic(out_path, document)
    print(json.dumps({
        "market": "US",
        "tickers": len(symbols),
        "quotes": len(quotes),
        "records": len(items),
        "estimates": sum(1 for item in items.values() if item["estimate"]),
        "bytes": size,
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"::warning::US earnings dates not rebuilt (previous file kept): {error}")
        sys.exit(1)
