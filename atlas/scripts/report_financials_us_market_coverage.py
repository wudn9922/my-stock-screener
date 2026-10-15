"""Render whole-current-directory SEC screening and financial coverage from local snapshots.

Metric inputs and cost-of-revenue raw/derived rules come from report_financials_us_coverage.py.
This report keeps the broad NasdaqTrader cohort separate from the tracked 637-symbol report.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import report_financials_us_coverage as coverage  # noqa: E402

DEFAULT_SNAPSHOT = REPO_ROOT / "docs/report/fundamentals/us-market-snapshot.json"
DEFAULT_PROGRESS = REPO_ROOT / "docs/report/fundamentals/us-market-progress.json"
DEFAULT_OUT = REPO_ROOT / "docs/report/fundamentals/us-market-backfill.json"
DEFAULT_MARKDOWN = REPO_ROOT / "atlas/docs/US_FINANCIAL_MARKET_HISTORY_BACKFILL.md"
DEFAULT_FINANCIALS = REPO_ROOT / "docs/report/financials"


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def compute_cutoff(as_of: date) -> date:
    try:
        return as_of.replace(year=as_of.year - 15)
    except ValueError:
        return as_of.replace(year=as_of.year - 15, day=28)


def load_market_baseline(revision: str, symbols: list[str], cutoff: date) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read only financial files present in the baseline tree, avoiding a git subprocess per listing."""
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=REPO_ROOT,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    commit_time = subprocess.run(
        ["git", "show", "-s", "--format=%cI", resolved], cwd=REPO_ROOT,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--name-only", resolved, "--", "docs/report/financials"],
        cwd=REPO_ROOT, check=True, capture_output=True,
    ).stdout
    symbols_set = set(symbols)
    tracked = {
        Path(raw.decode()).stem for raw in tree.split(b"\0")
        if raw.endswith(b".json") and Path(raw.decode()).stem in symbols_set
    }
    rows: dict[str, Any] = {
        symbol: {
            "fileState": "missing",
            "windows": {"calendar15Year": {kind: coverage.get_window(None, kind, cutoff, None)
                                              for kind in coverage.KINDS}},
        }
        for symbol in symbols
    }
    for symbol in sorted(tracked):
        path = f"docs/report/financials/{symbol}.json"
        result = subprocess.run(["git", "show", f"{resolved}:{path}"], cwd=REPO_ROOT,
                                check=True, capture_output=True)
        try:
            document = json.loads(result.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid baseline JSON at {resolved}:{path}") from error
        if not isinstance(document, dict) or document.get("symbol", symbol).upper() != symbol.upper():
            raise ValueError(f"baseline financial identity mismatch at {resolved}:{path}")
        rows[symbol] = {
            "fileState": "ok",
            "windows": {"calendar15Year": {kind: coverage.get_window(document, kind, cutoff, None,
                                                                         allow_derived_cost=False)
                                              for kind in coverage.KINDS}},
        }
    return {
        "revision": resolved,
        "asOf": commit_time,
        "cutoff": cutoff.isoformat(),
        "financialFilesFound": len(tracked),
        "symbols": sorted(tracked),
        "costOfRevenueBasis": "raw-reported only",
    }, rows


def source_count(document: dict[str, Any], kind: str) -> dict[str, int]:
    block = document.get(kind) if isinstance(document, dict) else None
    ends = block.get("end", []) if isinstance(block, dict) else []
    src = block.get("src", []) if isinstance(block, dict) else []
    result = Counter()
    for index in range(len(ends) if isinstance(ends, list) else 0):
        value = src[index] if isinstance(src, list) and index < len(src) else "yahoo"
        result[value if value in ("sec", "yahoo") else "other"] += 1
    return dict(sorted(result.items()))


def issuer_screen_summary(progress: dict[str, Any]) -> dict[str, Any]:
    issuers = progress.get("issuers", {}) if isinstance(progress.get("issuers"), dict) else {}
    status_counts = Counter(item.get("status", "unknown") for item in issuers.values() if isinstance(item, dict))
    confirmed_blank_checks = sum(
        1 for item in issuers.values()
        if isinstance(item, dict) and (item.get("screeningEvidence") or {}).get("sic") == 6770
    )
    operating_candidates = sum(
        1 for item in issuers.values()
        if isinstance(item, dict) and item.get("screening") == "operating_candidate"
    )
    unknown_metadata = sum(
        1 for item in issuers.values()
        if isinstance(item, dict) and item.get("status") in ("unknownmetadata", "failure")
    )
    pending_new = sum(
        1 for cik in progress.get("unseededIssuerQueue", [])
        if issuers.get(cik, {}).get("status") in ("pendingmetadata", "pendingfinancial")
    )
    pending_existing = sum(
        1 for cik in progress.get("metadataOnlyIssuerQueue", [])
        if issuers.get(cik, {}).get("status") == "seeded_existing_shell_screen_pending"
    )
    return {
        "mappedIssuerGroups": len(issuers),
        "statusCounts": dict(sorted(status_counts.items())),
        "confirmedSIC6770BlankChecks": confirmed_blank_checks,
        "operatingCandidatesScreened": operating_candidates,
        "unknownMetadataOrFailure": unknown_metadata,
        "pendingNewIssuers": pending_new,
        "existingSeededIssuersPendingScreen": pending_existing,
    }


def build_report(snapshot: dict[str, Any], progress: dict[str, Any], as_of: date,
                 baseline_revision: str, financials_dir: Path) -> dict[str, Any]:
    cutoff = compute_cutoff(as_of)
    eligible = snapshot.get("eligibleSymbols")
    if not isinstance(eligible, list):
        raise ValueError("listing snapshot lacks eligibleSymbols")
    symbols = sorted(row["symbol"] for row in eligible if isinstance(row, dict) and isinstance(row.get("symbol"), str))
    if len(symbols) != len(set(symbols)):
        raise ValueError("listing snapshot contains duplicate normalized symbols")
    symbol_sha = hashlib.sha256("\n".join(symbols).encode()).hexdigest()
    if symbol_sha != snapshot.get("cohortSymbolsSha256"):
        raise ValueError("listing cohort hash does not match snapshot")
    baseline, baseline_rows = load_market_baseline(baseline_revision, symbols, cutoff)
    outcomes = progress.get("symbols", {}) if isinstance(progress.get("symbols"), dict) else {}
    issuer_by_cik = progress.get("issuers", {}) if isinstance(progress.get("issuers"), dict) else {}
    symbol_to_cik = {
        symbol: cik for cik, issuer in issuer_by_cik.items()
        if isinstance(issuer, dict)
        for symbol in issuer.get("symbols", [])
    }
    rows: dict[str, Any] = {}
    files_found = sec_files = yahoo_only_files = 0
    source_totals = {kind: Counter() for kind in coverage.KINDS}
    for symbol in symbols:
        document: dict[str, Any] | None = None
        state = "missing"
        path = financials_dir / f"{symbol}.json"
        if path.is_file():
            try:
                parsed = read_json(path)
                if isinstance(parsed, dict) and parsed.get("symbol", symbol).upper() == symbol.upper():
                    document, state = parsed, "ok"
                    files_found += 1
                    has_sec = any(source == "sec" for kind in coverage.KINDS
                                  for source in source_count(document, kind))
                    if has_sec:
                        sec_files += 1
                    else:
                        yahoo_only_files += 1
                else:
                    state = "invalid-symbol-or-shape"
            except (OSError, json.JSONDecodeError):
                state = "unreadable"
        windows = {
            window_name: {
                kind: coverage.get_window(document, kind, cutoff, limits[kind])
                for kind in coverage.KINDS
            }
            for window_name, limits in (
                ("calendar15Year", {"annual": None, "quarterly": None}),
                ("latestRecords", {"annual": 15, "quarterly": 60}),
            )
        }
        for kind in coverage.KINDS:
            source_totals[kind].update(source_count(document or {}, kind))
        progress_row = outcomes.get(symbol, {}) if isinstance(outcomes.get(symbol, {}), dict) else {}
        refresh_state = progress_row.get("status")
        reasons = []
        if state != "ok":
            reasons.append({"code": state, "queueStatus": refresh_state})
        elif document:
            issues = document.get("quality", {}).get("issues", [])
            if isinstance(issues, list):
                reasons.extend({"code": str(issue)} for issue in issues)
            for kind, target in (("annual", 15), ("quarterly", 60)):
                count = windows["latestRecords"][kind]["periods"]
                if count < target:
                    reasons.append({"code": f"short-{kind}-history", "availablePeriods": count, "targetPeriods": target})
        rows[symbol] = {
            "file": symbol + ".json",
            "fileState": state,
            "inBaseline": baseline_rows[symbol]["fileState"] == "ok",
            "cik": symbol_to_cik.get(symbol),
            "issuerStatus": issuer_by_cik.get(symbol_to_cik.get(symbol), {}).get("status") if symbol_to_cik.get(symbol) else None,
            "listingQueueStatus": refresh_state,
            "screeningStatus": progress_row.get("screeningStatus"),
            "source": document.get("source") if document else None,
            "qualityLevel": document.get("quality", {}).get("level") if document else None,
            "qualityIssues": document.get("quality", {}).get("issues", []) if document else [],
            "secConcepts": coverage.relevant_lineage(document),
            "periodSources": {kind: source_count(document or {}, kind) for kind in coverage.KINDS},
            "reasons": reasons,
            "windows": windows,
        }
    baseline_summary = {
        kind: coverage.summarize_baseline(baseline_rows, kind)
        for kind in coverage.KINDS
    }
    validations = progress.get("currentDirectoryValidations", [])
    latest_validation = validations[-1] if validations else None
    screen_summary = issuer_screen_summary(progress)
    latest_batch = (progress.get("batches") or [None])[-1]
    directory = snapshot.get("directoryCounts", {})
    return {
        "version": 1,
        "asOf": as_of.isoformat(),
        "cutoff": cutoff.isoformat(),
        "scope": {
            "description": "Frozen cohort of symbols accepted by the current NasdaqTrader directory common-equity filters",
            "snapshotAt": snapshot.get("snapshotAt"),
            "snapshotSha256": progress.get("snapshotSha256"),
            "sourcePairSha256": snapshot.get("sourcePairSha256"),
            "sourceFiles": snapshot.get("sources"),
            "listingSymbols": len(symbols),
            "listingSymbolsSha256": symbol_sha,
            "directoryCounts": directory,
            "currentDirectoryValidation": latest_validation,
            "currentDirectoryValidationHistory": validations,
            "absenceMeaning": "not_current_directory only; not a finding of legal delisting",
        },
        "issuerScreening": {
            **screen_summary,
            "tickerMapRetrievedAt": progress.get("tickerMapRetrievedAt"),
            "tickerMapSource": progress.get("tickerMapSource"),
            "tickerMapSymbolCount": progress.get("tickerMapSymbolCount"),
            "activeDirectoryMappedSymbols": progress.get("mappedSymbolCount"),
            "unmappedSymbols": progress.get("unmappedSymbols", []),
            "policy": {
                "confirmedBlankCheck": "SEC submissions.sic == 6770",
                "operatingCandidate": "active directory + fresh SEC ticker/CIK association + validated submissions CIK and available ticker-class association + SIC != 6770",
                "entityTypeAloneIsNotShellEvidence": True,
                "operatingCandidateIsNotLegalNonShellCertification": True,
                "spacNameHeuristicIsSeparate": True,
            },
        },
        "queue": {
            "progressPath": progress.get("snapshotPath", "docs/report/fundamentals/us-market-snapshot.json").replace("us-market-snapshot.json", "us-market-progress.json"),
            "progressStatus": progress.get("status"),
            "batchCount": len(progress.get("batches", [])),
            "latestBatch": latest_batch,
            "remainingNewIssuerQueue": screen_summary["pendingNewIssuers"],
            "remainingExistingMetadataQueue": screen_summary["existingSeededIssuersPendingScreen"],
            "pendingIndexSymbols": progress.get("pendingIndexSymbols", []),
            "resumeCommand": "python atlas/scripts/backfill_financials_us_market.py --resume --batch-size 500",
            "retryCommand": "python atlas/scripts/backfill_financials_us_market.py --resume --retry --batch-size 500",
            "rawCachePath": "/tmp/us-15y-sec-raw (cache loss causes re-fetch; progress and snapshot are committed)",
        },
        "baseline": baseline,
        "baselineCoverage": baseline_summary,
        "financialFileCounts": {
            "listingSymbols": len(symbols),
            "financialFilesFound": files_found,
            "secBackedFiles": sec_files,
            "yahooOnlyFiles": yahoo_only_files,
            "missingOrInvalidFiles": len(symbols) - files_found,
            "periodSources": {kind: dict(sorted(source_totals[kind].items())) for kind in coverage.KINDS},
        },
        "calendar15Year": {"definition": "period end >= cutoff", "summary": coverage.aggregate(rows, "calendar15Year")},
        "latestRecords": {"definition": "latest 15 annual / 60 quarterly source rows where available", "summary": coverage.aggregate(rows, "latestRecords")},
        "symbols": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    scope = report["scope"]
    screens = report["issuerScreening"]
    queue = report["queue"]
    files = report["financialFileCounts"]
    calendar = report["calendar15Year"]["summary"]
    latest = report["latestRecords"]["summary"]
    lines = [
        "# US market financial-history backfill",
        "",
        f"As of {report['asOf']}, the 15-calendar-year cutoff is {report['cutoff']}.",
        f"The frozen NasdaqTrader snapshot contains {scope['listingSymbols']:,} directory-filtered symbols across {screens['mappedIssuerGroups']:,} SEC-mapped CIK groups. It was fetched at {scope['snapshotAt']} and has source-pair SHA-256 `{scope['sourcePairSha256']}`.",
        f"There are currently {files['financialFilesFound']:,} financial files among those listing symbols: {files['secBackedFiles']:,} SEC-backed and {files['yahooOnlyFiles']:,} Yahoo-only. The whole cohort is not yet financially refreshed.",
        "",
        "## Listing and issuer screening",
        "",
        f"The NasdaqTrader filters accepted {scope['directoryCounts'].get('acceptedListingRows', 0):,} unique listing rows and excluded {scope['directoryCounts'].get('excludedRows', 0):,} rows. The {scope['directoryCounts'].get('spacNameHeuristicRows', 0):,} SPAC-name matches are a string heuristic, separate from confirmed blank-check issuers.",
        f"SEC submissions confirmed {screens['confirmedSIC6770BlankChecks']:,} SIC 6770 blank-check issuers and identified {screens['operatingCandidatesScreened']:,} active-directory operating candidates. {screens['unknownMetadataOrFailure']:,} issuer decisions remain unknown or failed. An SIC other than 6770 is a queueing basis and does not certify legal non-shell status; entityType alone is not treated as shell evidence.",
        f"The current resume queue has {queue['remainingNewIssuerQueue']:,} new issuer groups pending and {queue['remainingExistingMetadataQueue']:,} existing SEC-seeded issuer groups still awaiting a submissions screen. Existing financial files remain available while that metadata queue is pending.",
        f"Financial index reconciliation pending for {len(queue.get('pendingIndexSymbols', [])):,} symbol files.",
        "",
        "## Current batch and resume",
        "",
    ]
    batch = queue.get("latestBatch")
    if batch:
        lines.append(f"Batch {batch.get('batchId')} ({batch.get('phase', 'unknown')}) attempted {len(batch.get('attemptedCIKs', [])):,} issuer groups; status: {batch.get('status', 'unknown')}. SEC requests this batch: {batch.get('secRequestsAfter', 0) - batch.get('secRequestsBefore', 0)}; raw SEC bytes received: {batch.get('secBytesAfter', 0) - batch.get('secBytesBefore', 0):,}.")
        lines.append(f"The batch checkpoint has {batch.get('remainingNewIssuerQueue', batch.get('remainingPendingNewIssuers', '—'))} new issuer groups pending and {batch.get('remainingPendingMetadataOnlyIssuers', '—')} existing metadata screens pending.")
    lines.extend([
        "",
        f"Run `{queue['resumeCommand']}` to continue the next stable batch. Add `--retry` only after reviewing failure and unknown-metadata decisions. Each resume fetches both current NasdaqTrader directories again and records any frozen symbol that is no longer present as `not_current_directory`; that label does not assert legal delisting.",
        "",
        "## Financial metric coverage",
        "",
        "These are observed financial-file rows across the full frozen listing cohort. They do not imply every issuer has 15 years of history or every metric input. Target counts require at least 15 annual or 60 quarterly rows; cost of revenue keeps raw reported and display-only derived values separate.",
        "",
        "| Window | Period type | Rows | Symbols with target rows | Symbols contiguous at target | Symbols complete on all 7 metrics | Target |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for window_name, data in (("Calendar cutoff", calendar), ("Latest records", latest)):
        for kind in coverage.KINDS:
            block = data[kind]
            lines.append(f"| {window_name} | {kind} | {block['periodRows']:,} | {block['symbolsWithAtLeastTargetPeriods']:,} | {block['symbolsWithAtLeastTargetContiguousPeriods']:,} | {block['symbolsWithAllSevenMetricsCompleteAtTarget']:,} | {block['targetPeriods']} |")
    lines.extend([
        "",
        "### Calendar-window metric cells",
        "",
        "| Period | Metric | Available | Raw reported | Derived display | Missing | Coverage |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for kind in coverage.KINDS:
        for metric in coverage.METRICS:
            data = calendar[kind]["metrics"][metric]
            pct = "—" if data["coveragePercent"] is None else f"{data['coveragePercent']:.2f}%"
            raw = data.get("rawReportedAvailable", 0) if metric == "costOfRevenue" else "—"
            derived = data.get("derivedAvailable", 0) if metric == "costOfRevenue" else "—"
            lines.append(f"| {kind} | {metric} | {data['available']:,} | {raw} | {derived} | {data['missing']:,} | {pct} |")
    lines.extend([
        "",
        "The seven fields follow the existing coverage reporter and financial display rules: EPS, revenue, cost of revenue, non-operating income, gross margin, operating income, and net margin. Derived cost of revenue is a report-only revenue-minus-gross-profit calculation; source financial JSON is not rewritten with that estimate.",
        "",
        f"Machine-readable report: `docs/report/fundamentals/us-market-backfill.json`. The immutable listing snapshot and per-issuer checkpoints are `docs/report/fundamentals/us-market-snapshot.json` and `docs/report/fundamentals/us-market-progress.json`. The SEC raw-body cache is `{queue['rawCachePath']}`; losing it causes a paced refetch, while queue progress remains committed.",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--baseline-revision", default="c1c9b4e1e")
    parser.add_argument("--financials-dir", type=Path, default=DEFAULT_FINANCIALS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--markdown-out", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args(argv)
    snapshot = read_json(args.snapshot)
    progress = read_json(args.progress)
    report = build_report(snapshot, progress, date.fromisoformat(args.as_of), args.baseline_revision, args.financials_dir)
    atomic_write(args.out, json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    atomic_write(args.markdown_out, render_markdown(report))
    print(f"Wrote {args.out} and {args.markdown_out}")
    print(f"Frozen listing symbols: {report['scope']['listingSymbols']}; SEC-mapped issuer groups: {report['issuerScreening']['mappedIssuerGroups']}; financial files: {report['financialFileCounts']['financialFilesFound']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
