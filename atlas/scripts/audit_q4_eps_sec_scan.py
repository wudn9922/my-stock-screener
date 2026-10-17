#!/usr/bin/env python3
"""Audit cached SEC Q4 EPS filings and write a review staging report.

This is an offline, read-only companion to ``q4_eps_sec_scan.py``. It consumes that scanner's
``results/*.json`` manifests, verifies each cached filing body's SHA-256, and uses the structural
table parser from ``backfill_q4_eps_8k.py``. It never fetches SEC data and never edits financial
JSON. Only fill-null and exact source-confirmation decisions with grade-A evidence are included
in ``stagedDecisions``; conflicts and incomplete evidence remain unapplied.

Usage: python atlas/scripts/audit_q4_eps_sec_scan.py --manifest-dir .cache/q4-eps-sec-scan/results
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backfill_q4_eps_8k as table_parser  # noqa: E402
import sec_inline_xbrl_q4 as inline_xbrl  # noqa: E402
import build_financials as fin  # noqa: E402

DEFAULT_MANIFEST_DIR = REPO_ROOT / ".cache/q4-eps-sec-scan/results"
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache/q4-eps-sec-scan"
DEFAULT_FINANCIALS_DIR = REPO_ROOT / "docs/report/financials"
DEFAULT_REPORT = REPO_ROOT / "docs/report/fundamentals/q4-eps-sec-scan-staging.json"
SCHEMA = "q4-eps-sec-audit-v1"
PERIOD_END_TOLERANCE_DAYS = 7
CACHE_OK = frozenset({"ok", "cached", "fetched", "reused-cache"})
HTML_SUFFIXES = frozenset({".htm", ".html", ".xhtml", ".txt", ".xml"})
FIELDS = ("epsBasic", "epsDiluted")
CANDIDATE_RULES_VERSION = 10


def _load_json(path):
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _same_number(left, right):
    left_number, right_number = _number(left), _number(right)
    return left_number == right_number if left_number is not None and right_number is not None else left is right


def _iso_date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _normalized_cik(value):
    raw = str(value or "").strip()
    if not raw.isdigit() or not 1 <= len(raw) <= 10 or int(raw) <= 0:
        return None
    return str(int(raw))


def _financial_period(document, target):
    """Find the current financial row for this Q4 without guessing among duplicate labels."""
    if not isinstance(document, dict):
        return None, "financial-document-missing"
    quarterly = document.get("quarterly")
    if not isinstance(quarterly, dict):
        return None, "quarterly-section-missing"
    ends, fys, fqs = quarterly.get("end") or [], quarterly.get("fy") or [], quarterly.get("fq") or []
    matches = [index for index, (fy, fq, end) in enumerate(zip(fys, fqs, ends))
               if fq == 4 and str(end) == str(target.get("end"))
               and (target.get("fy") is None or fy == target.get("fy"))]
    if len(matches) != 1:
        return None, "financial-period-missing" if not matches else "financial-period-ambiguous"
    index = matches[0]
    values = quarterly.get("values") if isinstance(quarterly.get("values"), dict) else {}

    def at(field):
        column = values.get(field) if isinstance(values.get(field), list) else []
        return column[index] if index < len(column) else None

    return {"index": index, "values": {field: at(field) for field in FIELDS},
            "netIncome": at("netIncome")}, "ok"


def _cache_path(cache_path, cache_dir, manifest_dir):
    if not isinstance(cache_path, str) or not cache_path.strip():
        return None
    path = Path(cache_path)
    if path.is_absolute():
        return path
    candidates = (Path(cache_dir) / path, Path(manifest_dir).parent / path, Path.cwd() / path)
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[0])


def _read_verified_document(item, cache_dir, manifest_dir):
    name = str(item.get("name") or "")
    suffix = Path(name).suffix.lower()
    base = {
        "name": name,
        "url": item.get("url"),
        "cachePath": item.get("cachePath"),
        "expectedSha256": item.get("sha256"),
        "scannerStatus": item.get("status"),
        "formatStatus": item.get("formatStatus"),
        "parseStatus": item.get("parseStatus"),
    }
    if suffix == ".pdf":
        return None, dict(base, status="pdf-unreadable", reason="PDF extraction is not supported offline")
    if suffix not in HTML_SUFFIXES:
        return None, dict(base, status="unsupported-format", reason="only cached HTML/HTM/TXT documents are parsed")
    if item.get("status") not in CACHE_OK:
        return None, dict(base, status="unreadable", reason=f"scanner document status is {item.get('status')!r}")
    expected = str(item.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        return None, dict(base, status="missing-sha256", reason="scanner manifest has no valid document SHA-256")
    path = _cache_path(item.get("cachePath"), cache_dir, manifest_dir)
    if path is None:
        return None, dict(base, status="unreadable", reason="scanner manifest has no cached document path")
    try:
        body = path.read_bytes()
    except OSError as error:
        return None, dict(base, cachePath=str(path), status="unreadable", reason=f"cached bytes cannot be read: {error}")
    digest = hashlib.sha256(body).hexdigest()
    base["cachePath"] = str(path)
    base["actualSha256"] = digest
    if digest.lower() != expected.lower():
        return None, dict(base, status="hash-mismatch", reason="cached bytes do not match the scanner SHA-256")
    if item.get("formatStatus") == "format-error":
        return None, dict(base, status="unreadable-format", reason="scanner identified an invalid document format")
    if item.get("formatStatus") == "parse-error":
        return None, dict(base, status="unreadable-format", reason="scanner's HTML prefix parser failed")
    try:
        try:
            text = body.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = body.decode("latin-1")
    except UnicodeError as error:
        return None, dict(base, status="unreadable", reason=f"document decoding failed: {error}")
    if not text.strip():
        return None, dict(base, status="unreadable", reason="cached document is empty")
    return text, dict(base, status="readable", actualSha256=digest)


def _archive_identity_error(item, target, filing):
    """Bind every parsed body to the SEC CIK/accession/document named by the manifest."""
    name = str(item.get("name") or "")
    accession = str(filing.get("accessionNumber") or "")
    cik = str(target.get("cik") or target.get("provenanceCik") or "")
    if not cik.isdigit() or not accession or not name or Path(name).name != name:
        return "manifest-lacks-cik-accession-or-safe-document-name"
    compact = re.sub(r"[^0-9]", "", accession)
    parsed = urlparse(str(item.get("url") or ""))
    expected_path = f"/Archives/edgar/data/{int(cik)}/{compact}/{name}"
    if (parsed.scheme != "https" or parsed.netloc.lower() != "www.sec.gov"
            or parsed.path != expected_path or parsed.params or parsed.query or parsed.fragment):
        return "document-url-does-not-match-sec-cik-accession-and-filename"
    return None


def _basic_location(table, end):
    """Use the structural parser's expanded grid to recover basic-EPS row/cell provenance."""
    parsed, grid = table_parser._table_grid(table)
    if not grid or not any(grid):
        return None
    infos, _data_start, contexts = table_parser._header_contexts(parsed, grid)
    if not contexts:
        return None
    target_columns = [column for column, context in enumerate(contexts)
                      if table_parser._is_target_quarter_header(context["text"], end)
                      and (not table_parser._non_gaap(parsed.caption)
                           or table_parser._explicit_gaap(context["text"]))]
    if not target_columns:
        return None
    rows = []
    for row_index, info in enumerate(infos):
        kind, explicit = table_parser._eps_kind(info.get("label"), infos, row_index)
        if kind != "basic":
            continue
        values = table_parser._number_in_columns(info, target_columns)
        if len(values) == 1:
            rows.append((row_index, info, values[0], explicit))
    explicit_rows = [row for row in rows if row[3]]
    if explicit_rows:
        rows = explicit_rows
    if len(rows) != 1:
        return None
    row_index, info, (value, entry), _explicit = rows[0]
    return {"row": entry.row, "column": entry.col, "rowIndex": row_index,
            "rowLabel": info.get("label"), "cellText": entry.cell.text,
            "targetColumns": target_columns,
            "columnHeader": contexts[entry.col]["text"] if entry.col < len(contexts) else "",
            "footnoteMarkers": list(entry.cell.footnote_markers), "parsedValue": value}


