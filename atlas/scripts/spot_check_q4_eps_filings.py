#!/usr/bin/env python3
"""Spot-check latest stored Q4 Basic/Diluted EPS against official SEC filing facts.

CompanyFacts are used only from the already populated missing-Q4 cache. Other symbols
are checked through their 10-K submissions and Inline XBRL filing, not CompanyFacts.
"""
import argparse
import csv
import hashlib
import gzip
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import repair_q4_eps as repair  # noqa: E402
import seed_financials_sec as sec  # noqa: E402
import sec_inline_xbrl_q4 as inline  # noqa: E402
from build_valuation_us import read_json  # noqa: E402

SYMBOLS = ("NFLX", "GOOGL", "TSLA", "NVDA", "AMZN", "WMT", "AMD", "AVGO", "CMG", "SMCI")
DEFAULT_DIR = ROOT / "docs/report/financials"
DEFAULT_CACHE = ROOT / ".cache/q4-eps-spot-check"
DEFAULT_FACT_CACHE = ROOT / ".cache/q4-eps-evidence/facts"
DEFAULT_OLD_STATE = ROOT / ".cache/q4-eps-sec-state.json"
DEFAULT_OUTPUT = ROOT / "docs/report/financials/q4-eps-spot-check.json"
DEFAULT_CSV = ROOT / "docs/report/financials/q4-eps-spot-check.csv"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


def _recent_rows(submissions):
    recent = ((submissions.get("filings") or {}).get("recent") or {})
    length = max((len(rows) for rows in recent.values() if isinstance(rows, list)), default=0)
    result = []
    for index in range(length):
        result.append({key: rows[index] for key, rows in recent.items()
                       if isinstance(rows, list) and index < len(rows)})
    return result


def _q4_target(document):
    annual = document.get("annual") or {}
    quarterly = document.get("quarterly") or {}
    annual_ends = {}
    for fy, end in zip(annual.get("fy") or [], annual.get("end") or []):
        annual_ends.setdefault(fy, []).append(end)
    candidates = []
    for index, (fy, fq, end) in enumerate(zip(quarterly.get("fy") or [], quarterly.get("fq") or [], quarterly.get("end") or [])):
        if fq != 4:
            continue
        try:
            q4_end = date.fromisoformat(end)
            annual_matches = [annual_end for annual_end in annual_ends.get(fy, [])
                              if abs((date.fromisoformat(annual_end) - q4_end).days) <= 10]
        except (TypeError, ValueError):
            annual_matches = []
        if len(annual_matches) == 1:
            candidates.append((end, fy, index))
    if not candidates:
        return None
    end, fy, index = max(candidates)
    return fy, end, index


def _file_value(document, end, fy, field):
    q = document.get("quarterly") or {}
    indexes = [i for i, (row_fy, row_end, fq) in enumerate(zip(
        q.get("fy") or [], q.get("end") or [], q.get("fq") or []))
        if fq == 4 and row_fy == fy and row_end == end]
    if len(indexes) != 1:
        return None, None
    index = indexes[0]
    values = q.get("values") or {}
    column = values.get(field) or []
    value = column[index] if index < len(column) else None
    grades = q.get("epsQuality") or []
    grade = grades[index].get(field) if index < len(grades) and isinstance(grades[index], dict) else None
    return value, grade


def _decode_cached_html(body):
    return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body


def _baseline_value(symbol, fy, end, field):
    try:
        document = json.loads(__import__("subprocess").check_output(
            ["git", "show", f"HEAD:docs/report/financials/{symbol}.json"], cwd=ROOT, text=True,
            stderr=__import__("subprocess").DEVNULL))
    except (OSError, ValueError, __import__("subprocess").CalledProcessError):
        return None
    return _file_value(document, end, fy, field)[0]


