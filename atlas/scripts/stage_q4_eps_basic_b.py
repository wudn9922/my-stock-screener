#!/usr/bin/env python3
"""Find, source-check, and optionally stage strict Basic Q4 EPS reconstructions.

Only null Basic EPS fields are eligible. SEC filing downloads use seed_financials_sec.SecClient,
checkpoint every accession, resume automatically, and stop on the first 403. No financial file is
written until the SEC A/B historical-validation subgroup passes and the raw filing contexts are
checked. Candidate failures remain missing and are recorded with their exact reason.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import audit_q4_eps_basic_reconstruction as audit  # noqa: E402
import build_financials as fin  # noqa: E402
import q4_eps_evidence as evidence  # noqa: E402
import report_q4_eps_basic_backtest as backtest  # noqa: E402
import sec_inline_xbrl_q4 as inline  # noqa: E402
import seed_financials_sec as sec  # noqa: E402
from build_valuation_us import read_json, write_atomic  # noqa: E402

FINANCIALS = ROOT / "docs/report/financials"
EVIDENCE_CACHE = ROOT / ".cache/q4-eps-evidence"
FILINGS_CACHE = EVIDENCE_CACHE / "raw-filings"
STATE_PATH = EVIDENCE_CACHE / "raw-filing-state.json"
REPORT_PATH = FINANCIALS / "q4-eps-basic-B-source-audit.json"
DECISIONS_PATH = FINANCIALS / "q4-eps-basic-B-source-decisions.jsonl"
NUMERATOR_REVIEW_PATH = FINANCIALS / "q4-eps-basic-B-numerator-review.jsonl"
GROUP = ("strict-positive-income-shares-range-le-2pct-q4shares-within-q3-15pct-and-"
         "q4-income-ratio-over-10pct-and-q4-net-income-sign-match")
ERROR_FLOOR = Decimal("0.10")
MIN_SHARES = Decimal(str(sec.MIN_SHARE_COUNT))


def _decimal(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def load_basic_eps_numerator_reviews(review_path=NUMERATOR_REVIEW_PATH):
    """Load unique manual EPS-note reviews keyed by symbol and fiscal year."""
    reviews = {}
    path = Path(review_path)
    if not path.exists():
        return reviews
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            key = (str(row["symbol"]), int(row["fiscalYear"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"invalid numerator review at {path}:{line_number}") from error
        if key in reviews:
            raise RuntimeError(f"duplicate Basic EPS numerator review: {key[0]} {key[1]}")
        reviews[key] = row
    return reviews


def basic_eps_numerator_review_failure(review, facts):
    """Require an approved EPS-note review bound to the four SEC input facts."""
    if not isinstance(review, dict):
        return "basic-eps-numerator-note-review-missing"
    source_labels = {
        "annualIncome": "annualIncomeFact",
        "nineMonthIncome": "nineMonthIncomeFact",
        "annualBasicShares": "annualBasicShareFact",
        "nineMonthBasicShares": "nineMonthBasicShareFact",
    }
    identity_fields = ("cik", "concept", "unit", "start", "end", "form", "filed",
                       "accession", "filingUrl", "filingSha256")
    for fact_label, review_label in source_labels.items():
        expected = review.get(review_label)
        observed = facts.get(fact_label)
        if not isinstance(expected, dict) or not isinstance(observed, dict):
            return f"basic-eps-numerator-review-source-missing:{fact_label}"
        if any(str(expected.get(field) or "") != str(observed.get(field) or "")
               for field in identity_fields):
            return f"basic-eps-numerator-review-source-mismatch:{fact_label}"
        expected_value, observed_value = _decimal(expected.get("rawValue")), _decimal(
            observed.get("rawValue", observed.get("value", observed.get("val"))))
        if expected_value is None or observed_value is None or expected_value != observed_value:
            return f"basic-eps-numerator-review-source-mismatch:{fact_label}:rawValue"
    if review.get("decision") != "approved":
        return review.get("failureReason") or "basic-eps-numerator-note-review-not-approved"
    return None


def _json_state(path=STATE_PATH):
    try:
        state = read_json(path)
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    if state.get("version") != 1:
        state = {"version": 1, "sources": {}, "submissions": {}, "requests": 0}
    state.setdefault("sources", {})
    state.setdefault("submissions", {})
    state.setdefault("requests", 0)
    return state


def _save_state(path, state):
    write_atomic(str(path), state)


def _fact_url(cik):
    return sec.FACTS_URL.format(cik=sec.cik10(cik))


def _signature_key(symbol, end, fy):
    return f"{symbol}|{fy}|{end}"


def _source_fact(company, concept, unit, start, end, forms, *, cutoff=None):
    rows = [dict(row, concept=concept, unit=unit, cik=sec.cik10(company.get("cik")),
                 url=_fact_url(company.get("cik")), val=row.get("value"), accn=row.get("accession"))
            for row in ((company.get("concepts") or {}).get(concept) or [])
            if row.get("unit") == unit and row.get("start") == start and row.get("end") == end
            and row.get("form") in forms and row.get("filed") and row.get("accession")
            and (cutoff is None or row.get("filed") <= cutoff)]
    selected, problem = audit._one_version(rows)
    return selected, problem


def _share_fact(company, start, end, cutoff):
    return _source_fact(company, audit.BASIC_SHARES, "shares", start, end,
                        {"10-Q", "10-Q/A"}, cutoff=cutoff)


def _source_rows(document, company, q4_index):
    q = document.get("quarterly") or {}
    fy, end = q["fy"][q4_index], q["end"][q4_index]
    q3_indexes = [i for i, (year, fq) in enumerate(zip(q.get("fy") or [], q.get("fq") or []))
                  if year == fy and fq == 3]
    if len(q3_indexes) != 1:
        return None, "q3-period-not-unique"
    q3_end = q["end"][q3_indexes[0]]
    annual_rows = [row for row in ((company.get("concepts") or {}).get(audit.COMMON_INCOME) or [])
                   if row.get("unit") == "USD" and row.get("end") == end
                   and row.get("form") in {"10-K", "10-K/A"} and row.get("start")
                   and row.get("filed") and row.get("accession")]
    if not annual_rows:
        return None, "annual-common-income-missing"
    starts = sorted({row["start"] for row in annual_rows if row.get("start")})
    if not starts:
        return None, "annual-common-income-start-missing"
    annual_start = starts[-1]
    annual_income, problem = _source_fact(company, audit.COMMON_INCOME, "USD", annual_start, end,
                                          {"10-K", "10-K/A"})
    if problem:
        return None, "annual-common-income-" + problem
    annual_shares, problem = _source_fact(company, audit.BASIC_SHARES, "shares", annual_start, end,
                                          {"10-K", "10-K/A"})
    if problem:
        return None, "annual-basic-shares-" + problem
    if not (annual_income.get("accession"), annual_income.get("filed"), annual_income.get("form")) == (
            annual_shares.get("accession"), annual_shares.get("filed"), annual_shares.get("form")):
        return None, "annual-income-share-filing-version-mismatch"
    cutoff = annual_income["filed"]
    nine_income, problem = _source_fact(company, audit.COMMON_INCOME, "USD", annual_start, q3_end,
                                        {"10-Q", "10-Q/A"}, cutoff=cutoff)
    if problem:
        return None, "nine-month-common-income-" + problem
    nine_shares, problem = _source_fact(company, audit.BASIC_SHARES, "shares", annual_start, q3_end,
                                        {"10-Q", "10-Q/A"}, cutoff=cutoff)
    if problem:
        return None, "nine-month-basic-shares-" + problem
    if not (nine_income.get("accession"), nine_income.get("filed"), nine_income.get("form")) == (
            nine_shares.get("accession"), nine_shares.get("filed"), nine_shares.get("form")):
        return None, "nine-month-income-share-filing-version-mismatch"
    q123 = []
    qstarts = []
    prior_end = None
    for quarter in (1, 2, 3):
        matches = [i for i, (year, qtr) in enumerate(zip(q.get("fy") or [], q.get("fq") or []))
                   if year == fy and qtr == quarter]
        if len(matches) != 1:
            return None, f"q{quarter}-period-not-unique"
        qend = q["end"][matches[0]]
        try:
            qstart = annual_start if quarter == 1 else (date.fromisoformat(prior_end) + timedelta(days=1)).isoformat()
        except (TypeError, ValueError):
            return None, f"q{quarter}-start-date-invalid"
        fact, problem = _share_fact(company, qstart, qend, cutoff)
        if problem:
            return None, f"q{quarter}-basic-shares-{problem}"
        q123.append(fact)
        qstarts.append(qstart)
        prior_end = qend
    return {"annualStart": annual_start, "q3End": q3_end, "annualIncome": annual_income,
            "annualShares": annual_shares, "nineMonthIncome": nine_income,
            "nineMonthShares": nine_shares, "q123BasicShares": q123,
            "q123Starts": qstarts, "annualFiled": cutoff}, None


def _rejected_direct_periods(directory=FINANCIALS):
    result = set()
    path = Path(directory) / "q4-eps-sec-A-decisions.jsonl"
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("field") == "epsBasic" and row.get("grade") == "D" and row.get("status") == "rejected":
            result.add((row.get("symbol"), (row.get("period") or {}).get("end")))
    return result


def scan_candidates(directory=FINANCIALS, evidence_cache=EVIDENCE_CACHE):
    """Identify null-only candidates that satisfy the passing historical subgroups."""
    directory, evidence_cache = Path(directory), Path(evidence_cache)
    state = read_json(evidence_cache / "state.json") if (evidence_cache / "state.json").exists() else {}
    rejected = _rejected_direct_periods(directory)
    records, reasons = [], Counter()
    for path in sorted(directory.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            doc = read_json(path)
        except (OSError, ValueError):
            reasons["invalid-financials-json"] += 1
            continue
        if doc.get("market") != "US":
            continue
        q = doc.get("quarterly") or {}
        values = q.get("values") or {}
        basic = values.get("epsBasic") or []
        cache_path = evidence_cache / "facts" / f"{path.stem}.json"
        if not cache_path.exists():
            continue
        try:
            company = read_json(cache_path)
        except (OSError, ValueError):
            reasons["invalid-companyfacts-cache"] += 1
            continue
        for index, fq in enumerate(q.get("fq") or []):
            if fq != 4 or (index < len(basic) and basic[index] is not None):
                continue
            fy, end = q["fy"][index], q["end"][index]
            if (path.stem, end) in rejected:
                reasons["direct-sec-basic-candidate-rejected"] += 1
                continue
            inputs, problem = backtest._eligible_inputs(doc, company, index)
            if problem:
                reasons[problem] += 1
                continue
            annual = inputs["annualShares"]
            q123 = inputs["q123BasicShares"]
            all_shares = [annual, *q123]
            if len(all_shares) != 4 or any(value is None or value < float(MIN_SHARES) for value in all_shares):
                reasons["share-count-below-100000-or-missing"] += 1
                continue
            dispersion = max(all_shares) / min(all_shares) - 1
            if inputs["q4Income"] <= 0:
                reasons["q4-common-income-not-positive"] += 1
                continue
            reported_q4_net_income = ((values.get("netIncome") or [])[index]
                                      if index < len(values.get("netIncome") or []) else None)
            if reported_q4_net_income is None:
                reasons["reported-q4-net-income-missing"] += 1
                continue
            if reported_q4_net_income <= 0:
                reasons["common-income-difference-conflicts-with-reported-q4-net-income-sign"] += 1
                continue
            if dispersion > 0.02:
                reasons["basic-share-range-over-2pct"] += 1
                continue
            q4_share_deviation = abs(inputs["q4Shares"] / q123[2] - 1) if q123[2] else math.inf
            if q4_share_deviation > 0.15:
                reasons["estimated-q4-basic-shares-outside-q3-15pct"] += 1
                continue
            if inputs["q4Shares"] < float(MIN_SHARES):
                reasons["estimated-q4-basic-shares-below-100000"] += 1
                continue
            income_ratio = (abs(inputs["q4Income"]) / abs(inputs["annualIncome"])
                            if inputs["annualIncome"] else math.inf)
            if income_ratio <= 0.10:
                reasons["q4-income-ratio-subgroup-failed-median-gate"] += 1
                continue
            if abs(inputs["estimate"]) < 0.10:
                reasons["near-zero-eps-subgroup-failed-median-gate"] += 1
                continue
            sources, source_problem = _source_rows(doc, company, index)
            if source_problem:
                reasons[source_problem] += 1
                continue
            records.append({"symbol": path.stem, "fiscalYear": fy, "periodEnd": end,
                            "index": index, "estimate": inputs["estimate"],
                            "q4CommonIncome": inputs["q4Income"], "q4BasicShares": inputs["q4Shares"],
                            "reportedQ4NetIncome": reported_q4_net_income,
                            "q4IncomeAnnualRatio": income_ratio, "shareRange": dispersion,
                            "q4SharesToQ3Deviation": q4_share_deviation,
                            "annualStart": inputs["annualStart"], "q3End": inputs["q3End"],
                            "company": company, "financials": doc, "inputFacts": sources})
    return records, reasons


def _accessions(records):
    result = {}
    for record in records:
        facts = record["inputFacts"]
        for label, fact in (("annualIncome", facts["annualIncome"]),
                            ("annualBasicShares", facts["annualShares"]),
                            ("nineMonthIncome", facts["nineMonthIncome"]),
                            ("nineMonthBasicShares", facts["nineMonthShares"]),
                            *((f"q{index + 1}BasicShares", fact) for index, fact in enumerate(facts["q123BasicShares"]))):
            accession = fact.get("accession") or fact.get("accn")
            cik = fact.get("cik") or record["company"].get("cik")
            if not accession or not cik:
                continue
            key = (sec.cik10(cik), str(accession))
            item = result.setdefault(key, {"cik": key[0], "accession": key[1], "forms": []})
            form = str(fact.get("form") or "").upper()
            if form and form not in item["forms"]:
                item["forms"].append(form)
    return [result[key] for key in sorted(result)]


def _submission_rows(payload):
    recent = ((payload or {}).get("filings") or {}).get("recent") or {}
    accessions = recent.get("accessionNumber") or []
    names = ("form", "filingDate", "primaryDocument")
    arrays = [recent.get(name) or [] for name in names]
    rows = {}
    for index, accession in enumerate(accessions):
        row = {"accession": str(accession)}
        for name, values in zip(names, arrays):
            row[name] = values[index] if index < len(values) else None
        rows[str(accession)] = row
    return rows


def _primary_from_submissions(payload, accession, forms):
    row = _submission_rows(payload).get(str(accession))
    if not row or str(row.get("form") or "").upper() not in forms:
        return None
    document = str(row.get("primaryDocument") or "")
    if not document.lower().endswith((".htm", ".html")):
        return None
    return {"primaryDocument": document, "form": row.get("form"), "filed": row.get("filingDate")}


def fetch_filings(records, *, cache=FILINGS_CACHE, state_path=STATE_PATH, budget=300,
                  client=None, environ=None, clock=time.monotonic):
    environ = os.environ if environ is None else environ
    refusal = sec.refusal_reason(environ)
    if refusal:
        return {"status": "refused", "reason": refusal, "sources": {}}
    try:
        user_agent = sec.resolve_user_agent(environ)
    except sec.UserAgentError as error:
        return {"status": "refused", "reason": str(error), "sources": {}}
    client = client or sec.SecClient(user_agent, stop_on_forbidden=True)
    cache, state_path = Path(cache), Path(state_path)
    cache.mkdir(parents=True, exist_ok=True)
    state = _json_state(state_path)
    started = clock()
    starting_requests = client.requests
    status, error_text = "done", None
    sources = state["sources"]
    submissions = state["submissions"]
    for item in _accessions(records):
        key = item["cik"] + ":" + item["accession"]
        entry = sources.get(key) or {**item, "status": "pending"}
        sources[key] = entry
        acccompact = item["accession"].replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{int(item['cik'])}/{acccompact}/"
        html_path = cache / f"{acccompact}.html"
        if not entry.get("primaryDocument"):
            if budget is not None and clock() - started >= budget:
                status = "paused"
                break
            try:
                submissions_path = cache / f"CIK{item['cik']}-submissions.json"
                cached_submissions = submissions.get(item["cik"])
                if submissions_path.exists():
                    submission_payload = read_json(submissions_path)
                    submissions[item["cik"]] = {"url": f"https://data.sec.gov/submissions/CIK{item['cik']}.json",
                                                 "cachePath": str(submissions_path)}
                elif cached_submissions and cached_submissions.get("cachePath"):
                    submission_payload = read_json(cached_submissions["cachePath"])
                else:
                    submission_payload = None
                if submission_payload is None:
                    submission_url = f"https://data.sec.gov/submissions/CIK{item['cik']}.json"
                    submission_payload = client.get_json(submission_url)
                    write_atomic(str(submissions_path), submission_payload)
                    submissions[item["cik"]] = {"url": submission_url, "cachePath": str(submissions_path)}
                resolved = _primary_from_submissions(submission_payload, item["accession"], set(item["forms"]))
                if resolved is None:
                    entry.update({"status": "accession-not-found-in-recent-submissions",
                                  "submissionsUrl": f"https://data.sec.gov/submissions/CIK{item['cik']}.json"})
                    _save_state(state_path, state)
                    continue
            except sec.SecForbidden as error:
                status, error_text = "stopped-403", str(error)
                entry.update({"status": status, "error": error_text})
                _save_state(state_path, state)
                break
            except (sec.FetchError, sec.TooManyFailures, OSError, ValueError) as error:
                status, error_text = "fetch-error", str(error)
                entry.update({"status": status, "error": error_text})
                _save_state(state_path, state)
                break
            entry.update({"primaryDocument": resolved["primaryDocument"], "form": resolved["form"],
                          "filed": resolved["filed"],
                          "submissionsUrl": submissions[item["cik"]]["url"],
                          "documentUrl": base + resolved["primaryDocument"]})
            _save_state(state_path, state)
        if not html_path.exists():
            if budget is not None and clock() - started >= budget:
                status = "paused"
                break
            try:
                body = client.get_bytes(entry["documentUrl"])
            except sec.SecForbidden as error:
                status, error_text = "stopped-403", str(error)
                entry.update({"status": status, "error": error_text})
                _save_state(state_path, state)
                break
            except (sec.FetchError, sec.TooManyFailures, OSError) as error:
                status, error_text = "fetch-error", str(error)
                entry.update({"status": status, "error": error_text})
                _save_state(state_path, state)
                break
            html_path.write_bytes(body)
        body = html_path.read_bytes()
        sha = hashlib.sha256(body).hexdigest()
        entry.update({"status": "ok", "htmlCache": str(html_path), "sha256": sha,
                      "bytes": len(body), "documentUrl": entry["documentUrl"]})
        _save_state(state_path, state)
    requests_this_run = client.requests - starting_requests
    state["requests"] = int(state.get("requests", 0)) + requests_this_run
    _save_state(state_path, state)
    wanted_items = _accessions(records)
    good = sum(1 for item in wanted_items
               if (sources.get(item["cik"] + ":" + item["accession"]) or {}).get("status") == "ok")
    wanted = len(wanted_items)
    return {"status": status, "reason": error_text, "sources": sources,
            "requestsThisRun": requests_this_run, "requestsTotal": state["requests"],
            "filingsOk": good, "filingsRequested": wanted}


def _period_fact(parsed, fact, *, role):
    expected = _decimal(fact.get("value", fact.get("val")))
    if expected is None:
        return None, f"invalid-companyfacts-value-{role}"
    start, end = fact.get("start"), fact.get("end")
    concept = fact.get("concept")
    unit = fact.get("unit")
    rows = []
    for item in parsed.get("facts") or []:
        context = item.get("context") or {}
        if (item.get("name") == concept and item.get("unit") == unit
                and context.get("start") == start and context.get("end") == end):
            rows.append(item)
    if not rows:
        return None, f"raw-inline-fact-not-found-{role}"
    plain = [item for item in rows if not ((item.get("context") or {}).get("dimensions") or [])]
    values = {Decimal(str(item.get("value"))) for item in plain if item.get("value") is not None}
    if not plain or len(values) != 1:
        return None, f"raw-inline-unqualified-fact-missing-or-conflicting-{role}"
    raw_value = next(iter(values))
    try:
        expected_raw = _decimal(fact.get("originalValue", fact.get("value", fact.get("val"))))
    except Exception:
        expected_raw = None
    if expected_raw is not None and raw_value != expected_raw:
        return None, f"companyfacts-vs-raw-filing-value-mismatch-{role}"
    dimensions = sorted({(dim.get("dimension"), dim.get("member"))
                         for row in rows for dim in ((row.get("context") or {}).get("dimensions") or [])})
    return {"rawValue": str(raw_value), "dimensions": dimensions,
            "unqualifiedFactCount": len(plain), "matchingFactCount": len(rows)}, None


def _class_axis_proof(parsed, fact, start, end):
    class_members = set()
    dimensioned = []
    for item in parsed.get("facts") or []:
        if item.get("name") != fact.get("concept"):
            continue
        context = item.get("context") or {}
        if context.get("start") != start or context.get("end") != end:
            continue
        dims = context.get("dimensions") or []
        for dim in dims:
            dimension = str(dim.get("dimension") or "")
            member = str(dim.get("member") or "")
            class_members.add(member or dimension)
            dimensioned.append({"fact": item.get("name"), "dimension": dimension,
                                "member": member, "value": str(item.get("value"))})
    if len(class_members) > 1:
        return None, {"classMembers": sorted(class_members), "dimensionedFacts": dimensioned}
    if dimensioned:
        return None, {"classMembers": sorted(class_members), "dimensionedFacts": dimensioned,
                      "reason": "dimensioned-facts-require-class-attribution-review"}
    # No class axis appears on this exact fact period. Filing text is checked separately
    # for explicit multiple common or ordinary share classes.
    return 1, {"classMembers": [], "dimensionedFacts": [],
               "reason": "no class-of-stock dimensions in exact period facts"}


def _cover_common_class_evidence(text):
    """Read the SEC cover's registered-class table and retain its exact evidence."""
    headings = list(re.finditer(r"\btitle\s+of\s+(?:each|the)\s+class\b", text, re.IGNORECASE))
    classes = []
    excerpts = []
    for heading in headings:
        later = text[heading.end():]
        stops = [match.start() + heading.end() for pattern in (
            r"\bsecurities registered pursuant to section 12\s*\(g\)",
            r"\bindicate by check mark\b",
            r"\bthe aggregate market value\b",
        ) if (match := re.search(pattern, later, re.IGNORECASE))]
        stop = min(stops, default=min(len(text), heading.end() + 1800))
        # SEC XBRL-to-text conversion removes table cell boundaries. Bound the
        # cover-table scan so later financial statements cannot look like titles.
        stop = min(stop, heading.end() + 1800)
        section = text[heading.end():stop]
        entries = []
        pattern = r"\b(?:(class\s+(?!of\b|the\b)[a-z0-9]+)\s+)?(?:common|ordinary)\s+(?:stock|shares)\b"
        for match in re.finditer(pattern, section, re.IGNORECASE):
            tail = section[match.end():match.end() + 55]
            if re.match(r"\s+(?:(?:purchase|stock)\s+)?warrants?\b", tail, re.IGNORECASE):
                continue
            label = (match.group(1) or "common").strip().title()
            entries.append({"class": label, "title": re.sub(r"\s+", " ", section[
                max(0, match.start() - 40):min(len(section), match.end() + 90)]).strip()})
        if entries:
            classes.extend(entries)
            excerpts.append(re.sub(r"\s+", " ", text[heading.start():stop]).strip()[:700])
    return classes, excerpts