def _row_excerpt(table, row_indices, evidence):
    parsed = table_parser._parsed_table(table)
    rows = []
    for row_index in sorted(set(index for index in row_indices if isinstance(index, int))):
        if 0 <= row_index < len(parsed.rows):
            rows.append({"row": row_index, "cells": [cell.text for cell in parsed.rows[row_index]]})
    caption = str(getattr(parsed, "caption", "") or "")
    header = str(evidence.get("columnHeader") or "")
    lines = [part for part in (caption, header) if part]
    lines.extend("row {0}: {1}".format(row["row"], " | ".join(row["cells"])) for row in rows)
    return {"caption": caption, "columnHeader": header, "rows": rows, "text": "\n".join(lines)}


def _split_normalization(document, period_end, filing_date, value, scan_as_of=None):
    split = document.get("split") if isinstance(document, dict) else None
    events = split.get("events") if isinstance(split, dict) else None
    events_source = split.get("eventsSource") if isinstance(split, dict) else None
    events_checked = _iso_date(split.get("eventsCheckedAt")) if isinstance(split, dict) else None
    coverage_start = _iso_date(split.get("eventsCoverageStart")) if isinstance(split, dict) else None
    coverage_end = _iso_date(split.get("eventsCoverageEnd")) if isinstance(split, dict) else None
    snapshot_date = _iso_date(document.get("checkedAt")) if isinstance(document, dict) else None
    scan_date = _iso_date(str(scan_as_of)[:10]) if scan_as_of else None
    latest_period_end = None
    if isinstance(document, dict):
        for section_name in ("annual", "quarterly"):
            section = document.get(section_name)
            if isinstance(section, dict):
                for raw_end in section.get("end") or []:
                    end_day = _iso_date(raw_end)
                    if end_day is not None and (latest_period_end is None or end_day > latest_period_end):
                        latest_period_end = end_day
    expected_symbol = str(document.get("symbol") or "").strip().upper() if isinstance(document, dict) else ""
    split_as_of = scan_date or date.today()
    coverage_required_end = max((day for day in (_iso_date(period_end), latest_period_end) if day is not None),
                                default=None)
    complete = bool(isinstance(split, dict) and split.get("eventsComplete") is True
                    and isinstance(events_source, str) and events_source.strip() and events_checked is not None
                    and split.get("eventsSymbol") == expected_symbol and bool(expected_symbol)
                    and coverage_start is not None and coverage_end is not None and split.get("eventsParserVersion") == 4
                    and fin._valid_split_event_map(events)
                    and _iso_date(period_end) is not None and coverage_start <= _iso_date(period_end)
                    and coverage_required_end is not None and coverage_end >= coverage_required_end
                    and (snapshot_date is None or events_checked >= snapshot_date)
                    and 0 <= (split_as_of - events_checked).days <= fin.SPLIT_CHART_MAX_STALENESS_DAYS)
    result = {"source": "financial-document.split.events", "events": [],
              "eventsComplete": complete, "eventsSource": events_source,
              "eventsCheckedAt": events_checked.isoformat() if events_checked else None,
              "eventsSymbol": split.get("eventsSymbol") if isinstance(split, dict) else None,
              "eventsCoverageStart": coverage_start.isoformat() if coverage_start else None,
              "eventsCoverageEnd": coverage_end.isoformat() if coverage_end else None,
              "eventsParserVersion": split.get("eventsParserVersion") if isinstance(split, dict) else None,
              "factor": 1.0, "status": "as-filed", "method": "reported EPS retained; no post-filing split events recorded"}
    if not isinstance(events, list) or not complete:
        result.update(status="pending", method="split-event basis unavailable",
                      reason="split event history is missing or not verified complete")
        return result, None
    end_day = _iso_date(period_end)
    filed_day = _iso_date(filing_date)
    if end_day is None:
        result.update(status="pending", method="period-end date is invalid", reason="invalid period end")
        return result, None
    factor = 1.0
    uncertain = []
    applied = []
    relevant_seen = False
    for event in events:
        if not isinstance(event, (list, tuple)) or len(event) < 2:
            uncertain.append(event)
            continue
        event_day = _iso_date(event[0])
        try:
            ratio = float(event[1])
        except (TypeError, ValueError, OverflowError):
            event_day, ratio = event_day, math.nan
        if event_day is None or not math.isfinite(ratio) or ratio <= 0:
            uncertain.append(event)
            continue
        if event_day <= end_day:
            continue
        relevant_seen = True
        event_record = {"date": event_day.isoformat(), "ratio": ratio}
        if filed_day is None:
            uncertain.append(event_record)
        elif event_day <= filed_day:
            uncertain.append(event_record)
        else:
            factor *= ratio
            applied.append(event_record)
    result["events"] = applied + uncertain
    if uncertain:
        result.update(status="pending", factor=factor,
                      method="split basis is uncertain; no EPS normalization applied",
                      reason="a split after period end was on/before filing date, had an invalid date/ratio, or filing date is invalid")
        return result, None
    if applied:
        normalized = round(float(value) / factor, 6)
        result.update(status="normalized", factor=factor,
                      method="reported EPS divided by product of independent split ratios dated after filing date",
                      appliedEvents=applied, normalizedValue=normalized)
        return result, normalized
    if relevant_seen:
        result.update(status="normalized", factor=1.0, appliedEvents=[],
                      method="no relevant split event remained after filing date",
                      normalizedValue=round(float(value), 6))
        return result, round(float(value), 6)
    result["normalizedValue"] = round(float(value), 6)
    return result, result["normalizedValue"]