def _companyfacts_candidates(company, period_end):
    result = {"epsBasic": [], "epsDiluted": []}
    us_gaap = ((company.get("concepts") or {}) if isinstance(company, dict) else {})
    for field, concept in repair.EPS_CONCEPTS.items():
        for item in us_gaap.get(concept, []):
            start, end = item.get("start"), item.get("end")
            try:
                duration = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
                distance = abs((date.fromisoformat(end) - date.fromisoformat(period_end)).days)
            except (TypeError, ValueError):
                continue
            if item.get("unit") == "USD/shares" and 80 <= duration <= 100 and distance <= 5:
                result[field].append({**item, "durationDays": duration, "endDistanceDays": distance})
    return result


def _filing_candidates(parsed, period_end):
    result = {"epsBasic": [], "epsDiluted": []}
    names = {"epsBasic": "EarningsPerShareBasic", "epsDiluted": "EarningsPerShareDiluted"}
    target = date.fromisoformat(period_end)
    for field, concept in names.items():
        for fact in parsed.get("facts") or []:
            if fact.get("name") != concept or fact.get("unit") != "USD/shares" or fact.get("value") is None:
                continue
            context = fact.get("context") or {}
            try:
                start, end = date.fromisoformat(context.get("start")), date.fromisoformat(context.get("end"))
            except (TypeError, ValueError):
                continue
            days = (end - start).days + 1
            distance = abs((end - target).days)
            if 80 <= days <= 100 and distance <= 5:
                result[field].append({
                    "rawValue": str(fact["value"]), "start": context["start"], "end": context["end"],
                    "durationDays": days, "endDistanceDays": distance,
                    "unit": fact["unit"], "concept": fact["name"],
                    "contextRef": fact.get("contextRef"), "dimensions": context.get("dimensions") or [],
                })
    return result


def _choose_latest_10k(submissions, period_end):
    rows = [row for row in _recent_rows(submissions)
            if row.get("form") in {"10-K", "10-K/A"} and row.get("reportDate") == period_end
            and row.get("accessionNumber") and row.get("primaryDocument")]
    rows.sort(key=lambda row: (str(row.get("filingDate") or ""), str(row.get("accessionNumber") or "")), reverse=True)
    return rows[0] if rows else None


def _q4_earnings_8ks(submissions, period_end):
    """List Item 2.02 filings after fiscal year-end, in release order."""
    target = date.fromisoformat(period_end)
    rows = []
    for row in _recent_rows(submissions):
        if row.get("form") not in {"8-K", "8-K/A"} or not row.get("accessionNumber"):
            continue
        items = {part.strip() for part in str(row.get("items") or "").split(",")}
        if "2.02" not in items:
            continue
        try:
            filed = date.fromisoformat(row.get("filingDate"))
        except (TypeError, ValueError):
            continue
        if target < filed <= target + timedelta(days=120):
            rows.append(row)
    rows.sort(key=lambda row: (row.get("filingDate", ""), row.get("accessionNumber", "")))
    return rows


def _choose_q4_earnings_8k(submissions, period_end):
    rows = _q4_earnings_8ks(submissions, period_end)
    return rows[0] if rows else None


def _archive_base(cik, filing):
    accession_path = filing["accessionNumber"].replace("-", "")
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/",
            accession_path)


def _read_archive_index(cik, filing, symbol_dir, client):
    base, accession_path = _archive_base(cik, filing)
    index_path = Path(symbol_dir) / f"{accession_path}-index.json"
    if index_path.exists():
        return read_json(index_path), base
    index = client.get_json(base + "index.json")
    index_path.write_text(json.dumps(index), encoding="utf-8")
    return index, base


def _ex99_document(index):
    items = (((index or {}).get("directory") or {}).get("item") or [])
    candidates = []
    for item in items:
        name = str(item.get("name") or "")
        compact = re.sub(r"[^a-z0-9]", "", name.lower())
        if "ex991" in compact or "exhibit991" in compact:
            candidates.append(name)
    return sorted(candidates, key=lambda name: ("99.2" in name.lower() or "99_2" in name.lower(), len(name), name))[0] if candidates else None