def _share_class_proof(parsed, fact, start, end):
    member_count, proof = _class_axis_proof(parsed, fact, start, end)
    if member_count is None:
        return None, proof
    text = parsed.get("filingText", "")
    registered_classes, cover_excerpts = _cover_common_class_evidence(text)
    if len(registered_classes) != 1:
        return None, {**proof, "registeredCommonClasses": registered_classes,
                      "coverTableExcerpts": cover_excerpts,
                      "reason": "cover-table-does-not-prove-one-registered-common-class"}

    # A named Class B/A line outside the cover can reveal a second class even when
    # the cover omits the distinction. Treat this as a fail-closed mismatch.
    explicit = set()
    pattern = (r"\bclass\s+(?!of\b|the\b)([a-z0-9]+)\s+(?:common|ordinary)\s+(?:stock|shares)\b|"
               r"\b(?:common|ordinary)\s+(?:stock|shares)\s*,?\s*class\s+(?!of\b|the\b)([a-z0-9]+)\b")
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        explicit.add("Class " + (match.group(1) or match.group(2)).upper())
    registered_label = registered_classes[0]["class"]
    if len(explicit) > 1 or (explicit and registered_label != "Common" and registered_label not in explicit):
        return None, {**proof, "registeredCommonClasses": registered_classes,
                      "coverTableExcerpts": cover_excerpts,
                      "filingTextClasses": sorted(explicit),
                      "reason": "filing-identifies-common-class-conflict"}
    return 1, {**proof, "shareClass": registered_label, "shareClassCount": 1,
                "registeredCommonClasses": registered_classes,
                "coverTableExcerpts": cover_excerpts,
                "filingTextClasses": sorted(explicit),
                "reason": "cover-table-lists-one-registered-common-class-and-facts-are-unqualified"}