def _extract_from_document(text, *, target_net_income, period_end, filing, item, body_info):
    try:
        tables = table_parser.structured_tables_of(text)
    except Exception as error:  # report parser failures from unusual source documents; keep batch going
        return {"status": "parser-error", "reason": f"structural HTML table parse failed: {type(error).__name__}: {error}",
                "tableCount": None, "candidates": [], "ambiguities": []}
    candidates, ambiguities = [], []
    safe_visible_basic_values = []
    visible_basic_context_flags = []
    for table_index, table in enumerate(tables):
        try:
            currency = (body_info.get("financialDocument") or {}).get("currency")
            result, reason = table_parser.table_eps(table, target_net_income, period_end, table_index=table_index,
                                                    expected_currency=currency)
            if result is None and reason is None:
                result, reason = table_parser.table_basic_eps(table, period_end, table_index=table_index,
                                                              expected_currency=currency)
        except Exception as error:  # malformed table should remain a review item, not abort other documents
            ambiguities.append(f"table {table_index}: parser error: {type(error).__name__}: {error}")
            continue
        if reason:
            ambiguities.append(reason)
            if "Basic EPS" in reason:
                visible_basic_context_flags.append(reason)
        if result is None:
            continue
        evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
        quality_by_field = result.get("qualityByField")
        basic_quality = quality_by_field.get("epsBasic") if isinstance(quality_by_field, dict) else None
        if result.get("epsBasicAsReported", result.get("epsBasic")) is not None:
            if isinstance(basic_quality, dict) and basic_quality.get("candidateValidated") is True:
                safe_visible_basic_values.append(result.get("epsBasicAsReported", result.get("epsBasic")))
        visible_basic_context_flags.extend(evidence.get("basicReviewFlags") or [])
        for field, raw_field in (("epsDiluted", "epsDilutedAsReported"), ("epsBasic", "epsBasicAsReported")):
            value = result.get(raw_field, result.get(field))
            if value is None:
                continue
            if field == "epsDiluted":
                row = evidence.get("epsCell") if isinstance(evidence.get("epsCell"), dict) else {}
                location = {"tableIndex": table_index, "row": row.get("row"), "column": row.get("column"),
                            "rowLabel": (evidence.get("epsRow") or {}).get("label"),
                            "cellText": row.get("text"), "targetColumns": evidence.get("targetColumns"),
                            "columnHeader": evidence.get("columnHeader"),
                            "footnoteMarkers": row.get("footnoteMarkers", [])}
            else:
                basic_cell = evidence.get("basicEpsCell") if isinstance(evidence.get("basicEpsCell"), dict) else {}
                basic_row = evidence.get("basicEpsRow") if isinstance(evidence.get("basicEpsRow"), dict) else {}
                if (not isinstance(basic_cell.get("row"), int)
                        or not isinstance(basic_cell.get("column"), int)
                        or not basic_row.get("label") or not basic_cell.get("text")):
                    ambiguities.append(f"table {table_index}: Basic EPS lacks a unique row/cell source locator")
                    continue
                location = {"tableIndex": table_index, "row": basic_cell["row"],
                            "column": basic_cell["column"], "rowLabel": basic_row["label"],
                            "cellText": basic_cell["text"],
                            "targetColumns": evidence.get("targetColumns"),
                            "columnHeader": evidence.get("columnHeader"),
                            "footnoteMarkers": basic_cell.get("footnoteMarkers", [])}
            row_indices = [location.get("row")]
            for key in ("netIncomeRow", "dilutedSharesRow"):
                supporting = evidence.get(key)
                if isinstance(supporting, dict):
                    row_indices.append(supporting.get("row"))
            excerpt = _row_excerpt(table, row_indices, evidence)
            split_info, normalized = _split_normalization(
                body_info.get("financialDocument") or {}, period_end, filing.get("filingDate"), value,
                body_info.get("scanGeneratedAt"))
            quality_by_field = result.get("qualityByField")
            field_quality = quality_by_field.get(field) if isinstance(quality_by_field, dict) else None
            field_quality = field_quality if isinstance(field_quality, dict) else None
            if field_quality is None:
                field_quality = {
                    "candidateValidated": result.get("candidateValidated") is True,
                    "manualReviewRequired": result.get("manualReviewRequired") is True,
                    "reviewFlags": result.get("reviewFlags") or [],
                }
            candidates.append({
                "field": field,
                "reportedValue": value,
                "normalizedValue": normalized,
                "provenance": {
                    "cik": filing.get("targetCik"),
                    "accession": filing.get("accessionNumber"),
                    "form": filing.get("form"),
                    "exhibit": item.get("name"),
                    "primaryDocument": filing.get("primaryDocument"),
                    "documentRole": "primary-document" if item.get("name") == filing.get("primaryDocument")
                    else "additional-filing-document",
                    "filingDate": filing.get("filingDate"),
                    "periodStart": body_info.get("targetPeriodStart"),
                    "periodEnd": evidence.get("reportPeriod", {}).get("end"),
                    "field": field,
                    "reportedValue": value,
                    "url": item.get("url"),
                    "sha256": body_info.get("actualSha256"),
                    "rawCachePath": body_info.get("cachePath"),
                    "matchReasons": filing.get("matchReasons") or [],
                    "rowColumn": location,
                    "tableEvidence": evidence,
                    "excerpt": excerpt,
                },
                "splitNormalization": split_info,
                "quality": {
                    "uniqueCandidate": result.get("uniqueCandidate") is True,
                    "candidateValidated": field_quality.get("candidateValidated") is True,
                    "manualReviewRequired": field_quality.get("manualReviewRequired") is True,
                    "reviewFlags": field_quality.get("reviewFlags") or [],
                    "diagnostics": result.get("diagnostics") or {},
                },
            })
    xbrl_candidates, xbrl_ambiguities = _extract_inline_xbrl_candidates(
        text, period_start=body_info.get("targetPeriodStart"), period_end=period_end,
        target_cik=filing.get("targetCik"),
        filing=filing, item=item, body_info=body_info,
        safe_visible_basic_values=safe_visible_basic_values,
        visible_basic_context_flags=visible_basic_context_flags)
    candidates.extend(xbrl_candidates)
    ambiguities.extend(xbrl_ambiguities)
    if candidates:
        return {"status": "candidate" if not ambiguities else "candidate-with-ambiguity",
                "tableCount": len(tables), "candidates": candidates, "ambiguities": ambiguities}
    if ambiguities:
        return {"status": "ambiguous", "reason": "; ".join(ambiguities), "tableCount": len(tables),
                "candidates": [], "ambiguities": ambiguities}
    return {"status": "no-result", "reason": "no period-specific GAAP diluted EPS table found",
            "tableCount": len(tables), "candidates": [], "ambiguities": []}


