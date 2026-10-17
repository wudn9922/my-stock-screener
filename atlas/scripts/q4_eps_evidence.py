"""Pure evidence rules for Q4 EPS staging.

This module deliberately does not fetch data or write financial files. Callers supply
SEC companyfacts payloads and filing facts, then persist the returned staging records
only after a separate audit approves them. Basic and diluted EPS stay independent.

Direct records are grade A only when the fact is an exact us-gaap basic/diluted EPS
concept with the required USD/shares unit and Q4-like duration, and it passes the
same-sign / 25% net-income-to-Q3-shares cross-check. A grade B reconstruction is
limited to basic EPS: it differences common-attributable annual and nine-month income
and computes Q4 basic weighted-average shares from actual day-weighted annual and
nine-month averages. There is intentionally no diluted-share reconstruction path.

SEC companyfacts is entity-wide and does not expose all filing dimensions. A caller
must resolve any dimension, class attribution, preferred dividend, NCI, restatement,
and split-basis questions before supplying reconstruction facts with the explicit
metadata this module requires. Missing or uncertain metadata fails closed.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
from urllib.parse import urlparse
import re


SCHEMA = "q4-eps-evidence-staging-v1"
DIRECT_EPS_CONCEPTS = {
    "epsBasic": "EarningsPerShareBasic",
    "epsDiluted": "EarningsPerShareDiluted",
}
SHARES_BASIC_CONCEPT = "WeightedAverageNumberOfSharesOutstandingBasic"
SEC_DURATION_DAYS = (80, 100)  # XBRL start/end dates are inclusive.
SEC_END_TOLERANCE_DAYS = 5
DIRECT_CROSSCHECK_MAX_RELATIVE_ERROR = Decimal("0.25")
ALLOWED_ANNUAL_FORMS = {"10-K", "10-K/A"}
ALLOWED_NINE_MONTH_FORMS = {"10-Q", "10-Q/A"}


class EvidenceError(ValueError):
    """Validation error with a stable machine-readable reason."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _day(value):
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _decimal(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _decimal_text(value):
    """Stable, lossless decimal text suitable for JSON staging."""
    number = _decimal(value)
    if number is None:
        return None
    return format(number, "f")


def _cik(value):
    text = str(value or "").strip()
    if not text.isdigit() or len(text) > 10:
        return None
    return text.zfill(10)


def _official_url(url):
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in {
        "data.sec.gov", "www.sec.gov", "sec.gov"
    }


def _companyfacts_url(url, cik):
    if not _official_url(url):
        return False
    parsed = urlparse(url)
    match = re.fullmatch(r"/api/xbrl/companyfacts/CIK(\d{1,10})\.json", parsed.path)
    return bool(match and _cik(match.group(1)) == cik and parsed.hostname == "data.sec.gov")


def _filing_url(cik, accession):
    if not accession:
        return None
    accession = str(accession)
    compact = accession.replace("-", "")
    if not compact.isdigit():
        return None
    # Accession folders have issuer-chosen primary document names; do not fabricate
    # an accession-number.htm URL. The archive directory is a stable official index.
    return ("https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{compact}/")


def _version_key(fact):
    filed = _day(fact.get("filed"))
    accession = str(fact.get("accn") or fact.get("accession") or "")
    form = str(fact.get("form") or "").upper()
    amended = form.endswith("/A")
    # Most recent filing wins; amendments win a filed-date tie; accession provides a
    # stable tie-break for same-day filings. No filing is chosen by payload order.
    return (filed.toordinal() if filed else -1, amended, accession, form)


def _source_fact(fact, *, cik=None, default_url=None):
    """Copy the trace fields shared by direct and reconstructed facts."""
    fact_cik = _cik(fact.get("cik") or cik)
    accession = fact.get("accn") or fact.get("accession")
    url = fact.get("url") or default_url
    return {
        "cik": fact_cik,
        "accession": str(accession) if accession is not None else None,
        "filed": fact.get("filed"),
        "form": fact.get("form"),
        "start": fact.get("start"),
        "end": fact.get("end"),
        "concept": fact.get("concept"),
        "unit": fact.get("unit"),
        "value": _decimal_text(fact.get("val", fact.get("value"))),
        "url": url,
        "filingUrl": _filing_url(fact_cik, accession) if fact_cik else None,
        "basis": fact.get("shareBasis", fact.get("basis")),
        "version": {
            "filed": fact.get("filed"),
            "form": fact.get("form"),
            "accession": str(accession) if accession is not None else None,
        },
    }


def extract_direct_q4_candidates(companyfacts, *, cik, period_end, companyfacts_url):
    """Extract eligible, separate Basic/Diluted candidates from official companyfacts.

    Eligibility is exact unit ``USD/shares``, an inclusive 80–100 calendar-day
    duration, and an end date within five days of the fiscal Q4 end. Candidates retain
    every filing/version field needed for review. The function never chooses a value.
    Invalid issuer/source identity raises :class:`EvidenceError`.
    """
    normalized_cik = _cik(cik)
    target_end = _day(period_end)
    if normalized_cik is None:
        raise EvidenceError("invalid-cik")
    if target_end is None:
        raise EvidenceError("invalid-target-period-end")
    if not _companyfacts_url(companyfacts_url, normalized_cik):
        raise EvidenceError("nonofficial-or-mismatched-companyfacts-url")
    if not isinstance(companyfacts, dict):
        raise EvidenceError("invalid-companyfacts-payload")
    payload_cik = _cik(companyfacts.get("cik"))
    if payload_cik != normalized_cik:
        raise EvidenceError("companyfacts-cik-mismatch")

    us_gaap = ((companyfacts.get("facts") or {}).get("us-gaap") or {})
    result = {field: [] for field in DIRECT_EPS_CONCEPTS}
    for field, concept in DIRECT_EPS_CONCEPTS.items():
        concept_data = us_gaap.get(concept) or {}
        facts = ((concept_data.get("units") or {}).get("USD/shares") or [])
        if not isinstance(facts, list):
            continue
        for raw in facts:
            if not isinstance(raw, dict):
                continue
            start, end = _day(raw.get("start")), _day(raw.get("end"))
            value = _decimal(raw.get("val"))
            if start is None or end is None or value is None or end < start:
                continue
            duration = (end - start).days + 1
            if not SEC_DURATION_DAYS[0] <= duration <= SEC_DURATION_DAYS[1]:
                continue
            if abs((end - target_end).days) > SEC_END_TOLERANCE_DAYS:
                continue
            if not raw.get("accn") or not raw.get("filed") or not raw.get("form"):
                continue
            if _day(raw.get("filed")) is None:
                continue
            candidate = {
                "field": field,
                "metric": "basic" if field == "epsBasic" else "diluted",
                "concept": concept,
                "unit": "USD/shares",
                "rawValue": _decimal_text(value),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "durationDays": duration,
                "targetEndDistanceDays": abs((end - target_end).days),
                "cik": normalized_cik,
                "accession": str(raw["accn"]),
                "filed": raw["filed"],
                "form": str(raw["form"]).upper(),
                "url": companyfacts_url,
                "filingUrl": _filing_url(normalized_cik, raw["accn"]),
                "frame": raw.get("frame"),
                "basis": "as-reported-at-filing",
                "version": {
                    "filed": raw["filed"],
                    "form": str(raw["form"]).upper(),
                    "accession": str(raw["accn"]),
                },
            }
            result[field].append(candidate)
    return result


def select_direct_q4_candidate(candidates, *, period_start, period_end):
    """Select a filing version deterministically or return an explicit failure.

    Exact start/end alignment wins where available, followed by closest end, latest
    filed date, amendment, accession and form. Conflicting duplicates in the selected
    filing version fail closed; an older value is never silently used as fallback.
    """
    if not candidates:
        return None, "no-matching-direct-fact"
    target_start, target_end = _day(period_start), _day(period_end)
    if target_start is None or target_end is None or target_end < target_start:
        return None, "invalid-target-period"

    def rank(item):
        start, end = _day(item.get("start")), _day(item.get("end"))
        if start is None or end is None:
            return (999999, 999999, 1, 0, "")
        filed = _day(item.get("filed"))
        form = str(item.get("form") or "").upper()
        return (
            abs((start - target_start).days),
            abs((end - target_end).days),
            -_version_key(item)[0],
            -int(form.endswith("/A")),
            str(item.get("accession") or ""),
        )

    ordered = sorted(candidates, key=rank)
    best = ordered[0]
    selected_version = (best.get("start"), best.get("end"), best.get("filed"),
                        best.get("form"), best.get("accession"))
    same_version = [item for item in candidates if (
        item.get("start"), item.get("end"), item.get("filed"), item.get("form"),
        item.get("accession")) == selected_version]
    values = {_decimal_text(item.get("rawValue", item.get("value"))) for item in same_version}
    if len(values) > 1:
        return None, "conflicting-facts-in-selected-filing-version"
    return best, None


def _failure_record(*, symbol, cik, field, period_start, period_end, reason,
                    source=None, method="direct-sec-companyfacts"):
    return {
        "schema": SCHEMA,
        "symbol": symbol,
        "cik": _cik(cik),
        "period": {"start": period_start, "end": period_end},
        "field": field,
        "value": None,
        "currency": "USD",
        "unit": "USD/shares",
        "grade": None,
        "status": "rejected",
        "method": method,
        "source": source,
        "validation": {"passed": False, "failureReason": reason},
    }


def stage_direct_q4_eps(companyfacts, *, symbol, cik, period_start, period_end,
                        companyfacts_url, cross_checks, split_multiplier=1,
                        target_share_basis="current-share-basis", raw_filing_evidence=None):
    """Stage direct companyfacts candidates, requiring a matching original filing record for Grade A.

    ``cross_checks`` maps ``epsBasic``/``epsDiluted`` to ``{"income": {"value",
    "unit":"USD"}, "shares":{"value", "unit":"shares", "basis": target_share_basis}}``.
    They are diagnostics and cannot reject a directly reported value. A Grade A candidate also
    needs ``raw_filing_evidence[field]`` that ties the value to the same accession, exact period,
    original filing URL/hash, excerpt and table/iXBRL locator. ``split_multiplier`` must be
    supported by the evidence's splitAdjustment trace; it is never inferred from EPS ratios.
    """
    multiplier = _decimal(split_multiplier)
    if multiplier is None or multiplier <= 0:
        return {
            field: _failure_record(symbol=symbol, cik=cik, field=field,
                                   period_start=period_start, period_end=period_end,
                                   reason="invalid-split-multiplier")
            for field in DIRECT_EPS_CONCEPTS
        }
    try:
        candidates = extract_direct_q4_candidates(
            companyfacts, cik=cik, period_end=period_end,
            companyfacts_url=companyfacts_url)
    except EvidenceError as error:
        return {
            field: _failure_record(symbol=symbol, cik=cik, field=field,
                                   period_start=period_start, period_end=period_end,
                                   reason=error.reason)
            for field in DIRECT_EPS_CONCEPTS
        }

    result = {}
    for field in DIRECT_EPS_CONCEPTS:
        candidate, reason = select_direct_q4_candidate(
            candidates[field], period_start=period_start, period_end=period_end)
        if reason:
            result[field] = _failure_record(
                symbol=symbol, cik=cik, field=field, period_start=period_start,
                period_end=period_end, reason=reason)
            continue

        adjusted = _decimal(candidate["rawValue"]) * multiplier
        error = None
        flags = []
        check = (cross_checks or {}).get(field) or {}
        income = check.get("income") or {}
        shares = check.get("shares") or {}
        income_value = _decimal(income.get("value"))
        shares_value = _decimal(shares.get("value"))
        if income.get("unit") != "USD":
            flags.append("cross-check-income-unit-mismatch")
        elif shares.get("unit") != "shares":
            flags.append("cross-check-shares-unit-mismatch")
        elif shares.get("basis") != target_share_basis:
            flags.append("cross-check-share-basis-mismatch")
        elif income_value is None:
            flags.append("cross-check-income-missing-or-invalid")
        elif shares_value is None or shares_value <= 0:
            flags.append("cross-check-shares-missing-or-nonpositive")
        else:
            expected = income_value / shares_value
            if expected == 0:
                error = Decimal(0) if adjusted == 0 else None
                if adjusted != 0:
                    flags.append("cross-check-zero-income-eps-mismatch")
            else:
                if adjusted == 0 or (adjusted > 0) != (expected > 0):
                    flags.append("cross-check-sign-mismatch")
                error = abs(adjusted - expected) / abs(expected)
                if error > DIRECT_CROSSCHECK_MAX_RELATIVE_ERROR:
                    flags.append("cross-check-outside-25-percent")

        filing_evidence = (raw_filing_evidence or {}).get(field)
        evidence_reason = _validate_raw_filing_evidence(
            filing_evidence, candidate=candidate, field=field,
            period_start=period_start, period_end=period_end,
            raw_value=candidate["rawValue"], split_multiplier=multiplier)
        adjudication = filing_evidence.get("diagnosticAdjudication") if isinstance(filing_evidence, dict) else None
        if (evidence_reason is None and flags
                and not (isinstance(adjudication, dict) and adjudication.get("status") == "resolved"
                         and str(adjudication.get("reason") or "").strip())):
            evidence_reason = "cross-check-diagnostic-needs-source-review"

        source = {
            "provider": "SEC EDGAR companyfacts",
            "url": candidate["url"],
            "filingUrl": candidate["filingUrl"],
            "cik": candidate["cik"],
            "accession": candidate["accession"],
            "filed": candidate["filed"],
            "form": candidate["form"],
            "start": candidate["start"],
            "end": candidate["end"],
            "concept": candidate["concept"],
            "unit": candidate["unit"],
            "rawValue": candidate["rawValue"],
            "frame": candidate["frame"],
            "version": candidate["version"],
            "basis": {
                "reported": "as-reported-at-filing",
                "target": target_share_basis,
                "splitMultiplier": _decimal_text(multiplier),
            },
            "rawFilingEvidence": filing_evidence,
        }
        verified = evidence_reason is None
        result[field] = {
            "schema": SCHEMA,
            "symbol": symbol,
            "cik": _cik(cik),
            "period": {"start": period_start, "end": period_end},
            "field": field,
            "value": _decimal_text(adjusted),
            "currency": "USD",
            "unit": "USD/shares",
            "grade": "A" if verified else None,
            "status": "validated" if verified else (
                "pending-diagnostic-review" if evidence_reason == "cross-check-diagnostic-needs-source-review"
                else "pending-raw-filing-validation"),
            "method": "direct-sec-companyfacts",
            "source": source,
            "validation": {
                "passed": verified,
                "failureReason": evidence_reason,
                "diagnosticFlags": flags,
                "crossCheckRelativeError": _decimal_text(error),
                "crossCheckMaximumRelativeError": _decimal_text(DIRECT_CROSSCHECK_MAX_RELATIVE_ERROR),
            },
        }
    return result


def _validate_raw_filing_evidence(evidence, *, candidate, field, period_start,
                                  period_end, raw_value, split_multiplier):
    """Re-read and structurally verify the exact original SEC fact before assigning Grade A."""
    if field not in DIRECT_EPS_CONCEPTS:
        return "unsupported-direct-eps-field"
    if not isinstance(evidence, dict):
        return "missing-original-sec-filing-evidence"
    if evidence.get("accession") != candidate.get("accession"):
        return "raw-filing-accession-mismatch"
    if str(evidence.get("form") or "").upper() != str(candidate.get("form") or "").upper():
        return "raw-filing-form-mismatch"
    if evidence.get("periodStart") != period_start or evidence.get("periodEnd") != period_end:
        return "raw-filing-period-mismatch"
    if evidence.get("field") != field:
        return "raw-filing-eps-type-mismatch"
    if _decimal(evidence.get("reportedValue")) != _decimal(raw_value):
        return "raw-filing-value-mismatch"
    if evidence.get("unit") != "USD/shares":
        return "raw-filing-unit-mismatch"
    url = urlparse(str(evidence.get("url") or ""))
    accession_digits = re.sub(r"[^0-9]", "", str(candidate.get("accession") or ""))
    expected_cik = _cik(candidate.get("cik"))
    archive = re.fullmatch(r"/Archives/edgar/data/(\d+)/(\d+)/([^/]+)", url.path)
    if (url.scheme != "https" or url.netloc.lower() != "www.sec.gov" or not archive
            or not expected_cik or _cik(archive.group(1)) != expected_cik
            or not accession_digits or archive.group(2) != accession_digits
            or url.params or url.query or url.fragment):
        return "raw-filing-url-cik-accession-or-document-mismatch"
    digest = str(evidence.get("filingSha256") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        return "raw-filing-hash-missing-or-invalid"
    if not str(evidence.get("excerpt") or "").strip():
        return "raw-filing-excerpt-missing"
    if not isinstance(evidence.get("locator"), dict) or not evidence["locator"]:
        return "raw-filing-locator-missing"
    raw_path = evidence.get("rawBodyPath")
    if not isinstance(raw_path, str) or not raw_path.strip() or evidence.get("rawBodyUrl") != evidence.get("url"):
        return "raw-filing-body-location-missing-or-mismatched"
    try:
        body = Path(raw_path).read_bytes()
    except OSError:
        return "raw-filing-body-unreadable"
    if hashlib.sha256(body).hexdigest() != digest.lower():
        return "raw-filing-body-hash-mismatch"
    locator_error = _verify_raw_filing_locator(
        body, evidence=evidence, candidate=candidate, field=field,
        period_start=period_start, period_end=period_end, raw_value=raw_value)
    if locator_error:
        return locator_error
    split_adjustment = evidence.get("splitAdjustment")
    if isinstance(split_adjustment, dict):
        if _decimal(split_adjustment.get("factor")) != split_multiplier:
            return "split-adjustment-not-independently-traced"
        if split_multiplier != 1 and not split_adjustment.get("source"):
            return "split-adjustment-source-missing"
    elif split_multiplier != 1:
        return "split-adjustment-not-independently-traced"
    if not str(evidence.get("reportedBasis") or "").strip():
        return "raw-filing-share-basis-missing"
    return None


def _verify_raw_filing_locator(body, *, evidence, candidate, field, period_start, period_end, raw_value):
    locator = evidence.get("locator") or {}
    try:
        text = body.decode("utf-8", errors="replace")
    except AttributeError:
        return "raw-filing-body-is-not-bytes"
    if locator.get("type") in {"ixbrl", "inline-xbrl", "xbrl"} or locator.get("factId"):
        try:
            from sec_inline_xbrl_q4 import parse_inline_xbrl
            parsed = parse_inline_xbrl(text)
        except Exception:
            return "raw-filing-inline-xbrl-could-not-be-parsed"
        concept = DIRECT_EPS_CONCEPTS.get(field)
        expected_cik = _cik(candidate.get("cik"))
        matches = []
        for fact in parsed.get("facts", []):
            if fact.get("name") != concept or fact.get("namespace", "").lower() != "us-gaap":
                continue
            if not re.fullmatch(r"https?://fasb\.org/us-gaap/\d{4}(?:[-/][A-Za-z0-9._-]+)?",
                                str(fact.get("namespaceUri") or "")):
                continue
            context = fact.get("context") if isinstance(fact.get("context"), dict) else {}
            entity_ids = context.get("entityIdentifiers") if isinstance(context.get("entityIdentifiers"), list) else []
            context_start = _day(context.get("start"))
            context_end = _day(context.get("end"))
            duration_days = (context_end - context_start).days + 1 if context_start and context_end else None
            if (context.get("start") != period_start or context.get("end") != period_end
                    or duration_days is None or not 80 <= duration_days <= 100
                    or fact.get("unit") != "USD/shares" or context.get("dimensions")
                    or context.get("typedDimensions") or context.get("hasSegment") or context.get("hasScenario")
                    or len(entity_ids) != 1 or not isinstance(entity_ids[0], dict)
                    or _cik(entity_ids[0].get("identifier")) != expected_cik):
                continue
            scheme = urlparse(str(entity_ids[0].get("scheme") or ""))
            if (scheme.scheme not in {"http", "https"}
                    or (scheme.hostname or "").lower() not in {"sec.gov", "www.sec.gov", "data.sec.gov"}):
                continue
            if _decimal(fact.get("value")) != _decimal(raw_value):
                continue
            if locator.get("factId") and fact.get("id") != locator.get("factId"):
                continue
            if locator.get("contextRef") and fact.get("contextRef") != locator.get("contextRef"):
                continue
            matches.append(fact)
        return None if len(matches) == 1 else "raw-filing-inline-xbrl-locator-does-not-resolve-uniquely"

    try:
        import backfill_q4_eps_8k as table_parser
        tables = table_parser.structured_tables_of(text)
    except Exception:
        return "raw-filing-table-could-not-be-parsed"
    table_index = locator.get("tableIndex", locator.get("table"))
    row_index = locator.get("row", locator.get("rowIndex"))
    column_index = locator.get("column", locator.get("columnIndex"))
    if (isinstance(table_index, bool) or not isinstance(table_index, int) or table_index < 0
            or isinstance(row_index, bool) or not isinstance(row_index, int)
            or isinstance(column_index, bool) or not isinstance(column_index, int)
            or table_index >= len(tables)):
        return "raw-filing-table-locator-is-invalid"
    table = tables[table_index]
    result, reason = table_parser.table_eps(table, None, period_end, table_index=table_index,
                                            expected_currency="USD")
    if result is None and reason is None:
        result, reason = table_parser.table_basic_eps(table, period_end, table_index=table_index,
                                                      expected_currency="USD")
    if result is None or reason:
        return "raw-filing-table-locator-does-not-resolve-to-q4-eps"
    raw_key = "epsDilutedAsReported" if field == "epsDiluted" else "epsBasicAsReported"
    if _decimal(result.get(raw_key, result.get(field))) != _decimal(raw_value):
        return "raw-filing-table-locator-value-mismatch"
    evidence_table = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    if field == "epsDiluted":
        cell = evidence_table.get("epsCell") if isinstance(evidence_table.get("epsCell"), dict) else {}
        review_quality = ((result.get("qualityByField") or {}).get("epsDiluted") or {})
    else:
        cell = evidence_table.get("basicEpsCell") if isinstance(evidence_table.get("basicEpsCell"), dict) else {}
        review_quality = ((result.get("qualityByField") or {}).get("epsBasic") or {})
    if cell.get("row") != row_index or cell.get("column") != column_index:
        return "raw-filing-table-locator-row-or-column-mismatch"
    review_flags = set(review_quality.get("reviewFlags") or [])
    formula_diagnostics = {
        "diluted-eps-sign-differs-from-net-income",
        "net-income-does-not-match-stored-quarter-value",
        "net-income-target-match-is-ambiguous",
        "net-income-divided-by-shares-differs-from-diluted-eps",
    }
    if review_quality.get("candidateValidated") is not True and not (field == "epsDiluted" and review_flags
                                                                      and review_flags <= formula_diagnostics):
        return "raw-filing-table-locator-candidate-needs-review"
    return None


def _normalize_reconstruction_fact(candidates, *, role, cik, start, end, unit, cutoff=None):
    """Resolve one filing version for a single exact period and unit."""
    if isinstance(candidates, dict):
        candidates = [candidates]
    if not isinstance(candidates, (list, tuple)) or not candidates:
        raise EvidenceError(f"missing-{role}")
    start_day, end_day = _day(start), _day(end)
    if start_day is None or end_day is None:
        raise EvidenceError("invalid-requested-fact-period")

    period_matches = [f for f in candidates if isinstance(f, dict)
                      and _day(f.get("start")) == start_day
                      and _day(f.get("end")) == end_day]
    if not period_matches:
        raise EvidenceError(f"period-mismatch-{role}")
    concept_values = {str(f.get("concept") or "") for f in period_matches}
    concept_values.discard("")
    if len(concept_values) != 1:
        raise EvidenceError(f"concept-missing-or-conflicting-{role}")
    concept = next(iter(concept_values))

    unit_matches = [f for f in period_matches if f.get("unit") == unit]
    if not unit_matches:
        raise EvidenceError(f"unit-mismatch-{role}")
    eligible = []
    after_cutoff = False
    for fact in unit_matches:
        fact_cik = _cik(fact.get("cik"))
        if fact_cik != cik:
            continue
        filed = _day(fact.get("filed"))
        if filed is None or not fact.get("form") or not (fact.get("accn") or fact.get("accession")):
            continue
        if cutoff is not None and filed > cutoff:
            after_cutoff = True
            continue
        value = _decimal(fact.get("val", fact.get("value")))
        if value is None:
            continue
        if not _official_url(fact.get("url")):
            continue
        eligible.append(fact)
    if not eligible:
        if after_cutoff:
            raise EvidenceError(f"fact-filed-after-reference-version-{role}")
        raise EvidenceError(f"invalid-or-untraceable-fact-{role}")

    latest = max(_version_key(fact) for fact in eligible)
    latest_facts = [fact for fact in eligible if _version_key(fact) == latest]
    values = {_decimal_text(f.get("val", f.get("value"))) for f in latest_facts}
    if len(values) != 1:
        raise EvidenceError(f"conflicting-facts-in-selected-version-{role}")
    selected = dict(latest_facts[0])
    selected["cik"] = cik
    selected["concept"] = concept
    selected["unit"] = unit
    selected["value"] = _decimal_text(selected.get("val", selected.get("value")))
    selected["start"] = start_day.isoformat()
    selected["end"] = end_day.isoformat()
    selected["accession"] = str(selected.get("accn") or selected.get("accession"))
    selected["url"] = selected.get("url")
    return selected


def _same_filing(left, right):
    return (left.get("accession"), left.get("filed"), left.get("form")) == (
        right.get("accession"), right.get("filed"), right.get("form"))


def _fact_source(fact):
    source = _source_fact(fact)
    source["url"] = fact.get("url")
    return source


def _reconstruction_failure(*, symbol, cik, fiscal_year, period_start, period_end, reason):
    return {
        "schema": SCHEMA,
        "symbol": symbol,
        "cik": _cik(cik),
        "period": {"fiscalYear": fiscal_year, "start": period_start, "end": period_end},
        "field": "epsBasic",
        "value": None,
        "currency": "USD",
        "unit": "USD/shares",
        "grade": None,
        "status": "rejected",
        "method": "basic-eps-actual-day-reconstruction",
        "source": None,
        "validation": {"passed": False, "failureReason": reason},
    }


def reconstruct_basic_q4_eps(*, symbol, cik, fiscal_year, fiscal_start,
                             nine_month_end, q4_start, fiscal_end,
                             annual_income_facts, nine_month_income_facts,
                             annual_basic_share_facts, nine_month_basic_share_facts,
                             target_share_basis):
    """Stage a strict Basic EPS reconstruction (grade B only after every gate passes).

    All four inputs are versioned SEC facts, each with ``cik``, ``concept``, ``unit``,
    ``val`` (or ``value``), ``start``, ``end``, ``filed``, ``form``, ``accn`` and an
    official SEC ``url``. Income facts additionally require ``incomeAttribution`` to
    equal ``common``, ``preferredDividendsStatus`` in ``included``/``none``, and
    ``noncontrollingInterestStatus`` in ``excluded``/``none``. Share facts require a
    single, explicitly identified common share class, plus matching ``shareBasis``.

    The 9M version must be filed no later than the selected annual version. Annual
    income/share facts must come from one filing version; likewise the 9M pair. Exact
    fiscal boundaries are mandatory. A 53-week year is supported only when those
    exact boundaries are supplied and the actual day counts close without a gap or
    overlap. EPS is ``(annual common income - 9M common income) / Q4 basic weighted
    shares`` where Q4 shares are derived from the two actual-day weighted averages.
    """
    normalized_cik = _cik(cik)
    if normalized_cik is None:
        return _reconstruction_failure(symbol=symbol, cik=cik, fiscal_year=fiscal_year,
                                       period_start=q4_start, period_end=fiscal_end,
                                       reason="invalid-cik")
    dates = tuple(_day(value) for value in (fiscal_start, nine_month_end, q4_start, fiscal_end))
    if any(value is None for value in dates):
        return _reconstruction_failure(symbol=symbol, cik=normalized_cik, fiscal_year=fiscal_year,
                                       period_start=q4_start, period_end=fiscal_end,
                                       reason="invalid-fiscal-boundaries")
    fy_start, nine_end, q4_begin, fy_end = dates
    if not (fy_start <= nine_end < q4_begin <= fy_end) or (nine_end.toordinal() + 1) != q4_begin.toordinal():
        return _reconstruction_failure(symbol=symbol, cik=normalized_cik, fiscal_year=fiscal_year,
                                       period_start=q4_start, period_end=fiscal_end,
                                       reason="discontinuous-fiscal-periods")

    annual_start, annual_end = fy_start.isoformat(), fy_end.isoformat()
    nine_start, nine_end_text = fy_start.isoformat(), nine_end.isoformat()
    q4_start_text, q4_end_text = q4_begin.isoformat(), fy_end.isoformat()
    try:
        annual_income = _normalize_reconstruction_fact(
            annual_income_facts, role="annual-income", cik=normalized_cik,
            start=annual_start, end=annual_end, unit="USD")
        annual_shares = _normalize_reconstruction_fact(
            annual_basic_share_facts, role="annual-basic-shares", cik=normalized_cik,
            start=annual_start, end=annual_end, unit="shares")
        annual_filed = _day(annual_income.get("filed"))
        if annual_filed is None or not _same_filing(annual_income, annual_shares):
            raise EvidenceError("annual-facts-version-mismatch")
        if str(annual_income.get("form")).upper() not in ALLOWED_ANNUAL_FORMS:
            raise EvidenceError("annual-income-not-10k")
        if str(annual_shares.get("form")).upper() not in ALLOWED_ANNUAL_FORMS:
            raise EvidenceError("annual-shares-not-10k")

        nine_income = _normalize_reconstruction_fact(
            nine_month_income_facts, role="nine-month-income", cik=normalized_cik,
            start=nine_start, end=nine_end_text, unit="USD", cutoff=annual_filed)
        nine_shares = _normalize_reconstruction_fact(
            nine_month_basic_share_facts, role="nine-month-basic-shares", cik=normalized_cik,
            start=nine_start, end=nine_end_text, unit="shares", cutoff=annual_filed)
        if not _same_filing(nine_income, nine_shares):
            raise EvidenceError("nine-month-facts-version-mismatch")
        if str(nine_income.get("form")).upper() not in ALLOWED_NINE_MONTH_FORMS:
            raise EvidenceError("nine-month-income-not-10q")
        if str(nine_shares.get("form")).upper() not in ALLOWED_NINE_MONTH_FORMS:
            raise EvidenceError("nine-month-shares-not-10q")
        if _day(nine_income.get("filed")) > annual_filed:
            raise EvidenceError("nine-month-version-after-annual-filing")

        if annual_income.get("concept") != nine_income.get("concept"):
            raise EvidenceError("income-concept-mismatch")
        for income_fact in (annual_income, nine_income):
            if income_fact.get("incomeAttribution") != "common":
                raise EvidenceError("income-not-confirmed-common-attributable")
            if income_fact.get("preferredDividendsStatus") not in {"included", "none"}:
                raise EvidenceError("preferred-dividend-treatment-uncertain")
            if income_fact.get("noncontrollingInterestStatus") not in {"excluded", "none"}:
                raise EvidenceError("noncontrolling-interest-treatment-uncertain")
        if (annual_income.get("preferredDividendsStatus") != nine_income.get("preferredDividendsStatus")
                or annual_income.get("noncontrollingInterestStatus") != nine_income.get("noncontrollingInterestStatus")):
            raise EvidenceError("income-attribution-treatment-mismatch")

        if annual_shares.get("concept") != nine_shares.get("concept"):
            raise EvidenceError("basic-share-concept-mismatch")
        if annual_shares.get("concept") != SHARES_BASIC_CONCEPT:
            raise EvidenceError("shares-not-basic-weighted-average")
        if annual_shares.get("shareClass") != nine_shares.get("shareClass"):
            raise EvidenceError("share-class-mismatch")
        if not annual_shares.get("shareClass") or annual_shares.get("shareClassCount") != 1:
            raise EvidenceError("multiple-or-uncertain-common-share-classes")
        if nine_shares.get("shareClassCount") != 1:
            raise EvidenceError("multiple-or-uncertain-common-share-classes")
        annual_basis, nine_basis = annual_shares.get("shareBasis"), nine_shares.get("shareBasis")
        if not annual_basis or not nine_basis:
            raise EvidenceError("share-basis-unknown")
        if annual_basis != nine_basis or annual_basis != target_share_basis:
            raise EvidenceError("split-basis-mismatch")

        annual_income_value = _decimal(annual_income.get("value"))
        nine_income_value = _decimal(nine_income.get("value"))
        annual_share_value = _decimal(annual_shares.get("value"))
        nine_share_value = _decimal(nine_shares.get("value"))
        if any(value is None for value in (annual_income_value, nine_income_value,
                                            annual_share_value, nine_share_value)):
            raise EvidenceError("invalid-numeric-input")
        if annual_share_value <= 0 or nine_share_value <= 0:
            raise EvidenceError("annual-or-nine-month-shares-nonpositive")

        annual_days = (fy_end - fy_start).days + 1
        nine_days = (nine_end - fy_start).days + 1
        q4_days = (fy_end - q4_begin).days + 1
        if min(annual_days, nine_days, q4_days) <= 0 or annual_days != nine_days + q4_days:
            raise EvidenceError("actual-day-intervals-do-not-reconcile")
        if not 80 <= q4_days <= 120:
            raise EvidenceError("q4-duration-outside-expected-range")
        # Long years (including 53-week years) are accepted only after exact boundaries
        # were checked above and the full/9M/Q4 actual-day totals reconcile.
        q4_shares = (annual_share_value * annual_days - nine_share_value * nine_days) / q4_days
        if q4_shares <= 0:
            raise EvidenceError("computed-q4-basic-shares-nonpositive")
        q4_income = annual_income_value - nine_income_value
        eps = q4_income / q4_shares

        source = {
            "provider": "SEC EDGAR filings / companyfacts",
            "annualIncome": _fact_source(annual_income),
            "nineMonthIncome": _fact_source(nine_income),
            "annualBasicShares": _fact_source(annual_shares),
            "nineMonthBasicShares": _fact_source(nine_shares),
            "basis": {
                "income": "common-attributable USD",
                "shares": annual_basis,
                "shareClass": annual_shares.get("shareClass"),
                "splitAdjustedConsistently": True,
            },
            "version": {
                "annual": annual_income["version"] if "version" in annual_income else {
                    "filed": annual_income.get("filed"), "form": annual_income.get("form"),
                    "accession": annual_income.get("accession")},
                "nineMonth": nine_income["version"] if "version" in nine_income else {
                    "filed": nine_income.get("filed"), "form": nine_income.get("form"),
                    "accession": nine_income.get("accession")},
            },
        }
        return {
            "schema": SCHEMA,
            "symbol": symbol,
            "cik": normalized_cik,
            "period": {"fiscalYear": fiscal_year, "start": q4_start_text,
                       "end": q4_end_text, "days": q4_days},
            "field": "epsBasic",
            "value": _decimal_text(eps),
            "currency": "USD",
            "unit": "USD/shares",
            "grade": "B",
            "status": "validated",
            "method": "basic-eps-actual-day-reconstruction",
            "calculation": {
                "formula": "(annual common income - nine-month common income) / Q4 basic weighted-average shares",
                "annualIncome": _decimal_text(annual_income_value),
                "nineMonthIncome": _decimal_text(nine_income_value),
                "q4CommonIncome": _decimal_text(q4_income),
                "annualBasicShares": _decimal_text(annual_share_value),
                "nineMonthBasicShares": _decimal_text(nine_share_value),
                "annualDays": annual_days,
                "nineMonthDays": nine_days,
                "q4Days": q4_days,
                "q4BasicWeightedAverageShares": _decimal_text(q4_shares),
            },
            "source": source,
            "validation": {"passed": True, "failureReason": None},
        }
    except EvidenceError as error:
        return _reconstruction_failure(symbol=symbol, cik=normalized_cik,
                                       fiscal_year=fiscal_year,
                                       period_start=q4_start_text,
                                       period_end=q4_end_text,
                                       reason=error.reason)