@lru_cache(maxsize=512)
def _parse_filing(path):
    raw_html = Path(path).read_bytes()
    parsed = inline.parse_inline_xbrl(raw_html)
    parsed["filingText"] = inline.extract_filing_text(raw_html).get("text", "")
    return parsed


def _verified_fact(fact, fetch_result, *, role, parsed_cache=None):
    accession = str(fact.get("accession") or fact.get("accn"))
    cik = sec.cik10(fact.get("cik"))
    entry = fetch_result["sources"].get(cik + ":" + accession) or {}
    if entry.get("status") != "ok" or not entry.get("htmlCache"):
        return None, {"failureReason": "raw-filing-source-not-available", "role": role,
                      "accession": accession, "filingStatus": entry.get("status")}
    path = Path(entry["htmlCache"])
    try:
        parsed = _parse_filing(str(path))
    except (OSError, UnicodeError, ValueError) as error:
        return None, {"failureReason": f"raw-filing-parse-failed:{type(error).__name__}", "role": role}
    match, problem = _period_fact(parsed, fact, role=role)
    if problem:
        return None, {"failureReason": problem, "role": role, "accession": accession}
    source = dict(fact)
    source["originalValue"] = fact.get("value", fact.get("val"))
    source["rawValue"] = match["rawValue"]
    source["dimensions"] = match["dimensions"]
    source["filingSha256"] = entry.get("sha256")
    source["filingUrl"] = entry.get("documentUrl")
    source["filingCache"] = str(path)
    source["rawInlineFactCount"] = match["matchingFactCount"]
    source["shareBasis"] = "current-financials-basis"
    return source, None


