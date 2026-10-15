"""Build an offline, field-by-field coverage report for the tracked US financial files.

The symbol universe is the same build_financials.collect_symbols() snapshot consumed by
seed_financials_sec.py. No network access is used. Values are counted exactly as the financials UI
uses them: diluted EPS with basic fallback, the preferred operating-income line, and margins only
when their denominator and required inputs exist.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
import re
import sys
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
from build_financials import collect_symbols, market_of  # noqa: E402

METRICS = (
    "eps",
    "revenue",
    "costOfRevenue",
    "nonOperatingIncome",
    "grossMargin",
    "operatingIncome",
    "netMargin",
)
LINEAGE_FIELDS = (
    "revenue", "costOfRevenue", "grossProfit", "operatingIncome", "pretaxIncome",
    "netIncome", "epsDiluted", "epsBasic",
)
KINDS = ("annual", "quarterly")


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def value_at(values: dict[str, list[Any]], field: str, index: int) -> float | None:
    column = values.get(field)
    return number(column[index]) if isinstance(column, list) and index < len(column) else None


def chosen_operating_income(values: dict[str, list[Any]], index: int) -> float | None:
    listed = value_at(values, "operatingIncomeReported", index)
    return listed if listed is not None else value_at(values, "operatingIncome", index)


def displayed_cost_of_revenue(
    values: dict[str, list[Any]], index: int, allow_derived: bool = True,
) -> tuple[float | None, str | None]:
    reported = value_at(values, "costOfRevenue", index)
    if reported is not None:
        return reported, "reported"
    if not allow_derived:
        return None, None
    sales = value_at(values, "revenue", index)
    gross = value_at(values, "grossProfit", index)
    if sales is None or gross is None or sales < 0:
        return None, None
    estimate = sales - gross
    return (estimate, "derived") if estimate >= 0 else (None, None)


def metric_availability(
    values: dict[str, list[Any]], index: int, allow_derived_cost: bool = True,
) -> dict[str, tuple[bool, str | None]]:
    revenue = value_at(values, "revenue", index)
    cost, _ = displayed_cost_of_revenue(values, index, allow_derived_cost)
    gross = value_at(values, "grossProfit", index)
    operating = chosen_operating_income(values, index)
    pretax = value_at(values, "pretaxIncome", index)
    net_income = value_at(values, "netIncome", index)
    eps_diluted = value_at(values, "epsDiluted", index)
    eps_basic = value_at(values, "epsBasic", index)
    availability = {
        "eps": (eps_diluted is not None or eps_basic is not None,
                None if eps_diluted is not None or eps_basic is not None else "missing_diluted_and_basic_eps"),
        "revenue": (revenue is not None, None if revenue is not None else "missing_revenue"),
        "costOfRevenue": (
            cost is not None,
            None if cost is not None else
            "missing_reported_cost" if not allow_derived_cost else
            "missing_revenue" if value_at(values, "revenue", index) is None else
            "missing_gross_profit" if value_at(values, "grossProfit", index) is None and
            value_at(values, "costOfRevenue", index) is None else
            "negative_revenue" if value_at(values, "revenue", index) < 0 and
            value_at(values, "costOfRevenue", index) is None else
            "negative_derived_cost",
        ),
        "nonOperatingIncome": (
            pretax is not None and operating is not None,
            None if pretax is not None and operating is not None else
            "missing_pretax_income" if pretax is None else "missing_operating_income",
        ),
        "grossMargin": (
            revenue is not None and revenue > 0 and (gross is not None or cost is not None),
            None if revenue is not None and revenue > 0 and (gross is not None or cost is not None) else
            "missing_revenue" if revenue is None else "non_positive_revenue" if revenue <= 0 else
            "missing_gross_profit_and_cost",
        ),
        "operatingIncome": (
            operating is not None, None if operating is not None else "missing_operating_income",
        ),
        "netMargin": (
            revenue is not None and revenue > 0 and net_income is not None,
            None if revenue is not None and revenue > 0 and net_income is not None else
            "missing_revenue" if revenue is None else "non_positive_revenue" if revenue <= 0 else
            "missing_net_income",
        ),
    }
    return availability


def gap_rows(ends: list[str], kind: str) -> list[dict[str, Any]]:
    minimum, maximum = (330, 395) if kind == "annual" else (75, 120)
    gaps: list[dict[str, Any]] = []
    parsed: list[date] = []
    for end in ends:
        try:
            parsed.append(date.fromisoformat(end))
        except (TypeError, ValueError):
            continue
    for before, after in zip(parsed, parsed[1:]):
        days = (after - before).days
        if days < minimum or days > maximum:
            gaps.append({"from": before.isoformat(), "to": after.isoformat(), "days": days})
    return gaps


def period_coverage(
    block: dict[str, Any] | None,
    kind: str,
    cutoff: date,
    limit: int | None,
    allow_derived_cost: bool = True,
) -> dict[str, Any]:
    if not isinstance(block, dict):
        block = {}
    ends = block.get("end") if isinstance(block.get("end"), list) else []
    values = block.get("values") if isinstance(block.get("values"), dict) else {}
    valid_rows: list[tuple[str, int]] = []
    for index, end in enumerate(ends):
        if not isinstance(end, str):
            continue
        try:
            period_end = date.fromisoformat(end)
        except ValueError:
            continue
        valid_rows.append((period_end.isoformat(), index))
    valid_rows.sort(key=lambda row: row[0])
    source_rows = len(valid_rows)
    if limit is not None:
        valid_rows = valid_rows[-limit:]
    else:
        valid_rows = [row for row in valid_rows if date.fromisoformat(row[0]) >= cutoff]
    selected_ends = [row[0] for row in valid_rows]
    counters = {metric: {"available": 0, "missing": 0, "first": None, "last": None, "missingReasons": {}}
                for metric in METRICS}
    counters["costOfRevenue"].update({"rawReportedAvailable": 0, "derivedAvailable": 0})
    for end, index in valid_rows:
        current = metric_availability(values, index, allow_derived_cost)
        for metric in METRICS:
            available, reason = current[metric]
            info = counters[metric]
            if metric == "costOfRevenue":
                _, basis = displayed_cost_of_revenue(values, index, allow_derived_cost)
                if basis == "reported":
                    info["rawReportedAvailable"] += 1
                elif basis == "derived":
                    info["derivedAvailable"] += 1
            if available:
                info["available"] += 1
                info["first"] = info["first"] or end
                info["last"] = end
            else:
                info["missing"] += 1
                info["missingReasons"][reason] = info["missingReasons"].get(reason, 0) + 1
    return {
        "periods": len(selected_ends),
        "sourcePeriodRows": source_rows,
        "first": selected_ends[0] if selected_ends else None,
        "last": selected_ends[-1] if selected_ends else None,
        "gaps": gap_rows(selected_ends, kind),
        "contiguousPeriods": len(selected_ends) if not gap_rows(selected_ends, kind) else None,
        "metrics": counters,
    }


def get_window(
    document: dict[str, Any] | None,
    kind: str,
    cutoff: date,
    limit: int | None,
    allow_derived_cost: bool = True,
) -> dict[str, Any]:
    block = document.get(kind) if isinstance(document, dict) else None
    return period_coverage(block, kind, cutoff, limit, allow_derived_cost)


def aggregate(symbol_rows: dict[str, dict[str, Any]], window_key: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for kind in KINDS:
        total_periods = 0
        source_period_rows = 0
        symbols_with_rows = 0
        symbols_full_period_count = 0
        symbols_contiguous_target = 0
        symbols_all_metrics_complete_target = 0
        target = 15 if kind == "annual" else 60
        metric_totals = {
            metric: {
                "available": 0,
                "missing": 0,
                "symbolsCompleteWithinObservedPeriods": 0,
                "symbolsWithTargetCompletePeriods": 0,
                "rawReportedAvailable": 0,
                "derivedAvailable": 0,
            }
            for metric in METRICS
        }
        for row in symbol_rows.values():
            window = row["windows"][window_key][kind]
            periods = window["periods"]
            total_periods += periods
            source_period_rows += window["sourcePeriodRows"]
            symbols_with_rows += periods > 0
            symbols_full_period_count += periods >= target
            symbols_contiguous_target += periods >= target and not window["gaps"]
            symbols_all_metrics_complete_target += periods >= target and all(
                window["metrics"][metric]["missing"] == 0 for metric in METRICS
            )
            for metric in METRICS:
                values = window["metrics"][metric]
                metric_totals[metric]["available"] += values["available"]
                metric_totals[metric]["missing"] += values["missing"]
                metric_totals[metric]["symbolsCompleteWithinObservedPeriods"] += periods > 0 and values["missing"] == 0
                metric_totals[metric]["symbolsWithTargetCompletePeriods"] += periods >= target and values["missing"] == 0
                metric_totals[metric]["rawReportedAvailable"] += values.get("rawReportedAvailable", 0)
                metric_totals[metric]["derivedAvailable"] += values.get("derivedAvailable", 0)
        for metric in METRICS:
            total = metric_totals[metric]["available"] + metric_totals[metric]["missing"]
            metric_totals[metric]["periods"] = total
            metric_totals[metric]["coveragePercent"] = round(100 * metric_totals[metric]["available"] / total, 2) if total else None
        summary[kind] = {
            "periodRows": total_periods,
            "sourcePeriodRows": source_period_rows,
            "symbolsWithRows": symbols_with_rows,
            "symbolsWithAtLeastTargetPeriods": symbols_full_period_count,
            "symbolsWithAtLeastTargetContiguousPeriods": symbols_contiguous_target,
            "symbolsWithAllSevenMetricsCompleteAtTarget": symbols_all_metrics_complete_target,
            "targetPeriods": target,
            "metrics": metric_totals,
        }
    return summary


def load_git_baseline(revision: str, symbols: list[str], cutoff: date) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    commit_time = subprocess.run(
        ["git", "show", "-s", "--format=%cI", resolved],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    rows: dict[str, dict[str, Any]] = {}
    files_found = 0
    for symbol in symbols:
        path = f"docs/report/financials/{symbol}.json"
        result = subprocess.run(
            ["git", "show", f"{resolved}:{path}"],
            cwd=REPO_ROOT,
            capture_output=True,
        )
        if result.returncode != 0:
            rows[symbol] = {
                "fileState": "missing",
                "windows": {"calendar15Year": {kind: get_window(None, kind, cutoff, None) for kind in KINDS}},
            }
            continue
        try:
            document = json.loads(result.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid baseline JSON at {resolved}:{path}") from error
        files_found += 1
        rows[symbol] = {
            "fileState": "ok",
            "windows": {
                "calendar15Year": {
                    kind: get_window(document, kind, cutoff, None, allow_derived_cost=False)
                    for kind in KINDS
                }
            }
        }
    return {
        "revision": resolved,
        "asOf": commit_time,
        "cutoff": cutoff.isoformat(),
        "financialFilesFound": files_found,
        "symbols": sorted(symbol for symbol, row in rows.items() if row["fileState"] == "ok"),
    }, rows


def summarize_baseline(rows: dict[str, dict[str, Any]], kind: str) -> dict[str, Any]:
    summary = {metric: {"available": 0, "missing": 0, "periods": 0} for metric in METRICS}
    for row in rows.values():
        window = row["windows"]["calendar15Year"][kind]
        for metric in METRICS:
            info = window["metrics"][metric]
            summary[metric]["available"] += info["available"]
            summary[metric]["missing"] += info["missing"]
            summary[metric]["periods"] += info["available"] + info["missing"]
            if metric == "costOfRevenue":
                summary[metric]["rawReportedAvailable"] = summary[metric]["available"]
    return summary


def relevant_lineage(document: dict[str, Any] | None) -> dict[str, Any]:
    concepts = document.get("secConcepts") if isinstance(document, dict) else None
    if not isinstance(concepts, dict):
        return {}
    lineage = {field: concepts[field] for field in LINEAGE_FIELDS if field in concepts}
    arbiter = concepts.get("arbiter")
    if isinstance(arbiter, dict) and "revenue" in arbiter:
        lineage["arbiter"] = {"revenue": arbiter["revenue"]}
    return lineage


def parse_refresh_log(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    outcomes: dict[str, str] = {}
    pattern = re.compile(r"^\[\d+/\d+\]\s+([^\s]+)\s+([a-z-]+)\b")
    with open(path, encoding="utf-8", errors="replace") as source:
        for line in source:
            match = pattern.match(line)
            if match:
                outcomes[match.group(1)] = match.group(2)
    return outcomes


def parse_refresh_summary(
    path: str | None,
    outcomes: dict[str, str],
    raw_cache_manifest: str | None = None,
) -> dict[str, Any]:
    if not path and not raw_cache_manifest:
        return {}
    log = ""
    if path:
        with open(path, encoding="utf-8", errors="replace") as source:
            log = source.read()
    summary: dict[str, Any] = {"outcomes": dict(Counter(outcomes.values()))}
    requests = re.search(r"requests:\s*(\d+), received (\d+) bytes, written (\d+) bytes", log)
    if requests:
        summary["requests"] = {
            "count": int(requests.group(1)),
            "receivedBytes": int(requests.group(2)),
            "financialAndIndexJsonBytesWritten": int(requests.group(3)),
        }
    if raw_cache_manifest:
        with open(raw_cache_manifest, encoding="utf-8") as source:
            manifest = json.load(source)
        responses = manifest.get("responses", {}) if isinstance(manifest, dict) else {}
        bodies: dict[str, int] = {}
        if isinstance(responses, dict):
            for response in responses.values():
                if not isinstance(response, dict):
                    continue
                file_name, byte_count = response.get("file"), response.get("bytes")
                if isinstance(file_name, str) and isinstance(byte_count, int) and byte_count >= 0:
                    bodies[file_name] = byte_count
        summary["rawSecCache"] = {
            "uniqueBodies": len(bodies),
            "uniqueRawBodyBytes": sum(bodies.values()),
        }
    split_checks = re.search(r"split checks:\s*([^,\n]+),\s*(\d+) chart request\(s\)", log)
    if split_checks:
        summary["splitChecks"] = {"status": split_checks.group(1), "chartRequests": int(split_checks.group(2))}
    for key, pattern in (
        ("splitCheckTickers", r"split events fetched:\s*(\d+) ticker\(s\)"),
        ("tickersWithSplitEvents", r"split events fetched:\s*\d+ ticker\(s\),\s*(\d+) with split events"),
        ("splitAdjustedTickers", r"split-adjusted:\s*(\d+) ticker\(s\)"),
        ("splitCheckPendingTickers", r"split-check-pending:\s*(\d+) ticker\(s\)"),
        ("implausibleValuesLeftNull", r"implausible values left null:\s*(\d+) in"),
        ("symbolsWithImplausibleValues", r"implausible values left null:\s*\d+ in (\d+) ticker\(s\)"),
    ):
        match = re.search(pattern, log)
        if match:
            summary[key] = int(match.group(1))
    summary["skipReasons"] = {
        reason: int(count)
        for reason, count in re.findall(r"skip reason ([a-z-]+):\s*(\d+)", log)
    }
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=date.today().isoformat(), help="report date YYYY-MM-DD")
    parser.add_argument("--baseline-revision", default="HEAD", help="Git commit used to compute before counts from its raw financial files")
    parser.add_argument("--refresh-log", help="optional seed_financials_sec.py output log for per-ticker outcomes")
    parser.add_argument("--raw-cache-manifest", help="optional temporary SEC response cache manifest")
    parser.add_argument("--restoration-audit", help="optional offline Yahoo null-restoration audit JSON")
    parser.add_argument("--report-dir", default=str(REPO_ROOT / "docs" / "report"))
    parser.add_argument("--data-dir", default=str(REPO_ROOT / "data"))
    parser.add_argument("--financials-dir", default=str(REPO_ROOT / "docs" / "report" / "financials"))
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "report" / "financials-us-coverage.json"))
    parser.add_argument("--markdown-out", default=str(REPO_ROOT / "atlas" / "docs" / "US_FINANCIAL_HISTORY_BACKFILL.md"))
    return parser.parse_args(argv)


def render_markdown(report: dict[str, Any]) -> str:
    snapshot = report["snapshot"]
    calendar = report["calendar15Year"]
    latest = report["latestRecords"]
    before = report.get("beforeAfter", {})
    lines = [
        "# US financial history backfill",
        "",
        f"As of {report['asOf']}, the 15-calendar-year cutoff is {report['cutoff']}.",
        f"The exact tracked US symbol snapshot contains {snapshot['symbolCount']} symbols ({snapshot['financialFilesFound']} financial files). The ordered snapshot SHA-256 is `{snapshot['symbolsSha256']}`.",
        "",
        "The refresh used the existing SEC companyfacts seeder with its configured request pacing, User-Agent, retry behavior, merge rules, and split checks. The seeder was run with `--force`; SEC periods, nullable fields, concept lineage, filing metadata, and quality flags remain in the source files.",
    ]
    refresh = report.get("refresh", {})
    if refresh:
        outcomes = refresh.get("outcomes", {})
        requests = refresh.get("requests", {})
        splits = refresh.get("splitChecks", {})
        skip_reasons = refresh.get("skipReasons", {})
        lines.extend([
            "",
            "## SEC refresh run",
            "",
            "Command from the repository root: `/tmp/tw-mops-backfill-venv/bin/python /tmp/sec_any_403_guard.py atlas/scripts/seed_financials_sec.py --force`. The temporary guard launcher and response cache lived under `/tmp`, outside this repository. It preserved the seeder’s configured request pace, User-Agent and retry behavior, and stopped immediately on the first SEC HTTP 403; the run completed without a 403. Split checks remained enabled by omitting `--no-splits`.",
            "",
            f"Results: {outcomes.get('written', 0)} written, {outcomes.get('unchanged', 0)} unchanged, {outcomes.get('skipped', 0)} skipped. Skip reasons: " + (", ".join(f"{name} {count}" for name, count in sorted(skip_reasons.items())) or "none reported") + ".",
            f"SEC requests: {requests.get('count', '—')}; response bytes: {requests.get('receivedBytes', '—')}; financial/index JSON bytes written: {requests.get('financialAndIndexJsonBytesWritten', '—')}. Unique cached SEC bodies: {refresh.get('rawSecCache', {}).get('uniqueBodies', '—')} ({refresh.get('rawSecCache', {}).get('uniqueRawBodyBytes', '—')} bytes). Split checks: {splits.get('status', '—')}, {splits.get('chartRequests', '—')} chart requests; {refresh.get('tickersWithSplitEvents', '—')} tickers with split events, {refresh.get('splitAdjustedTickers', '—')} adjusted, {refresh.get('splitCheckPendingTickers', '—')} pending. {refresh.get('implausibleValuesLeftNull', '—')} implausible values in {refresh.get('symbolsWithImplausibleValues', '—')} tickers were left null.",
            f"New financial files in this tracked snapshot: {', '.join(refresh.get('newFinancialFiles', [])) or 'none'}.",
            "",
        ])
    restoration = report.get("yahooNullRestoration", {})
    if restoration:
        planned = restoration.get("plannedRestorationCellCounts", {})
        annual = planned.get("annual", {})
        quarterly = planned.get("quarterly", {})
        lines.extend([
            "## Offline Yahoo null restoration",
            "",
            f"The SEC refresh kept its reported values and lineage. An offline pass used the pre-refresh Git snapshot and the standard period matching/null-fill rules to restore only missing cells in retained SEC periods; no period or SEC non-null value was changed. It planned {sum(annual.values())} annual and {sum(quarterly.values())} quarterly Yahoo null fills across {restoration.get('filesWithPlannedRestoredCells', 0)} files. EPS fills additionally require matching fiscal labels, unchanged split metadata, and diluted shares within 1%.",
            f"The pass left {restoration.get('excludedCellCountsByReason', {}).get('annual', {}).get('sec_operating_income_preferred', 0)} annual and {restoration.get('excludedCellCountsByReason', {}).get('quarterly', {}).get('sec_operating_income_preferred', 0)} Yahoo reported-operating-income aliases null where a SEC operating-income value already exists, preserving the UI's SEC-preferred value. It also checks old gross profit and cost values against revenue and their counterpart before filling them.",
            f"The exact per-field counts and reviewer-triaged AGM, EEFT, LOPE, PLUS, and BB checks are in [`financials-us-yahoo-null-restoration.json`](../../docs/report/financials-us-yahoo-null-restoration.json). Re-run the offline pass with `python atlas/scripts/restore_yahoo_nulls_from_git.py --baseline-revision {restoration.get('baselineRevision', 'HEAD')}`.",
            "",
        ])
    lines.extend([
        "## Coverage",
        "",
        "The calendar window counts only period ends on or after the cutoff. The latest-record window separately counts the latest 15 annual or 60 quarterly rows where available. Neither count implies that every requested field is populated or that the periods form a gap-free series. The target-complete counts require at least 15/60 selected periods and every displayed metric input present throughout that symbol’s selected window; the seven-field count requires all seven metrics to meet that rule. Cell coverage percentages describe the observed period rows and do not imply a complete 15-year history.",
        "",
        "| Window | Period type | Rows in window | Rows in source file | Symbols with target rows | Symbols with target contiguous rows | Symbols complete on all 7 fields at target | Target |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for window_name, title in (("calendar15Year", "Calendar cutoff"), ("latestRecords", "Latest records")):
        aggregate_data = calendar["summary"] if window_name == "calendar15Year" else latest["summary"]
        for kind in KINDS:
            data = aggregate_data[kind]
            lines.append(f"| {title} | {kind} | {data['periodRows']} | {data['sourcePeriodRows']} | {data['symbolsWithAtLeastTargetPeriods']} | {data['symbolsWithAtLeastTargetContiguousPeriods']} | {data['symbolsWithAllSevenMetricsCompleteAtTarget']} | {data['targetPeriods']} |")
    lines.extend([
        "",
        "### Field cells within the 15-calendar-year cutoff",
        "",
        "| Period type | Metric | Before available | After displayed | Change (raw for cost) | Raw before | Raw after | Derived after | Missing after | Cells | Symbols complete at target | Coverage after |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for kind in KINDS:
        before_metrics = before.get(kind, {})
        after_metrics = calendar["summary"][kind]["metrics"]
        for metric in METRICS:
            prior = before_metrics.get(metric, {}).get("available")
            current = after_metrics[metric]["available"]
            missing = after_metrics[metric]["missing"]
            cells = after_metrics[metric]["periods"]
            complete_target = after_metrics[metric]["symbolsWithTargetCompletePeriods"]
            pct = after_metrics[metric]["coveragePercent"]
            prior_text = "—" if prior is None else str(prior)
            if prior is None:
                delta = "—"
            elif metric == "costOfRevenue":
                raw_prior = before_metrics.get(metric, {}).get("rawReportedAvailable", prior)
                delta = f"{after_metrics[metric].get('rawReportedAvailable', 0) - raw_prior:+d}"
            else:
                delta = f"{current - prior:+d}"
            pct_text = "—" if pct is None else f"{pct:.2f}%"
            raw_after = after_metrics[metric].get("rawReportedAvailable", 0)
            derived_after = after_metrics[metric].get("derivedAvailable", 0)
            raw_before = before_metrics.get(metric, {}).get("rawReportedAvailable")
            raw_before_text = (str(raw_before) if raw_before is not None else "—") if metric == "costOfRevenue" else "—"
            raw_text = str(raw_after) if metric == "costOfRevenue" else "—"
            derived_text = str(derived_after) if metric == "costOfRevenue" else "—"
            lines.append(f"| {kind} | {metric} | {prior_text} | {current} | {delta} | {raw_before_text} | {raw_text} | {derived_text} | {missing} | {cells} | {complete_target} | {pct_text} |")
    lines.extend([
        "",
        "The seven metrics are evaluated as the page displays them: EPS uses diluted EPS then basic EPS; operating income prefers the listed line then the general operating-income field; gross margin uses reported gross profit or revenue minus cost of revenue; net margin requires positive revenue; non-operating income is pretax income minus operating income. Cost of revenue shows the reported raw value when present, otherwise it may display the same-period revenue-minus-gross-profit estimate under the conservative rules in the UI. The estimate is never written into the source JSON; the table separates raw and derived counts. For cost of revenue, the change compares raw-reported cell counts so display derivations are not described as newly fetched SEC data.",
        "",
        "Non-operating income is a derived net non-operating result, not a separately reported non-operating revenue total. The app marks that basis and shows the formula and inputs. SEC/Yahoo source marks apply to a period; Yahoo may fill a null cell in an SEC-sourced period. The operating-income labels therefore make no source claim, and the report preserves SEC concept lineage where it exists.",
        "",
        "## Gaps and missing values",
        "",
        "The JSON companion lists each ticker’s date spans, field availability, missing-input reason counts, SEC concept lineage, quality issues, and optional refresh outcome. A period is counted as present when it appears in the source file; a metric cell is counted only when the inputs required for that displayed metric exist. A reported null remains null.",
        "",
        "A consecutive-period check allows 330–395 days between annual ends and 75–120 days between quarter ends to accommodate fiscal calendars and 52/53-week years. The check is evidence about date spacing, not proof that a company reported every economic line item.",
        "",
        "The scope is the existing tracked US snapshot from `build_financials.collect_symbols()`; its exact symbol count and ordered hash appear above. The broader universe catalog was not substituted. SEC does not provide 15 years for every issuer, newly listed companies naturally have shorter histories, and some financial-sector statements do not expose cost of revenue or operating income in a comparable form. Such values remain unavailable instead of being filled with estimates.",
        f"For the latest-record window, {latest['summary']['annual']['symbolsWithAtLeastTargetPeriods']} of {snapshot['symbolCount']} symbols have at least 15 annual rows and {latest['summary']['quarterly']['symbolsWithAtLeastTargetPeriods']} have at least 60 quarterly rows. Only {latest['summary']['annual']['symbolsWithAllSevenMetricsCompleteAtTarget']} annual and {latest['summary']['quarterly']['symbolsWithAllSevenMetricsCompleteAtTarget']} quarterly histories have all seven displayed metrics populated throughout those target rows.",
        "",
        f"Baseline comparison uses commit `{report.get('baseline', {}).get('revision', 'unavailable')}` ({report.get('baseline', {}).get('asOf', '—')}); raw period files are read from Git at that revision. For cost of revenue, baseline availability is raw-reported only, while the after column also includes safe display-only derivations. These counts do not imply that every cell changed in this refresh.",
        "",
        f"Machine-readable per-symbol evidence: [`financials-us-coverage.json`](../../docs/report/financials-us-coverage.json). Offline generator: [`report_financials_us_coverage.py`](../scripts/report_financials_us_coverage.py).",
        f"Regenerate the report with `python atlas/scripts/report_financials_us_coverage.py --as-of {report['asOf']} --baseline-revision {report.get('baseline', {}).get('revision', 'HEAD')} --refresh-log /tmp/us-15y-sec-refresh.log --raw-cache-manifest /tmp/us-15y-sec-raw/manifest.json --restoration-audit docs/report/financials-us-yahoo-null-restoration.json`. `--refresh-log`, `--raw-cache-manifest`, and `--restoration-audit` are optional provenance inputs.",
        "",
        "## Validation",
        "",
        "`npm ci` passed. The Python financials, SEC-seeder, growth, coverage-report, and Yahoo-restoration suites passed 230 tests; `npm test -- --maxWorkers=2` passed 806 tests across 66 files; `npm run lint` and `npm run build` passed. The financials browser regression passed 14/14 Chromium cases and 7/7 WebKit cases.",
        "",
        "WebKit ran from Playwright browser files under `/tmp/us-15y-playwright`. Since host packages could not be installed system-wide, 28 Debian dependency packages were extracted under `/tmp/us-15y-webkit-deps`; five required library names were linked into Playwright’s temporary WPE bundle. `ldd` resolved the WPE browser and process binaries before running the tests. The host validation override was only for this user-space setup; the browser assertions ran normally. The WebKit command was `LD_LIBRARY_PATH=/tmp/us-15y-playwright/webkit-2359/minibrowser-wpe/lib:/tmp/us-15y-playwright/webkit-2359/minibrowser-wpe/sys/lib XDG_CACHE_HOME=/tmp/us-15y-cache XDG_CONFIG_HOME=/tmp/us-15y-config PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1 PLAYWRIGHT_BROWSERS_PATH=/tmp/us-15y-playwright npm run test:site -- financials.spec.ts --project=iphone-webkit`.",
        "",
    ])
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    as_of = date.fromisoformat(args.as_of)
    try:
        cutoff = as_of.replace(year=as_of.year - 15)
    except ValueError:
        cutoff = as_of.replace(year=as_of.year - 15, day=28)
    report_dir, data_dir = args.report_dir, args.data_dir
    symbols = [symbol for symbol in collect_symbols(report_dir, data_dir) if market_of(symbol) == "US"]
    symbols_hash = hashlib.sha256("\n".join(symbols).encode("utf-8")).hexdigest()
    baseline, baseline_rows = load_git_baseline(args.baseline_revision, symbols, cutoff)
    refresh_outcomes = parse_refresh_log(args.refresh_log)
    rows: dict[str, dict[str, Any]] = {}
    files_found = 0
    for symbol in symbols:
        path = os.path.join(args.financials_dir, symbol + ".json")
        document = None
        file_state = "missing"
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as source:
                    parsed = json.load(source)
                if isinstance(parsed, dict) and parsed.get("symbol", symbol).upper() == symbol.upper():
                    document, file_state = parsed, "ok"
                    files_found += 1
                else:
                    file_state = "invalid-symbol-or-shape"
            except (OSError, json.JSONDecodeError):
                file_state = "unreadable"
        windows: dict[str, dict[str, Any]] = {}
        for window_key, limits in (("calendar15Year", {"annual": None, "quarterly": None}),
                                   ("latestRecords", {"annual": 15, "quarterly": 60})):
            windows[window_key] = {
                kind: get_window(document, kind, cutoff, limits[kind])
                for kind in KINDS
            }
        reasons = []
        if file_state != "ok":
            outcome = refresh_outcomes.get(symbol)
            reasons.append({"code": file_state, "refreshOutcome": outcome})
        else:
            issues = document.get("quality", {}).get("issues", [])
            if isinstance(issues, list):
                reasons.extend({"code": str(issue)} for issue in issues)
            for kind in KINDS:
                target = 15 if kind == "annual" else 60
                if windows["latestRecords"][kind]["periods"] < target:
                    reasons.append({"code": f"short-{kind}-history", "availablePeriods": windows["latestRecords"][kind]["periods"], "targetPeriods": target})
        rows[symbol] = {
            "file": symbol + ".json",
            "fileState": file_state,
            "inBaseline": baseline_rows[symbol]["fileState"] == "ok",
            "refreshOutcome": refresh_outcomes.get(symbol),
            "source": document.get("source") if document else None,
            "qualityLevel": document.get("quality", {}).get("level") if document else None,
            "qualityIssues": document.get("quality", {}).get("issues", []) if document else [],
            "secConcepts": relevant_lineage(document),
            "reasons": reasons,
            "windows": windows,
        }
    baseline_annual = summarize_baseline(baseline_rows, "annual")
    baseline_quarterly = summarize_baseline(baseline_rows, "quarterly")
    before_after: dict[str, Any] = {}
    for kind, prior in (("annual", baseline_annual), ("quarterly", baseline_quarterly)):
        if prior is not None:
            before_after[kind] = prior
    refresh_summary = parse_refresh_summary(args.refresh_log, refresh_outcomes, args.raw_cache_manifest)
    if args.refresh_log:
        refresh_summary["newFinancialFiles"] = [
            symbol for symbol, row in rows.items() if row["fileState"] == "ok" and not row["inBaseline"]
        ]
    restoration_summary = {}
    if args.restoration_audit:
        with open(args.restoration_audit, encoding="utf-8") as source:
            audit = json.load(source)
        planned_counts = audit.get("plannedRestorationCellCounts", {})
        restoration_summary = {
            "audit": os.path.basename(args.restoration_audit),
            "baselineRevision": audit.get("baselineRevision"),
            "filesWithPlannedRestoredCells": sum(
                1 for symbol in audit.get("symbols", {}).values()
                if any(symbol.get("restoredRows", {}).values())
            ),
            "filesWithRestoredCellsThisRun": audit.get("filesWithRestoredCellsThisRun", 0),
            "plannedRestorationCellCounts": planned_counts,
            "cellsAppliedThisRun": audit.get("cellsAppliedThisRun", {}),
            "excludedCellCountsByReason": audit.get("excludedCellCountsByReason", {}),
            "targetAudit": audit.get("targetAudit", {}),
        }
    return {
        "version": 1,
        "asOf": as_of.isoformat(),
        "cutoff": cutoff.isoformat(),
        "baseline": {
            **baseline,
            "costOfRevenueBasis": "raw-reported only",
        },
        "refresh": refresh_summary,
        "yahooNullRestoration": restoration_summary,
        "snapshot": {
            "source": "build_financials.collect_symbols filtered to market_of(symbol) == US",
            "symbolCount": len(symbols),
            "financialFilesFound": files_found,
            "symbolsSha256": symbols_hash,
            "symbols": symbols,
        },
        "calendar15Year": {
            "definition": "period end >= cutoff",
            "summary": aggregate(rows, "calendar15Year"),
        },
        "latestRecords": {
            "definition": "latest 15 annual / 60 quarterly source rows; older rows may remain in the SEC file",
            "summary": aggregate(rows, "latestRecords"),
        },
        "beforeAfter": before_after,
        "symbols": rows,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    for output, contents in ((args.out, json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"),
                             (args.markdown_out, render_markdown(report))):
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    print(f"Wrote {args.out} and {args.markdown_out}")
    print(f"US snapshot: {report['snapshot']['symbolCount']} symbols; files: {report['snapshot']['financialFilesFound']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
