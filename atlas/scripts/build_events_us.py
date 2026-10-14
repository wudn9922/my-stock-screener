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
  for midnight (date only) and for times during the session. A timestamp exactly at 00:00 UTC is a
  date-only placeholder: its UTC date is kept (the Eastern date would be the evening before).
- earningsTimestamp can still hold the last report (already past) while earningsTimestampStart/End
  already describe the next one: a past earningsTimestamp falls back to the start of that range (an
  estimate); when nothing is today or later the stock is left out.
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
# ... and at least this share of the equity quotes must carry a timestamp at all (Yahoo renaming the
# fields would otherwise replace a good file with an almost empty one)
MIN_DATED_SHARE = 0.5
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
    us_market = (universe.get("markets") or {}).get("US") or {}
    # The full-market list (scope "full", every US common stock) would multiply the requests: the US CSVs
    # in data/ (S&P 500 + custom stocks, the liquid list's members) stand in for it
    full = us_market.get("scope") == "full"
    for row in [] if full else us_market.get("rows") or []:
        if isinstance(row, list) and len(row) > column:
            symbols.add(row[column])
    latest = read_json(os.path.join(report_dir, "latest.json")) or {}
    for group in latest.get("groups") or []:
        if isinstance(group, dict) and group.get("market") == "US":
            for item in group.get("items") or []:
                if isinstance(item, dict) and item.get("status") != "failed":
                    symbols.add(item.get("symbol"))
    themes = read_json(os.path.join(report_dir, "themes.json")) or {}
    for theme in themes.get("themes") or []:
        for member in (theme.get("constituents") or []) if isinstance(theme, dict) else []:
            if isinstance(member, dict):
                symbols.add(member.get("symbol"))
    if full or not any(is_us_symbol(symbol) for symbol in symbols):
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


def is_date_only(timestamp):
    """Exactly 00:00 UTC: a date without a time of day (the Eastern date would be the evening before)."""
    return int(timestamp) % 86400 == 0


def market_date(timestamp):
    """Calendar date of an earnings timestamp: the UTC date for a date-only placeholder, else Eastern."""
    if is_date_only(timestamp):
        return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
    return eastern_date(timestamp)


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


def build_item(raw, now=None):
    """
    One earnings record for the file, or None when the quote has no usable date.
    now (aware datetime): when given, a date before today (US Eastern) is not "next" any more and the
    start of Yahoo's range is used instead; None when nothing is upcoming.
    """
    if raw.get("quoteType") not in (None, "EQUITY"):
        return None
    at, start, end = raw.get("at"), raw.get("start"), raw.get("end")
    estimate = raw["estimate"]
    if now is not None:
        today = now.astimezone(EASTERN).date().isoformat()
        if at is None or market_date(at) < today:
            # The last report can linger in earningsTimestamp while the range already points ahead
            if start is None or market_date(start) < today:
                return None
            at = start
            estimate = estimate or end is None or market_date(end) != market_date(start)
    if at is None:
        return None
    window = None
    if start is not None and end is not None:
        first, last = sorted((market_date(start), market_date(end)))
        if first != last:
            window = [first, last]
    date_only = is_date_only(at)
    return {
        "earningsDate": market_date(at),
        "earningsAt": int(at),
        "window": window,
        "estimate": estimate,
        "session": None if date_only else session_of(eastern_moment(at), estimate),
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


def main(report_dir=REPORT_DIR, data_dir=DATA_DIR, out_dir=None, now=None):
    out_dir = out_dir or os.environ.get("ATLAS_VALUATION_OUT_DIR") or "public/valuation"
    out_path = os.path.join(out_dir, "us-events.json")

    symbols = us_tickers(report_dir, data_dir)
    if not symbols:
        raise RuntimeError("no US tickers in docs/report or data/")
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    quotes = fetch_quotes(Yahoo(), symbols)
    if len(quotes) < MIN_QUOTE_COVERAGE * len(symbols):
        raise RuntimeError(f"only {len(quotes)} of {len(symbols)} US quotes returned")
    equities = [raw for raw in quotes.values() if raw.get("quoteType") in (None, "EQUITY")]
    dated = [raw for raw in equities if raw.get("at") is not None or raw.get("start") is not None]
    if len(dated) < MIN_DATED_SHARE * len(equities):
        raise RuntimeError(f"only {len(dated)} of {len(equities)} equity quotes carry an earnings timestamp")

    items = {}
    for symbol, raw in sorted(quotes.items()):
        item = build_item(raw, now)
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