def _extract_inline_xbrl_candidates(text, *, period_start=None, period_end, target_cik, filing, item, body_info,
                                    safe_visible_basic_values=(), visible_basic_context_flags=()):
    """Return us-gaap Q4 per-share facts with a duration context and exact unit provenance."""
    if Path(str(item.get("name") or "")).suffix.lower() == ".txt" and len(
            re.findall(r"<\s*DOCUMENT\b", text, flags=re.I)) > 1:
        return [], ["SEC complete-submission text contains multiple DOCUMENT blocks; inline XBRL context IDs are not isolated"]
    expected_cik = _normalized_cik(target_cik)
    if expected_cik is None:
        return [], ["target SEC filer CIK is missing or malformed for Inline XBRL entity validation"]
    try:
        parsed = inline_xbrl.parse_inline_xbrl(text)
    except Exception as error:  # unusual source markup remains reviewable
        return [], [f"inline XBRL parser error: {type(error).__name__}: {error}"]
    target = _iso_date(period_end)
    expected_start = _iso_date(period_start)
    if target is None:
        return [], ["inline XBRL target period end is invalid"]
    names = {"EarningsPerShareDiluted": "epsDiluted", "EarningsPerShareBasic": "epsBasic"}
    candidates, ambiguities = [], []
    for fact in parsed.get("facts", []):
        field = names.get(fact.get("name"))
        if field is None:
            if "earningspershare" in str(fact.get("name") or "").lower():
                ambiguities.append(f"nonstandard EPS concept {fact.get('namespace')}:{fact.get('name')} requires taxonomy review")
            continue
        if str(fact.get("namespace") or "").lower() != "us-gaap":
            ambiguities.append(f"{fact.get('namespace')}:{fact.get('name')} is not the standard us-gaap concept")
            continue
        namespace_uri = str(fact.get("namespaceUri") or "")
        if not re.fullmatch(r"https?://fasb\.org/us-gaap/\d{4}(?:[-/][A-Za-z0-9._-]+)?", namespace_uri):
            ambiguities.append(f"{fact.get('namespace')}:{fact.get('name')} has an unverified us-gaap namespace URI")
            continue
        context = fact.get("context") if isinstance(fact.get("context"), dict) else {}
        start, end = _iso_date(context.get("start")), _iso_date(context.get("end"))
        if start is None or end is None:
            continue
        duration = (end - start).days
        if not 80 <= duration <= 100 or abs((end - target).days) > PERIOD_END_TOLERANCE_DAYS:
            continue
        if expected_start is not None and abs((start - expected_start).days) > PERIOD_END_TOLERANCE_DAYS:
            ambiguities.append(f"{fact.get('name')} Q4 context start {start} does not match fiscal Q4 start {expected_start}")
            continue
        if fact.get("unit") != "USD/shares":
            ambiguities.append(f"{fact.get('name')} Q4 context uses unsupported unit {fact.get('unit')!r}")
            continue
        entity_ids = context.get("entityIdentifiers") if isinstance(context.get("entityIdentifiers"), list) else []
        if len(entity_ids) != 1 or not isinstance(entity_ids[0], dict):
            ambiguities.append(f"{fact.get('name')} Q4 context has missing or ambiguous entity identifier")
            continue
        entity = entity_ids[0]
        scheme = str(entity.get("scheme") or "")
        scheme_url = urlparse(scheme)
        observed_cik = _normalized_cik(entity.get("identifier"))
        if (scheme_url.scheme not in {"http", "https"}
                or (scheme_url.hostname or "").lower() not in {"sec.gov", "www.sec.gov", "data.sec.gov"}
                or observed_cik != expected_cik):
            ambiguities.append(f"{fact.get('name')} Q4 context entity CIK {entity.get('identifier')!r} "
                               f"does not match target CIK {target_cik!r}")
            continue
        dimensions = context.get("dimensions") if isinstance(context.get("dimensions"), list) else []
        typed_dimensions = context.get("typedDimensions") if isinstance(context.get("typedDimensions"), list) else []
        if (dimensions or typed_dimensions or context.get("hasSegment") or context.get("hasScenario")):
            ambiguities.append(
                f"{fact.get('name')} Q4 context contains segment/scenario or explicit/typed dimensions; "
                "entity-wide EPS basis is unresolved")
            continue
        value = fact.get("value")
        try:
            reported = float(value)
        except (TypeError, ValueError, OverflowError):
            reported = math.nan
        if not math.isfinite(reported):
            ambiguities.append(f"{fact.get('name')} Q4 context value is not finite numeric data")
            continue
        quality_flags = []
        if field == "epsBasic":
            matching_visible_basic = [value for value in safe_visible_basic_values
                                      if _same_number(value, reported)]
            if len(matching_visible_basic) != 1:
                quality_flags.append("inline-basic-not-corroborated-by-entity-total-visible-row")
            if visible_basic_context_flags:
                quality_flags.append("inline-basic-has-visible-scoped-or-class-specific-row")
        split_info, normalized = _split_normalization(
            body_info.get("financialDocument") or {}, period_end, filing.get("filingDate"), reported,
            body_info.get("scanGeneratedAt"))
        provenance = {
            "cik": expected_cik, "field": field,
            "accession": filing.get("accessionNumber"), "form": filing.get("form"),
            "exhibit": item.get("name"), "primaryDocument": filing.get("primaryDocument"),
            "documentRole": "primary-document" if item.get("name") == filing.get("primaryDocument")
            else "additional-filing-document", "filingDate": filing.get("filingDate"),
            "periodStart": start.isoformat(), "periodEnd": end.isoformat(), "reportedValue": round(reported, 6),
            "url": item.get("url"), "sha256": body_info.get("actualSha256"),
            "rawCachePath": body_info.get("cachePath"),
            "matchReasons": filing.get("matchReasons") or [],
            "ixbrl": {"namespace": fact.get("namespace"), "namespaceUri": namespace_uri,
                      "concept": fact.get("name"),
                      "contextRef": fact.get("contextRef"), "context": context,
                      "entityIdentity": {"scheme": scheme, "cik": entity.get("identifier")},
                      "unitRef": fact.get("unitRef"), "unit": fact.get("unit"),
                      "factId": fact.get("id"), "format": fact.get("format"),
                      "decimals": fact.get("decimals"), "rawText": fact.get("rawText"),
                      "durationDays": duration,
                      "visibleBasicRowMatched": field == "epsBasic" and not quality_flags,
                      "visibleBasicContextFlags": list(visible_basic_context_flags)
                      if field == "epsBasic" else []},
            "excerpt": {"text": f"{fact.get('namespace')}:{fact.get('name')}[{fact.get('contextRef')}] "
                                 f"{start.isoformat()} to {end.isoformat()} = {reported} {fact.get('unit')}"},
        }
        candidates.append({"field": field, "reportedValue": reported, "normalizedValue": normalized,
                           "provenance": provenance, "splitNormalization": split_info,
                           "quality": {"uniqueCandidate": True, "candidateValidated": not quality_flags,
                                       "manualReviewRequired": bool(quality_flags),
                                       "reviewFlags": quality_flags,
                                       "diagnostics": {"source": "direct-us-gaap-ixbrl-context"}}})
    return candidates, ambiguities


