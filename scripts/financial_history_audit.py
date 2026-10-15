#!/usr/bin/env python3
"""Reproducible coverage and data-quality inventory for financial history files.

This audit uses the stored company fiscal-year (fy) and fiscal-quarter (fq)
labels. It deliberately does not infer fiscal periods from calendar dates or
quality labels. It is read-only with respect to source data and replaces only
the generated files in the requested report directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ANNUAL_YEARS = tuple(range(2016, 2026))
QUARTERS = tuple((year, quarter) for year in ANNUAL_YEARS for quarter in range(1, 5))
FIELDS = (
    "revenue", "costOfRevenue", "grossProfit", "operatingIncome",
    "operatingIncomeReported", "pretaxIncome", "taxProvision", "netIncome",
    "epsDiluted", "epsBasic", "dilutedShares", "interestExpense",
    "operatingCashFlow", "capex", "fcfYahoo", "equity", "totalDebt", "cash",
    "totalAssets", "dividendsPaid", "buybacks",
)
CORE_FIELDS = ("revenue", "netIncome", "totalAssets", "equity", "operatingCashFlow")
MARKET_EXCHANGES = {"US": {"NASDAQ", "NYSE", "NYSE American", "NYSE Arca", "Cboe BZX"},
                    "TW": {"TWSE", "TPEx"}}


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def fiscal_rows(data: Any, series_name: str, symbol: str, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        issues.append(issue("P1", symbol, "structure", f"{series_name} is not an object"))
        return []
    arrays = {key: value for key, value in data.items() if isinstance(value, list)}
    sizes = {key: len(value) for key, value in arrays.items()}
    count = max(sizes.values(), default=0)
    if sizes and len(set(sizes.values())) > 1:
        issues.append(issue("P1", symbol, "structure", f"{series_name} column lengths differ: {sizes}"))
    value_columns = data.get("values") if isinstance(data.get("values"), dict) else {}
    rows: list[dict[str, Any]] = []
    for index in range(count):
        row: dict[str, Any] = {}
        for key, values in arrays.items():
            if key != "values":
                row[key] = values[index] if index < len(values) else None
        row["values"] = {
            field: values[index] if isinstance(values, list) and index < len(values) else None
            for field, values in value_columns.items()
        }
        rows.append(row)
    return rows


def issue(severity: str, symbol: str, category: str, detail: str) -> dict[str, Any]:
    return {"severity": severity, "symbol": symbol, "category": category, "detail": detail}


def write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(row.get(key)) for key in headers})


def csv_value(value: Any) -> Any:
    if isinstance(value, (list, tuple, set)):
        return ";".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def git_value(root: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def calculate_data_delta(root: Path, revision: str) -> dict[str, Any]:
    """Compare checked-in financial JSON paths against a valid git commit."""
    try:
        subprocess.run(["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=root,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        changed_paths = subprocess.run(
            ["git", "diff", "--name-only", revision, "--", "docs/report/financials"], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f"could not compare financial files against revision {revision!r}") from error

    changed_files = [path for path in changed_paths
                     if path.endswith(".json") and not path.endswith("/index.json")]
    keys = ("changedFiles", "annualPeriodsAdded", "annualPeriodsRemoved", "quarterPeriodsAdded",
            "quarterPeriodsRemoved", "newNonNullValueCells", "removedNonNullValueCells",
            "changedNonNullValues", "netBytesAdded")
    delta = {market: {key: 0 for key in keys} for market in ("US", "TW")}

    def period_values(document: dict[str, Any], frequency: str) -> dict[tuple[int, ...], dict[str, Any]]:
        records = fiscal_rows(document.get(frequency), frequency, str(document.get("symbol", "?")), [])
        indexed: dict[tuple[int, ...], dict[str, Any]] = {}
        for record in records:
            fy, fq = record.get("fy"), record.get("fq")
            if not isinstance(fy, int):
                continue
            if frequency == "quarterly":
                if not isinstance(fq, int):
                    continue
                period = (fy, fq)
            else:
                period = (fy,)
            indexed[period] = record["values"]
        return indexed

    for relative in changed_files:
        current_path = root / relative
        old_result = subprocess.run(["git", "show", f"{revision}:{relative}"], cwd=root,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        old_blob = old_result.stdout if old_result.returncode == 0 else b""
        try:
            old_doc = json.loads(old_blob.decode("utf-8")) if old_blob else {}
            current_doc = read_json(current_path) if current_path.is_file() else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid JSON while comparing financial file {relative}") from error
        market = current_doc.get("market") or old_doc.get("market")
        if market not in delta:
            market = "TW" if current_path.stem.endswith(".TW") else "US"
        delta[market]["changedFiles"] += 1
        delta[market]["netBytesAdded"] += (current_path.stat().st_size if current_path.is_file() else 0) - len(old_blob)
        for frequency, added_key, removed_key in (
            ("annual", "annualPeriodsAdded", "annualPeriodsRemoved"),
            ("quarterly", "quarterPeriodsAdded", "quarterPeriodsRemoved"),
        ):
            before, after = period_values(old_doc, frequency), period_values(current_doc, frequency)
            delta[market][added_key] += len(set(after) - set(before))
            delta[market][removed_key] += len(set(before) - set(after))
            for period in set(before) | set(after):
                old_values, new_values = before.get(period, {}), after.get(period, {})
                for field in set(old_values) | set(new_values):
                    was_value, now_value = old_values.get(field), new_values.get(field)
                    was_number, now_number = is_number(was_value), is_number(now_value)
                    if now_number and not was_number:
                        delta[market]["newNonNullValueCells"] += 1
                    elif was_number and not now_number:
                        delta[market]["removedNonNullValueCells"] += 1
                    elif was_number and now_number and was_value != now_value:
                        delta[market]["changedNonNullValues"] += 1
    return {
        "baseRevision": revision,
        "changedFinancialFiles": len(changed_files),
        "markets": delta,
        "method": "compares stored fiscal-period keys and numeric value cells against the specified git revision",
    }


def period_field_counts(records_by_company: list[list[dict[str, Any]]], period_type: str) -> tuple[int, Counter[str]]:
    """Count unique, non-empty fiscal periods and their non-null numeric fields."""
    expected_periods: tuple[Any, ...] = ANNUAL_YEARS if period_type == "annual" else QUARTERS
    present_periods = 0
    nonnull_by_field: Counter[str] = Counter()
    for records in records_by_company:
        by_period: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            period = record.get("fy") if period_type == "annual" else (record.get("fy"), record.get("fq"))
            if period in expected_periods:
                by_period[period].append(record)
        for period in expected_periods:
            rows = by_period.get(period, [])
            if len(rows) != 1:
                continue
            values = rows[0]["values"]
            if any(is_number(value) for value in values.values()):
                present_periods += 1
            for field in FIELDS:
                if is_number(values.get(field)):
                    nonnull_by_field[field] += 1
    return present_periods, nonnull_by_field


def stale_source_crosscheck_rows(source_rows: list[dict[str, Any]], current_file_hashes: dict[str, str | None],
                                 current_cik_by_symbol: dict[str, Any]) -> list[dict[str, Any]]:
    """Find checks that no longer describe the checked-in file or current CIK mapping."""
    stale = []
    for row in source_rows:
        symbol = row.get("symbol", "")
        file_hash_matches = bool(row.get("financialFileSha256")) and row.get("financialFileSha256") == current_file_hashes.get(symbol)
        is_historical_override = str(row.get("sampleGroup", "")).endswith(":cik-override")
        mapped_cik = str((current_cik_by_symbol.get(symbol, {}) or {}).get("cik", ""))
        cik_matches = is_historical_override or row.get("cik") == mapped_cik
        if not file_hash_matches or not cik_matches:
            stale.append(row)
    return stale


def json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def analyze_company(symbol: str, market: str, name: str, exchange: str, current: bool,
                    sector: str, industry: str, index_item: dict[str, Any] | None,
                    financial_path: Path, ci: dict[str, Any] | None,
                    issues: list[dict[str, Any]], issuer: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    present = financial_path.is_file()
    data: dict[str, Any] = read_json(financial_path) if present else {}
    annual = fiscal_rows(data.get("annual"), "annual", symbol, issues) if present else []
    quarterly = fiscal_rows(data.get("quarterly"), "quarterly", symbol, issues) if present else []

    annual_by_fy: dict[int, list[dict[str, Any]]] = defaultdict(list)
    quarter_by_period: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in annual:
        fy = row.get("fy")
        if isinstance(fy, int):
            annual_by_fy[fy].append(row)
        elif fy is not None:
            issues.append(issue("P1", symbol, "period", f"non-integer annual fy: {fy!r}"))
    for row in quarterly:
        fy, fq = row.get("fy"), row.get("fq")
        if isinstance(fy, int) and isinstance(fq, int):
            quarter_by_period[(fy, fq)].append(row)
            if fq not in (1, 2, 3, 4):
                issues.append(issue("P1", symbol, "period", f"fiscal quarter out of range: FY{fy} Q{fq}"))
        elif fy is not None or fq is not None:
            issues.append(issue("P1", symbol, "period", f"invalid quarterly period: fy={fy!r}, fq={fq!r}"))
    for fy, rows in annual_by_fy.items():
        if len(rows) > 1:
            issues.append(issue("P1", symbol, "duplicate-period", f"duplicate annual FY{fy} ({len(rows)} rows)"))
    for period, rows in quarter_by_period.items():
        if len(rows) > 1:
            issues.append(issue("P1", symbol, "duplicate-period", f"duplicate quarterly FY{period[0]} Q{period[1]} ({len(rows)} rows)"))

    annual_target = [annual_by_fy.get(year, []) for year in ANNUAL_YEARS]
    quarter_target = [quarter_by_period.get(period, []) for period in QUARTERS]
    valid_annual = lambda rows: len(rows) == 1 and any(is_number(v) for v in rows[0]["values"].values())
    valid_quarter = lambda rows: len(rows) == 1 and any(is_number(v) for v in rows[0]["values"].values())
    missing_fy = [year for year, rows in zip(ANNUAL_YEARS, annual_target) if not valid_annual(rows)]
    missing_q = [f"{year}Q{quarter}" for (year, quarter), rows in zip(QUARTERS, quarter_target) if not valid_quarter(rows)]
    core_annual = all(len(rows) == 1 and all(is_number(rows[0]["values"].get(field)) for field in CORE_FIELDS)
                      for rows in annual_target)
    core_quarter = all(len(rows) == 1 and all(is_number(rows[0]["values"].get(field)) for field in CORE_FIELDS)
                       for rows in quarter_target)
    missing_core_annual_fields = [field for field in CORE_FIELDS if any(
        len(rows) != 1 or not is_number(rows[0]["values"].get(field)) for rows in annual_target)]
    missing_core_quarter_fields = [field for field in CORE_FIELDS if any(
        len(rows) != 1 or not is_number(rows[0]["values"].get(field)) for rows in quarter_target)]

    source_counts: Counter[str] = Counter()
    file_source_counts: Counter[str] = Counter()
    for row in annual + quarterly:
        src = row.get("src")
        if isinstance(src, list):
            source_counts.update(str(item) for item in src if item)
        elif src:
            source_counts[str(src)] += 1
    unlabeled_periods = sum(1 for row in annual + quarterly if not row.get("src"))
    if present and unlabeled_periods and data.get("source"):
        file_source_counts[str(data["source"])] += unlabeled_periods
        issues.append(issue("P2", symbol, "provenance", "some periods rely on a file-level source label, not period-level provenance"))
    elif present and unlabeled_periods:
        issues.append(issue("P1", symbol, "provenance", "financial file has periods with no source label"))
    if not present and current:
        issues.append(issue("P1", symbol, "coverage", "current market symbol has no detailed financial JSON"))

    # Gross profit identity is a robust cross-source check; tolerates rounding.
    for period_type, rows in (("annual", annual), ("quarterly", quarterly)):
        for row in rows:
            values = row["values"]
            revenue, cost, gross = (values.get("revenue"), values.get("costOfRevenue"), values.get("grossProfit"))
            if all(is_number(value) for value in (revenue, cost, gross)):
                tolerance = max(1.0, abs(revenue) * 0.015)
                if abs(gross - (revenue - cost)) > tolerance:
                    label = f"FY{row.get('fy')}" + (f" Q{row.get('fq')}" if period_type == "quarterly" else "")
                    issues.append(issue("P1", symbol, "accounting-identity",
                                        f"{period_type} {label}: grossProfit differs from revenue - costOfRevenue"))

    quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
    if present:
        unit = data.get("unit")
        if not unit:
            # Schema v1 has no unit field; keep this as an explicit quality finding.
            issues.append(issue("P2", symbol, "metadata", "unit is not recorded in legacy financial JSON"))
        if not data.get("currency"):
            issues.append(issue("P1", symbol, "metadata", "currency is missing"))
    first_fy = min(annual_by_fy) if annual_by_fy else None
    last_fy = max(annual_by_fy) if annual_by_fy else None
    last_quarter = max(quarter_by_period) if quarter_by_period else None
    period_ends = [row.get("end") for row in annual + quarterly if isinstance(row.get("end"), str)]
    symbol_queue_status = (ci or {}).get("status") if isinstance(ci, dict) else None
    queue_status = ((issuer or {}).get("status") if isinstance(issuer, dict) else None) or symbol_queue_status
    if market == "TW":
        recovery_status = "awaiting-official-historical-source"
        recovery_reason = "MOPS historical result fetch returned HTTP 503; no ten-year backfill values have been ingested"
    elif queue_status == "excluded_sic_6770":
        recovery_status = "requires-shell-issuer-review"
        recovery_reason = "SEC seeder classifies this issuer under SIC 6770 and excludes it from automatic seeding; review legal status and available filings individually"
    elif queue_status in ("foreign-filer",):
        recovery_status = "requires-foreign-filer-source-review"
        recovery_reason = "SEC queue classified the issuer as a foreign filer; quarterly regime and alternative filings need review"
    elif queue_status in ("unknownmetadata", "failure", "unknown_cik_mapping"):
        recovery_status = "sec-metadata-retry-unresolved"
        recovery_reason = f"SEC queue status after retry: {queue_status}"
    elif queue_status in ("no-facts", "no-us-gaap", "non-usd"):
        recovery_status = "sec-facts-limited-review"
        recovery_reason = f"SEC queue status: {queue_status}; alternate taxonomy, currency, or source availability is unresolved"
    else:
        recovery_status = "targeted-period-gap-review"
        recovery_reason = "current source file exists or SEC evidence is available; missing fiscal periods/values need safe incremental repair"
    values = {
        "symbol": symbol, "market": market, "name": name, "exchange": exchange,
        "currentTarget": current, "cohortClass": "current" if current else "unlisted-candidate",
        "sector": sector, "industry": industry,
        "companyId": data.get("companyId") or data.get("lei") or (symbol.split(".")[0] if market == "TW" else None),
        "companyIdBasis": "CIK" if (ci or {}).get("cik") or data.get("cik") else ("Taiwan company code in ticker" if market == "TW" else "unknown"),
        "cik": (ci or {}).get("cik") or data.get("cik"),
        "hasIndexItem": index_item is not None, "hasFinancialFile": present,
        "currency": data.get("currency"), "unit": data.get("unit"), "source": data.get("source"),
        "financialFileSha256": sha256(financial_path) if present else None,
        "financialFileBytes": financial_path.stat().st_size if present else 0,
        "periodSourceCount": sum(source_counts.values()), "periodSources": dict(source_counts),
        "fileLevelSourceFallbacks": dict(file_source_counts), "unlabeledPeriods": unlabeled_periods,
        "firstAnnualFY": first_fy, "latestAnnualFY": last_fy,
        "annualPeriodRows": len(annual), "quarterPeriodRows": len(quarterly),
        "annualFY2016_2025Present": 10 - len(missing_fy),
        "quarterFY2016_2025Present": 40 - len(missing_q),
        "annualFY2026Present": valid_annual(annual_by_fy.get(2026, [])),
        "quarterPeriodsFY2026Present": sum(bool(quarter_by_period.get((2026, q))) for q in range(1, 5)),
        "missingFY2016_2025": missing_fy, "missingQuartersFY2016_2025": missing_q,
        "missingCoreAnnualFields": missing_core_annual_fields,
        "missingCoreQuarterFields": missing_core_quarter_fields,
        "annualTenYearComplete": len(missing_fy) == 0,
        "quarterFortyComplete": len(missing_q) == 0,
        "coreAnnualComplete": core_annual, "coreQuarterlyComplete": core_quarter,
        "firstObservedFY": first_fy, "latestObservedQuarter": f"{last_quarter[0]}Q{last_quarter[1]}" if last_quarter else None,
        "latestPeriodEnd": max(period_ends) if period_ends else None,
        "observedHistoryAnnualPeriods2016_2025": sum(bool(annual_by_fy.get(year)) for year in ANNUAL_YEARS),
        "qualityLevelLegacy": quality.get("level"), "qualityIssuesLegacy": quality.get("issues", []),
        "foreignReporter": data.get("foreignReporter"),
        "secQueueStatus": queue_status,
        "secSymbolStatus": symbol_queue_status,
        "backfillAssessment": recovery_status,
        "backfillReason": recovery_reason,
        "quarterlyApplicability": "review-foreign-reporter-interim-regime" if data.get("foreignReporter") else "quarterly-target",
        "filingDateAvailable": False, "acceptedAtAvailable": False,
        "accessionNumberAvailable": False, "restatementVintageAvailable": False,
        "pointInTimeUsable": False,
    }
    detail = {"annual": annual, "quarterly": quarterly, "fieldMetrics": defaultdict(lambda: {"annual": [0, 0], "quarterly": [0, 0]}),
              "record": values, "sourceCounts": source_counts, "fileSourceCounts": file_source_counts, "quality": quality}
    return values, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path, default=Path("reports/financial-backfill"))
    parser.add_argument("--github-repo-size-kb", type=int, help="Optional GitHub API repo size in KB for reproducible capacity reporting")
    parser.add_argument("--compare-revision", help="Git revision used to count actual financial period/value additions")
    parser.add_argument("--sec-retry-manifest", type=Path, help="Optional compact JSON log for a completed SEC retry batch")
    args = parser.parse_args()
    root = args.root.resolve()
    out = (root / args.out).resolve() if not args.out.is_absolute() else args.out.resolve()
    index_path = root / "docs/report/financials/index.json"
    universe_path = root / "docs/report/universe.json"
    directory_path = root / "docs/atlas/symbols/directory.json"
    progress_path = root / "docs/report/fundamentals/us-market-progress.json"
    sec_retry_manifest_path = None
    if args.sec_retry_manifest:
        sec_retry_manifest_path = args.sec_retry_manifest.resolve() if args.sec_retry_manifest.is_absolute() else (root / args.sec_retry_manifest).resolve()
        if not sec_retry_manifest_path.is_file():
            parser.error(f"SEC retry manifest not found: {sec_retry_manifest_path}")
        try:
            sec_retry_manifest_path.relative_to(root)
        except ValueError:
            parser.error("SEC retry manifest must be inside --root so its input hash is portable")
    for required in (index_path, universe_path, directory_path):
        if not required.is_file():
            parser.error(f"required input not found: {required}")

    index = read_json(index_path)
    universe = read_json(universe_path)
    directory = read_json(directory_path)
    progress = read_json(progress_path) if progress_path.is_file() else {}
    index_items = index.get("items", {})
    dir_symbols: dict[str, tuple[str, str]] = {}
    for item in directory.get("items", []):
        if not isinstance(item, list) or len(item) < 3:
            continue
        symbol, name, exchange = (str(item[0]), str(item[1]), str(item[2]))
        market = "TW" if exchange in MARKET_EXCHANGES["TW"] else "US" if exchange in MARKET_EXCHANGES["US"] else ""
        if market:
            dir_symbols[symbol] = (name, exchange)

    report_rows = {market: universe.get("markets", {}).get(market, {}).get("rows", []) for market in ("US", "TW")}
    row_fields = universe.get("fields", [])
    market_extras: dict[str, dict[str, dict[str, Any]]] = {}
    for market, rows in report_rows.items():
        map_rows: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not row:
                continue
            obj = dict(zip(row_fields, row))
            map_rows[str(obj.get("symbol"))] = obj
        market_extras[market] = map_rows

    current_by_market: dict[str, list[str]] = {"US": [], "TW": []}
    for symbol, (_, exchange) in dir_symbols.items():
        market = "TW" if exchange in MARKET_EXCHANGES["TW"] else "US"
        current_by_market[market].append(symbol)
    index_by_market = {"US": [], "TW": []}
    for symbol, item in index_items.items():
        market = item.get("market") if isinstance(item, dict) else None
        if market in index_by_market:
            index_by_market[market].append(symbol)

    progress_symbols = progress.get("symbols", {})
    progress_issuers = progress.get("issuers", {})
    company_rows: list[dict[str, Any]] = []
    detail_by_symbol: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    for market in ("US", "TW"):
        targets = set(current_by_market[market])
        orphan_candidates = set(index_by_market[market]) - targets
        for symbol in sorted(targets | orphan_candidates):
            current = symbol in targets
            name, exchange = dir_symbols.get(symbol, ("", "unlisted/unknown"))
            extra = market_extras[market].get(symbol, {})
            if not name:
                name = str(extra.get("name") or symbol)
            ci = progress_symbols.get(symbol, {}) if market == "US" else None
            issuer = progress_issuers.get((ci or {}).get("cik"), {}) if market == "US" and isinstance(ci, dict) else None
            data_path = root / "docs/report/financials" / f"{symbol}.json"
            row, detail = analyze_company(symbol, market, name, exchange, current,
                                         str(extra.get("sector") or ""), str(extra.get("industry") or ""),
                                         index_items.get(symbol), data_path, ci, issues, issuer)
            company_rows.append(row)
            detail_by_symbol[symbol] = detail

    # Aggregate company coverage and field-level numeric completeness against both
    # the target window and actually-present rows. Missing values remain missing.
    market_summary: dict[str, dict[str, Any]] = {}
    field_rows: list[dict[str, Any]] = []
    for market in ("US", "TW"):
        rows = [row for row in company_rows if row["market"] == market and row["currentTarget"]]
        count = len(rows)
        target_periods = {"annual": count * 10, "quarterly": count * 40}
        files = sum(row["hasFinancialFile"] for row in rows)
        annual_full = sum(row["annualTenYearComplete"] for row in rows)
        quarter_full = sum(row["quarterFortyComplete"] for row in rows)
        core_annual = sum(row["coreAnnualComplete"] for row in rows)
        core_both = sum(row["coreAnnualComplete"] and row["coreQuarterlyComplete"] for row in rows)
        pit = sum(row["pointInTimeUsable"] for row in rows)
        market_summary[market] = {
            "targetCompanies": count,
            "withDetailedFinancialFiles": files,
            "withDetailedFinancialFilesRate": files / count if count else None,
            "withTenValidAnnualFiscalPeriods2016_2025": annual_full,
            "tenAnnualPeriodsRate": annual_full / count if count else None,
            "withFortyQuarterlyFiscalPeriods2016_2025": quarter_full,
            "fortyQuarterlyPeriodsRate": quarter_full / count if count else None,
            "coreAnnualValuesComplete": core_annual,
            "coreAnnualValuesCompleteRate": core_annual / count if count else None,
            "coreAnnualAndQuarterValuesComplete": core_both,
            "coreAnnualAndQuarterValuesCompleteRate": core_both / count if count else None,
            "qualityAndPointInTimePassed": pit,
            "qualityAndPointInTimePassedRate": pit / count if count else None,
            "quarterlyForeignReporterReview": sum(row["foreignReporter"] is True for row in rows),
            "withAnnualFY2026": sum(row["annualFY2026Present"] for row in rows),
            "withQuarterlyDataFY2026": sum(row["quarterPeriodsFY2026Present"] > 0 for row in rows),
            "latestPeriodEnd": max((row["latestPeriodEnd"] for row in rows if row["latestPeriodEnd"]), default=None),
            "tenureAdjustedCompleteness": None,
            "tenureAdjustedCompletenessReason": "listing/incorporation dates are absent from the checked-in financial schema and current ticker directory",
            "annualWindowPeriodsExpected": target_periods["annual"],
            "quarterWindowPeriodsExpected": target_periods["quarterly"],
        }
        annual_rows_by_symbol = {row["symbol"]: detail_by_symbol[row["symbol"]]["annual"] for row in rows}
        quarterly_rows_by_symbol = {row["symbol"]: detail_by_symbol[row["symbol"]]["quarterly"] for row in rows}
        annual_period_counts: Counter[int] = Counter()
        quarterly_period_counts: Counter[tuple[int, int]] = Counter()
        for records in annual_rows_by_symbol.values():
            by_fy: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for record in records:
                if record.get("fy") in ANNUAL_YEARS:
                    by_fy[record["fy"]].append(record)
            for fy, periods in by_fy.items():
                if len(periods) == 1 and any(is_number(v) for v in periods[0]["values"].values()):
                    annual_period_counts[fy] += 1
        for records in quarterly_rows_by_symbol.values():
            by_fiscal_quarter: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
            for record in records:
                period = (record.get("fy"), record.get("fq"))
                if period in QUARTERS:
                    by_fiscal_quarter[period].append(record)
            for period, periods in by_fiscal_quarter.items():
                if len(periods) == 1 and any(is_number(v) for v in periods[0]["values"].values()):
                    quarterly_period_counts[period] += 1
        market_summary[market]["annualCoverageByFY"] = [
            {"fiscalYear": year, "companiesWithValidPeriod": annual_period_counts[year],
             "targetCompanies": count, "coverageRate": annual_period_counts[year] / count if count else None}
            for year in ANNUAL_YEARS
        ]
        market_summary[market]["quarterlyCoverageByFYFQ"] = [
            {"fiscalYear": year, "fiscalQuarter": quarter, "companiesWithValidPeriod": quarterly_period_counts[(year, quarter)],
             "targetCompanies": count, "coverageRate": quarterly_period_counts[(year, quarter)] / count if count else None}
            for year, quarter in QUARTERS
        ]
        for period_type in ("annual", "quarterly"):
            denominator = target_periods[period_type]
            records_by_company = [detail_by_symbol[row["symbol"]][period_type] for row in rows]
            present_periods, nonnull_by_field = period_field_counts(records_by_company, period_type)
            for field in FIELDS:
                nonnull = nonnull_by_field[field]
                field_rows.append({
                    "market": market, "periodType": period_type, "field": field,
                    "targetWindowPeriods": denominator, "presentPeriods": present_periods,
                    "nonNullValues": nonnull,
                    "completenessAgainstAllTargetPeriods": nonnull / denominator if denominator else None,
                    "completenessWithinPresentPeriods": nonnull / present_periods if present_periods else None,
                    "note": "null can mean unavailable or not applicable; schema does not distinguish them",
                })

    missing_rows = []
    for row in company_rows:
        if row["currentTarget"] and (row["missingFY2016_2025"] or row["missingQuartersFY2016_2025"] or not row["coreAnnualComplete"]):
            missing_rows.append({
                "market": row["market"], "symbol": row["symbol"], "name": row["name"],
                "hasFinancialFile": row["hasFinancialFile"],
                "missingAnnualFY": row["missingFY2016_2025"],
                "missingFiscalQuarters": row["missingQuartersFY2016_2025"],
                "annualPeriodsPresent": row["annualFY2016_2025Present"],
                "quarterPeriodsPresent": row["quarterFY2016_2025Present"],
                "coreAnnualComplete": row["coreAnnualComplete"],
                "missingCoreAnnualFields": row["missingCoreAnnualFields"],
                "missingCoreQuarterFields": row["missingCoreQuarterFields"],
                "firstObservedFY": row["firstObservedFY"],
                "listingDateKnown": False,
                "reason": "missing history or core values; tenure cannot be adjusted without listing/inception date evidence",
            })

    # Report discovered legacy fields too, while preserving the known compatibility fields.
    count_current = {market: len(current_by_market[market]) for market in ("US", "TW")}
    count_scanner = {market: universe.get("markets", {}).get(market, {}).get("counts", {}).get("listed") for market in ("US", "TW")}
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    input_hashes = {str(path.relative_to(root)): sha256(path) for path in (index_path, universe_path, directory_path) if path.is_file()}
    if progress_path.is_file():
        input_hashes[str(progress_path.relative_to(root))] = sha256(progress_path)
    if sec_retry_manifest_path:
        input_hashes[str(sec_retry_manifest_path.relative_to(root))] = sha256(sec_retry_manifest_path)
    annual_missing_counts = {market: sum(not row["annualTenYearComplete"] for row in company_rows if row["market"] == market and row["currentTarget"]) for market in ("US", "TW")}
    quarter_missing_counts = {market: sum(not row["quarterFortyComplete"] for row in company_rows if row["market"] == market and row["currentTarget"]) for market in ("US", "TW")}

    data_delta: dict[str, Any] | None = None
    if args.compare_revision:
        try:
            data_delta = calculate_data_delta(root, args.compare_revision)
        except ValueError as error:
            parser.error(str(error))

    source_crosscheck_rows: list[dict[str, Any]] = []
    source_crosscheck_path = out / "source-crosschecks.csv"
    if source_crosscheck_path.is_file():
        with source_crosscheck_path.open(encoding="utf-8", newline="") as handle:
            source_crosscheck_rows = list(csv.DictReader(handle))
    current_file_hashes = {row["symbol"]: row["financialFileSha256"] for row in company_rows}
    stale_crosscheck_rows = stale_source_crosscheck_rows(source_crosscheck_rows, current_file_hashes, progress_symbols)
    for stale_row in stale_crosscheck_rows:
        issues.append(issue(
            "P1", stale_row.get("symbol", ""), "stale-source-crosscheck",
            "source check file hash or current CIK mapping no longer matches; rerun the SEC Company Facts verification before relying on this result",
        ))
    usable_crosscheck_rows = [item for item in source_crosscheck_rows if item not in stale_crosscheck_rows]
    current_cik_crosschecks = [
        item for item in usable_crosscheck_rows
        if not str(item.get("sampleGroup", "")).endswith(":cik-override")
        and item.get("cik") == str((progress_symbols.get(item.get("symbol"), {}) or {}).get("cik", ""))
    ]
    current_crosscheck_counts = Counter(item.get("status", "unknown") for item in current_cik_crosschecks)
    verified_current_symbols = sorted({item["symbol"] for item in current_cik_crosschecks
                                       if item.get("status") == "passed" and item.get("valuesCompared", "0") != "0"})
    override_rows = [item for item in usable_crosscheck_rows if str(item.get("sampleGroup", "")).endswith(":cik-override")]
    current_row_by_symbol = {item["symbol"]: item for item in current_cik_crosschecks}
    identity_conflicts: list[dict[str, str]] = []
    for historical in override_rows:
        current = current_row_by_symbol.get(historical.get("symbol"))
        if (current and historical.get("cik") != current.get("cik") and historical.get("status") == "passed"
                and current.get("status") != "passed"):
            old_name = historical.get("rawEntityName") or "unknown prior SEC entity"
            new_name = current.get("rawEntityName") or "unknown current SEC entity"
            conflict = {
                "symbol": historical["symbol"], "historicalCik": historical["cik"],
                "historicalEntity": old_name, "currentCik": current["cik"], "currentEntity": new_name,
            }
            identity_conflicts.append(conflict)
            issues.append(issue(
                "P0", historical["symbol"], "company-identity",
                f"stored SEC history matches CIK {conflict['historicalCik']} ({old_name}); current ticker mapping is CIK {conflict['currentCik']} ({new_name}) with no overlapping Company Facts values; verify predecessor/successor lineage before future merges",
            ))
    for company in company_rows:
        match = next((item for item in identity_conflicts if item["symbol"] == company["symbol"]), None)
        if match:
            company["backfillAssessment"] = "entity-lineage-review"
            company["backfillReason"] = (
                f"stored history matches CIK {match['historicalCik']} ({match['historicalEntity']}); current mapping is "
                f"CIK {match['currentCik']} ({match['currentEntity']}); relationship requires filing evidence"
            )
    source_crosschecks = {
        "file": "source-crosschecks.csv" if source_crosscheck_rows else None,
        "fileSha256": sha256(source_crosscheck_path) if source_crosscheck_rows and source_crosscheck_path.is_file() else None,
        "source": "SEC Company Facts current CIK response, compared using the existing SEC extractor against stored SEC-labelled values",
        "staleRows": len(stale_crosscheck_rows),
        "staleSymbols": sorted({item.get("symbol", "") for item in stale_crosscheck_rows}),
        "attemptedCurrentCikCompanies": len(current_cik_crosschecks),
        "verifiedCurrentCikCompanies": len(verified_current_symbols),
        "verifiedSymbols": verified_current_symbols,
        "currentCikStatuses": dict(current_crosscheck_counts),
        "comparedNumericValues": sum(int(item.get("valuesCompared", 0)) for item in current_cik_crosschecks),
        "mismatches": sum(int(item.get("mismatches", 0)) for item in current_cik_crosschecks),
        "secResponseBytes": sum(int(item.get("responseBytes", 0)) for item in source_crosscheck_rows),
        "historicalCikOverrideChecks": len(override_rows),
        "identityLineageP0": identity_conflicts,
        "twRawValueCrosschecks": 0,
        "twCrosscheckBlockedBy": "MOPS historical result URL fetch returned HTTP 503; no original TW values retrieved",
        "limitation": "Company Facts is a current facts aggregate, not an independent filing document or complete point-in-time ledger; a match validates stored values against SEC's current aggregate only.",
    }

    batches = progress.get("batches", []) if isinstance(progress, dict) else []
    latest_batch = batches[-1] if batches and isinstance(batches[-1], dict) else None
    if sec_retry_manifest_path:
        latest_batch = read_json(sec_retry_manifest_path)
    sec_retry_summary = None
    if latest_batch:
        decisions = latest_batch.get("decisions", {})
        sec_retry_summary = {
            "batchId": latest_batch.get("batchId"),
            "startedAt": latest_batch.get("startedAt"),
            "finishedAt": latest_batch.get("finishedAt"),
            "phase": latest_batch.get("phase"),
            "status": latest_batch.get("status"),
            "attemptedIssuers": len(latest_batch.get("attemptedCIKs", [])),
            "decisionCounts": dict(Counter(decisions.values())),
            "secRequests": latest_batch.get("secRequests", max(0, latest_batch.get("secRequestsAfter", 0) - latest_batch.get("secRequestsBefore", 0))),
            "responseBytes": latest_batch.get("responseBytes", max(0, latest_batch.get("secBytesAfter", 0) - latest_batch.get("secBytesBefore", 0))),
        }
        if latest_batch.get("followUpResumeProbe"):
            sec_retry_summary["followUpResumeProbe"] = latest_batch["followUpResumeProbe"]

    capacity = {}
    for market in ("US", "TW"):
        market_companies = [row for row in company_rows if row["market"] == market]
        data_bytes = sum(row["financialFileBytes"] for row in market_companies)
        stored_period_rows = sum(row["annualPeriodRows"] + row["quarterPeriodRows"] for row in market_companies)
        missing_window_periods = sum(
            max(0, 10 - row["annualFY2016_2025Present"]) + max(0, 40 - row["quarterFY2016_2025Present"])
            for row in market_companies if row["currentTarget"]
        )
        avg_bytes_per_period = data_bytes / stored_period_rows if stored_period_rows else None
        estimate = int(missing_window_periods * avg_bytes_per_period) if avg_bytes_per_period is not None else None
        capacity[market] = {
            "currentFinancialJsonBytes": data_bytes,
            "storedAnnualAndQuarterPeriodRows": stored_period_rows,
            "missingTargetFiscalPeriods": missing_window_periods,
            "averageCurrentJsonBytesPerStoredPeriod": avg_bytes_per_period,
            "roughAdditionalJsonBytesToFillEveryTargetWindow": estimate,
            "roughEstimateLowBytes": int(estimate * 0.7) if estimate is not None else None,
            "roughEstimateHighBytes": int(estimate * 1.3) if estimate is not None else None,
        }
    git_objects = git_value(root, "count-objects", "-v") or ""
    git_object_stats = {}
    for line in git_objects.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            git_object_stats[key] = value
    git_dir_output = git_value(root, "rev-parse", "--git-dir")
    git_dir = (root / git_dir_output).resolve() if git_dir_output and not Path(git_dir_output).is_absolute() else Path(git_dir_output) if git_dir_output else None
    git_history_bytes = sum(path.stat().st_size for path in git_dir.rglob("*") if path.is_file()) if git_dir and git_dir.exists() else None
    capacity_report = {
        "githubApiRepoSizeKB": args.github_repo_size_kb,
        "localGitDirectoryBytes": git_history_bytes,
        "localGitIsShallow": git_value(root, "rev-parse", "--is-shallow-repository") == "true",
        "gitCountObjects": git_object_stats,
        "markets": capacity,
        "estimateNote": "Rough JSON-only projection: current market file bytes divided by stored annual+quarter rows, multiplied by missing FY2016–FY2025/fiscal-quarter rows. ±30% envelope; does not imply all missing periods are legally obtainable, does not include sidecars, reports, Git compression, or PIT metadata.",
    }

    summary = {
        "schemaVersion": 1, "generatedAt": generated,
        "snapshotType": "baseline" if out.name == "baseline" else "current",
        "gitCommit": git_value(root, "rev-parse", "HEAD"),
        "gitBranch": git_value(root, "branch", "--show-current"),
        "window": {"fiscalYears": list(ANNUAL_YEARS), "fiscalQuarters": 40},
        "marketDirectoryCounts": count_current,
        "scannerUniverseListedCounts": count_scanner,
        "markets": market_summary,
        "unlistedFinancialIndexCandidates": {
            market: sum(row["market"] == market and not row["currentTarget"] for row in company_rows)
            for market in ("US", "TW")
        },
        "companiesMissingAnyWindowPeriod": {
            market: {"annual": annual_missing_counts[market], "quarterly": quarter_missing_counts[market]}
            for market in ("US", "TW")
        },
        "qualityIssueCounts": dict(Counter(item["severity"] for item in issues)),
        "financialIndexGeneratedAt": index.get("generatedAt"),
        "universeGeneratedAt": universe.get("generatedAt"),
        "directoryGeneratedAt": directory.get("generatedAt"),
        "inputSha256": input_hashes,
        "financialDataDelta": data_delta,
        "latestSecRetryBatch": sec_retry_summary,
        "capacity": capacity_report,
        "sourceCrosschecks": source_crosschecks,
        "githubRepoSizeKB": args.github_repo_size_kb,
        "financialJsonFiles": len(list((root / "docs/report/financials").glob("*.json"))) - 1,
        "financialJsonBytes": sum(p.stat().st_size for p in (root / "docs/report/financials").glob("*.json") if p.is_file()),
        "githubRepoSizeNote": "Use GitHub repository API size for full repository size; local checkout may be sparse/shallow.",
    }
    coverage = {
        "schemaVersion": 1,
        "generatedAt": generated,
        "baselineCommit": summary["gitCommit"],
        "windowFiscalYears": list(ANNUAL_YEARS),
        "quarterWindowDefinition": "fiscalQuarter (fy, fq), with no calendar-date remapping",
        "coreFieldsDefinition": list(CORE_FIELDS),
        "markets": market_summary,
        "limitations": [
            "Company listing/incorporation dates are not present, so eligibility-adjusted completeness is unknown.",
            "Legacy financial JSON has period end and fiscal labels, but generally no filingDate, acceptedAt, accession number, or restatement vintage; no legacy period is certified point-in-time usable.",
            "Null values are not tagged as not-applicable versus missing; field completeness is descriptive, not an error rate.",
            "A financial JSON file or legacy quality.good label does not certify ten-year coverage.",
            "Current ticker directories do not form a complete historical delisted-company directory; index-only symbols are candidates only.",
            "Foreign reporter status alone does not establish whether quarterly filings are required in each jurisdiction.",
        ],
        "inputs": input_hashes,
        "financialDataDelta": data_delta,
        "latestSecRetryBatch": sec_retry_summary,
        "capacity": capacity_report,
        "sourceCrosschecks": source_crosschecks,
    }

    out.mkdir(parents=True, exist_ok=True)
    json_write(out / "coverage.json", coverage)
    json_write(out / "run-metadata.json", summary)
    json_write(out / "capacity.json", capacity_report)
    write_csv(out / "coverage.csv", list(company_rows[0].keys()) if company_rows else [], company_rows)
    write_csv(out / "missing-periods.csv", ["market", "symbol", "name", "hasFinancialFile", "missingAnnualFY", "missingFiscalQuarters", "annualPeriodsPresent", "quarterPeriodsPresent", "coreAnnualComplete", "missingCoreAnnualFields", "missingCoreQuarterFields", "firstObservedFY", "listingDateKnown", "reason"], missing_rows)
    write_csv(out / "field-completeness.csv", ["market", "periodType", "field", "targetWindowPeriods", "presentPeriods", "nonNullValues", "completenessAgainstAllTargetPeriods", "completenessWithinPresentPeriods", "note"], field_rows)
    write_csv(out / "quality-issues.csv", ["severity", "symbol", "category", "detail"], issues)

    source_summary = {
        "schemaVersion": 1,
        "generatedAt": generated,
        "legacyFinancialSchema": {
            "version": 1, "perCompanyFilePattern": "docs/report/financials/{symbol}.json",
            "periodKeys": ["annual.fy", "quarterly.fy", "quarterly.fq", "end"],
            "currencyRecorded": True, "unitRecorded": False,
            "perPeriodSourceLabels": True,
            "filingDate": False, "acceptedAt": False, "accessionNumber": False,
            "conceptPerValue": False, "restatementStatus": False, "derivedStatus": "only sparse top-level quarterly derived flags in some files",
            "qualityFlags": "file-level legacy quality object; not a ten-year completeness grade",
        },
        "byMarketSourceCounts": {market: {
            "periodLevel": dict(sum((Counter(row["periodSources"]) for row in company_rows
                                      if row["market"] == market and row["currentTarget"]), Counter())),
            "fileLevelFallback": dict(sum((Counter(row["fileLevelSourceFallbacks"]) for row in company_rows
                                             if row["market"] == market and row["currentTarget"]), Counter())),
        } for market in ("US", "TW")},
        "officialAvailabilityAssessment": {
            "US": "Existing SEC seeder and checkpointed current-market backfill are in repo. SEC companyfacts/submissions bulk ZIPs total about 2.98 GB; existing per-CIK workflow avoids repeatedly downloading full archives. SEC companyfacts are current facts, not complete filing-vintage data for point-in-time backtests.",
            "TW": "Existing OpenAPI fundamentals do not provide a ten-year three-statement history. MOPS redirectToOld returned HTTP 200/result URLs for 2016Q1 income, balance-sheet, and cash-flow queries, but each historical result fetch returned upstream HTTP 503; no historical values were downloaded. TWSE OpenAPI is licensed under the government open-data license but only serves current seasons. TPEx reuse/republication needs dataset-specific authorization; MOPS historical redistribution terms were not verified.",
        },
        "limitations": coverage["limitations"],
        "inputSha256": input_hashes,
        "financialDataDelta": data_delta,
        "latestSecRetryBatch": sec_retry_summary,
        "secRetryManifest": ({
            "file": str(sec_retry_manifest_path.relative_to(root)),
            "sha256": sha256(sec_retry_manifest_path),
        } if sec_retry_manifest_path else None),
        "sourceCrosschecks": source_crosschecks,
    }
    json_write(out / "source-provenance.json", source_summary)
    if data_delta is not None:
        json_write(out / "data-delta.json", data_delta)

    # Compact human summary, with the strict metrics kept separate.
    lines = ["# Financial history coverage audit", "",
             f"Generated: {generated}", f"Git commit: `{summary['gitCommit']}`", f"Fiscal window: FY2016–FY2025 (40 fiscal quarters)", "",
             "This report measures periods using stored fiscal labels. File count, year coverage, quarterly coverage, field completeness, and point-in-time eligibility are separate measures.", "",
             "## Market coverage", "",
             "| Market | Directory companies | Financial files | 10 FY periods | 40 fiscal quarters | Core annual complete | Core annual + quarter complete | Quality + PIT passed |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for market in ("US", "TW"):
        m = market_summary[market]
        lines.append(f"| {market} | {m['targetCompanies']} | {m['withDetailedFinancialFiles']} ({m['withDetailedFinancialFilesRate']:.1%}) | {m['withTenValidAnnualFiscalPeriods2016_2025']} ({m['tenAnnualPeriodsRate']:.1%}) | {m['withFortyQuarterlyFiscalPeriods2016_2025']} ({m['fortyQuarterlyPeriodsRate']:.1%}) | {m['coreAnnualValuesComplete']} ({m['coreAnnualValuesCompleteRate']:.1%}) | {m['coreAnnualAndQuarterValuesComplete']} ({m['coreAnnualAndQuarterValuesCompleteRate']:.1%}) | {m['qualityAndPointInTimePassed']} ({m['qualityAndPointInTimePassedRate']:.1%}) |")
    lines += ["", "## Scope and limitations", "",
              f"Current directory counts: US {count_current['US']}; TW {count_current['TW']}. The scanner universe reports US {count_scanner['US']} and TW {count_scanner['TW']}; directory and scanner scopes differ.",
              "Tenure-adjusted completeness is not calculated because the checked-in sources do not supply reliable listing/incorporation dates. Index-only symbols are listed as unlisted candidates; they are not asserted to be a complete delisted-company census.",
              "Legacy files do not carry filing dates, accepted timestamps, accession numbers, or restatement vintages. Therefore they are not certified for point-in-time historical backtests.",
              "For field-level figures, see `field-completeness.csv`; null may indicate either an unavailable or inapplicable value.", "",
              f"FY2026 data present: US annual {market_summary['US']['withAnnualFY2026']} companies, quarterly {market_summary['US']['withQuarterlyDataFY2026']}; TW annual {market_summary['TW']['withAnnualFY2026']}, quarterly {market_summary['TW']['withQuarterlyDataFY2026']}. Latest stored period ends: US {market_summary['US']['latestPeriodEnd']}, TW {market_summary['TW']['latestPeriodEnd']}.",
              f"Latest SEC retry batch: {sec_retry_summary['attemptedIssuers']} issuers, {sec_retry_summary['secRequests']} SEC requests, {sec_retry_summary['responseBytes']:,} response bytes; decisions {sec_retry_summary['decisionCounts']}." if sec_retry_summary else "No SEC retry batch is recorded.",
              f"Follow-up SEC resume probe: {sec_retry_summary['followUpResumeProbe']['result']} after validating {sec_retry_summary['followUpResumeProbe']['currentDirectoryCandidateCount']} NasdaqTrader symbols; no financial company files changed." if sec_retry_summary and sec_retry_summary.get("followUpResumeProbe") else "",
              f"SEC Company Facts source cross-checks: {source_crosschecks['verifiedCurrentCikCompanies']} current mapped companies verified across {source_crosschecks['comparedNumericValues']:,} values; {source_crosschecks['mismatches']} mismatches. Historical-CIK identity P0s: {len(identity_conflicts)}. Taiwan raw-value cross-checks: {source_crosschecks['twRawValueCrosschecks']} (MOPS 503)." if source_crosscheck_rows else "No raw-source cross-check output is recorded.",
              f"Technical audit issues: P0 {summary['qualityIssueCounts'].get('P0', 0)}, P1 {summary['qualityIssueCounts'].get('P1', 0)}, P2 {summary['qualityIssueCounts'].get('P2', 0)}.",
              f"Input hashes and data-source limits are recorded in `source-provenance.json`. Financial JSON bytes: {summary['financialJsonBytes']:,}; GitHub API repository size at baseline: {args.github_repo_size_kb or 'not supplied'} KB; local Git directory: {git_history_bytes if git_history_bytes is not None else 'unknown'} bytes (shallow={capacity_report['localGitIsShallow']}).",
              "Rough incremental JSON storage projection to fill every target window: " + "; ".join(
                  f"{market} {capacity_report['markets'][market]['roughEstimateLowBytes']:,}–{capacity_report['markets'][market]['roughEstimateHighBytes']:,} bytes"
                  if capacity_report['markets'][market]['roughEstimateLowBytes'] is not None else f"{market} unavailable"
                  for market in ("US", "TW")) + ". See `capacity.json` for assumptions.", ""]
    if data_delta is not None:
        lines += ["## Data changes since baseline revision", "",
                  f"Compared with `{args.compare_revision}` across {data_delta['changedFinancialFiles']} changed financial company files.", "",
                  "| Market | Annual periods added | Quarterly periods added | New non-null value cells | Changed existing values | Removed values | Net bytes |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for market in ("US", "TW"):
            d = data_delta["markets"][market]
            lines.append(f"| {market} | {d['annualPeriodsAdded']} | {d['quarterPeriodsAdded']} | {d['newNonNullValueCells']} | {d['changedNonNullValues']} | {d['removedNonNullValueCells']} | {d['netBytesAdded']} |")
        lines.append("")
    (out / "backfill-summary.md").write_text("\n".join(lines), encoding="utf-8")

    table_rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in (
            market, market_summary[market]["targetCompanies"], market_summary[market]["withDetailedFinancialFiles"],
            f"{market_summary[market]['withDetailedFinancialFilesRate']:.1%}",
            f"{market_summary[market]['tenAnnualPeriodsRate']:.1%}",
            f"{market_summary[market]['fortyQuarterlyPeriodsRate']:.1%}",
            f"{market_summary[market]['coreAnnualValuesCompleteRate']:.1%}",
            f"{market_summary[market]['qualityAndPointInTimePassedRate']:.1%}")) + "</tr>"
        for market in ("US", "TW"))
    html_doc = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Financial history coverage audit</title>
<style>body{{font:16px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#17202a}}table{{border-collapse:collapse;width:100%}}th,td{{padding:.6rem;border:1px solid #ccd1d1;text-align:left}}th{{background:#edf2f4}}.note{{max-width:80ch;line-height:1.5}}</style>
<h1>Financial history coverage audit</h1><p>Generated {html.escape(generated)} · fiscal-year window 2016–2025 · source commit {html.escape(str(summary['gitCommit']))}</p>
<p class="note">Fiscal labels (fy/fq) determine periods. Legacy quality labels are not completeness grades. No legacy file is certified point-in-time usable because filing-date, acceptance-time and filing-vintage evidence is not present.</p>
<table><thead><tr><th>Market</th><th>Directory companies</th><th>Files</th><th>File rate</th><th>10 FY</th><th>40 quarters</th><th>Core annual</th><th>Quality + PIT</th></tr></thead><tbody>{table_rows}</tbody></table>
<p>Downloadable data: <a href="coverage.csv">coverage.csv</a>, <a href="missing-periods.csv">missing-periods.csv</a>, <a href="field-completeness.csv">field-completeness.csv</a>, <a href="quality-issues.csv">quality-issues.csv</a>, <a href="coverage.json">coverage.json</a>, <a href="source-provenance.json">source-provenance.json</a>, <a href="capacity.json">capacity.json</a>{', <a href="source-crosschecks.csv">source-crosschecks.csv</a>' if source_crosscheck_rows else ''}{', <a href="data-delta.json">data-delta.json</a>' if data_delta is not None else ''}.</p>
<p>Listing dates are unavailable, so tenure-adjusted completeness is unknown. Index-only symbols are delisting candidates, not a complete delisted universe.</p></html>"""
    (out / "coverage-report.html").write_text(html_doc, encoding="utf-8")
    print(json.dumps({"out": str(out), "markets": {
        market: {key: value for key, value in market_summary[market].items()
                 if key in ("targetCompanies", "withDetailedFinancialFiles", "withTenValidAnnualFiscalPeriods2016_2025",
                            "withFortyQuarterlyFiscalPeriods2016_2025", "coreAnnualValuesComplete",
                            "coreAnnualAndQuarterValuesComplete", "qualityAndPointInTimePassed")}
        for market in ("US", "TW")}, "issues": dict(Counter(i["severity"] for i in issues)),
        "gitCommit": summary["gitCommit"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