def _adjust_share_fact(fact, document):
    """Put a raw SEC weighted-share fact on the stored file's current split basis."""
    factor = backtest._split_factor(document, fact.get("filed"))
    raw_value = _decimal(fact.get("rawValue", fact.get("originalValue", fact.get("value", fact.get("val")))))
    if factor is None or raw_value is None:
        return None, {"failureReason": "share-filing-date-invalid-for-split-basis"}
    adjusted = raw_value * Decimal(str(factor))
    fact["originalValue"] = str(raw_value)
    fact["splitFactor"] = factor
    fact["value"] = str(adjusted)
    # The pure reconstruction reader prioritizes CompanyFacts' `val` key.
    fact["val"] = str(adjusted)
    fact["shareBasis"] = "current-financials-basis"
    return {"filed": fact.get("filed"), "accession": fact.get("accession"),
            "concept": fact.get("concept"), "unit": fact.get("unit"),
            "url": fact.get("url"), "filingUrl": fact.get("filingUrl"),
            "filingSha256": fact.get("filingSha256"), "rawValue": str(raw_value),
            "splitFactor": factor, "adjustedValue": str(adjusted),
            "basis": "current-financials-basis"}, None


def validate_candidates(records, fetch_result):
    decisions, counts = [], Counter()
    numerator_reviews = load_basic_eps_numerator_reviews()
    source_refs = (("annualIncome", "annualIncome"), ("annualBasicShares", "annualShares"),
                   ("nineMonthIncome", "nineMonthIncome"), ("nineMonthBasicShares", "nineMonthShares"))
    for record in records:
        doc, company, inputs = record["financials"], record["company"], record["inputFacts"]
        cik = sec.cik10(company.get("cik"))
        facts = {}
        failure = None
        raw_validation = {}
        for label, key in source_refs:
            verified, problem = _verified_fact(inputs[key], fetch_result, role=label)
            raw_validation[label] = problem or {"passed": True, "value": verified.get("rawValue"),
                                                "filingSha256": verified.get("filingSha256"),
                                                "dimensions": verified.get("dimensions"),
                                                "sourceFact": {key: verified.get(key) for key in
                                                               ("cik", "accession", "filed", "form", "start", "end",
                                                                "concept", "unit", "url", "filingUrl",
                                                                "filingSha256", "originalValue", "rawValue")}}
            if problem:
                failure = problem["failureReason"]
                break
            facts[label] = verified
        q123_facts = []
        if failure is None:
            for index, source_fact in enumerate(inputs["q123BasicShares"]):
                label = f"q{index + 1}BasicShares"
                verified, problem = _verified_fact(source_fact, fetch_result, role=label)
                raw_validation[label] = problem or {"passed": True, "value": verified.get("rawValue"),
                                                   "filingSha256": verified.get("filingSha256"),
                                                   "dimensions": verified.get("dimensions"),
                                                   "sourceFact": {key: verified.get(key) for key in
                                                                  ("cik", "accession", "filed", "form", "start", "end",
                                                                   "concept", "unit", "url", "filingUrl",
                                                                   "filingSha256", "originalValue", "rawValue")}}
                if problem:
                    failure = problem["failureReason"]
                    break
                q123_facts.append(verified)
        class_proofs = {}
        split_adjustments = {}
        annual_text = ""
        if failure is None:
            for label in ("annualIncome", "nineMonthIncome", "annualBasicShares", "nineMonthBasicShares",
                          "q1BasicShares", "q2BasicShares", "q3BasicShares"):
                if label.startswith("q"):
                    fact = q123_facts[int(label[1]) - 1]
                else:
                    fact = facts[label]
                entry = fetch_result["sources"].get(cik + ":" + str(fact.get("accession")))
                parsed = _parse_filing(str(Path(entry["htmlCache"])))
                if label == "annualIncome":
                    annual_text = parsed.get("filingText", "")
                member_count, proof = _share_class_proof(parsed, fact, fact.get("start"), fact.get("end"))
                class_proofs[label] = proof
                if member_count != 1:
                    failure = "dimensioned-or-uncertain-common-share-classes"
                    break
        accounting_treatment = _accounting_treatment_evidence(annual_text) if annual_text else []
        numerator_review = numerator_reviews.get((record["symbol"], int(record["fiscalYear"])))
        if failure is None:
            review_failure = basic_eps_numerator_review_failure(numerator_review, facts)
            if review_failure:
                failure = review_failure
        # Create the explicit semantic evidence required by the pure B validator.
        if failure is None:
            for label in ("annualIncome", "nineMonthIncome"):
                facts[label].update({"incomeAttribution": "common", "preferredDividendsStatus": "included",
                                    "noncontrollingInterestStatus": "excluded"})
            for label in ("annualBasicShares", "nineMonthBasicShares"):
                facts[label].update({"shareClass": "common", "shareClassCount": 1,
                                     "shareBasis": "current-financials-basis"})
            for index, fact in enumerate(q123_facts):
                adjustment, problem = _adjust_share_fact(fact, doc)
                if problem:
                    failure = problem["failureReason"]
                    break
                split_adjustments[f"q{index + 1}BasicShares"] = adjustment
                fact.update({"shareClass": "common", "shareClassCount": 1,
                             "shareBasis": "current-financials-basis"})
            for label in ("annualBasicShares", "nineMonthBasicShares"):
                fact = facts[label]
                adjustment, problem = _adjust_share_fact(fact, doc)
                if problem:
                    failure = problem["failureReason"]
                    break
                split_adjustments[label] = adjustment
            if failure:
                staged = {"grade": None, "value": None, "status": "rejected",
                          "validation": {"passed": False, "failureReason": failure}}
                counts[failure] += 1
                decisions.append({"schema": "q4-eps-evidence-staging-v1", "symbol": record["symbol"],
                                  "cik": cik, "period": {"fiscalYear": record["fiscalYear"], "end": record["periodEnd"]},
                                  "field": "epsBasic", "grade": "D", "status": "retained-missing",
                                  "method": "basic-eps-actual-day-reconstruction", "value": None,
                                  "validation": staged["validation"], "inputFactChecks": raw_validation,
                                  "shareClassProof": class_proofs,
                                  "accountingTreatmentEvidence": accounting_treatment,
                                  "epsNumeratorNoteReview": numerator_review})
                continue
            staged = evidence.reconstruct_basic_q4_eps(
                symbol=record["symbol"], cik=cik, fiscal_year=record["fiscalYear"],
                fiscal_start=record["annualStart"], nine_month_end=record["q3End"],
                q4_start=(date.fromisoformat(record["q3End"]) + timedelta(days=1)).isoformat(),
                fiscal_end=record["periodEnd"],
                annual_income_facts=[facts["annualIncome"]],
                nine_month_income_facts=[facts["nineMonthIncome"]],
                annual_basic_share_facts=[facts["annualBasicShares"]],
                nine_month_basic_share_facts=[facts["nineMonthBasicShares"]],
                target_share_basis="current-financials-basis")
            if staged.get("grade") != "B":
                failure = (staged.get("validation") or {}).get("failureReason") or "basic-reconstruction-rejected"
            elif abs(float(staged["value"]) - float(record["estimate"])) > 1e-8:
                failure = "raw-fact-reconstruction-vs-candidate-estimate-mismatch"
        else:
            staged = {"grade": None, "value": None, "status": "rejected",
                      "validation": {"passed": False, "failureReason": failure}}
        if failure:
            counts[failure] += 1
            decision = {"schema": "q4-eps-evidence-staging-v1", "symbol": record["symbol"],
                        "cik": cik, "period": {"fiscalYear": record["fiscalYear"], "end": record["periodEnd"]},
                        "field": "epsBasic", "grade": "D", "status": "retained-missing",
                        "method": "basic-eps-actual-day-reconstruction", "value": None,
                        "validation": {"passed": False, "failureReason": failure},
                        "inputFactChecks": raw_validation, "shareClassProof": class_proofs,
                        "accountingTreatmentEvidence": accounting_treatment,
                        "epsNumeratorNoteReview": numerator_review}
        else:
            counts["validated-grade-B"] += 1
            decision = staged
            decision["inputFactChecks"] = raw_validation
            decision["shareClassProof"] = class_proofs
            decision["candidateGates"] = {"historicalGroup": GROUP,
                                          "historicalGroupGatePassed": True,
                                          "q4IncomeAnnualRatio": record["q4IncomeAnnualRatio"],
                                          "shareRange": record["shareRange"],
                                          "q4SharesToQ3Deviation": record["q4SharesToQ3Deviation"],
                                          "estimatedEpsAbsolute": abs(record["estimate"])}
            decision["source"]["filingFactsSha256"] = {key: facts[key].get("filingSha256") for key, _ in source_refs}
            decision["source"]["rawFilingFactValidation"] = raw_validation
            decision["source"]["shareClassProof"] = class_proofs
            decision["source"]["accountingTreatmentEvidence"] = accounting_treatment
            decision["source"]["epsNumeratorNoteReview"] = numerator_review
            decision["source"]["splitAdjustments"] = split_adjustments
        decisions.append(decision)
    return decisions, counts