def _document_entries(filing):
    cache = filing.get("cache")
    return cache.get("documents") if isinstance(cache, dict) and isinstance(cache.get("documents"), list) else []


def _audit_filing(filing, target, financial_document, financial_period, cache_dir, manifest_dir):
    documents = []
    cache = filing.get("cache")
    unexamined = cache.get("unexaminedDocuments") if isinstance(cache, dict) else None
    access_unknown = filing.get("historyCoverageUnknown") is True or bool(unexamined)
    if unexamined:
        documents.append({"status": "unexamined-attachment", "coverageUnknown": True,
                          "reason": "SEC filing index contains unsupported attachment formats",
                          "names": unexamined, "candidates": [], "ambiguities": []})
    cache_status = cache.get("status") if isinstance(cache, dict) else None
    if cache_status != "ok":
        access_unknown = True
    for item in _document_entries(filing):
        if not isinstance(item, dict):
            documents.append({"status": "unreadable", "reason": "scanner document entry is malformed"})
            access_unknown = True
            continue
        identity_error = _archive_identity_error(item, target, filing)
        if identity_error:
            documents.append({"name": item.get("name"), "url": item.get("url"),
                              "status": "source-identity-mismatch", "reason": identity_error,
                              "candidates": [], "ambiguities": []})
            access_unknown = True
            continue
        text, body_info = _read_verified_document(item, cache_dir, manifest_dir)
        body_info["name"] = item.get("name")
        if text is None:
            body_info.update({"candidates": [], "ambiguities": []})
            documents.append(body_info)
            access_unknown = True
            continue
        if Path(str(item.get("name") or "")).suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            documents.append(dict(body_info, status="not-machine-readable", coverageUnknown=True,
                                  reason="PDF or image attachment requires source review; HTML parsing cannot establish EPS absence",
                                  candidates=[], ambiguities=[]))
            access_unknown = True
            continue
        body_info["financialDocument"] = financial_document
        body_info["scanGeneratedAt"] = target.get("scanGeneratedAt")
        body_info["targetPeriodStart"] = target.get("start")
        filing_for_parse = dict(filing, targetCik=target.get("cik"))
        extraction = _extract_from_document(text, target_net_income=financial_period.get("netIncome"),
                                            period_end=target.get("end"), filing=filing_for_parse,
                                            item=item, body_info=body_info)
        body_info.pop("financialDocument", None)
        documents.append(dict(body_info, **extraction))
        if extraction.get("status") in ("ambiguous", "candidate-with-ambiguity", "parser-error"):
            access_unknown = True
    if not _document_entries(filing):
        status = cache_status or "missing-cache-result"
        access_unknown = True
        reason = f"filing has no cached documents (cache status {status!r})"
    elif cache_status != "ok":
        reason = f"scanner cache result is incomplete (cache status {cache_status!r})"
    else:
        reason = None
    return {"accession": filing.get("accessionNumber"), "form": filing.get("form"),
            "filingDate": filing.get("filingDate"), "matchReasons": filing.get("matchReasons") or [],
            "cacheStatus": cache_status,
            "documents": documents, "coverageUnknown": access_unknown, "reason": reason}


