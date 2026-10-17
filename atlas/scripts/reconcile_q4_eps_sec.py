#!/usr/bin/env python3
"""Stage and apply per-period grade-A SEC Q4 EPS evidence from resumable caches.

No network requests are made here. ``fetch_q4_eps_companyfacts.py`` downloads only
companies with a missing Q4 field; this script also reuses the earlier Q4-only cache.
Staging validates all candidates, reports the SEC comparison gate, and writes an
atomic JSONL review file. Applying rechecks every old cell before changing any file.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_financials as fin  # noqa: E402
import repair_q4_eps as repair  # noqa: E402
import seed_financials_sec as sec  # noqa: E402
import q4_eps_evidence as evidence  # noqa: E402
from build_valuation_us import read_json, write_atomic  # noqa: E402

DEFAULT_DIR = REPO_ROOT / "docs/report/financials"
DEFAULT_OLD_STATE = REPO_ROOT / ".cache/q4-eps-sec-state.json"
DEFAULT_RAW_STATE = REPO_ROOT / ".cache/q4-eps-evidence/state.json"
DEFAULT_RAW_FACTS = REPO_ROOT / ".cache/q4-eps-evidence/facts"
DEFAULT_STAGE = REPO_ROOT / "docs/report/financials/q4-eps-sec-A-decisions.jsonl"
SEC_MEDIAN_MAX = 0.01
MAX_RELATIVE_ERROR = 0.25
CLASS_AMBIGUOUS_DIRECT_FACTS = {("0001067983", "BRK-B", "epsBasic")}


def _number(value):
    return repair._number(value)


def _q4_start(document, fy, end):
    q = document.get("quarterly") or {}
    prev = [(q.get("end") or [])[i] for i, (y, fq) in enumerate(zip(q.get("fy") or [], q.get("fq") or []))
            if y == fy and fq == 3 and i < len(q.get("end") or [])]
    if len(prev) == 1:
        try:
            return (date.fromisoformat(prev[0]) + timedelta(days=1)).isoformat()
        except ValueError:
            pass
    try:
        return (date.fromisoformat(end) - timedelta(days=89)).isoformat()
    except (TypeError, ValueError):
        return None


def _q3_period(document, fy):
    quarterly = document.get("quarterly") or {}
    matches = [index for index, (row_fy, fq) in enumerate(zip(
        quarterly.get("fy") or [], quarterly.get("fq") or []))
        if row_fy == fy and fq == 3]
    if len(matches) != 1:
        return None, None
    q3_index = matches[0]
    q3_end = (quarterly.get("end") or [None] * (q3_index + 1))[q3_index]
    q2_matches = [index for index, (row_fy, fq) in enumerate(zip(
        quarterly.get("fy") or [], quarterly.get("fq") or []))
        if row_fy == fy and fq == 2]
    if len(q2_matches) != 1:
        return None, q3_end
    q2_index = q2_matches[0]
    q2_end = (quarterly.get("end") or [None] * (q2_index + 1))[q2_index]
    try:
        q3_start = (date.fromisoformat(q2_end) + timedelta(days=1)).isoformat()
    except (TypeError, ValueError):
        return None, q3_end
    return (q3_start, q3_end), q3_index


def _select_share_fact(raw_company, concept, q3_start, q3_end, cutoff, preferred_accession=None):
    if not isinstance(raw_company, dict):
        return None
    rows = ((raw_company.get("concepts") or {}).get(concept) or [])
    eligible = [fact for fact in rows if fact.get("unit") == "shares"
                and fact.get("start") == q3_start and fact.get("end") == q3_end
                and _number(fact.get("value")) is not None and _number(fact.get("value")) > 0
                and fact.get("filed") and fact.get("accession")
                and (cutoff is None or str(fact.get("filed")) <= cutoff)]
    if preferred_accession:
        same = [fact for fact in eligible if fact.get("accession") == preferred_accession]
        if same:
            eligible = same
    if not eligible:
        return None
    latest_filed = max(str(fact.get("filed")) for fact in eligible)
    eligible = [fact for fact in eligible if str(fact.get("filed")) == latest_filed]
    latest_accession = sorted(str(fact.get("accession")) for fact in eligible)[-1]
    eligible = [fact for fact in eligible if str(fact.get("accession")) == latest_accession]
    values = {_number(fact.get("value")) for fact in eligible}
    return eligible[0] if len(values) == 1 else None


def _split_events(document):
    events = []
    for event in ((document.get("split") or {}).get("events") or []):
        if not isinstance(event, (list, tuple)) or len(event) < 2:
            continue
        split_day, ratio = repair._parse_date(event[0]), _number(event[1])
        if split_day is None or ratio is None or ratio <= 0:
            continue
        if ratio >= sec.MIN_SPLIT_RATIO or ratio <= 1 / sec.MIN_SPLIT_RATIO:
            events.append((split_day.isoformat(), ratio))
    return sorted(events)


def _basic_q3_shares(document, fy, raw_company, candidate):
    """Resolve Q3 basic shares in the current file basis without borrowing diluted shares."""
    period, q3_index = _q3_period(document, fy)
    if period is None:
        return None, None
    q3_start, q3_end = period
    q = document.get("quarterly") or {}
    stored_basic = _number(repair._column_value(q.get("values") or {}, "basicShares", q3_index))
    if stored_basic is not None and stored_basic > 0:
        return stored_basic, {
            "period": {"start": q3_start, "end": q3_end},
            "concept": "basicShares",
            "unit": "shares",
            "value": stored_basic,
            "source": "financials-quarterly-basicShares",
            "splitBasis": "current financials basis",
        }
    cutoff = candidate.get("filed")
    basic = _select_share_fact(raw_company, "WeightedAverageNumberOfSharesOutstandingBasic",
                               q3_start, q3_end, cutoff, candidate.get("accession"))
    diluted = _select_share_fact(raw_company, "WeightedAverageNumberOfDilutedSharesOutstanding",
                                 q3_start, q3_end, cutoff, candidate.get("accession"))
    if basic is None:
        return None, {"period": {"start": q3_start, "end": q3_end}, "failureReason": "missing-or-conflicting-q3-basic-shares"}

    current_diluted = _number(repair._column_value(q.get("values") or {}, "dilutedShares", q3_index))
    raw_diluted = _number((diluted or {}).get("value"))
    events = _split_events(document)
    factor = 1.0
    if current_diluted and raw_diluted and raw_diluted > 0:
        factor = sec._suffix_factor(events, q3_end, current_diluted / raw_diluted)
    elif cutoff:
        for split_day, ratio in events:
            if split_day > cutoff:
                factor *= ratio
    adjusted = _number(basic.get("value")) * factor
    return adjusted, {
        "period": {"start": q3_start, "end": q3_end},
        "concept": "WeightedAverageNumberOfSharesOutstandingBasic",
        "unit": "shares",
        "rawValue": basic.get("value"),
        "value": adjusted,
        "filed": basic.get("filed"),
        "form": basic.get("form"),
        "accession": basic.get("accession"),
        "splitFactor": factor,
        "splitBasis": "current financials basis; inferred with seed _suffix_factor using same-period diluted-share basis",
    }


def _company_signature(document):
    rows = [(end, fy) for fy, fq, end in zip(
        (document.get("quarterly") or {}).get("fy") or [],
        (document.get("quarterly") or {}).get("fq") or [],
        (document.get("quarterly") or {}).get("end") or [],
    ) if fq == 4]
    return repair._signature([{"end": end, "fy": fy} for end, fy in rows])


def _compact_to_companyfacts(compact):
    concepts = {}
    for name, rows in (compact.get("concepts") or {}).items():
        units = defaultdict(list)
        for fact in rows:
            units[str(fact.get("unit"))].append({
                "val": fact.get("value"), "start": fact.get("start"), "end": fact.get("end"),
                "form": fact.get("form"), "filed": fact.get("filed"), "accn": fact.get("accession"),
                "frame": fact.get("frame"),
            })
        concepts[name] = {"units": dict(units)}
    return {"cik": int(compact["cik"]), "facts": {"us-gaap": concepts}}


def _old_cache_companyfacts(old_state, symbol, signature):
    company = (old_state.get("facts") or {}).get(symbol) or {}
    if company.get("signature") != signature or company.get("status") != "ok":
        return None
    facts = defaultdict(lambda: defaultdict(list))
    for end, fields in (company.get("q4") or {}).items():
        for field, rows in fields.items():
            concept = evidence.DIRECT_EPS_CONCEPTS.get(field)
            if not concept:
                continue
            for item in rows if isinstance(rows, list) else []:
                facts[concept]["USD/shares"].append({
                    "val": item.get("value"), "start": item.get("start"), "end": item.get("end"),
                    "form": item.get("form"), "filed": item.get("filed"), "accn": item.get("accn"),
                    "frame": item.get("frame"),
                })
    return {"cik": int((old_state.get("tickerMap") or {}).get(symbol, 0) or 0),
            "facts": {"us-gaap": {name: {"units": dict(units)} for name, units in facts.items()}}}


def _existing_trace_candidate(trace, *, field, cik, companyfacts_url):
    if not isinstance(trace, dict) or trace.get("concept") != evidence.DIRECT_EPS_CONCEPTS.get(field):
        return None
    return {
        "field": field,
        "metric": "basic" if field == "epsBasic" else "diluted",
        "concept": trace.get("concept"), "unit": trace.get("unit"),
        "rawValue": str(trace.get("reportedValue")),
        "start": trace.get("start"), "end": trace.get("end"),
        "durationDays": trace.get("durationDays"), "targetEndDistanceDays": 0,
        "cik": cik, "accession": trace.get("accession"), "filed": trace.get("filed"),
        "form": trace.get("form"), "url": companyfacts_url,
        "filingUrl": evidence._filing_url(cik, trace.get("accession")),
        "frame": None, "basis": "as-reported-at-filing",
        "version": {"filed": trace.get("filed"), "form": trace.get("form"),
                    "accession": trace.get("accession")},
    }


def _source(candidate, *, raw_company=None, split_factor=1, current_basis="stored financials share basis",
            document=None):
    filed = str(candidate.get("filed") or "")
    split_events = [
        {"date": day, "ratio": ratio}
        for day, ratio in _split_events(document or {})
        if filed and day > filed
    ]
    symbol = str((document or {}).get("symbol") or "")
    split_trace = {
        "provider": "Yahoo Finance Chart API events=split (independent of EPS values)",
        "url": f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?events=split&range=max&interval=1mo"
        if symbol else None,
        "eventsAfterFiling": split_events,
        "rawPayloadSha256": None,
    }
    return {
        "provider": "SEC EDGAR companyfacts",
        "url": candidate.get("url"),
        "filingUrl": candidate.get("filingUrl"),
        "cik": candidate.get("cik"),
        "accession": candidate.get("accession"),
        "filed": candidate.get("filed"),
        "form": candidate.get("form"),
        "start": candidate.get("start"),
        "end": candidate.get("end"),
        "concept": candidate.get("concept"),
        "unit": candidate.get("unit"),
        "rawValue": candidate.get("rawValue"),
        "frame": candidate.get("frame"),
        "version": candidate.get("version"),
        "companyfactsPayloadSha256": (raw_company or {}).get("canonicalPayloadSha256"),
        "splitBasis": {
            "basisBeforeAdjustment": "as-reported-at-filing",
            "basisAfterAdjustment": current_basis,
            "splitFactorDivisor": split_factor,
            "eventTrace": split_trace,
        },
    }


def _write_jsonl_atomic(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _read_jsonl(path):
    with Path(path).open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def stage_directory(directory=DEFAULT_DIR, *, old_state_path=DEFAULT_OLD_STATE,
                    raw_state_path=DEFAULT_RAW_STATE, raw_facts_dir=DEFAULT_RAW_FACTS):
    old_state = read_json(old_state_path) if Path(old_state_path).exists() else {}
    raw_state = read_json(raw_state_path) if Path(raw_state_path).exists() else {}
    ticker_map = raw_state.get("tickerMap") or old_state.get("tickerMap") or {}
    summary = Counter()
    records, comparisons, abs_errors = [], [], []
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            document = read_json(path)
            fin.read_sections(document)
        except (OSError, TypeError, ValueError):
            summary["corrupt"] += 1
            continue
        if document.get("market") != "US":
            continue
        rows = repair._q4_rows(document)
        if not rows:
            continue
        symbol = path.stem
        cik = ticker_map.get(symbol)
        if not cik:
            summary["no-cik"] += sum(1 for row in rows if any(row["values"].get(f) is None for f in repair.EPS_CONCEPTS))
            continue
        cik = sec.cik10(cik)
        companyfacts_url = sec.FACTS_URL.format(cik=cik)
        signature = repair._signature(rows)
        raw_company = None
        raw_path = Path(raw_facts_dir) / f"{symbol}.json"
        raw_entry = (raw_state.get("companies") or {}).get(symbol) or {}
        if raw_path.exists() and raw_entry.get("signature") == signature and raw_entry.get("status") == "ok":
            raw_company = read_json(raw_path)
            payload = _compact_to_companyfacts(raw_company)
        else:
            payload = _old_cache_companyfacts(old_state, symbol, signature)
        if payload is not None and not payload.get("cik"):
            payload["cik"] = int(cik)
        if payload is None:
            summary["no-companyfacts-cache"] += sum(1 for row in rows if any(row["values"].get(f) is None for f in repair.EPS_CONCEPTS))
            continue
        q = document.get("quarterly") or {}
        qvalues = q.get("values") or {}
        for row in rows:
            start = _q4_start(document, row["fy"], row["end"])
            if start is None:
                summary["invalid-q4-start"] += 1
                continue
            income = repair._column_value(qvalues, "netIncome", row["index"])
            if document.get("currency") != "USD" or document.get("foreignReporter") is True:
                for field in repair.EPS_CONCEPTS:
                    if row["values"].get(field) is None:
                        records.append({"symbol": symbol, "cik": cik, "periodEnd": row["end"],
                                        "field": field, "grade": "D", "status": "rejected",
                                        "failureReason": "foreign-reporter-or-currency-basis-needs-separate-validation"})
                        summary["foreign-basis-rejected"] += 1
                continue
            try:
                candidates = evidence.extract_direct_q4_candidates(
                    payload, cik=cik, period_end=row["end"], companyfacts_url=companyfacts_url)
            except evidence.EvidenceError as error:
                summary[f"companyfacts-{error.reason}"] += 1
                continue
            trace_for_period = (document.get("secQ4EpsFacts") or {}).get(row["end"], {})
            for field in repair.EPS_CONCEPTS:
                candidate, candidate_reason = evidence.select_direct_q4_candidate(
                    candidates[field], period_start=start, period_end=row["end"])
                if candidate is None:
                    candidate = _existing_trace_candidate(trace_for_period.get(field), field=field,
                                                          cik=cik, companyfacts_url=companyfacts_url)
                if candidate is None:
                    if row["values"].get(field) is None:
                        summary[f"no-direct-fact-{field}"] += 1
                    continue
                if (cik, symbol, field) in CLASS_AMBIGUOUS_DIRECT_FACTS:
                    summary["rejected-share-class-basis-unknown"] += 1
                    records.append({
                        "schema": evidence.SCHEMA, "symbol": symbol, "cik": cik,
                        "period": {"fiscalYear": row["fy"], "start": start, "end": row["end"]},
                        "field": field, "oldValue": row["values"].get(field), "value": None,
                        "grade": None, "status": "pending-share-class-review", "method": "direct-sec-companyfacts",
                        "source": _source(candidate, raw_company=raw_company, document=document),
                        "validation": {"passed": False, "failureReason": "companyfacts-does-not-disambiguate-berkshire-a-and-b-share-classes"},
                    })
                    continue
                if field == "epsBasic":
                    q3_shares, q3_share_trace = _basic_q3_shares(
                        document, row["fy"], raw_company, candidate)
                else:
                    q3_shares = _number(repair._q3_diluted_shares(document, row["fy"]))
                    q3_share_trace = {
                        "concept": "dilutedShares", "unit": "shares",
                        "value": q3_shares,
                        "source": "financials-quarterly-dilutedShares",
                        "splitBasis": "current financials basis",
                    } if q3_shares is not None else None
                fact = {"value": _number(candidate.get("rawValue")), "concept": candidate.get("concept"),
                        "unit": candidate.get("unit"), "start": candidate.get("start"),
                        "end": candidate.get("end"), "form": candidate.get("form"),
                        "filed": candidate.get("filed"), "accn": candidate.get("accession")}
                adjusted, split_factor, rejected = repair.normalize_and_check_q4_eps(
                    document, row["end"], income, q3_shares, fact)
                old_value = row["values"].get(field)
                if rejected:
                    summary[f"pending-{rejected}"] += 1
                    records.append({"schema": evidence.SCHEMA, "symbol": symbol, "cik": cik,
                                    "period": {"start": start, "end": row["end"]}, "field": field,
                                    "oldValue": old_value, "value": None, "grade": None,
                                    "status": "pending-split-basis-review", "method": "direct-sec-companyfacts",
                                    "source": _source(candidate, raw_company=raw_company,
                                                      split_factor=split_factor, document=document),
                                    "validation": {"passed": False, "failureReason": rejected}})
                    continue
                cross_check = repair.q4_eps_diagnostics(adjusted, income, q3_shares)
                expected = cross_check["impliedEps"]
                relative = cross_check["relativeDifference"]
                comparison = None
                if old_value is not None:
                    comparison = abs(adjusted - _number(old_value)) / max(abs(_number(old_value) or 0), 0.10)
                    comparisons.append(comparison)
                    abs_errors.append(abs(adjusted - float(old_value)))
                # CompanyFacts identifies the filing accession and XBRL concept, but it omits the
                # original context/table coordinates needed to disambiguate dimensions, labels,
                # and share basis. Keep this as a candidate until the raw filing scanner confirms it.
                action = "needs-original-filing-review"
                records.append({
                    "schema": evidence.SCHEMA,
                    "symbol": symbol,
                    "cik": cik,
                    "period": {"fiscalYear": row["fy"], "start": start, "end": row["end"]},
                    "field": field,
                    "oldValue": old_value,
                    "value": round(adjusted, 4),
                    "grade": None,
                    "status": action,
                    "method": "direct-sec-companyfacts",
                    "source": _source(candidate, raw_company=raw_company,
                                      split_factor=split_factor, document=document),
                    "crossCheck": {
                        "netIncome": income,
                        "q3Shares": q3_shares,
                        "q3ShareSource": q3_share_trace,
                        "impliedEps": expected,
                        "relativeError": relative,
                        "maximumRelativeError": MAX_RELATIVE_ERROR,
                        "sameSign": cross_check["sameSign"],
                        "diagnosticFlags": cross_check["flags"],
                    },
                    "validation": {
                        "passed": False,
                        "failureReason": "original-sec-filing-context-not-yet-verified",
                        "existingValueComparisonError": comparison,
                        "absoluteEpsError": None if old_value is None else abs(adjusted - float(old_value)),
                    },
                })
                summary[action] += 1
    ordered = sorted(comparisons)
    median = None
    if ordered:
        middle = len(ordered) // 2
        median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    summary["comparisonCount"] = len(comparisons)
    summary["comparisonMedianError"] = median
    if abs_errors:
        abs_errors.sort()
        mid = len(abs_errors) // 2
        summary["comparisonMedianAbsoluteEpsError"] = abs_errors[mid] if len(abs_errors) % 2 else (abs_errors[mid - 1] + abs_errors[mid]) / 2
    else:
        summary["comparisonMedianAbsoluteEpsError"] = None
    summary["thresholdPassed"] = median is not None and median <= SEC_MEDIAN_MAX
    return records, summary


def apply_records(records, directory=DEFAULT_DIR):
    """Fill nulls only when the original filing bytes and locator are revalidated at apply time."""
    candidates = [r for r in records if r.get("grade") == "A" and r.get("status") == "fill-null"]
    actionable = []
    for record in candidates:
        source = record.get("source") or {}
        target_period = record.get("period") if isinstance(record.get("period"), dict) else {}
        field = record.get("field")
        target_start = target_period.get("start")
        target_end = target_period.get("end")
        source_start, source_end = source.get("start"), source.get("end")
        raw = source.get("rawFilingEvidence")
        # The SEC context itself must be the target fiscal Q4, not merely any valid
        # 80–100 day quarter from the same issuer and accession.
        if (field not in evidence.DIRECT_EPS_CONCEPTS
                or not isinstance(target_start, str)
                or not isinstance(target_end, str)
                or source_start != target_start
                or source_end != target_end
                or not isinstance(raw, dict)
                or raw.get("periodStart") != source_start
                or raw.get("periodEnd") != source_end):
            continue
        financial_path = Path(directory) / f"{record.get('symbol')}.json"
        try:
            financial_document = read_json(financial_path)
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(financial_document, dict):
            continue
        split_factor = evidence._decimal((source.get("splitBasis") or {}).get("splitFactorDivisor"))
        expected_split_factor, split_failure = repair._verified_split_factor(
            financial_document, target_end, {"filed": source.get("filed")})
        if (split_failure is not None or split_factor is None
                or expected_split_factor is None
                or split_factor != evidence._decimal(expected_split_factor)):
            continue
        validation_error = evidence._validate_raw_filing_evidence(
            raw, candidate={"accession": source.get("accession"), "form": source.get("form"),
                            "cik": source.get("cik")},
            field=field, period_start=source_start,
            period_end=source_end, raw_value=source.get("rawValue"),
            split_multiplier=split_factor)
        if (validation_error is not None or raw.get("reportedBasis") is None
                or not (record.get("validation") or {}).get("passed")):
            continue
        raw_path = raw.get("rawBodyPath")
        if (not isinstance(raw_path, str) or not raw_path.strip()
                or raw.get("rawBodyUrl") != raw.get("url")):
            continue
        try:
            body = Path(raw_path).read_bytes()
        except OSError:
            continue
        digest = str(raw.get("sha256") or raw.get("filingSha256") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or hashlib.sha256(body).hexdigest() != digest:
            continue
        raw_value = evidence._decimal(source.get("rawValue"))
        try:
            stored_value = evidence._decimal(record.get("value"))
            expected_value = round(float(raw_value / split_factor), 6) if raw_value is not None and split_factor else None
        except (TypeError, ValueError, ZeroDivisionError, OverflowError):
            expected_value = None
        if stored_value is None or expected_value is None or float(stored_value) != expected_value:
            continue
        actionable.append(record)

    # Different source versions for one field are never resolved by input order.
    groups = defaultdict(list)
    for record in actionable:
        period = record.get("period") if isinstance(record.get("period"), dict) else {}
        groups[(record.get("symbol"), period.get("end"), record.get("field"))].append(record)
    conflicting = set()
    for key, rows in groups.items():
        values = {str(row.get("value")) for row in rows}
        if len(values) > 1:
            conflicting.add(key)
    if conflicting:
        actionable = [record for record in actionable
                      if (record.get("symbol"), (record.get("period") or {}).get("end"), record.get("field"))
                      not in conflicting]

    documents = {}
    locations = {}
    for record in actionable:
        path = Path(directory) / f"{record['symbol']}.json"
        document = documents.setdefault(record["symbol"], read_json(path))
        q = document.get("quarterly") or {}
        matches = [i for i, (fy, end, fq) in enumerate(zip(q.get("fy") or [], q.get("end") or [], q.get("fq") or []))
                   if fq == 4 and end == record["period"]["end"] and fy == record["period"].get("fiscalYear")]
        if len(matches) != 1:
            raise RuntimeError(f"staged period no longer unique: {record['symbol']} {record['period']}")
        index = matches[0]
        current = repair._column_value(q.get("values") or {}, record["field"], index)
        if record.get("oldValue") is not None or current is not None:
            raise RuntimeError(f"refusing to overwrite non-null EPS: {record['symbol']} {record['period']['end']} {record['field']}")
        locations[(record["symbol"], record["period"]["end"], record["field"])] = (index, record)

    for symbol, document in documents.items():
        q = document.get("quarterly") or {}
        values = q.setdefault("values", {})
        grades = q.get("epsQuality")
        if not isinstance(grades, list):
            grades = [None] * len(q.get("end") or [])
        while len(grades) < len(q.get("end") or []):
            grades.append(None)
        traces = document.setdefault("secQ4EpsFacts", {})
        for (record_symbol, end, field), (index, record) in locations.items():
            if record_symbol != symbol:
                continue
            column = values.setdefault(field, [None] * len(q.get("end") or []))
            while len(column) < len(q.get("end") or []):
                column.append(None)
            if column[index] is not None:
                raise RuntimeError(f"refusing to overwrite non-null EPS during apply: {symbol} {end} {field}")
            column[index] = record.get("value")
            values[field] = column
            grade = grades[index] if isinstance(grades[index], dict) else {}
            grade[field] = "A"
            grades[index] = grade
            src = record["source"]
            period_trace = traces.setdefault(end, {})
            period_trace[field] = {
                "concept": src["concept"], "unit": src["unit"], "start": src["start"], "end": src["end"],
                "durationDays": (date.fromisoformat(src["end"]) - date.fromisoformat(src["start"])).days,
                "form": src["form"], "filed": src["filed"], "accession": src["accession"],
                "reportedValue": _number(src["rawValue"]),
                "splitFactor": src.get("splitBasis", {}).get("splitFactorDivisor"),
                "value": record["value"], "currency": "USD", "grade": "A", "method": record["method"],
                "cik": src["cik"], "url": src["url"], "filingUrl": src["filingUrl"],
                "version": src["version"], "rawFilingEvidence": src["rawFilingEvidence"],
            }
        q["epsQuality"] = grades
        q["values"] = values
        document["quarterly"] = q
        write_atomic(str(Path(directory) / f"{symbol}.json"), document)
    return {"filesWritten": len(documents), "fieldsProcessed": len(actionable),
            "rejectedPriorAValuesDemotedToD": 0,
            "skippedMissingRawFilingEvidence": len(candidates) - len(actionable)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--old-state", type=Path, default=DEFAULT_OLD_STATE)
    parser.add_argument("--raw-state", type=Path, default=DEFAULT_RAW_STATE)
    parser.add_argument("--raw-facts", type=Path, default=DEFAULT_RAW_FACTS)
    parser.add_argument("--stage", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and args.stage.exists():
        records = _read_jsonl(args.stage)
        # Recompute the validation median from the staged comparison records.
        errors = [r["validation"].get("existingValueComparisonError") for r in records
                  if r.get("grade") == "A" and r.get("validation", {}).get("existingValueComparisonError") is not None]
        errors.sort()
        if not errors:
            print("STOPPED: no SEC comparison rows; no EPS written.")
            return 2
        middle = len(errors) // 2
        median = errors[middle] if len(errors) % 2 else (errors[middle - 1] + errors[middle]) / 2
        if median > SEC_MEDIAN_MAX:
            print(f"STOPPED: SEC median validation error {median:.4%} over {len(errors)} comparisons; threshold <=1%; no EPS written.")
            return 2
        result = apply_records(records, args.dir)
        print(json.dumps({"status": "applied", "comparisonCount": len(errors),
                          "medianError": median, **result}, ensure_ascii=False, sort_keys=True))
        return 0
    records, summary = stage_directory(args.dir, old_state_path=args.old_state,
                                       raw_state_path=args.raw_state, raw_facts_dir=args.raw_facts)
    _write_jsonl_atomic(args.stage, records)
    print(json.dumps({"status": "staged", "records": len(records), **dict(summary),
                      "stage": str(args.stage)}, ensure_ascii=False, sort_keys=True))
    if summary.get("comparisonCount", 0) == 0:
        print("STOPPED: no SEC comparison rows; no EPS written.")
        return 2
    if not summary.get("thresholdPassed"):
        print(f"STOPPED: SEC median validation error {summary['comparisonMedianError']:.4%} over "
              f"{summary['comparisonCount']} comparisons; threshold <=1%; no EPS written.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