def _gate(backtest_report_path=FINANCIALS / "q4-eps-basic-B-backtest.json"):
    report = read_json(backtest_report_path)
    item = (report.get("groups") or {}).get(GROUP) or {}
    if (item.get("n", 0) < 1 or item.get("p50NormalizedErrorFloor010") is None
            or item.get("p90NormalizedErrorFloor010") is None
            or item["p50NormalizedErrorFloor010"] > 0.02
            or item["p90NormalizedErrorFloor010"] > 0.06
            or item.get("p50AbsUsdPerShare") is None or item["p50AbsUsdPerShare"] > 0.005
            or item.get("p90AbsUsdPerShare") is None or item["p90AbsUsdPerShare"] > 0.02
            or item.get("signErrorCount") != 0):
        return False, item
    return True, item


def apply_records(decisions, financials_dir=FINANCIALS, ledger_path=DECISIONS_PATH):
    """Write validated B rows after rechecking that each formal EPS cell is still null."""
    valid = [row for row in decisions if row.get("grade") == "B" and row.get("status") == "validated"]
    by_symbol = {}
    already_applied = set()
    for row in valid:
        path = Path(financials_dir) / f"{row['symbol']}.json"
        doc = by_symbol.setdefault(row["symbol"], read_json(path))
        q = doc.get("quarterly") or {}
        matches = [i for i, (fy, end, fq) in enumerate(zip(q.get("fy") or [], q.get("end") or [], q.get("fq") or []))
                   if fq == 4 and fy == row["period"]["fiscalYear"] and end == row["period"]["end"]]
        if len(matches) != 1:
            raise RuntimeError(f"Q4 EPS period is no longer unique: {row['symbol']} {row['period']}")
        i = matches[0]
        column = ((q.get("values") or {}).get("epsBasic") or [])
        grades = q.get("epsQuality") or []
        grade = grades[i].get("epsBasic") if i < len(grades) and isinstance(grades[i], dict) else None
        existing = column[i] if i < len(column) else None
        if existing is not None:
            trace = ((doc.get("secQ4EpsFacts") or {}).get(row["period"]["end"]) or {}).get("epsBasic")
            source = (trace or {}).get("source") or {}
            same_source = (source.get("version") or {}).get("annual") == (row.get("source") or {}).get("version", {}).get("annual")
            if (grade == "B" and trace and trace.get("grade") == "B"
                    and trace.get("method") == row.get("method") and same_source
                    and float(existing) == round(float(row["value"]), 4)):
                already_applied.add((row["symbol"], row["period"]["end"]))
                continue
            raise RuntimeError(f"refusing to overwrite non-null Basic Q4 EPS: {row['symbol']} {row['period']['end']}")
        if grade == "A":
            raise RuntimeError(f"refusing to overwrite grade-A Basic Q4 EPS: {row['symbol']} {row['period']['end']}")
    for row in valid:
        if (row["symbol"], row["period"]["end"]) in already_applied:
            continue
        doc = by_symbol[row["symbol"]]
        q = doc["quarterly"]
        i = next(i for i, (fy, end, fq) in enumerate(zip(q["fy"], q["end"], q["fq"]))
                 if fq == 4 and fy == row["period"]["fiscalYear"] and end == row["period"]["end"])
        values = q.setdefault("values", {})
        column = values.setdefault("epsBasic", [None] * len(q["end"]))
        while len(column) < len(q["end"]):
            column.append(None)
        if column[i] is not None:
            raise RuntimeError(f"Basic EPS changed before write: {row['symbol']} {row['period']['end']}")
        column[i] = round(float(row["value"]), 4)
        values["epsBasic"] = column
        grades = q.setdefault("epsQuality", [None] * len(q["end"]))
        while len(grades) < len(q["end"]):
            grades.append(None)
        grade = grades[i] if isinstance(grades[i], dict) else {}
        grade["epsBasic"] = "B"
        grades[i] = grade
        q["epsQuality"] = grades
        doc["quarterly"] = q
        trace = doc.setdefault("secQ4EpsFacts", {}).setdefault(row["period"]["end"], {})
        trace["epsBasic"] = {"concept": "NetIncomeLossAvailableToCommonStockholdersBasic + WeightedAverageNumberOfSharesOutstandingBasic",
                             "unit": "USD/shares", "start": row["period"]["start"],
                             "end": row["period"]["end"], "durationDays": row["period"]["days"],
                             "filed": row["source"]["version"]["annual"]["filed"],
                             "accession": row["source"]["version"]["annual"]["accession"],
                             "value": column[i], "currency": "USD", "grade": "B",
                             "method": row["method"], "calculation": row["calculation"],
                             "source": row["source"], "validation": row["validation"]}
        quality = doc.setdefault("quality", {})
        issues = set(quality.get("issues") or [])
        issues.add("q4-eps-from-shares")
        quality["issues"] = [code for code in fin.ISSUES if code in issues] + sorted(issues - set(fin.ISSUES))
    write_decisions(decisions, ledger_path)
    for symbol, doc in by_symbol.items():
        write_atomic(str(Path(financials_dir) / f"{symbol}.json"), doc)
    return len(valid) - len(already_applied)