def _release_documents(index):
    items = (((index or {}).get("directory") or {}).get("item") or [])
    matches = []
    for item in items:
        name = str(item.get("name") or "")
        lowered = name.lower()
        if not lowered.endswith((".htm", ".html")):
            continue
        if not re.search(r"q4|earnings|results|release|ex99|exhibit99|businessupdate|(?:^|\W)pr(?:\W|$)", lowered):
            continue
        if re.search(r"index|headers|r1|summary", lowered):
            continue
        matches.append(name)
    return sorted(set(matches), key=lambda name: (
        not bool(re.search(r"earnings|results|release|q4", name.lower())),
        not bool(re.search(r"ex99|exhibit99", name.lower())), name.lower()))


def _get_q4_release_candidates(symbol, cik, submissions, period_end, cache, client):
    filings = _q4_earnings_8ks(submissions, period_end)
    if not filings:
        return {"epsBasic": [], "epsDiluted": []}, {"reason": "no Item 2.02 8-K filed within 120 days after Q4 end"}
    symbol_dir = Path(cache) / symbol
    results = {"epsBasic": [], "epsDiluted": []}
    sources = []
    for filing in filings:
        index, base = _read_archive_index(cik, filing, symbol_dir, client)
        docs = _release_documents(index)
        if not docs:
            continue
        base, accession_path = _archive_base(cik, filing)
        for document_name in docs[:4]:
            url = base + document_name
            body_path = symbol_dir / f"{accession_path}-{Path(document_name).name}"
            if body_path.exists():
                body = _decode_cached_html(body_path.read_bytes())
            else:
                body = client.get_bytes(url)
                body_path.write_bytes(body)
            parsed = inline.parse_inline_xbrl(body)
            candidates = _filing_candidates(parsed, period_end)
            extracted = inline.extract_filing_text(body)
            text = extracted["text"]
            lower = text.lower()
            has_eps_text = any(term in lower for term in (
                "earnings per share", "diluted eps", "basic eps", "per diluted share",
                "net income per share", " eps attributable to common stockholders", " eps "))
            if not has_eps_text and not any(candidates.values()):
                continue
            source = {
                "provider": "SEC EDGAR Item 2.02 8-K earnings release",
                "accession": filing["accessionNumber"], "filed": filing.get("filingDate"),
                "form": filing.get("form"), "reportDate": filing.get("reportDate"), "items": filing.get("items"),
                "exhibit": document_name, "url": url, "filingUrl": base,
                "filingSha256": hashlib.sha256(body).hexdigest(),
                "inlineFactCount": sum(len(items) for items in candidates.values()),
                "epsTextFound": has_eps_text,
                "epsRows": [row for table in extracted["tables"] for row in table
                            if any(term in " ".join(row).lower() for term in
                                   ("earnings per share", "diluted eps", "basic eps", "per diluted share",
                                    "net income per share", " eps attributable to common stockholders", " eps "))],
            }
            sources.append(source)
            for field in repair.EPS_CONCEPTS:
                if candidates.get(field):
                    results[field].extend({**item, "provider": "SEC EDGAR Inline XBRL 8-K earnings release",
                                           "accession": filing["accessionNumber"], "filed": filing.get("filingDate"),
                                           "form": filing.get("form"), "url": url, "filingUrl": base,
                                           "exhibit": document_name} for item in candidates[field])
        if any(results.values()):
            break
    return results, ({"earningsReleases": sources} if sources else {"reason": "no EPS release document found among Item 2.02 8-K exhibits"})


