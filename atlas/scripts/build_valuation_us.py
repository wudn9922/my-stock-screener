"""
Builds public/valuation/us.json from Yahoo Finance (git-ignored; built in GitHub Actions).

SEC (data.sec.gov / www.sec.gov) answers 403 to GitHub Actions runners whatever the User-Agent,
and Yahoo's quote / fundamentals endpoints answer 429 to Node's fetch, so this step is Python with
curl_cffi (Chrome TLS impersonation, the same library yfinance uses for the daily report).

  python scripts/build_valuation_us.py
  Env: ATLAS_DIRECTORY_PATH (default public/symbols/directory.json),
       ATLAS_VALUATION_OUT_DIR (default public/valuation),
       YAHOO_ANNUAL_BUDGET_SECONDS (default 900): time allowed for annual-EPS requests per run.

- EPS TTM: v7/finance/quote epsTrailingTwelveMonths, 250 symbols per request, refreshed every run.
- EPS annual: fundamentals-timeseries annualDilutedEPS (fallback annualBasicEPS), one request per
  symbol, only for symbols whose stored fiscal year is missing or older than ~13 months; the rest keep
  the previous run's value (the workflow seeds public/valuation from the last deployment).
- Only companies reporting in USD: for ADRs Yahoo's EPS is per ordinary share, not per ADR, so the
  P/E would be off by the ADR ratio (e.g. TSM). Those symbols get no record rather than a wrong one.

A failure keeps the previous us.json and exits 1 so the workflow shows a warning.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
TIMESERIES_URL = (
    "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"
)
QUOTE_BATCH = 250
QUOTE_DELAY = 0.5
ANNUAL_DELAY = 0.12
# Re-check the annual figure once the stored fiscal year ended more than this long ago
ANNUAL_STALE_DAYS = 400
# Symbols without any annual data are retried at most this often
ANNUAL_RETRY_DAYS = 7
MAX_CONSECUTIVE_FAILURES = 8
MIN_US_RECORDS = 1000
SOURCE = "Yahoo Finance"


def round4(value):
    return round(float(value), 4)


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def us_symbols_from_directory(document):
    items = (document or {}).get("items") or []
    return [
        item[0]
        for item in items
        if isinstance(item, list)
        and len(item) >= 3
        and isinstance(item[0], str)
        and item[2] not in ("TWSE", "TPEx")
    ]


def parse_quotes(payload):
    """quoteResponse → {symbol: epsTtm} for USD-reporting equities."""
    result = {}
    for quote in ((payload or {}).get("quoteResponse") or {}).get("result") or []:
        symbol = quote.get("symbol")
        eps = finite_number(quote.get("epsTrailingTwelveMonths"))
        if not isinstance(symbol, str) or eps is None:
            continue
        if quote.get("quoteType") not in (None, "EQUITY"):
            continue
        if quote.get("financialCurrency") not in (None, "USD"):
            continue
        result[symbol] = round4(eps)
    return result


def parse_annual(payload):
    """timeseries → (eps, period_end 'YYYY-MM-DD') of the latest USD fiscal year, or None."""
    best = {}
    for series in ((payload or {}).get("timeseries") or {}).get("result") or []:
        kind = ((series.get("meta") or {}).get("type") or [None])[0]
        if kind not in ("annualDilutedEPS", "annualBasicEPS"):
            continue
        for point in series.get(kind) or []:
            if not point:
                continue
            date = point.get("asOfDate")
            value = finite_number((point.get("reportedValue") or {}).get("raw"))
            if not isinstance(date, str) or value is None:
                continue
            if point.get("currencyCode") not in (None, "USD"):
                continue
            current = best.get(kind)
            if current is None or date > current[1]:
                best[kind] = (round4(value), date)
    return best.get("annualDilutedEPS") or best.get("annualBasicEPS")


def needs_annual(previous, today):
    if not previous:
        return True
    period_end = previous.get("annualPeriodEnd")
    if isinstance(period_end, str):
        try:
            ended = datetime.strptime(period_end, "%Y-%m-%d").date()
        except ValueError:
            return True
        return (today - ended).days > ANNUAL_STALE_DAYS
    checked = previous.get("annualCheckedAt")
    if isinstance(checked, str):
        try:
            last = datetime.strptime(checked, "%Y-%m-%d").date()
        except ValueError:
            return True
        return (today - last).days >= ANNUAL_RETRY_DAYS
    return True


def build_record(eps_ttm, annual, previous, today_iso, annual_checked):
    previous = previous or {}
    record = {
        "epsTtm": eps_ttm,
        "epsAnnual": previous.get("epsAnnual"),
        "fiscalYear": previous.get("fiscalYear"),
        "asOf": today_iso,
        "source": SOURCE,
    }
    if previous.get("annualPeriodEnd"):
        record["annualPeriodEnd"] = previous["annualPeriodEnd"]
    if annual is not None:
        eps, period_end = annual
        record["epsAnnual"] = eps
        record["fiscalYear"] = int(period_end[:4])
        record["annualPeriodEnd"] = period_end
    if annual_checked:
        record["annualCheckedAt"] = today_iso
    elif previous.get("annualCheckedAt"):
        record["annualCheckedAt"] = previous["annualCheckedAt"]
    return record


def read_json(path):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return None


def write_atomic(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(path + ".tmp", "w", encoding="utf-8") as file:
        file.write(body)
    os.replace(path + ".tmp", path)
    return len(body.encode("utf-8"))


class Yahoo:
    def __init__(self):
        from curl_cffi import requests as curl_requests

        self.session = curl_requests.Session(impersonate="chrome")
        self.session.get("https://fc.yahoo.com/", timeout=20)
        response = self.session.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=20
        )
        crumb = response.text.strip()
        if response.status_code != 200 or not crumb or "<" in crumb:
            raise RuntimeError(f"Yahoo crumb HTTP {response.status_code}: {crumb[:80]!r}")
        self.crumb = crumb

    def get_json(self, url, params):
        response = self.session.get(url, params={**params, "crumb": self.crumb}, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        return response.json()


def main():
    directory_path = os.environ.get("ATLAS_DIRECTORY_PATH") or "public/symbols/directory.json"
    out_dir = os.environ.get("ATLAS_VALUATION_OUT_DIR") or "public/valuation"
    budget = float(os.environ.get("YAHOO_ANNUAL_BUDGET_SECONDS") or 900)
    out_path = os.path.join(out_dir, "us.json")

    symbols = us_symbols_from_directory(read_json(directory_path))
    if len(symbols) < MIN_US_RECORDS:
        raise RuntimeError(f"directory has only {len(symbols)} US symbols")

    previous_doc = read_json(out_path) or {}
    previous_items = {}
    if previous_doc.get("source", "").startswith(SOURCE):
        previous_items = previous_doc.get("items") or {}

    now = datetime.now(timezone.utc)
    today = now.date()
    today_iso = today.isoformat()
    yahoo = Yahoo()

    eps_ttm = {}
    failures = 0
    for start in range(0, len(symbols), QUOTE_BATCH):
        batch = symbols[start:start + QUOTE_BATCH]
        try:
            payload = yahoo.get_json(
                QUOTE_URL,
                {
                    "symbols": ",".join(batch),
                    "fields": "symbol,epsTrailingTwelveMonths,financialCurrency,quoteType",
                },
            )
            eps_ttm.update(parse_quotes(payload))
            failures = 0
        except Exception as error:  # noqa: BLE001 - keep going; the count check decides
            failures += 1
            print(f"quote batch {start}: {error}", file=sys.stderr)
            if failures >= 3:
                raise RuntimeError(f"quote requests keep failing: {error}") from error
        time.sleep(QUOTE_DELAY)

    if len(eps_ttm) < MIN_US_RECORDS:
        raise RuntimeError(f"only {len(eps_ttm)} US quotes with EPS")

    annual = {}
    checked = set()
    pending = [symbol for symbol in eps_ttm if needs_annual(previous_items.get(symbol), today)]
    deadline = time.monotonic() + budget
    period2 = int(now.timestamp())
    period1 = int((now - timedelta(days=5 * 365)).timestamp())
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
                    "type": "annualDilutedEPS,annualBasicEPS",
                    "period1": period1,
                    "period2": period2,
                },
            )
            checked.add(symbol)
            result = parse_annual(payload)
            if result is not None:
                annual[symbol] = result
            failures = 0
        except Exception as error:  # noqa: BLE001
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                stop_reason = f"{failures} consecutive failures ({error})"
                break
        time.sleep(ANNUAL_DELAY)

    items = {
        symbol: build_record(
            eps,
            annual.get(symbol),
            previous_items.get(symbol),
            today_iso,
            symbol in checked,
        )
        for symbol, eps in sorted(eps_ttm.items())
    }

    document = {
        "version": 1,
        "market": "US",
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "source": f"{SOURCE} (v7 quote epsTrailingTwelveMonths; fundamentals-timeseries annualDilutedEPS)",
        "notes": (
            "USD-reporting companies only (ADR EPS is per ordinary share). "
            "epsTtm refreshed every run; epsAnnual = latest fiscal year, re-checked after the period "
            "is over ~13 months old. asOf = data date."
        ),
        "items": items,
    }
    size = write_atomic(out_path, document)
    print(json.dumps({
        "market": "US",
        "records": len(items),
        "withAnnual": sum(1 for item in items.values() if item.get("epsAnnual") is not None),
        "annualRequested": len(checked),
        "annualPending": len(pending) - len(checked),
        "annualStopped": stop_reason,
        "bytes": size,
    }))
    if stop_reason and stop_reason != "time budget":
        print(f"::warning::US annual EPS refresh stopped early: {stop_reason}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"::warning::US valuation not rebuilt (previous file kept): {error}")
        sys.exit(1)
