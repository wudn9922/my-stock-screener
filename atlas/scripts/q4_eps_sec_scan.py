#!/usr/bin/env python3
"""Build a resumable cache of historical SEC filings relevant to stored Q4 periods.

This is a read-only discovery/cache tool. It never changes financial data. It reads the stored
quarter labels, resolves the issuer CIK from SEC provenance already recorded on those periods when
possible, retrieves the SEC submissions record and every historical block listed in ``filings.files``,
then caches relevant filing documents by accession. A filing is matched by its reported period date
(or a documented comparative period), not by how many days after quarter end it was filed.

SEC's submissions endpoint is queried per CIK. This script does not request a bulk submissions ZIP:
the per-CIK response explicitly lists the historical JSON blocks needed for this scan, and no
documented ZIP size/availability is assumed here.

Usage: python atlas/scripts/q4_eps_sec_scan.py --budget 600
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

SCRIPTS = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS.parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import seed_financials_sec as sec  # noqa: E402
from build_valuation_us import read_json, write_atomic  # noqa: E402

DEFAULT_FINANCIALS_DIR = REPO_ROOT / "docs/report/financials"
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache/q4-eps-sec-scan"
DEFAULT_STATE = DEFAULT_CACHE_DIR / "state.json"
EXISTING_RAW_CACHE_DIR = REPO_ROOT / ".cache/q4-eps-evidence/raw-filings"
EXISTING_RAW_STATE = REPO_ROOT / ".cache/q4-eps-evidence/raw-filing-state.json"
STATE_VERSION = 1
CANDIDATE_RULES_VERSION = 10
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_BLOCK_URL = "https://data.sec.gov/submissions/{name}"
ARCHIVES_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json"
ARCHIVES_DOCUMENT_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{name}"

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "10-KT", "10-KT/A", "20-F", "20-F/A", "40-F", "40-F/A"})
QUARTER_FORMS = frozenset({"10-Q", "10-Q/A"})
CURRENT_FORMS = frozenset({"8-K", "8-K/A"})
FOREIGN_CURRENT_FORMS = frozenset({"6-K", "6-K/A"})
RELEVANT_FORMS = ANNUAL_FORMS | QUARTER_FORMS | CURRENT_FORMS | FOREIGN_CURRENT_FORMS
PERIOD_END_TOLERANCE_DAYS = 7  # accommodates 52/53-week fiscal-end and stored-label drift.


def _iso_date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _cik(value):
    if isinstance(value, bool):
        return None
    raw = str(value or "").strip()
    if not raw.isdigit() or not 1 <= len(raw) <= 10 or int(raw) <= 0:
        return None
    return raw.zfill(10)


def _official_companyfacts_cik(record):
    """Accept a stored CIK only when it is tied to the official SEC provenance URL/method."""
    if not isinstance(record, dict):
        return None
    cik = _cik(record.get("cik"))
    if not cik:
        return None
    method = str(record.get("method") or "").lower()
    for field in ("url", "source", "companyFactsUrl"):
        value = record.get(field)
        if not isinstance(value, str):
            continue
        parsed = urlparse(value)
        match = re.fullmatch(r"/api/xbrl/companyfacts/CIK(\d{10})\.json", parsed.path)
        if parsed.scheme == "https" and parsed.netloc == "data.sec.gov" and match:
            return cik if match.group(1) == cik else None
    # A method label and numeric CIK can be stale or copied onto another issuer's record.
    # Only the exact official CompanyFacts URL binds this record to a filer identity.
    return None


def _q4_evidence_cik(document, period_end):
    """Look for SEC identity on the EPS evidence associated with this actual Q4 end."""
    evidence = document.get("secQ4EpsFacts")
    found = []
    row = evidence.get(period_end) if isinstance(evidence, dict) else None
    row = row if isinstance(row, dict) else {}
    for fact in row.values():
        cik = _official_companyfacts_cik(fact)
        if not cik and isinstance(fact, dict):
            cited_cik = _cik(fact.get("cik"))
            accession = str(fact.get("accession") or "")
            expected_accession = re.sub(r"[^0-9]", "", accession)
            for field in ("url", "filingUrl", "sourceUrl"):
                value = fact.get(field)
                if not isinstance(value, str):
                    continue
                parsed = urlparse(value)
                match = re.fullmatch(r"/Archives/edgar/data/(\d+)/(\d+)(?:/[^/]+)?/?", parsed.path)
                archive_cik = _cik(match.group(1)) if match else None
                if (parsed.scheme == "https" and parsed.netloc.lower() == "www.sec.gov" and match
                        and expected_accession and match.group(2) == expected_accession
                        and (not cited_cik or match.group(1) == str(int(cited_cik)))):
                    # The archive path identifies the filer of this accession. That filer can
                    # be a historical predecessor CIK, which is preferable to today's ticker map.
                    cik = archive_cik
                    break
        if cik:
            found.append(cik)
    # Older direct filing ledgers are stored beside the CompanyFacts records.
    source_rows = document.get("q4EpsSources")
    source_row = source_rows.get(period_end) if isinstance(source_rows, dict) else None
    if isinstance(source_row, dict):
        # Current ledger records are field-keyed; the legacy exhibit writer stored one
        # flat record at the period key. Inspect both shapes, but bind every CIK to the
        # accession URL instead of trusting the ticker or record's CIK alone.
        source_facts = ([source_row] if source_row.get("accession") or source_row.get("url")
                        or source_row.get("filingUrl") else list(source_row.values()))
        for fact in source_facts:
            if not isinstance(fact, dict):
                continue
            cited_cik = _cik(fact.get("cik"))
            accession = str(fact.get("accession") or "")
            expected_accession = re.sub(r"[^0-9]", "", accession)
            parsed = urlparse(str(fact.get("url") or fact.get("filingUrl") or ""))
            match = re.fullmatch(r"/Archives/edgar/data/(\d+)/(\d+)(?:/[^/]+)?/?", parsed.path)
            archive_cik = _cik(match.group(1)) if match else None
            if (parsed.scheme == "https" and parsed.netloc.lower() == "www.sec.gov" and match
                    and expected_accession and match.group(2) == expected_accession
                    and (not cited_cik or match.group(1) == str(int(cited_cik)))):
                found.append(archive_cik)
    if not found:
        return None
    # Basic and diluted evidence normally agree. On malformed mixed provenance, require a majority.
    counts = {value: found.count(value) for value in set(found)}
    best = max(counts, key=lambda value: (counts[value], value))
    return best if counts[best] > len(found) / 2 else None


def _raw_a_trace(document, period_end, field, current_value):
    """Whether a stored grade-A value carries the raw filing locator required by this scan."""
    facts = document.get("secQ4EpsFacts")
    fact = facts.get(period_end, {}).get(field) if isinstance(facts, dict) else None
    evidence = fact.get("rawFilingEvidence") if isinstance(fact, dict) else None
    if not isinstance(evidence, dict) or fact.get("grade") != "A":
        return False
    accession = str(evidence.get("accession") or "")
    cited_cik = _cik(evidence.get("cik") or (fact or {}).get("cik"))
    url = urlparse(str(evidence.get("url") or ""))
    archive = re.fullmatch(r"/Archives/edgar/data/(\d+)/(\d+)(?:/[^/]+)?/?", url.path)
    start, end = _iso_date(evidence.get("periodStart")), _iso_date(evidence.get("periodEnd"))
    duration = (end - start).days if start is not None and end is not None else None
    try:
        reported = float(evidence.get("reportedValue"))
        stored = float(current_value)
        normalized = float(evidence.get("normalizedValue"))
        fact_reported = float((fact or {}).get("reportedValue", reported))
    except (TypeError, ValueError, OverflowError):
        return False
    if (not archive or url.scheme != "https" or url.netloc.lower() != "www.sec.gov"
            or not cited_cik or archive.group(1) != str(int(cited_cik))
            or archive.group(2) != re.sub(r"[^0-9]", "", accession)
            or evidence.get("periodEnd") != period_end or duration is None or not 80 <= duration <= 100
            or evidence.get("field") != field or evidence.get("unit") != "USD/shares"
            or evidence.get("currency") != "USD" or not evidence.get("form")
            or not evidence.get("excerpt") or not isinstance(evidence.get("locator"), dict)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", str(evidence.get("filingSha256") or ""))
            or not math.isfinite(reported) or not math.isfinite(normalized) or not math.isfinite(stored)
            or not math.isfinite(fact_reported) or fact_reported != reported
            or normalized != stored or not evidence.get("splitBasisVerified")):
        return False
    return True


def q4_targets(document):
    """Return Q4 periods that need an original-source search or verification.

    Existing non-null values remain in ``existingValues`` and are never rewritten by this
    scanner. A value is omitted only when it already carries a raw filing trace meeting the
    minimum source-locator contract; a CompanyFacts grade by itself is not enough.
    """
    quarterly = document.get("quarterly") or {}
    ends = quarterly.get("end") or []
    fys = quarterly.get("fy") or []
    fqs = quarterly.get("fq") or []
    starts = quarterly.get("start") or []
    values = quarterly.get("values") or {}
    basic = values.get("epsBasic") or []
    diluted = values.get("epsDiluted") or []
    eps_quality = quarterly.get("epsQuality") or []
    all_q4_ends = sorted({_iso_date(end) for fq, end in zip(fqs, ends)
                          if fq == 4 and _iso_date(end) is not None})
    targets = []
    for index, (fy, fq, period_end) in enumerate(zip(fys, fqs, ends)):
        if fq != 4 or _iso_date(period_end) is None:
            continue
        basic_value = basic[index] if index < len(basic) else None
        diluted_value = diluted[index] if index < len(diluted) else None
        parsed_end = _iso_date(period_end)
        start = _iso_date(starts[index]) if index < len(starts) else None
        if start is None:
            q3_ends = [_iso_date(end) for row_fy, row_fq, end in zip(fys, fqs, ends)
                       if row_fy == fy and row_fq == 3 and _iso_date(end) is not None
                       and _iso_date(end) < parsed_end]
            if q3_ends:
                start = max(q3_ends) + timedelta(days=1)
        if start is not None and not 80 <= (parsed_end - start).days + 1 <= 100:
            start = None
        next_q4 = next((end for end in all_q4_ends if end > parsed_end), None)
        stored_facts = document.get("secQ4EpsFacts") or {}
        stored_period = stored_facts.get(period_end) if isinstance(stored_facts, dict) else {}
        stored_period = stored_period if isinstance(stored_period, dict) else {}
        values_by_field = {"epsBasic": basic_value, "epsDiluted": diluted_value}
        quality_row = eps_quality[index] if index < len(eps_quality) and isinstance(eps_quality[index], dict) else {}
        grades = {field: quality_row.get(field) or (
            (stored_period.get(field) or {}).get("grade") if isinstance(stored_period.get(field), dict) else None
        ) for field in values_by_field}
        missing = [field for field, value in values_by_field.items() if value is None]
        verify = [field for field, value in values_by_field.items()
                  if value is not None and not _raw_a_trace(document, period_end, field, value)
                  and not (field == "epsBasic" and grades[field] == "B")]
        preserved_exceptions = {"epsBasic": "existing-ADR-grade-B-exception-preserved"} \
            if grades["epsBasic"] == "B" and basic_value is not None else {}
        if not missing and not verify:
            continue
        targets.append({
            "fy": fy,
            "start": start.isoformat() if start else None,
            "end": period_end,
            "nextQ4End": next_q4.isoformat() if next_q4 else None,
            "missing": missing,
            "verificationFields": sorted(set(missing + verify)),
            "existingValues": {field: value for field, value in values_by_field.items() if value is not None},
            "existingGrades": grades,
            "preservedExceptions": preserved_exceptions,
            "provenanceCik": _q4_evidence_cik(document, period_end),
        })
    return sorted(targets, key=lambda target: (str(target.get("end") or ""), str(target.get("fy") or "")),
                  reverse=True)


def _filing_rows(payload):
    """Normalize the SEC recent-feed and archive-block submissions shapes to filing rows."""
    if not isinstance(payload, dict):
        return []
    filings = payload.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        recent = payload
    accessions = recent.get("accessionNumber") or []
    rows = []
    for index, accession in enumerate(accessions):
        if not isinstance(accession, str) or not accession.strip():
            continue
        row = {"accessionNumber": accession.strip()}
        for field in ("filingDate", "reportDate", "acceptanceDateTime", "form", "items", "primaryDocument",
                      "primaryDocDescription", "fileNumber", "filmNumber", "size"):
            values = recent.get(field)
            row[field] = values[index] if isinstance(values, list) and index < len(values) else None
        rows.append(row)
    return rows


def _merge_filing(left, right):
    merged = dict(left)
    for field, value in right.items():
        if value not in (None, "", []):
            if merged.get(field) in (None, "", []):
                merged[field] = value
            elif field == "sourceBlocks" and isinstance(value, list):
                merged[field] = sorted(set(merged[field]) | set(value))
    return merged


def dedupe_filings(rows):
    """Deduplicate by accession, retaining richer metadata and all source block names."""
    by_accession = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        accession = str(row.get("accessionNumber") or "").strip()
        if not accession:
            continue
        normalized = dict(row)
        normalized["accessionNumber"] = accession
        normalized["sourceBlocks"] = sorted(set(row.get("sourceBlocks") or []))
        by_accession[accession] = _merge_filing(by_accession.get(accession, {}), normalized)
    return sorted(by_accession.values(), key=lambda row: (str(row.get("filingDate") or ""),
                                                           row["accessionNumber"]))


def _same_comparative_end(target, report_date):
    """True for the same fiscal-year-end anniversary in any later annual filing."""
    if target is None or report_date is None or report_date <= target:
        return False
    year_gap = report_date.year - target.year
    if year_gap < 1:
        return False
    try:
        anniversary = target.replace(year=report_date.year)
    except ValueError:  # February 29 → February 28 in a non-leap year.
        anniversary = target.replace(year=report_date.year, day=28)
    return abs((report_date - anniversary).days) <= PERIOD_END_TOLERANCE_DAYS


def candidate_filings(period_end, filings, next_q4_end=None):
    """Map an actual Q4 end to relevant filings, with no fixed filing-date age cutoff.

    Direct matches use reportDate within a week, which accommodates stored Yahoo month-end labels
    for 52/53-week fiscal periods. Every later Item 2.02 / Item 9.01 8-K and every later 6-K is
    inspected, including filings years after the next annual report. Annual comparisons and
    amendments are searched across complete history. FilingDate never gates a direct period match.
    """
    target = _iso_date(period_end)
    if target is None:
        return []
    found = []
    for row in filings:
        form = str(row.get("form") or "").upper().strip()
        if form not in RELEVANT_FORMS:
            continue
        report = _iso_date(row.get("reportDate"))
        filed = _iso_date(row.get("filingDate"))
        reasons = []
        if report is not None and abs((report - target).days) <= PERIOD_END_TOLERANCE_DAYS:
            reasons.append("report-date-for-period")
        if form in ANNUAL_FORMS and _same_comparative_end(target, report):
            reasons.append("annual-comparative-period")
        items = str(row.get("items") or "")
        has_item_202 = bool(re.search(r"(?:^|[,;\s])2\.02(?:$|[,;\s])", items))
        has_item_901 = bool(re.search(r"(?:^|[,;\s])9\.01(?:$|[,;\s])", items))
        description = str(row.get("primaryDocDescription") or "").lower()
        earnings_description = bool(re.search(r"earnings|financial results|quarterly results|annual results", description))
        if form.endswith("/A") and filed is not None and filed >= target:
            reasons.append("amendment-after-q4-end-full-history")
        if (form in CURRENT_FORMS and filed is not None and target <= filed
                and (has_item_202 or has_item_901 or earnings_description)):
            reasons.append("later-8-k-results-or-financial-exhibits")
        if form in QUARTER_FORMS and form.endswith("/A") and filed is not None and target <= filed:
            reasons.append("later-10-q-amendment-full-history")
        if form in FOREIGN_CURRENT_FORMS and filed is not None and target <= filed:
            reasons.append("later-6-k-full-history")
        if not reasons:
            continue
        if form in CURRENT_FORMS:
            if has_item_202:
                reasons.append("8-k-item-2.02")
            if has_item_901:
                reasons.append("8-k-item-9.01-screen")
            if earnings_description and not has_item_202 and not has_item_901:
                reasons.append("8-k-earnings-description")
            if not (has_item_202 or has_item_901 or earnings_description):
                if report is not None and abs((report - target).days) <= PERIOD_END_TOLERANCE_DAYS:
                    reasons.append("8-k-reported-period-end")
                else:
                    reasons.append("other-8-k")
        if form in FOREIGN_CURRENT_FORMS:
            reasons.append("foreign-current-report")
        candidate = dict(row)
        candidate["matchReasons"] = reasons
        candidate["filedDaysAfterQ4End"] = (filed - target).days if filed else None
        found.append(candidate)
    return sorted(found, key=lambda row: (str(row.get("filingDate") or ""), row["accessionNumber"]))


def _url_key(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _write_bytes_atomic(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    os.replace(temporary, path)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read_state(path):
    try:
        state = read_json(path)
    except (OSError, ValueError, TypeError):
        state = None
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "tickerMap": None, "urls": {}, "companies": {}, "requests": 0}
    state.setdefault("tickerMap", None)
    state.setdefault("urls", {})
    state.setdefault("companies", {})
    state.setdefault("requests", 0)
    return state


def _load_existing_raw_state(state_path):
    try:
        state = read_json(state_path)
    except (OSError, ValueError, TypeError):
        return {"sources": {}, "submissions": {}}
    if not isinstance(state, dict):
        return {"sources": {}, "submissions": {}}
    return {
        "sources": state.get("sources") if isinstance(state.get("sources"), dict) else {},
        "submissions": state.get("submissions") if isinstance(state.get("submissions"), dict) else {},
    }


class ScanRun:
    """Small request/cache coordinator; one URL result is reused across periods and reruns."""
    def __init__(self, cache_dir, state_path, state, client, budget, clock, reporter,
                 existing_raw_cache_dir=EXISTING_RAW_CACHE_DIR, existing_raw_state_path=EXISTING_RAW_STATE,
                 refresh_submissions=False):
        self.cache_dir = Path(cache_dir)
        self.state_path = Path(state_path)
        self.state = state
        self.client = client
        self.budget = budget
        self.clock = clock
        self.reporter = reporter
        self.started = clock()
        self.requests_at_start = client.requests
        self.paused = False
        self.stopped_403 = False
        self.errors = []
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.existing_raw_cache_dir = Path(existing_raw_cache_dir)
        self.existing_raw_state = _load_existing_raw_state(existing_raw_state_path)
        self.refresh_submissions = bool(refresh_submissions)
        generation = state.get("refreshGeneration") if isinstance(state.get("refreshGeneration"), dict) else {}
        self.refresh_generation = generation
        self.refreshed_urls = set(generation.get("refreshedUrls", []))

    def _record_refreshed_url(self, url):
        if not self.refresh_submissions:
            return
        self.refreshed_urls.add(url)
        self.refresh_generation["refreshedUrls"] = sorted(self.refreshed_urls)
        self.state["refreshGeneration"] = self.refresh_generation
        self.checkpoint()

    def checkpoint(self):
        write_atomic(str(self.state_path), self.state)

    def has_budget(self):
        if self.budget is not None and self.clock() - self.started >= self.budget:
            self.paused = True
            return False
        return True

    def _cache_path(self, url, binary=False):
        suffix = ".bin" if binary else ".json"
        return self.cache_dir / "http" / (_url_key(url) + suffix)

    def _existing_json_path(self, url):
        """Find a URL in the older raw filing manifest without changing that cache."""
        for info in self.existing_raw_state["submissions"].values():
            if isinstance(info, dict) and info.get("url") == url and info.get("cachePath"):
                path = Path(info["cachePath"])
                if path.is_file():
                    return path, "existing-submissions-json"
        for info in self.existing_raw_state["sources"].values():
            if not isinstance(info, dict) or info.get("indexUrl") != url:
                continue
            accession = _accession_path(info.get("accession"))
            path = self.existing_raw_cache_dir / f"{accession}-index.json"
            if accession and path.is_file():
                return path, "existing-accession-index"
        return None, None

    def _existing_document(self, url):
        for info in self.existing_raw_state["sources"].values():
            if not isinstance(info, dict) or info.get("documentUrl") != url or info.get("status") != "ok":
                continue
            path = Path(info.get("htmlCache") or "")
            if not path.is_file():
                continue
            try:
                body = path.read_bytes()
            except OSError:
                continue
            digest = hashlib.sha256(body).hexdigest()
            if digest != info.get("sha256"):
                self.errors.append({"url": url, "status": "cache-integrity-error",
                                    "error": "existing raw cache SHA-256 does not match its manifest"})
                continue
            return body, path, digest
        return None

    def _record_external_cache(self, url, path, kind, digest, byte_count=None):
        self.state["urls"][url] = {
            "status": "ok",
            "externalCachePath": str(path),
            "sha256": digest,
            "reusedFrom": kind,
            "bytes": byte_count,
            "retrievedAt": _now(),
        }
        self.checkpoint()

    def json(self, url, allow_404=False):
        parsed_url = urlparse(url)
        refresh_this = (self.refresh_submissions and url not in self.refreshed_urls
                        and parsed_url.scheme == "https"
                        and ((parsed_url.netloc.lower() == "data.sec.gov"
                              and parsed_url.path.startswith("/submissions/"))
                             or url == sec.TICKERS_URL))
        info = self.state["urls"].get(url)
        if not refresh_this and info and info.get("status") in ("ok", "not-found-404"):
            if info["status"] == "not-found-404":
                return None, "cached-404"
            path_value = info.get("externalCachePath") or info.get("path")
            if path_value:
                path = Path(path_value) if info.get("externalCachePath") else self.cache_dir / path_value
                try:
                    payload = read_json(path)
                    if payload is not None:
                        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                        digest = hashlib.sha256(encoded).hexdigest()
                        if not info.get("sha256") or digest == info["sha256"]:
                            return payload, "cached"
                        self.errors.append({"url": url, "status": "cache-integrity-error",
                                            "error": "cached JSON SHA-256 does not match scanner state"})
                except (OSError, ValueError, TypeError):
                    pass
            info["status"] = "cache-corrupt"
            self.checkpoint()
        external_path, external_kind = (None, None) if refresh_this else self._existing_json_path(url)
        if external_path is not None:
            try:
                payload = read_json(external_path)
                if payload is None:
                    raise ValueError("existing cache contained no JSON value")
                encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            except (OSError, ValueError, TypeError) as error:
                self.errors.append({"url": url, "status": "parse-error",
                                    "error": f"existing cache parse failed: {error}"})
            else:
                self._record_external_cache(url, external_path, external_kind, hashlib.sha256(encoded).hexdigest(),
                                            external_path.stat().st_size)
                return payload, "reused-existing-cache"
        if not self.has_budget():
            return None, "paused"
        before = self.client.requests
        try:
            payload = self.client.get_json(url, allow_404=allow_404)
        except sec.SecForbidden as error:
            self._record_requests(before)
            self.stopped_403 = True
            self._save_error(url, "stopped-403", str(error))
            return None, "stopped-403"
        except (sec.FetchError, sec.TooManyFailures, OSError) as error:
            self._record_requests(before)
            status = "parse-error" if "unreadable response" in str(error).lower() else "fetch-error"
            self._save_error(url, status, str(error))
            self.errors.append({"url": url, "status": status, "error": str(error)})
            return None, status
        self._record_requests(before)
        if payload is None:
            self.state["urls"][url] = {"status": "not-found-404", "retrievedAt": _now()}
            self.checkpoint()
            self._record_refreshed_url(url)
            return None, "not-found-404"
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        relative = self._cache_path(url).relative_to(self.cache_dir)
        path = self.cache_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_bytes_atomic(path, encoded)
        self.state["urls"][url] = {"status": "ok", "path": str(relative), "sha256": hashlib.sha256(encoded).hexdigest(),
                                   "retrievedAt": _now()}
        self.checkpoint()
        self._record_refreshed_url(url)
        return payload, "fetched"

    def binary(self, url, relative_path):
        info = self.state["urls"].get(url)
        if info and info.get("status") in ("ok", "not-found-404"):
            if info["status"] == "not-found-404":
                return None, "cached-404", None
            path_value = info.get("externalCachePath") or info.get("path")
            if path_value:
                path = Path(path_value) if info.get("externalCachePath") else self.cache_dir / path_value
                try:
                    body = path.read_bytes()
                    digest = hashlib.sha256(body).hexdigest()
                    if not info.get("sha256") or digest == info["sha256"]:
                        return body, "cached", str(path)
                    self.errors.append({"url": url, "status": "cache-integrity-error",
                                        "error": "cached filing SHA-256 does not match scanner state"})
                except OSError:
                    pass
            info["status"] = "cache-corrupt"
            self.checkpoint()
        external = self._existing_document(url)
        if external is not None:
            body, path, digest = external
            self._record_external_cache(url, path, "existing-raw-filing-body", digest, len(body))
            return body, "reused-cache", str(path)
        if not self.has_budget():
            return None, "paused", None
        before = self.client.requests
        try:
            body = self.client.get_bytes(url, allow_404=True)
        except sec.SecForbidden as error:
            self._record_requests(before)
            self.stopped_403 = True
            self._save_error(url, "stopped-403", str(error))
            return None, "stopped-403", None
        except (sec.FetchError, sec.TooManyFailures, OSError) as error:
            self._record_requests(before)
            self._save_error(url, "fetch-error", str(error))
            self.errors.append({"url": url, "status": "fetch-error", "error": str(error)})
            return None, "fetch-error", None
        self._record_requests(before)
        if body is None:
            self.state["urls"][url] = {"status": "not-found-404", "retrievedAt": _now()}
            self.checkpoint()
            return None, "not-found-404", None
        path = self.cache_dir / relative_path
        _write_bytes_atomic(path, body)
        relative = path.relative_to(self.cache_dir)
        self.state["urls"][url] = {"status": "ok", "path": str(relative), "sha256": hashlib.sha256(body).hexdigest(),
                                   "bytes": len(body),
                                   "retrievedAt": _now()}
        self.checkpoint()
        return body, "fetched", str(path)

    def _save_error(self, url, status, error):
        self.state["urls"][url] = {"status": status, "error": error, "retrievedAt": _now()}
        self.checkpoint()

    def _record_requests(self, before):
        self.state["requests"] = int(self.state.get("requests", 0)) + self.client.requests - before
        self.checkpoint()


def _submissions_shape(payload):
    filings = payload.get("filings") if isinstance(payload, dict) else None
    return filings if isinstance(filings, dict) else {}


def _valid_submissions_payload(payload, expected_cik):
    expected = _cik(expected_cik)
    if not isinstance(payload, dict) or expected is None or _cik(payload.get("cik")) != expected:
        return False
    shape = _submissions_shape(payload)
    recent = shape.get("recent")
    return (isinstance(recent, dict) and _valid_filing_columns(recent)
            and isinstance(shape.get("files"), list))


def _valid_filing_columns(payload):
    required = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
    if not isinstance(payload, dict) or any(not isinstance(payload.get(key), list) for key in required):
        return False
    length = len(payload["accessionNumber"])
    if any(len(payload[key]) != length for key in required):
        return False
    if "items" in payload and (not isinstance(payload.get("items"), list)
                               or len(payload["items"]) != length):
        return False
    for index in range(length):
        accession = payload["accessionNumber"][index]
        form = payload["form"][index]
        filing_date = payload["filingDate"][index]
        report_date = payload["reportDate"][index]
        primary = payload["primaryDocument"][index]
        if (not isinstance(accession, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession)
                or not isinstance(form, str) or not form.strip()
                or not isinstance(filing_date, str) or _iso_date(filing_date) is None
                # SEC historical submissions occasionally have a valid accession but an
                # empty primaryDocument field (especially pre-2001). The archive index is
                # still sufficient to enumerate the filing's documents.
                or not isinstance(primary, str)
                or (report_date not in (None, "")
                    and (not isinstance(report_date, str) or _iso_date(report_date) is None))):
            return False
        if "items" in payload and payload["items"][index] not in (None, "") \
                and not isinstance(payload["items"][index], str):
            return False
    return True


def _valid_submissions_block_payload(payload, expected_cik=None):
    """Historical blocks are a flattened recent-shaped object, not necessarily nested filings."""
    if not isinstance(payload, dict):
        return False
    expected = _cik(expected_cik) if expected_cik is not None else None
    observed = _cik(payload.get("cik"))
    if observed is not None and expected is not None and observed != expected:
        return False
    filings = payload.get("filings")
    nested_cik = _cik(filings.get("cik")) if isinstance(filings, dict) else None
    if nested_cik is not None and expected is not None and nested_cik != expected:
        return False
    if isinstance(filings, dict) and isinstance(filings.get("recent"), dict):
        payload = filings["recent"]
    return _valid_filing_columns(payload)


def _index_files(payload):
    if not isinstance(payload, dict):
        return None
    directory = payload.get("directory") or {}
    files = directory.get("item") if isinstance(directory, dict) else None
    return files if isinstance(files, list) else None


def _document_names(record, index_payload):
    """Choose primary documents plus earnings/financial exhibits, not XBRL sidecars."""
    items = _index_files(index_payload)
    if items is None:
        return []
    form = str(record.get("form") or "").upper()
    primary = str(record.get("primaryDocument") or "").strip()
    available = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or Path(name).name != name or name in (".", ".."):
            continue
        if name.lower().endswith((".json", ".xsd", ".cal", ".def", ".lab", ".pre")):
            continue
        if not name.lower().endswith((".htm", ".html", ".txt", ".pdf", ".xhtml", ".xml",
                                     ".jpg", ".jpeg", ".png", ".tif", ".tiff")):
            continue
        available[name] = item
    chosen = []
    if primary and primary in available:
        chosen.append(primary)
    for name in available:
        if name not in chosen:
            chosen.append(name)
    return chosen


class _MarkupProbe(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = 0

    def handle_starttag(self, _tag, _attrs):
        self.tags += 1

    def handle_startendtag(self, _tag, _attrs):
        self.tags += 1


def _body_format_status(name, body):
    """Return container format and a lightweight HTML parse status, without extracting EPS."""
    if not body:
        return "format-error", "not-attempted"
    lower = name.lower()
    if lower.endswith(".pdf"):
        return ("ok", "not-attempted") if body.startswith(b"%PDF-") else ("format-error", "not-attempted")
    if lower.endswith((".htm", ".html", ".xhtml", ".txt", ".xml")):
        head = body[:4096].lstrip().lower()
        if not (b"<html" in head or b"<document" in head or b"<" in body[:200]):
            return "format-error", "not-attempted"
        try:
            # SEC submissions are generally UTF-8; Latin-1 preserves legacy bytes without data loss.
            try:
                text = body[:1_000_000].decode("utf-8-sig")
            except UnicodeDecodeError:
                text = body[:1_000_000].decode("latin-1")
            parser = _MarkupProbe()
            parser.feed(text)
            parser.close()
        except (UnicodeError, ValueError, AssertionError) as error:
            return "ok", f"parse-error: {type(error).__name__}: {error}"
        return ("ok", "parsed-prefix") if parser.tags else ("format-error", "not-attempted")
    return "format-error", "not-attempted"


def _accession_path(accession):
    return re.sub(r"[^0-9]", "", str(accession))


def _fetch_candidate_documents(run, cik, record):
    accession = str(record["accessionNumber"])
    cik_unpadded = str(int(cik))
    compact = _accession_path(accession)
    index_url = ARCHIVES_INDEX_URL.format(cik=cik_unpadded, accession=compact)
    index_payload, index_status = run.json(index_url, allow_404=True)
    if run.stopped_403 or run.paused:
        return {"status": index_status, "indexUrl": index_url, "documents": []}
    if index_payload is None:
        return {"status": "index-404" if index_status in ("not-found-404", "cached-404") else index_status,
                "indexUrl": index_url, "documents": []}
    files = _index_files(index_payload)
    if files is None:
        return {"status": "index-parse-error", "indexUrl": index_url, "documents": []}
    names = _document_names(record, index_payload)
    unsupported = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if (name and Path(name).name == name and name not in names
                and not name.lower().endswith((".xsd", ".cal", ".def", ".lab", ".pre", ".json"))):
            unsupported.append(name)
    if not names:
        return {"status": "missing-document", "indexUrl": index_url, "documents": [],
                "unexaminedDocuments": unsupported}
    output = []
    for name in names:
        url = ARCHIVES_DOCUMENT_URL.format(cik=cik_unpadded, accession=compact, name=name)
        relative = Path("filings") / cik / compact / name
        body, status, path = run.binary(url, relative)
        item_status = status
        format_status, parse_status = "unknown", "not-attempted"
        if body is not None:
            format_status, parse_status = _body_format_status(name, body)
            item_status = "format-error" if format_status == "format-error" else (
                "parse-error" if str(parse_status).startswith("parse-error") else "ok")
            saved = run.state["urls"].get(url, {})
            saved["formatStatus"] = format_status
            saved["parseStatus"] = parse_status
            run.state["urls"][url] = saved
            run.checkpoint()
        source = run.state["urls"].get(url, {})
        output.append({"name": name, "url": url, "status": item_status, "formatStatus": format_status,
                       "parseStatus": parse_status, "cachePath": path,
                       "sha256": source.get("sha256"), "bytes": source.get("bytes")})
        if run.stopped_403 or run.paused:
            break
    if all(item["status"] == "not-found-404" for item in output):
        status = "missing-document-404"
    elif any(item["status"] == "format-error" for item in output):
        status = "format-error"
    elif any(item["status"] == "parse-error" for item in output):
        status = "parse-error"
    elif all(item["status"] in ("ok", "cached", "fetched", "reused-cache") for item in output):
        status = "ok"
    else:
        status = "partial"
    return {"status": status, "indexUrl": index_url, "documents": output,
            "unexaminedDocuments": unsupported}


def _write_company_manifest(cache_dir, symbol, manifest):
    path = Path(cache_dir) / "results" / f"{symbol}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(str(path), manifest)
    return path


def run(*, financials_dir=DEFAULT_FINANCIALS_DIR, cache_dir=DEFAULT_CACHE_DIR, state_path=None,
        budget=None, client=None, environ=None, clock=time.monotonic, reporter=print,
        existing_raw_cache_dir=EXISTING_RAW_CACHE_DIR, existing_raw_state_path=EXISTING_RAW_STATE,
        symbols=None, refresh_submissions=False):
    environ = os.environ if environ is None else environ
    financials_dir, cache_dir = Path(financials_dir), Path(cache_dir)
    state_path = Path(state_path) if state_path is not None else cache_dir / "state.json"
    state = _read_state(state_path)
    summary = {"status": "done", "companies": 0, "periods": 0, "candidateFilings": 0,
               "documentsCached": 0, "missingCik": 0, "requests": 0, "stopped403": False,
               "paused": False, "cacheDirectory": str(cache_dir)}
    if sec.refusal_reason(environ):
        summary.update(status="refused", reason=sec.refusal_reason(environ))
        return summary
    try:
        user_agent = sec.resolve_user_agent(environ)
    except sec.UserAgentError as error:
        summary.update(status="refused", reason=str(error))
        return summary
    client = client or sec.SecClient(user_agent, stop_on_forbidden=True)
    # Keep the first-403 guarantee even when tests or callers inject a SecClient instance.
    if hasattr(client, "stop_on_forbidden"):
        client.stop_on_forbidden = True
    runner = ScanRun(cache_dir, state_path, state, client, budget, clock, reporter,
                     existing_raw_cache_dir=existing_raw_cache_dir,
                     existing_raw_state_path=existing_raw_state_path,
                     refresh_submissions=refresh_submissions)
    if state.get("candidateRulesVersion") != CANDIDATE_RULES_VERSION:
        # Manifests include candidate lists from the old broad 9.01 rule. Invalidate only these
        # derived manifests and per-company checkpoint metadata; retain every URL/body cache.
        state["companies"] = {}
        state["candidateRulesVersion"] = CANDIDATE_RULES_VERSION
        result_dir = cache_dir / "results"
        if result_dir.is_dir():
            for manifest_path in result_dir.glob("*.json"):
                try:
                    manifest_path.unlink()
                except OSError as error:
                    summary["status"] = "stopped-cache-error"
                    summary["reason"] = f"could not invalidate stale scan manifest {manifest_path}: {error}"
                    return summary
        runner.checkpoint()

    if refresh_submissions:
        generation = state.get("refreshGeneration") if isinstance(state.get("refreshGeneration"), dict) else {}
        if (generation.get("status") != "in-progress"
                or generation.get("candidateRulesVersion") != CANDIDATE_RULES_VERSION):
            generation = {"id": _now(), "status": "in-progress",
                          "candidateRulesVersion": CANDIDATE_RULES_VERSION, "refreshedUrls": []}
        generation.setdefault("refreshedUrls", [])
        state["refreshGeneration"] = generation
        runner.refresh_generation = generation
        runner.refreshed_urls = set(generation["refreshedUrls"])
        runner.checkpoint()

    company_documents = []
    symbol_filter = {str(symbol).upper().strip() for symbol in (symbols or []) if str(symbol).strip()}
    for path in sorted(financials_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        if symbol_filter and path.stem.upper() not in symbol_filter:
            continue
        try:
            document = read_json(path)
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(document, dict) or document.get("market") != "US":
            continue
        targets = q4_targets(document)
        if targets:
            company_documents.append((path.stem, document, targets))
    summary["companies"] = len(company_documents)
    summary["periods"] = sum(len(targets) for _, _, targets in company_documents)
    if not company_documents:
        return summary

    # Resolve the current official ticker map only for periods lacking stored official SEC provenance.
    # The official current ticker map is also an independent identity cross-check for stored
    # provenance CIKs. It is required even when every target already has a candidate CIK.
    needs_ticker_map = bool(company_documents)
    if needs_ticker_map and (not isinstance(state.get("tickerMap"), dict)
                             or (refresh_submissions and sec.TICKERS_URL not in runner.refreshed_urls)):
        payload, map_status = runner.json(sec.TICKERS_URL, allow_404=False)
        if runner.stopped_403:
            summary.update(status="stopped-403", stopped403=True)
            return summary
        if runner.paused:
            summary.update(status="paused", paused=True)
            return summary
        if not isinstance(payload, dict):
            summary.update(status="stopped-fetch-error", reason=f"ticker map {map_status}")
            return summary
        state["tickerMap"] = sec.parse_ticker_map(payload)
        runner.checkpoint()

    for symbol, document, targets in company_documents:
        # A company with a historical CIK switch is grouped by CIK; every Q4 keeps its own source identity.
        old_company_state = state.get("companies", {}).get(symbol, {})
        saved_filing_cache = old_company_state.get("filings", {}) if isinstance(old_company_state, dict) else {}
        accumulated_periods = []
        cik_summaries = []
        by_cik = {}
        for target in targets:
            cik = target["provenanceCik"]
            current_ticker_cik = (state.get("tickerMap") or {}).get(symbol)
            source_cik = _cik(cik) if cik else None
            current_cik = _cik(current_ticker_cik) if current_ticker_cik else None
            target["issuerIdentityVerified"] = bool(source_cik and current_cik and source_cik == current_cik)
            target["issuerIdentitySource"] = ("stored-sec-provenance-matched-to-current-official-ticker-map"
                                               if target["issuerIdentityVerified"] else None)
            if not cik:
                cik = current_ticker_cik
            if not cik:
                summary["missingCik"] += 1
                target["searchTrajectory"] = [{"stage": "identity", "status": "no-cik"}]
                accumulated_periods.append(target)
                continue
            target["cik"] = cik
            target["identityFromTickerMap"] = not bool(target.get("provenanceCik"))
            by_cik.setdefault(cik, []).append(target)
        for cik, cik_targets in by_cik.items():
            trajectory = []
            submissions_url = SUBMISSIONS_URL.format(cik=cik)
            payload, status = runner.json(submissions_url, allow_404=True)
            source_snapshot = state.get("urls", {}).get(submissions_url, {})
            trajectory.append({"stage": "submissions", "url": submissions_url, "status": status,
                              "refreshRequested": bool(refresh_submissions),
                              "refreshGeneration": runner.refresh_generation.get("id") if refresh_submissions else None,
                              "retrievedAt": source_snapshot.get("retrievedAt")})
            if runner.stopped_403:
                for target in cik_targets:
                    target["searchTrajectory"] = trajectory + [{"stage": "candidate-search", "status": "stopped-403"}]
                summary.update(status="stopped-403", stopped403=True)
                break
            if runner.paused:
                for target in cik_targets:
                    target["searchTrajectory"] = trajectory + [{"stage": "candidate-search", "status": "paused"}]
                summary.update(status="paused", paused=True)
                break
            if payload is None:
                for target in cik_targets:
                    result_status = "no-submissions" if status in ("not-found-404", "cached-404") else status
                    target["searchTrajectory"] = trajectory + [{"stage": "candidate-search", "status": result_status}]
                accumulated_periods.extend(cik_targets)
                continue
            if not _valid_submissions_payload(payload, cik):
                parse_error = {"url": submissions_url, "status": "parse-error",
                               "error": "submissions JSON is missing filings.recent/accessionNumber or filings.files"}
                runner.errors.append(parse_error)
                for target in cik_targets:
                    target["searchTrajectory"] = trajectory + [{"stage": "candidate-search", "status": "parse-error"}]
                accumulated_periods.extend(cik_targets)
                continue
            shape = _submissions_shape(payload)
            all_rows = []
            for row in _filing_rows(payload):
                row["sourceBlocks"] = ["recent"]
                all_rows.append(row)
            block_results = []
            files = shape.get("files") if isinstance(shape.get("files"), list) else []
            for block in files:
                if not isinstance(block, dict) or not isinstance(block.get("name"), str):
                    block_results.append({"name": None, "status": "parse-error", "records": 0,
                                          "reason": "historical block descriptor is malformed"})
                    continue
                name = block["name"]
                block_url = SUBMISSIONS_BLOCK_URL.format(name=name)
                block_payload, block_status = runner.json(block_url, allow_404=True)
                if block_payload is not None and not _valid_submissions_block_payload(block_payload, cik):
                    block_status = "parse-error"
                block_rows = _filing_rows(block_payload) if block_payload is not None and block_status not in (
                    "parse-error", "fetch-error", "not-found-404", "cached-404") else []
                block_results.append({"name": name, "url": block_url, "status": block_status,
                                      "records": len(block_rows)})
                for row in block_rows:
                    row["sourceBlocks"] = [name]
                    all_rows.append(row)
                if runner.stopped_403 or runner.paused:
                    break
            successful_block_statuses = {"fetched", "cached", "reused-existing-cache", "reused-cache"}
            block_failures = [result for result in block_results
                              if result.get("status") not in successful_block_statuses]
            history_complete = len(block_results) == len(files) and not block_failures
            trajectory.append({"stage": "historical-blocks", "listed": len(files),
                               "status": "complete" if history_complete else "partial",
                               "complete": history_complete, "failures": block_failures,
                               "results": block_results})
            if runner.stopped_403:
                for target in cik_targets:
                    target["searchTrajectory"] = trajectory + [{"stage": "candidate-search", "status": "stopped-403"}]
                summary.update(status="stopped-403", stopped403=True)
                break
            if runner.paused:
                for target in cik_targets:
                    target["searchTrajectory"] = trajectory + [{"stage": "candidate-search", "status": "paused"}]
                summary.update(status="paused", paused=True)
                break
            filings = dedupe_filings(all_rows)
            trajectory.append({"stage": "deduplicate-accessions", "recordsBefore": len(all_rows),
                               "uniqueAccessions": len(filings)})
            for target in cik_targets:
                candidates = candidate_filings(target["end"], filings, target.get("nextQ4End"))
                target["sourceCoverageComplete"] = history_complete
                target["sourceCoverageFresh"] = (
                    bool(refresh_submissions) and status in {"fetched", "cached"} and history_complete
                    and submissions_url in runner.refreshed_urls
                    and all(result.get("url") in runner.refreshed_urls for result in block_results)
                    and (not target.get("identityFromTickerMap") or sec.TICKERS_URL in runner.refreshed_urls))
                target["sourceCoverageRefreshGeneration"] = (
                    runner.refresh_generation.get("id") if refresh_submissions else None)
                target["sourceCoverageAsOf"] = source_snapshot.get("retrievedAt")
                target["sourceCoverageFailures"] = block_failures
                if not history_complete:
                    for candidate in candidates:
                        candidate["historyCoverageUnknown"] = True
                target["candidateFilings"] = candidates
                target["searchTrajectory"] = trajectory + [
                    {"stage": "period-match", "periodEnd": target["end"],
                     "rule": "reportDate within 7 days, all later 2.02/9.01 8-Ks and 6-Ks, all annual comparative periods and amendments; no fixed filingDate cutoff",
                     "relevantFormRecords": sum(1 for row in filings if str(row.get("form") or "").upper() in RELEVANT_FORMS),
                     "candidateCount": len(candidates),
                     "candidateAccessions": [row["accessionNumber"] for row in candidates]},
                ]
            # Download each accession once and share that result across periods with the same CIK.
            download_results = {}
            accession_candidates = {}
            for target in cik_targets:
                for filing in target.get("candidateFilings", []):
                    accession_candidates.setdefault(filing["accessionNumber"], filing)
            # Spend a bounded run on the most recent filings first; checkpoints still retain every
            # older target and the next run continues into the historical candidates.
            ordered_accessions = sorted(accession_candidates.items(),
                                        key=lambda pair: (str(pair[1].get("filingDate") or ""), pair[0]),
                                        reverse=True)
            for accession, filing in ordered_accessions:
                saved_key = f"{cik}:{accession}"
                cached = saved_filing_cache.get(saved_key) if isinstance(saved_filing_cache, dict) else None
                if cached and cached.get("status") in ("ok", "index-404", "missing-document", "format-error"):
                    download_results[accession] = cached
                    continue
                result = _fetch_candidate_documents(runner, cik, filing)
                download_results[accession] = result
                saved_filing_cache[saved_key] = result
                state.setdefault("companies", {}).setdefault(symbol, {})["filings"] = saved_filing_cache
                runner.checkpoint()
                summary["documentsCached"] += sum(1 for item in result.get("documents", [])
                                                    if item.get("status") in ("ok", "cached", "fetched"))
                if runner.stopped_403 or runner.paused:
                    break
            for target in cik_targets:
                for filing in target.get("candidateFilings", []):
                    filing["cache"] = download_results.get(filing["accessionNumber"])
                summary["candidateFilings"] += len(target.get("candidateFilings", []))
            accumulated_periods.extend(cik_targets)
            cik_summaries.append({
                "cik": cik,
                "identitySource": "stored-sec-q4-provenance" if any(t.get("provenanceCik") == cik for t in cik_targets)
                                 else "official-sec-ticker-map-fallback",
                "historicalBlockCount": len(files),
                "historicalBlocksComplete": history_complete,
                "historicalBlockFailures": block_failures,
                "uniqueAccessionCount": len(filings),
                "recentAndHistoricalRecords": len(all_rows),
            })
            if runner.stopped_403 or runner.paused:
                break
        accumulated_ids = {id(target) for target in accumulated_periods}
        accumulated_periods.extend(target for target in targets if id(target) not in accumulated_ids)
        manifest = {
            "schema": "q4-eps-sec-scan-v1",
            "candidateRulesVersion": CANDIDATE_RULES_VERSION,
            "symbol": symbol,
            "market": document.get("market"),
            "ciks": cik_summaries,
            "generatedAt": _now(),
            "periods": sorted(accumulated_periods,
                               key=lambda item: (str(item.get("end") or ""), str(item.get("fy") or ""))),
            "errors": runner.errors,
            "documentValidation": "container format and 1 MB HTML parser prefix status only; no EPS values or financial data are inferred",
        }
        _write_company_manifest(cache_dir, symbol, manifest)
        state.setdefault("companies", {})[symbol] = {
            "status": "paused" if runner.paused else "stopped-403" if runner.stopped_403 else
            "scanned-with-errors" if any(not entry.get("historicalBlocksComplete", False)
                                          for entry in cik_summaries) else "scanned",
            "ciks": [entry["cik"] for entry in cik_summaries],
            "manifest": str((Path("results") / f"{symbol}.json")),
            "periods": [target["end"] for target in accumulated_periods],
            "uniqueAccessions": sum(entry["uniqueAccessionCount"] for entry in cik_summaries),
            "filings": saved_filing_cache,
            "updatedAt": _now(),
        }
        runner.checkpoint()
        if runner.stopped_403 or runner.paused:
            break
    summary["requests"] = client.requests - runner.requests_at_start
    summary["refreshSubmissions"] = bool(refresh_submissions)
    if refresh_submissions:
        generation = runner.refresh_generation
        generation["status"] = "complete" if not (runner.stopped_403 or runner.paused or runner.errors) else "in-progress"
        generation["finishedAt"] = _now() if generation["status"] == "complete" else None
        generation["refreshedUrls"] = sorted(runner.refreshed_urls)
        state["refreshGeneration"] = generation
        runner.checkpoint()
        summary["refreshGeneration"] = generation.get("id")
    if runner.stopped_403:
        summary.update(status="stopped-403", stopped403=True)
    elif runner.paused:
        summary.update(status="paused", paused=True)
    elif runner.errors:
        summary.update(status="done-with-errors", errors=runner.errors)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_FINANCIALS_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--state", type=Path, help="checkpoint path (default: <cache-dir>/state.json)")
    parser.add_argument("--budget", type=float, help="seconds before checkpointing; rerun the same command to resume")
    parser.add_argument("--refresh-submissions", action="store_true",
                        help="refresh SEC submissions and listed history blocks before declaring coverage complete")
    parser.add_argument("--symbols", nargs="+", help="optional ticker filter for a bounded, prioritized verification run")
    args = parser.parse_args(argv)
    if args.budget is not None and args.budget <= 0:
        parser.error("--budget must be greater than zero")
    result = run(financials_dir=args.dir, cache_dir=args.cache_dir, state_path=args.state,
                 budget=args.budget, symbols=args.symbols, refresh_submissions=args.refresh_submissions)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in ("done", "done-with-errors", "paused") else 1


if __name__ == "__main__":
    raise SystemExit(main())