def _get_filing_candidates(symbol, cik, period_end, cache, client):
    symbol_dir = Path(cache) / symbol
    symbol_dir.mkdir(parents=True, exist_ok=True)
    submissions_path = symbol_dir / "submissions.json"
    if submissions_path.exists():
        submissions = read_json(submissions_path)
    else:
        submissions = client.get_json(SUBMISSIONS_URL.format(cik=sec.cik10(cik)))
        if submissions is None:
            return None, {"reason": "SEC submissions returned 404"}
        submissions_path.write_text(json.dumps(submissions), encoding="utf-8")
    filing = _choose_latest_10k(submissions, period_end)
    if filing is None:
        return None, {"reason": "no recent 10-K with matching annual reportDate"}
    accession = filing["accessionNumber"]
    accession_path = accession.replace("-", "")
    url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/"
           f"{filing['primaryDocument']}")
    body_path = symbol_dir / f"{accession_path}.html"
    if body_path.exists():
        body = _decode_cached_html(body_path.read_bytes())
    else:
        body = client.get_bytes(url)
        body_path.write_bytes(body)
    parsed = inline.parse_inline_xbrl(body)
    candidates = _filing_candidates(parsed, period_end)
    for values in candidates.values():
        for item in values:
            item.update({"provider": "SEC EDGAR Inline XBRL 10-K", "accession": accession,
                         "filed": filing.get("filingDate"), "form": filing.get("form"),
                         "url": url,
                         "filingUrl": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/"})
    source = {
        "provider": "SEC EDGAR Inline XBRL 10-K",
        "cik": sec.cik10(cik),
        "accession": accession,
        "filed": filing.get("filingDate"),
        "form": filing.get("form"),
        "reportDate": filing.get("reportDate"),
        "url": url,
        "filingUrl": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/",
        "filingSha256": hashlib.sha256(body).hexdigest(),
    }
    release_candidates, release_source = _get_q4_release_candidates(
        symbol, cik, submissions, period_end, cache, client)
    for field in repair.EPS_CONCEPTS:
        if not candidates.get(field):
            candidates[field] = release_candidates.get(field) or []
    source["earningsRelease"] = release_source
    return candidates, source


def run(*, financials_dir=DEFAULT_DIR, cache_dir=DEFAULT_CACHE, fact_cache=DEFAULT_FACT_CACHE,
        old_state_path=DEFAULT_OLD_STATE, output_path=DEFAULT_OUTPUT, csv_path=DEFAULT_CSV,
        budget=None, client=None, environ=None, clock=time.monotonic, reporter=print):
    environ = os.environ if environ is None else environ
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    state_path = cache_dir / "state.json"
    state = read_json(state_path) if state_path.exists() else {"version": 1, "companies": {}}
    try:
        old_state = read_json(old_state_path)
    except (OSError, ValueError):
        old_state = {}
    ticker_map = old_state.get("tickerMap") or {}
    reason = sec.refusal_reason(environ)
    if reason:
        return {"status": "refused", "reason": reason}
    try:
        user_agent = sec.resolve_user_agent(environ)
    except sec.UserAgentError as error:
        return {"status": "refused", "reason": str(error)}
    client = client or sec.SecClient(user_agent, stop_on_forbidden=True)
    started = clock()
    accounted_requests = client.requests
    results = []
    stopped403 = False
    for symbol in SYMBOLS:
        path = Path(financials_dir) / f"{symbol}.json"
        document = read_json(path)
        target = _q4_target(document)
        if target is None:
            results.append({"symbol": symbol, "status": "no-matching-q4-period"})
            continue
        fy, end, index = target
        current = {field: _file_value(document, end, fy, field) for field in repair.EPS_CONCEPTS}
        baseline = {field: _baseline_value(symbol, fy, end, field) for field in repair.EPS_CONCEPTS}
        old_candidate = (state.get("companies") or {}).get(symbol)
        if old_candidate and old_candidate.get("periodEnd") == end:
            source_candidates = old_candidate.get("candidates") or {}
            source = old_candidate.get("source") or {}
        else:
            cik = ticker_map.get(symbol)
            if not cik:
                results.append({"symbol": symbol, "fy": fy, "periodEnd": end,
                                "status": "no-cik-in-existing-ticker-map"})
                continue
            compact_path = Path(fact_cache) / f"{symbol}.json"
            source_candidates = {"epsBasic": [], "epsDiluted": []}
            companyfacts_source = None
            if compact_path.exists():
                company = read_json(compact_path)
                source_candidates = _companyfacts_candidates(company, end)
                companyfacts_source = {"provider": "existing SEC CompanyFacts cache",
                                       "url": sec.FACTS_URL.format(cik=sec.cik10(cik)),
                                       "cik": sec.cik10(cik), "payloadSha256": company.get("canonicalPayloadSha256")}
            if any(not source_candidates.get(field) for field in repair.EPS_CONCEPTS):
                if budget is not None and clock() - started >= budget:
                    results.append({"symbol": symbol, "status": "paused-before-sec-filing-fetch"})
                    break
                try:
                    filing_candidates, filing_source = _get_filing_candidates(symbol, cik, end, cache_dir, client)
                    for field in repair.EPS_CONCEPTS:
                        if not source_candidates.get(field):
                            source_candidates[field] = filing_candidates.get(field) or []
                except sec.SecForbidden as error:
                    state.setdefault("companies", {})[symbol] = {"periodEnd": end, "status": "stopped-403",
                                                                  "reason": str(error)}
                    state["requests"] = int(state.get("requests", 0)) + client.requests - accounted_requests
                    accounted_requests = client.requests
                    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    stopped403 = True
                    results.append({"symbol": symbol, "status": "stopped-403", "reason": str(error)})
                    break
                except (sec.FetchError, sec.TooManyFailures, OSError) as error:
                    results.append({"symbol": symbol, "status": "fetch-error", "reason": str(error)})
                    break
                source = {"provider": "SEC filing spot-check",
                          "cik": sec.cik10(cik),
                          "companyFactsCache": companyfacts_source,
                          "filing": filing_source,
                          "accession": filing_source.get("accession"),
                          "filed": filing_source.get("filed"),
                          "form": filing_source.get("form"),
                          "url": filing_source.get("url"),
                          "filingUrl": filing_source.get("filingUrl")}
            else:
                source = companyfacts_source
            state.setdefault("companies", {})[symbol] = {
                "periodEnd": end, "fiscalYear": fy, "status": "ok",
                "source": source, "candidates": source_candidates,
            }
            state["requests"] = int(state.get("requests", 0)) + client.requests - accounted_requests
            accounted_requests = client.requests
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        qvalues = (document.get("quarterly") or {}).get("values") or {}
        q4_income = repair._column_value(qvalues, "netIncome", index)
        q3_shares = repair._q3_diluted_shares(document, fy)
        release_sources = (((source.get("filing") or {}).get("earningsRelease") or {}).get("earningsReleases") or [])
        period_result = {"symbol": symbol, "fiscalYear": fy, "periodEnd": end,
                         "source": source, "fields": {}}
        for field in repair.EPS_CONCEPTS:
            facts = source_candidates.get(field) or []
            values = sorted({str(fact.get("value", fact.get("rawValue"))) for fact in facts})
            same_dimensions = len({json.dumps(fact.get("dimensions") or [], sort_keys=True) for fact in facts})
            field_result = {
                "before": baseline[field], "after": current[field][0], "grade": current[field][1],
                "reportedCandidates": facts,
                "reportedValueUnique": values[0] if len(values) == 1 else None,
                "candidateValueCount": len(values),
                "dimensionVariants": same_dimensions,
                "officialReleaseSources": [
                    {"accession": item.get("accession"), "filed": item.get("filed"),
                     "exhibit": item.get("exhibit"), "url": item.get("url"),
                     "filingSha256": item.get("filingSha256"),
                     "epsRows": item.get("epsRows") or []}
                    for item in release_sources if item.get("epsRows")
                ],
                "status": "verified-by-SEC-structured-fact" if len(values) == 1 else (
                    "official-release-rows-require-period-mapping" if any(item.get("epsRows") for item in release_sources)
                    else ("no-direct-q4-fact-in-SEC-structured-facts-or-release-text" if not facts
                          else "ambiguous-direct-facts")),
            }
            if len(values) == 1:
                raw_value = float(values[0])
                adjusted, split_factor, rejected = repair.normalize_and_check_q4_eps(
                    document, end, q4_income, q3_shares, {"value": raw_value})
                field_result.update({"splitFactor": split_factor,
                                     "reportedValueOnCurrentBasis": raw_value / split_factor if split_factor else None,
                                     "storedBasisCrosscheckResult": rejected,
                                     "reportedValueMatchesAfterWithinCent": (
                                         current[field][0] is not None and adjusted is not None
                                         and abs(float(current[field][0]) - adjusted) <= 0.01),
                                     "impliedCurrentBasisValue": adjusted})
            period_result["fields"][field] = field_result
        results.append(period_result)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reconciliation_path = Path(output_path).with_name("q4-eps-spot-check-reconciliations.json")
    try:
        reconciliation = read_json(reconciliation_path)
    except (OSError, ValueError):
        reconciliation = {}
    direct_by_key = {
        (row.get("symbol"), row.get("periodEnd"), row.get("field")): row
        for row in reconciliation.get("records", [])
    }
    for result in results:
        fields = result.get("fields") or {}
        for field, item in fields.items():
            source_row = direct_by_key.get((result.get("symbol"), result.get("periodEnd"), field))
            if source_row is None:
                continue
            item["directQ4FilingEvidence"] = {
                "reportedValue": source_row.get("value"),
                "currentBasisValue": source_row.get("adjustedValue"),
                "source": source_row.get("source"),
                "validation": source_row.get("validation"),
            }
            item["reportedValueUnique"] = source_row.get("value")
            item["impliedCurrentBasisValue"] = source_row.get("adjustedValue")
            item["reportedValueMatchesAfterWithinCent"] = (
                item.get("after") is not None and source_row.get("adjustedValue") is not None
                and abs(float(item["after"]) - float(source_row["adjustedValue"])) <= 0.01
            )
            item["status"] = "verified-by-direct-sec-q4-filing-row"
    output = {"schema": "q4-eps-spot-check-v1",
              "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "status": "stopped-403" if stopped403 else ("paused" if any(r.get("status", "").startswith("paused") for r in results) else "done"),
              "symbols": list(SYMBOLS), "results": results, "requestCountThisRun": client.requests,
              "totalRequests": state.get("requests", 0),
              "companyfactsPolicy": "use only the existing missing-Q4 cache; inspect filed 10-K Inline XBRL and Q4 Item 2.02 releases, with manually period-mapped visible GAAP rows for the ten-symbol spot check"}
    Path(output_path).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = []
    for result in results:
        for field, data in (result.get("fields") or {}).items():
            direct_source = data.get("directQ4FilingEvidence", {}).get("source") or {}
            rows.append({"symbol": result["symbol"], "fiscalYear": result.get("fiscalYear"),
                         "periodEnd": result.get("periodEnd"), "field": field,
                         "before": data.get("before"), "after": data.get("after"),
                         "reportedValue": data.get("reportedValueUnique"),
                         "currentBasisValue": data.get("impliedCurrentBasisValue"),
                         "splitFactor": data.get("splitFactor"), "grade": data.get("grade"),
                         "status": data.get("status"), "sourceAccession": (result.get("source") or {}).get("accession"),
                         "sourceUrl": (result.get("source") or {}).get("url"),
                         "directSourceAccession": direct_source.get("accession"),
                         "directSourceUrl": direct_source.get("url"),
                         "directEvidence": direct_source.get("evidence")})
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
    with Path(csv_path).open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=("symbol", "fiscalYear", "periodEnd", "field", "before", "after",
                                                         "reportedValue", "currentBasisValue", "splitFactor", "grade", "status",
                                                         "sourceAccession", "sourceUrl", "directSourceAccession", "directSourceUrl",
                                                         "directEvidence"))
        writer.writeheader()
        writer.writerows(rows)
    reporter(json.dumps({"status": output["status"], "symbolsProcessed": sum(1 for r in results if r.get("fields")),
                         "requests": client.requests, "output": str(output_path)}, sort_keys=True))
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--fact-cache", type=Path, default=DEFAULT_FACT_CACHE)
    parser.add_argument("--old-state", type=Path, default=DEFAULT_OLD_STATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--budget", type=float)
    args = parser.parse_args(argv)
    if args.budget is not None and args.budget <= 0:
        parser.error("--budget must be greater than zero")
    result = run(financials_dir=args.dir, cache_dir=args.cache, fact_cache=args.fact_cache,
                 old_state_path=args.old_state, output_path=args.output, csv_path=args.csv, budget=args.budget)
    print(json.dumps({key: result.get(key) for key in ("status", "requestCountThisRun", "totalRequests")}, sort_keys=True))
    return 2 if result.get("status") == "stopped-403" else 0


if __name__ == "__main__":
    raise SystemExit(main())