def _period_existing(target, financial_document):
    period, status = _financial_period(financial_document, target)
    if period is not None:
        return period, status, period["values"]
    snapshot = target.get("existingValues") if isinstance(target.get("existingValues"), dict) else {}
    return None, status, {field: snapshot.get(field) for field in FIELDS}


def _unknown_versions(unknown):
    diagnostic_statuses = {"pdf-unreadable", "unreadable", "unreadable-format", "hash-mismatch",
                           "missing-sha256", "unsupported-format", "parser-error", "ambiguous",
                           "candidate-with-ambiguity", "source-identity-mismatch"}
    return [{"accession": row.get("accession"), "form": row.get("form"),
             "filingDate": row.get("filingDate"), "reason": row.get("reason"),
             "documents": [{"name": doc.get("name"), "status": doc.get("status"),
                            "reason": doc.get("reason")}
                           for doc in row.get("documents", [])
                           if doc.get("status") in diagnostic_statuses]}
            for row in unknown]


def _field_comparison(field, target, existing_value, existing_status, versions, filings, snapshot_changed):
    candidate_rows = [candidate for version in versions for candidate in version.get("candidates", [])
                      if candidate.get("field") == field]
    base = {"field": field, "existingValue": existing_value,
            "existingValueSource": "financial-file-current" if existing_status == "ok" else "scan-manifest-snapshot",
            "candidates": candidate_rows,
            "versions": [{key: version.get(key) for key in ("accession", "form", "filingDate", "value", "status", "reason")}
                         for version in versions]}
    unknown = [filing for filing in filings if filing.get("coverageUnknown")]
    if not candidate_rows:
        if unknown:
            detail = next((row.get("reason") for row in unknown
                           if "historical-issuer-lineage-unverified" in str(row.get("reason") or "")), None)
            base.update(status="manual-review", reason=detail or
                        "SEC source coverage is incomplete or structurally ambiguous",
                        unknownVersions=_unknown_versions(unknown))
        else:
            base.update(status="no-result", reason="no cached filing version produced an EPS value")
        return base, None

    if snapshot_changed:
        base.update(status="manual-review", reason="financial value changed since the scanner manifest snapshot")
        return base, None
    if existing_status != "ok":
        base.update(status="manual-review", reason=f"current financial row is unavailable: {existing_status}")
        return base, None
    if unknown:
        detail = next((row.get("reason") for row in unknown
                       if "historical-issuer-lineage-unverified" in str(row.get("reason") or "")), None)
        base.update(status="manual-review", reason=detail or
                    "at least one SEC candidate version/document is unreadable or ambiguous",
                    unknownVersions=_unknown_versions(unknown))
        return base, None

    diagnostic_flags = {"diluted-eps-sign-differs-from-net-income",
                        "net-income-does-not-match-stored-quarter-value",
                        "net-income-target-match-is-ambiguous",
                        "net-income-divided-by-shares-differs-from-diluted-eps"}
    def has_only_diagnostic_flags(candidate):
        flags = set(candidate.get("quality", {}).get("reviewFlags", []))
        return bool(flags) and flags <= diagnostic_flags

    bad_quality = [candidate for candidate in candidate_rows
                   if not candidate.get("quality", {}).get("uniqueCandidate")
                   or (not candidate.get("quality", {}).get("candidateValidated")
                       and not has_only_diagnostic_flags(candidate))
                   or (candidate.get("quality", {}).get("manualReviewRequired")
                       and not has_only_diagnostic_flags(candidate))]
    if bad_quality:
        base.update(status="manual-review", reason="one or more parser candidates require manual review")
        return base, None
    split_pending = [candidate for candidate in candidate_rows
                     if candidate.get("splitNormalization", {}).get("status") == "pending"
                     or candidate.get("normalizedValue") is None]
    if split_pending:
        base.update(status="manual-review", reason="split-normalization basis is uncertain",
                    pendingSplitSources=[candidate.get("provenance") for candidate in split_pending])
        return base, None
    values = [candidate.get("normalizedValue") for candidate in candidate_rows]
    unique_values = []
    for value in values:
        if not any(_same_number(value, prior) for prior in unique_values):
            unique_values.append(value)
    if len(unique_values) != 1:
        base.update(status="manual-review", reason="SEC filing versions or tables disagree",
                    versionValues=[{"accession": candidate.get("provenance", {}).get("accession"),
                                    "form": candidate.get("provenance", {}).get("form"),
                                    "filingDate": candidate.get("provenance", {}).get("filingDate"),
                                    "reportedValue": candidate.get("reportedValue"),
                                    "normalizedValue": candidate.get("normalizedValue")}
                                   for candidate in candidate_rows])
        return base, None

    diagnostic_candidates = [candidate for candidate in candidate_rows
                             if set(candidate.get("quality", {}).get("reviewFlags", [])) & diagnostic_flags]
    if diagnostic_candidates:
        corroborating_accessions = {candidate.get("provenance", {}).get("accession")
                                    for candidate in candidate_rows
                                    if candidate.get("provenance", {}).get("accession")}
        base["diagnosticAdjudication"] = {
            "status": "formula-diagnostic-only",
            "accessions": sorted(corroborating_accessions),
            "flags": sorted({flag for candidate in diagnostic_candidates
                             for flag in candidate.get("quality", {}).get("reviewFlags", [])}),
            "decision": "unique original SEC disclosure and filing versions govern; formula/sign checks do not veto the reported EPS",
        }

    consensus = unique_values[0]
    base["consensusValue"] = consensus
    base["secVersionsCompared"] = sorted({str(candidate.get("provenance", {}).get("accession"))
                                          for candidate in candidate_rows})
    if existing_value is None:
        action = "fill-null"
    elif _same_number(existing_value, consensus):
        action = "exact-source-confirmation"
    else:
        base.update(status="manual-review", reason="SEC consensus conflicts with the existing financial value")
        return base, None
    staged = {
        "symbol": target.get("symbol"), "fy": target.get("fy"), "periodEnd": target.get("end"),
        "field": field, "grade": "A", "action": action,
        "existingValue": existing_value, "proposedValue": consensus,
        "secVersionsCompared": base["secVersionsCompared"],
        "splitNormalizationSource": "financial-document.split.events",
        "provenance": [candidate.get("provenance") for candidate in candidate_rows],
        "splitNormalization": [candidate.get("splitNormalization") for candidate in candidate_rows],
        "parserQuality": [candidate.get("quality") for candidate in candidate_rows],
    }
    if base.get("diagnosticAdjudication"):
        staged["diagnosticAdjudication"] = base["diagnosticAdjudication"]
    base.update(status="staged", action=action, grade="A", reason=None)
    return base, staged


