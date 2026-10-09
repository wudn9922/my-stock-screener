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
- Companies reporting in another currency (ADRs such as TSM, NVO, BABA): Yahoo's per-share EPS
  series are in the home currency and not reliably per ADR, so the ADR ratio is avoided altogether:
  ADR count = market cap / price, and EPS per ADR = net income (home currency → USD at today's
  rate) / ADR count. EPS TTM keeps Yahoo's quote value only when it agrees with that (same sign,
  within 35%); otherwise the symbol gets no record rather than a wrong one. Annual EPS per ADR =
  latest fiscal-year net income in USD / ADR count. Re-computed every run (a few hundred symbols).

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
# ADR EPS TTM from the quote must agree with net income / ADR count within this ratio
ADR_TTM_TOLERANCE = 0.35
ADR_BUDGET_SECONDS = 300


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
    """quoteResponse → {symbol: {eps, currency, marketCap, price}} for equities with EPS TTM."""
    result = {}
    for quote in ((payload or {}).get("quoteResponse") or {}).get("result") or []:
        symbol = quote.get("symbol")
        eps = finite_number(quote.get("epsTrailingTwelveMonths"))
        if not isinstance(symbol, str) or eps is None:
            continue
        if quote.get("quoteType") not in (None, "EQUITY"):
            continue
        currency = quote.get("financialCurrency") or "USD"
        result[symbol] = {
            "eps": round4(eps),
            "currency": currency if isinstance(currency, str) else "USD",
            "marketCap": finite_number(quote.get("marketCap")),
            "price": finite_number(quote.get("regularMarketPrice")),
        }
    return result


def parse_fx(payload):
    """quotes of '<CUR>=X' (units of CUR per USD) → {CUR: rate}."""
    rates = {"USD": 1.0}
    for quote in ((payload or {}).get("quoteResponse") or {}).get("result") or []:
        symbol = quote.get("symbol")
        rate = finite_number(quote.get("regularMarketPrice"))
        if isinstance(symbol, str) and symbol.endswith("=X") and rate and rate > 0:
            rates[symbol[:-2]] = rate
    return rates


def parse_net_income(payload):
    """timeseries → {'ttm'|'annual': (value, currency, 'YYYY-MM-DD')} of the latest net income."""
    kinds = {
        "trailingNetIncomeCommonStockholders": "ttm",
        "annualNetIncomeCommonStockholders": "annual",
    }
    result = {}
    for series in ((payload or {}).get("timeseries") or {}).get("result") or []:
        kind = ((series.get("meta") or {}).get("type") or [None])[0]
        if kind not in kinds:
            continue
        for point in series.get(kind) or []:
            if not point:
                continue
            date = point.get("asOfDate")
            value = finite_number((point.get("reportedValue") or {}).get("raw"))
            currency = point.get("currencyCode")
            if not isinstance(date, str) or value is None or not isinstance(currency, str):
                continue
            current = result.get(kinds[kind])
            if current is None or date > current[2]:
                result[kinds[kind]] = (value, currency, date)
    return result


def adr_valuation(quote, net_income, rates):
    """
    (epsTtm, (epsAnnual, period_end) or None) per ADR in USD, or None when the inputs are missing or
    Yahoo's EPS TTM disagrees with net income / ADR count.
    """
    market_cap, price = quote.get("marketCap"), quote.get("price")
    ttm = net_income.get("ttm")
    if not market_cap or not price or market_cap <= 0 or price <= 0 or not ttm:
        return None
    rate = rates.get(ttm[1])
    if not rate:
        return None
    adr_count = market_cap / price
    implied = ttm[0] / rate / adr_count
    eps = quote["eps"]
    if implied == 0 or (implied > 0) != (eps > 0):
        return None
    if abs(implied - eps) / max(abs(implied), abs(eps)) > ADR_TTM_TOLERANCE:
        return None
    annual = None
    yearly = net_income.get("annual")
    if yearly and rates.get(yearly[1]):
        annual = (round4(yearly[0] / rates[yearly[1]] / adr_count), yearly[2])
    return eps, annual


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


