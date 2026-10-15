#!/usr/bin/env python3
"""Cross-check SEC-backed stored financial periods against official SEC Company Facts.

This is a read-only verification pass. It uses the repository's SEC extractor and SEC request
client, makes one request per issuer, and writes only comparison metadata (not raw SEC payloads).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


SAMPLE_GROUPS = {
    "large-cap": ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "KO"),
    "retail": ("WMT", "COST", "TGT"),
    "industrial-tech": ("GE", "ORCL", "DELL", "ADBE", "F"),
    "financial": ("JPM", "BAC", "WFC", "GS", "C", "USB", "PNC", "MS"),
    "energy-utilities": ("XOM", "CVX", "NEE"),
    "small-mid-cap": ("AAME", "ACAD", "AMTX"),
    "healthcare": ("PFE", "UNH"),
    "foreign-issuer": ("TM", "SONY"),
}
COMPARE_FIELDS = (
    "revenue", "costOfRevenue", "grossProfit", "operatingIncome", "pretaxIncome", "taxProvision",
    "netIncome", "interestExpense", "operatingCashFlow", "capex", "equity", "totalDebt", "cash",
    "totalAssets", "dividendsPaid", "buybacks",
)


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def stored_periods(section: dict[str, Any], frequency: str) -> list[dict[str, Any]]:
    end = section.get("end", [])
    fy = section.get("fy", [])
    fq = section.get("fq", [])
    src = section.get("src", [])
    values = section.get("values", {}) if isinstance(section.get("values"), dict) else {}
    periods = []
    for index in range(max(len(end), len(fy), len(fq))):
        fiscal_year = fy[index] if index < len(fy) else None
        fiscal_quarter = fq[index] if index < len(fq) else None
        source = src[index] if index < len(src) else None
        if not isinstance(fiscal_year, int) or source != "sec":
            continue
        if fiscal_year < 2016 or fiscal_year > 2026:
            continue
        if frequency == "quarterly" and not isinstance(fiscal_quarter, int):
            continue
        values_at_period = {
            name: series[index] if isinstance(series, list) and index < len(series) else None
            for name, series in values.items()
        }
        periods.append({
            "end": end[index] if index < len(end) else None,
            "fy": fiscal_year, "fq": fiscal_quarter, "values": values_at_period,
        })
    return periods


def numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def period_map(result: Any, frequency: str) -> dict[tuple[int, ...], list[dict[str, Any]]]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "atlas/scripts"))
    import build_financials as financials

    indexed: dict[tuple[int, ...], list[dict[str, Any]]] = {}
    end_month = financials.fy_end_month([item["end"] for item in getattr(result, "annual", [])])
    for period in getattr(result, frequency, []):
        if frequency == "annual":
            fy, fq = financials.fiscal_year(period["end"]), None
        elif end_month is not None:
            fy, fq = financials.fiscal_quarter(period["end"], end_month)
        else:
            fy, fq = None, None
        if not isinstance(fy, int) or fy < 2016 or fy > 2026:
            continue
        key = (fy,) if frequency == "annual" else (fy, fq) if isinstance(fq, int) else None
        if key is not None:
            indexed.setdefault(key, []).append(period)
    return indexed


def end_distance_days(left: Any, right: Any) -> int:
    try:
        return abs((date.fromisoformat(left) - date.fromisoformat(right)).days)
    except (TypeError, ValueError):
        return 10_000


def choose_raw_period(candidates: list[dict[str, Any]], stored_end: Any) -> dict[str, Any] | None:
    if not candidates:
        return None
    return min(candidates, key=lambda item: end_distance_days(item.get("end"), stored_end))


def payload_digest(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def merge_crosscheck_rows(existing_rows: list[dict[str, Any]], new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append untouched symbol/CIK pairs and replace pairs refreshed by this run."""
    replacement_keys = {(row["symbol"], row["cik"]) for row in new_rows}
    merged_rows = []
    retained_keys = set()
    for row in existing_rows:
        key = (row.get("symbol"), row.get("cik"))
        if key not in replacement_keys and key not in retained_keys:
            merged_rows.append(row)
            retained_keys.add(key)
    return merged_rows + new_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--limit", type=int, default=32)
    parser.add_argument("--symbols", nargs="+", help="Limit this run to named symbols from the representative sample")
    parser.add_argument("--cik-overrides", nargs="*", default=[], help="Optional SYMBOL=CIK identity checks using a historical CIK")
    parser.add_argument("--append", action="store_true", help="Append new symbol rows to an existing cross-check CSV")
    parser.add_argument("--out", type=Path, default=Path("reports/financial-backfill/source-crosschecks.csv"))
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    root = args.root.resolve()
    sys.path.insert(0, str(root / "atlas/scripts"))
    import seed_financials_sec as sec  # imported after script path setup
    import build_financials  # imported after script path setup

    progress = read_json(root / "docs/report/fundamentals/us-market-progress.json")
    issuer_symbols = progress.get("symbols", {})
    cik_overrides = {}
    for item in args.cik_overrides:
        if "=" not in item:
            parser.error("--cik-overrides entries must be SYMBOL=CIK")
        symbol, cik = item.split("=", 1)
        cik = cik.strip().removeprefix("CIK").zfill(10)
        if not symbol.strip() or not cik.isdigit() or len(cik) != 10:
            parser.error(f"invalid CIK override: {item}")
        cik_overrides[symbol.strip().upper()] = cik
    candidates = [(symbol, group) for group, symbols in SAMPLE_GROUPS.items() for symbol in symbols]
    requested_symbols = set(args.symbols or [])
    symbols: list[tuple[str, str]] = []
    seen = set()
    for symbol, group in candidates:
        if requested_symbols and symbol not in requested_symbols:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        path = root / "docs/report/financials" / f"{symbol}.json"
        if path.is_file() and read_json(path).get("secConcepts") and issuer_symbols.get(symbol, {}).get("cik"):
            symbols.append((symbol, group))
        if not requested_symbols and len(symbols) >= args.limit:
            break
    if requested_symbols and requested_symbols - {symbol for symbol, _ in symbols}:
        parser.error(f"sample symbol(s) not available for SEC verification: {sorted(requested_symbols - {symbol for symbol, _ in symbols})}")

    client = sec.SecClient(sec.resolve_user_agent(os.environ))
    output_rows = []
    stopped = None
    for position, (symbol, group) in enumerate(symbols, 1):
        company_path = root / "docs/report/financials" / f"{symbol}.json"
        document = read_json(company_path)
        financial_file_sha256 = file_digest(company_path)
        verified_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        cik = cik_overrides.get(symbol, issuer_symbols[symbol]["cik"])
        bytes_before = client.bytes_received
        try:
            payload = client.company_facts(cik)
            if payload is None:
                raise sec.FetchError("SEC returned 404 for Company Facts")
            extracted = sec.extract_periods(payload, sec.reference_from_document(document))
        except (sec.FetchError, sec.TooManyFailures, sec.SecForbidden) as error:
            output_rows.append({"market": "US", "symbol": symbol, "sampleGroup": group, "cik": cik,
                                "status": "source-fetch-failed", "detail": type(error).__name__,
                                "annualPeriodsCompared": 0, "quarterlyPeriodsCompared": 0,
                                "valuesCompared": 0, "mismatches": 0, "unmatchedStoredPeriods": 0,
                                "financialFileSha256": financial_file_sha256, "verifiedAt": verified_at,
                                "rawEntityName": "", "payloadSha256": "",
                                "responseBytes": client.bytes_received - bytes_before})
            stopped = str(error) if isinstance(error, (sec.TooManyFailures, sec.SecForbidden)) else stopped
            if stopped:
                break
            continue
        comparisons = 0
        mismatches = 0
        unmatched = 0
        annual_compared = 0
        quarterly_compared = 0
        mismatch_fields: Counter[str] = Counter()
        for frequency in ("annual", "quarterly"):
            candidates_by_period = period_map(extracted, frequency)
            local_periods = stored_periods(document.get(frequency, {}), frequency)
            for stored in local_periods:
                candidates_for_period = candidates_by_period.get(
                    (stored["fy"],) if frequency == "annual" else (stored["fy"], stored["fq"]), [])
                raw = choose_raw_period(candidates_for_period, stored.get("end"))
                if raw is None or end_distance_days(raw.get("end"), stored.get("end")) > 24:
                    unmatched += 1
                    continue
                compared_this_period = False
                for field in COMPARE_FIELDS:
                    actual = stored["values"].get(field)
                    source_value = raw.get("values", {}).get(field)
                    if not numeric(actual) or not numeric(source_value):
                        continue
                    comparisons += 1
                    compared_this_period = True
                    if not math.isclose(actual, source_value, rel_tol=1e-9, abs_tol=1e-8):
                        mismatches += 1
                        mismatch_fields[field] += 1
                if compared_this_period:
                    if frequency == "annual":
                        annual_compared += 1
                    else:
                        quarterly_compared += 1
        if mismatches:
            status = "mismatch-found"
        elif comparisons:
            status = "passed"
        else:
            status = "no-overlapping-values"
        output_rows.append({
            "market": "US", "symbol": symbol, "sampleGroup": f"{group}:cik-override" if symbol in cik_overrides else group, "cik": cik,
            "status": status, "detail": json.dumps(dict(mismatch_fields), ensure_ascii=False, separators=(",", ":")),
            "annualPeriodsCompared": annual_compared, "quarterlyPeriodsCompared": quarterly_compared,
            "valuesCompared": comparisons, "mismatches": mismatches, "unmatchedStoredPeriods": unmatched,
            "financialFileSha256": financial_file_sha256, "verifiedAt": verified_at,
            "rawEntityName": payload.get("entityName", ""), "payloadSha256": payload_digest(payload),
            "responseBytes": client.bytes_received - bytes_before,
        })
        print(f"[{position}/{len(symbols)}] {symbol}: {status}; {comparisons} values; {mismatches} mismatches")

    output_path = args.out if args.out.is_absolute() else root / args.out
    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = ["market", "symbol", "sampleGroup", "cik", "status", "detail", "annualPeriodsCompared",
               "quarterlyPeriodsCompared", "valuesCompared", "mismatches", "unmatchedStoredPeriods",
               "financialFileSha256", "verifiedAt", "rawEntityName", "payloadSha256", "responseBytes"]
    if args.append and output_path.is_file():
        with output_path.open(encoding="utf-8", newline="") as handle:
            existing_rows = list(csv.DictReader(handle))
        output_rows = merge_crosscheck_rows(existing_rows, output_rows)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(output_rows)
    counts = Counter(row["status"] for row in output_rows)
    print(json.dumps({"out": str(output_path), "attempted": len(output_rows), "statuses": dict(counts),
                      "secRequests": client.requests, "secResponseBytes": client.bytes_received,
                      "stopped": bool(stopped)}, ensure_ascii=False))
    return 1 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