def audit_period(symbol, target, financial_document, cache_dir, manifest_dir):
    target = dict(target)
    target["symbol"] = symbol
    period, financial_status, existing_values = _period_existing(target, financial_document)
    snapshot = target.get("existingValues") if isinstance(target.get("existingValues"), dict) else {}
    snapshot_changed = financial_status == "ok" and any(
        not _same_number(existing_values[field], snapshot.get(field))
        for field in FIELDS if existing_values[field] is not None or snapshot.get(field) is not None)
    net_income = period.get("netIncome") if period else None
    filings = []
    versions = []
    if target.get("issuerIdentityVerified") is not True:
        filings.append({"accession": None, "form": "historical issuer identity", "filingDate": None,
                        "cacheStatus": "unverified", "coverageUnknown": True,
                        "reason": "historical-issuer-lineage-unverified: source CIK is not independently matched to the official ticker map",
                        "documents": []})
    trajectory = target.get("searchTrajectory") if isinstance(target.get("searchTrajectory"), list) else []
    submission = next((row for row in trajectory if isinstance(row, dict) and row.get("stage") == "submissions"), None)
    historical = [row for row in trajectory if isinstance(row, dict) and row.get("stage") == "historical-blocks"]
    source_coverage_complete = (
        target.get("sourceCoverageComplete") is True
        and target.get("sourceCoverageFresh") is True
        and submission is not None
        and submission.get("status") in {"fetched", "cached"}
        and submission.get("refreshRequested") is True
        and bool(target.get("sourceCoverageRefreshGeneration"))
        and target.get("sourceCoverageRefreshGeneration") == submission.get("refreshGeneration")
        and len(historical) == 1 and historical[0].get("complete") is True
    )
    if not source_coverage_complete:
        failures = target.get("sourceCoverageFailures")
        if failures is None:
            failures = historical[0].get("failures") if historical else []
        filings.append({"accession": None, "form": "SEC submissions history", "filingDate": None,
                        "cacheStatus": "incomplete", "coverageUnknown": True,
                        "reason": "SEC submissions recent/history blocks were missing, incomplete, or not force-refreshed",
                        "coverageFailures": failures or [], "documents": []})
    for filing in target.get("candidateFilings") or []:
        if not isinstance(filing, dict):
            filings.append({"status": "unreadable", "reason": "candidate filing entry is malformed",
                            "coverageUnknown": True, "documents": []})
            continue
        filing_result = _audit_filing(filing, target,
                                      financial_document if isinstance(financial_document, dict) else {},
                                      {"netIncome": net_income}, cache_dir, manifest_dir)
        filings.append(filing_result)
        per_accession = []
        for doc in filing_result.get("documents", []):
            per_accession.extend(doc.get("candidates") or [])
        for field in FIELDS:
            field_candidates = [candidate for candidate in per_accession if candidate.get("field") == field]
            if not field_candidates:
                continue
            vals = []
            for candidate in field_candidates:
                val = candidate.get("normalizedValue")
                if not any(_same_number(val, old) for old in vals):
                    vals.append(val)
            versions.append({"accession": filing.get("accessionNumber"), "form": filing.get("form"),
                             "filingDate": filing.get("filingDate"), "field": field,
                             "value": vals[0] if len(vals) == 1 else None,
                             "status": "candidate" if len(vals) == 1 else "ambiguous",
                             "reason": None if len(vals) == 1 else "documents/tables in this accession disagree",
                             "candidates": field_candidates})
    staged = []
    comparisons = {}
    for field in FIELDS:
        field_versions = [version for version in versions if version["field"] == field]
        comparison, stage = _field_comparison(field, target, existing_values[field], financial_status,
                                              field_versions, filings, snapshot_changed)
        comparisons[field] = comparison
        if stage:
            staged.append(stage)
    return {
        "symbol": symbol, "fy": target.get("fy"), "periodEnd": target.get("end"),
        "financialPeriodStatus": financial_status,
        "manifestExistingValues": snapshot,
        "existingValues": existing_values,
        "manifestSnapshotChanged": snapshot_changed,
        "filings": filings,
        "fieldComparisons": comparisons,
        "stagedDecisions": staged,
    }


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(*, manifest_dir=DEFAULT_MANIFEST_DIR, cache_dir=DEFAULT_CACHE_DIR,
        financials_dir=DEFAULT_FINANCIALS_DIR, output_path=DEFAULT_REPORT):
    """Read scanner manifests/cache and financial data; write a review report only."""
    manifest_dir, cache_dir, financials_dir = Path(manifest_dir), Path(cache_dir), Path(financials_dir)
    companies, staged, errors = [], [], []
    manifest_paths = sorted(manifest_dir.glob("*.json")) if manifest_dir.is_dir() else []
    for manifest_path in manifest_paths:
        manifest = _load_json(manifest_path)
        if (not isinstance(manifest, dict) or manifest.get("schema") != "q4-eps-sec-scan-v1"
                or manifest.get("candidateRulesVersion") != CANDIDATE_RULES_VERSION):
            errors.append({"manifest": str(manifest_path), "reason": "invalid or unsupported scanner manifest"})
            continue
        symbol = str(manifest.get("symbol") or manifest_path.stem)
        financial_document = _load_json(financials_dir / f"{symbol}.json")
        period_rows = []
        for target in manifest.get("periods") or []:
            if not isinstance(target, dict):
                errors.append({"manifest": str(manifest_path), "reason": "malformed period entry"})
                continue
            target = dict(target)
            target["scanGeneratedAt"] = manifest.get("generatedAt")
            audited = audit_period(symbol, target, financial_document, cache_dir, manifest_dir)
            audited["sourceManifest"] = str(manifest_path)
            period_rows.append(audited)
            staged.extend(audited["stagedDecisions"])
        companies.append({"symbol": symbol, "sourceManifest": str(manifest_path), "periods": period_rows})
    report = {
        "schema": SCHEMA,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "offline": True,
        "financialJsonModified": False,
        "inputs": {"manifestDirectory": str(manifest_dir), "cacheDirectory": str(cache_dir),
                   "financialsDirectory": str(financials_dir)},
        "summary": {"manifests": len(manifest_paths), "companies": len(companies),
                    "periods": sum(len(company["periods"]) for company in companies),
                    "stagedGradeA": len(staged), "errors": len(errors)},
        "companies": companies,
        "stagedDecisions": staged,
        "errors": errors,
        "stagingPolicy": "Only unanimous, validated Grade A SEC table evidence can stage fill-null or exact-source-confirmation decisions. No financial JSON is changed.",
    }
    if output_path is not None:
        _atomic_json(output_path, report)
        report["reportPath"] = str(output_path)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFEST_DIR,
                        help="q4_eps_sec_scan results directory (no SEC requests are made)")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
                        help="scanner cache root used to resolve relative cache paths")
    parser.add_argument("--dir", type=Path, default=DEFAULT_FINANCIALS_DIR,
                        help="financial JSON directory, read-only")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT,
                        help="staging report destination")
    args = parser.parse_args(argv)
    report = run(manifest_dir=args.manifest_dir, cache_dir=args.cache_dir,
                 financials_dir=args.dir, output_path=args.output)
    print(json.dumps({"status": "done", **report["summary"], "reportPath": report.get("reportPath")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