def build_record(eps_ttm, annual, previous, today_iso, annual_checked, adr=False):
    previous = previous or {}
    if adr:
        # Re-computed every run from net income; never carry over a per-share value
        previous = {}
    record = {
        "epsTtm": eps_ttm,
        "epsAnnual": previous.get("epsAnnual"),
        "fiscalYear": previous.get("fiscalYear"),
        "asOf": today_iso,
        "source": SOURCE,
    }
    if adr:
        record["method"] = "net income / ADR count"
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

    quotes = {}
    failures = 0
    for start in range(0, len(symbols), QUOTE_BATCH):
        batch = symbols[start:start + QUOTE_BATCH]
        try:
            payload = yahoo.get_json(
                QUOTE_URL,
                {
                    "symbols": ",".join(batch),
                    "fields": "symbol,epsTrailingTwelveMonths,financialCurrency,quoteType,"
                              "marketCap,regularMarketPrice",
                },
            )
            quotes.update(parse_quotes(payload))
            failures = 0
        except Exception as error:  # noqa: BLE001 - keep going; the count check decides
            failures += 1
            print(f"quote batch {start}: {error}", file=sys.stderr)
            if failures >= 3:
                raise RuntimeError(f"quote requests keep failing: {error}") from error
        time.sleep(QUOTE_DELAY)

    eps_ttm = {symbol: q["eps"] for symbol, q in quotes.items() if q["currency"] == "USD"}
    if len(eps_ttm) < MIN_US_RECORDS:
        raise RuntimeError(f"only {len(eps_ttm)} US quotes with EPS")
    period2 = int(now.timestamp())
    period1 = int((now - timedelta(days=5 * 365)).timestamp())

    # ADRs and other companies reporting in a foreign currency
    foreign = {symbol: q for symbol, q in quotes.items() if q["currency"] != "USD"}
    adr = {}
    adr_skipped = 0
    rates = {"USD": 1.0}
    if foreign:
        currencies = sorted({q["currency"] for q in foreign.values()})
        try:
            rates = parse_fx(yahoo.get_json(
                QUOTE_URL,
                {"symbols": ",".join(f"{c}=X" for c in currencies), "fields": "symbol,regularMarketPrice"},
            ))
        except Exception as error:  # noqa: BLE001 - ADRs are optional
            print(f"::warning::FX quotes failed, ADRs skipped: {error}")
            foreign = {}
    adr_deadline = time.monotonic() + ADR_BUDGET_SECONDS
    failures = 0
    for symbol, quote in sorted(foreign.items()):
        if time.monotonic() > adr_deadline or failures >= MAX_CONSECUTIVE_FAILURES:
            break
        try:
            payload = yahoo.get_json(
                TIMESERIES_URL.format(symbol=symbol),
                {
                    "symbol": symbol,
                    "type": "trailingNetIncomeCommonStockholders,annualNetIncomeCommonStockholders",
                    "period1": period1,
                    "period2": period2,
                },
            )
            failures = 0
        except Exception:  # noqa: BLE001
            failures += 1
            continue
        finally:
            time.sleep(ANNUAL_DELAY)
        result = adr_valuation(quote, parse_net_income(payload), rates)
        if result is None:
            adr_skipped += 1
        else:
            adr[symbol] = result

    annual = {}
    checked = set()
    pending = [symbol for symbol in eps_ttm if needs_annual(previous_items.get(symbol), today)]
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
    for symbol, (eps, adr_annual) in adr.items():
        items[symbol] = build_record(eps, adr_annual, None, today_iso, True, adr=True)
    items = dict(sorted(items.items()))

    document = {
        "version": 1,
        "market": "US",
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "source": f"{SOURCE} (v7 quote epsTrailingTwelveMonths; fundamentals-timeseries annualDilutedEPS)",
        "notes": (
            "epsTtm refreshed every run; epsAnnual = latest fiscal year, re-checked after the period "
            "is over ~13 months old. Foreign-currency reporters (ADRs, method field): EPS per ADR = "
            "net income in USD at today's rate / (market cap / price); epsTtm kept only when Yahoo's "
            "quote agrees within 35%. asOf = data date."
        ),
        "items": items,
    }
    size = write_atomic(out_path, document)
    print(json.dumps({
        "market": "US",
        "records": len(items),
        "withAnnual": sum(1 for item in items.values() if item.get("epsAnnual") is not None),
        "adr": len(adr),
        "adrSkipped": adr_skipped,
        "adrNotChecked": len(foreign) - len(adr) - adr_skipped,
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