def _accounting_treatment_evidence(text):
    found = []
    for label, pattern in (("participating-securities", r"participating securities"),
                           ("two-class-eps-method", r"two[- ]class method"),
                           ("preferred-dividends", r"dividends on preferred (?:shares|stock)")):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            excerpt = re.sub(r"\s+", " ", text[max(0, match.start() - 180):match.end() + 240]).strip()
            found.append({"treatment": label, "excerpt": excerpt[:600]})
    return found


def apply_numerator_reviews(decisions_path=DECISIONS_PATH, review_path=NUMERATOR_REVIEW_PATH,
                            financials_dir=FINANCIALS, report_path=REPORT_PATH,
                            state_path=STATE_PATH):
    """Apply accession-bound EPS-note decisions; only reviewed null fields can be filled."""
    decisions_path, financials_dir = Path(decisions_path), Path(financials_dir)
    decisions = [json.loads(line) for line in decisions_path.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    reviews = load_basic_eps_numerator_reviews(review_path)
    changed = []
    for row in decisions:
        symbol = row.get("symbol")
        period = row.get("period") or {}
        key = (symbol, int(period.get("fiscalYear", -1)))
        review = reviews.get(key)
        if review is None:
            continue
        if row.get("field") != "epsBasic" or review.get("periodEnd") != period.get("end"):
            raise RuntimeError(f"numerator review period mismatch: {symbol} {key[1]}")
        checks = row.get("inputFactChecks") or {}
        facts = {label: (checks.get(label) or {}).get("sourceFact") or {}
                 for label in ("annualIncome", "nineMonthIncome", "annualBasicShares", "nineMonthBasicShares")}
        failure = basic_eps_numerator_review_failure(review, facts)
        if failure and failure.startswith("basic-eps-numerator-review-source-"):
            raise RuntimeError(f"numerator review source binding failed: {symbol} {key[1]} {failure}")
        if review.get("decision") == "approved" and failure:
            raise RuntimeError(f"cannot apply approved numerator review: {symbol} {key[1]} {failure}")
        if review.get("decision") != "approved":
            if row.get("grade") != "D" or row.get("status") != "retained-missing":
                raise RuntimeError(f"unexpected retained-missing status for numerator review: {symbol} {key[1]}")
            row.update({"grade": "D", "status": "retained-missing", "value": None,
                        "candidateValue": row.get("candidateValue", row.get("value")),
                        "validation": {"passed": False, "failureReason": review.get("failureReason")},
                        "epsNumeratorNoteReview": review})
            row["epsNumeratorNoteReview"] = review
            continue
        if row.get("grade") == "B" and row.get("status") == "validated":
            row["epsNumeratorNoteReview"] = review
            row.setdefault("source", {})["epsNumeratorNoteReview"] = review
            continue
        if row.get("grade") != "D" or row.get("status") != "retained-missing":
            raise RuntimeError(f"unexpected candidate status for approved review: {symbol} {key[1]}")
        candidate = row.get("candidateValue")
        if candidate is None:
            raise RuntimeError(f"approved candidate value is missing: {symbol} {key[1]}")
        row.update({"grade": "B", "status": "validated", "value": candidate,
                    "validation": {"passed": True, "failureReason": None},
                    "epsNumeratorNoteReview": review})
        row.setdefault("source", {})["epsNumeratorNoteReview"] = review
        changed.append({"symbol": symbol, "fiscalYear": key[1], "value": candidate})

    filled = apply_records(decisions, financials_dir, decisions_path)
    # Keep retained-missing explanations in the financial files as well as the decision ledger.
    by_symbol = {}
    for row in decisions:
        review = row.get("epsNumeratorNoteReview")
        if row.get("grade") != "D" or not review:
            continue
        symbol, end = row["symbol"], row["period"]["end"]
        doc = by_symbol.setdefault(symbol, read_json(financials_dir / f"{symbol}.json"))
        q = doc.get("quarterly") or {}
        indexes = [i for i, (fy, period_end, fq) in enumerate(zip(q.get("fy") or [], q.get("end") or [],
                                                                   q.get("fq") or []))
                   if fq == 4 and fy == row["period"]["fiscalYear"] and period_end == end]
        if len(indexes) != 1:
            raise RuntimeError(f"reviewed D Q4 period is not unique: {symbol} {end}")
        index = indexes[0]
        values = ((q.get("values") or {}).get("epsBasic") or [])
        grades = q.get("epsQuality") or []
        existing = values[index] if index < len(values) else None
        grade = grades[index].get("epsBasic") if index < len(grades) and isinstance(grades[index], dict) else None
        if existing is not None or grade not in {None, "D"}:
            raise RuntimeError(f"refusing to alter non-null or non-D reviewed period: {symbol} {end}")
        trace = doc.setdefault("secQ4EpsFacts", {}).setdefault(end, {}).setdefault("epsBasic", {})
        trace.update({"grade": "D", "value": None, "status": "retained-missing",
                      "failureReason": review.get("failureReason"),
                      "rejectedCandidateValue": row.get("candidateValue"),
                      "epsNumeratorNoteReview": review})
    for symbol, doc in by_symbol.items():
        write_atomic(str(financials_dir / f"{symbol}.json"), doc)
    metadata_refresh = refresh_applied_source_metadata(decisions_path, state_path, financials_dir)
    accepted = sum(row.get("grade") == "B" and row.get("status") == "validated" for row in decisions)
    rejected = sum(row.get("decision") == "retain-missing" for row in reviews.values())
    report = read_json(report_path) if Path(report_path).exists() else {
        "schema": "q4-eps-basic-B-source-audit-v1"}
    report.update({"status": "applied-and-reverified", "grossBWriteCountBeforeSemanticReview": accepted + rejected,
                   "sourceValidatedGradeB": accepted, "filledNullFields": accepted,
                   "quarantinedUnresolvedAllocations": rejected,
                   "numeratorReview": {"reviewed": len(reviews), "approved": accepted,
                                       "retainedMissing": rejected, "restoredNullFields": filled,
                                       "restoredCandidates": changed,
                                       "sourceMetadataRefresh": metadata_refresh}})
    write_atomic(str(report_path), report)
    return report["numeratorReview"]


def refresh_applied_source_metadata(decisions_path=DECISIONS_PATH, state_path=STATE_PATH,
                                    financials_dir=FINANCIALS):
    """Re-audit accepted B filing evidence and enrich traces without changing EPS values."""
    decisions_path, financials_dir = Path(decisions_path), Path(financials_dir)
    state = _json_state(Path(state_path))
    fetch_result = {"sources": state.get("sources") or {}}
    decisions = [json.loads(line) for line in decisions_path.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    by_symbol = {}
    accepted = [row for row in decisions if row.get("grade") == "B" and row.get("status") == "validated"]
    fact_count = 0
    for row in accepted:
        checks = row.get("inputFactChecks") or {}
        if len(checks) != 7:
            raise RuntimeError(f"expected seven filed input facts for {row['symbol']} {row['period']}")
        proof_by_fact = {}
        annual_text = ""
        for label, check in checks.items():
            fact = dict(check.get("sourceFact") or {})
            fact["value"] = fact.get("originalValue")
            fact["val"] = fact.get("originalValue")
            cik, accession = sec.cik10(fact.get("cik")), str(fact.get("accession") or "")
            entry = fetch_result["sources"].get(cik + ":" + accession) or {}
            expected_sha = (check.get("filingSha256") or fact.get("filingSha256"))
            if entry.get("status") != "ok" or not entry.get("htmlCache") or entry.get("sha256") != expected_sha:
                raise RuntimeError(f"cached filing hash/status changed: {row['symbol']} {label} {accession}")
            verified, problem = _verified_fact(fact, fetch_result, role=label)
            if problem or verified.get("rawValue") != str(fact.get("rawValue")):
                raise RuntimeError(f"raw filing fact no longer verifies: {row['symbol']} {label}: {problem}")
            parsed = _parse_filing(entry["htmlCache"])
            count, proof = _share_class_proof(parsed, fact, fact.get("start"), fact.get("end"))
            if count != 1:
                raise RuntimeError(f"single registered common class is not proven: {row['symbol']} {label}: {proof}")
            proof.update({"cik": cik, "accession": accession, "filed": fact.get("filed"),
                          "form": fact.get("form"), "concept": fact.get("concept"),
                          "unit": fact.get("unit"), "start": fact.get("start"),
                          "end": fact.get("end"), "filingUrl": fact.get("filingUrl"),
                          "filingSha256": entry["sha256"]})
            proof_by_fact[label] = proof
            if label == "annualIncome":
                annual_text = parsed.get("filingText", "")
            fact_count += 1

        registered_labels = sorted({proof["shareClass"] for proof in proof_by_fact.values()})
        if len(registered_labels) != 1:
            raise RuntimeError(f"registered common-class label changes across filings: {row['symbol']} {registered_labels}")
        body_class_references = sorted({name for proof in proof_by_fact.values()
                                        for name in proof.get("filingTextClasses", [])})
        row["shareClassReconciliation"] = {
            "registeredCommonClass": registered_labels[0],
            "registeredCommonClassCountAcrossPeriods": len(registered_labels),
            "namedCommonClassReferencesInFilingText": body_class_references,
            "note": ("Cover title uses generic Common Stock/Shares while an annual statement uses a Class A label. "
                     "The 10-K/10-Q cover tables show one registered common class and no second class was found."
                     if registered_labels[0] == "Common" and body_class_references else None),
        }

        path = financials_dir / f"{row['symbol']}.json"
        doc = by_symbol.setdefault(row["symbol"], read_json(path))
        q = doc.get("quarterly") or {}
        matches = [i for i, (fy, end, fq) in enumerate(zip(q.get("fy") or [], q.get("end") or [], q.get("fq") or []))
                   if fq == 4 and fy == row["period"]["fiscalYear"] and end == row["period"]["end"]]
        if len(matches) != 1:
            raise RuntimeError(f"B Q4 period is no longer unique: {row['symbol']} {row['period']}")
        i = matches[0]
        values = ((q.get("values") or {}).get("epsBasic") or [])
        grades = q.get("epsQuality") or []
        existing = values[i] if i < len(values) else None
        grade = grades[i].get("epsBasic") if i < len(grades) and isinstance(grades[i], dict) else None
        if (grade != "B" or existing is None
                or float(existing) != round(float(row["value"]), 4)):
            raise RuntimeError(f"accepted B numeric value/grade changed: {row['symbol']} {row['period']['end']}")
        trace = (((doc.get("secQ4EpsFacts") or {}).get(row["period"]["end"]) or {}).get("epsBasic") or {})
        if (trace.get("grade") != "B" or trace.get("method") != row.get("method")
                or float(trace.get("value")) != float(existing)):
            raise RuntimeError(f"accepted B provenance no longer matches data: {row['symbol']} {row['period']['end']}")
        row["shareClassProof"] = proof_by_fact
        row.setdefault("source", {})["shareClassProof"] = proof_by_fact
        row["source"]["shareClassReconciliation"] = row["shareClassReconciliation"]
        row["source"]["accountingTreatmentEvidence"] = _accounting_treatment_evidence(annual_text)
        trace["source"] = row["source"]
        doc["secQ4EpsFacts"][row["period"]["end"]]["epsBasic"] = trace
    write_decisions(decisions, decisions_path)
    for symbol, doc in by_symbol.items():
        write_atomic(str(financials_dir / f"{symbol}.json"), doc)
    return {"status": "source-metadata-refreshed-no-eps-value-changes",
            "acceptedGradeBPeriods": len(accepted), "rawFilingFactsReverified": fact_count,
            "singleRegisteredCommonClassProofs": fact_count,
            "registeredCommonClasses": dict(Counter(row["shareClassReconciliation"]["registeredCommonClass"]
                                                       for row in accepted)),
            "numericEpsFieldsChanged": 0,
            "symbols": sorted({row["symbol"] for row in accepted})}


def write_decisions(decisions, ledger_path=DECISIONS_PATH):
    path = Path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as output:
        temporary = Path(output.name)
        for row in decisions:
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=FINANCIALS)
    parser.add_argument("--cache", type=Path, default=EVIDENCE_CACHE)
    parser.add_argument("--filings-cache", type=Path, default=FILINGS_CACHE)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--decisions", type=Path, default=DECISIONS_PATH)
    parser.add_argument("--budget", type=float, default=300)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--refresh-applied-source-metadata", action="store_true",
                        help="reverify SEC evidence and refresh existing grade-B provenance only")
    parser.add_argument("--apply-numerator-reviews", action="store_true",
                        help="apply accession-bound Basic EPS numerator-note review decisions")
    args = parser.parse_args(argv)
    if args.budget <= 0:
        parser.error("--budget must be greater than zero")
    if args.apply_numerator_reviews:
        result = apply_numerator_reviews(args.decisions, NUMERATOR_REVIEW_PATH, args.dir, args.report)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.refresh_applied_source_metadata:
        result = refresh_applied_source_metadata(args.decisions, args.state, args.dir)
        try:
            report = read_json(args.report)
        except (OSError, ValueError):
            report = {"schema": "q4-eps-basic-B-source-audit-v1"}
        report["sourceMetadataRefresh"] = result
        report["status"] = "applied-and-reverified"
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    gate_passed, gate = _gate(args.dir / "q4-eps-basic-B-backtest.json")
    if not gate_passed:
        report = {"schema": "q4-eps-basic-B-source-audit-v1", "status": "validation-threshold-failed-no-write",
                  "subgroup": {"key": GROUP, **gate}}
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "subgroup": report["subgroup"]}, ensure_ascii=False))
        return 2
    candidates, reasons = scan_candidates(args.dir, args.cache)
    fetched = fetch_filings(candidates, cache=args.filings_cache, state_path=args.state, budget=args.budget)
    if fetched["status"] in {"paused", "stopped-403", "fetch-error", "refused"}:
        report = {"schema": "q4-eps-basic-B-source-audit-v1", "status": fetched["status"],
                  "reason": fetched.get("reason"), "subgroup": {"key": GROUP, **gate},
                  "candidateCount": len(candidates), "candidateExclusions": dict(reasons),
                  "filingFetch": {key: value for key, value in fetched.items() if key != "sources"}}
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "candidateCount": len(candidates),
                          "filingFetch": report["filingFetch"]}, ensure_ascii=False))
        return 2 if fetched["status"] == "stopped-403" else 0
    decisions, decision_counts = validate_candidates(candidates, fetched)
    write_decisions(decisions, args.decisions)
    filled = 0
    if args.apply:
        filled = apply_records(decisions, args.dir, args.decisions)
    report = {"schema": "q4-eps-basic-B-source-audit-v1",
              "status": "applied" if args.apply else "validated-no-write",
              "method": "Basic EPS from common-attributable annual minus 9M income, divided by actual-day weighted Q4 basic shares",
              "subgroup": {"key": GROUP, **gate}, "candidateCount": len(candidates),
              "sourceValidatedGradeB": decision_counts["validated-grade-B"],
              "filledNullFields": filled,
              "candidateExclusions": dict(reasons), "rawFilingDecisionCounts": dict(decision_counts),
              "filingFetch": {key: value for key, value in fetched.items() if key != "sources"},
              "decisionsFile": str(args.decisions)}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
