#!/usr/bin/env python3
"""Small, independent SEC Q4 diluted EPS proof of concept.

The freeze-sample command is entirely local and reads the current financials and
SEC issuer map. It does not contact SEC or modify production financial files.
The retrieval and parsing commands are implemented below independently of Atlas
production calculations.
"""

from __future__ import annotations

import argparse
import csv
import email.utils
import gzip
import hashlib
import html.parser
import io
import json
import os
import re
import statistics
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
import zlib
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ATLAS_SCRIPTS = ROOT / "atlas" / "scripts"
sys.path.insert(0, str(ATLAS_SCRIPTS))
import build_financials as financials  # noqa: E402

csv.field_size_limit(100_000_000)


CLASSIFICATIONS = (
    "q4_net_income_missing",
    "annual_diluted_eps_missing",
    "q1_q3_incomplete",
    "derivation_rejected",
)
EARLY = (2012, 2018)
RECENT = (2019, 2025)
SIZE_LIMIT = 1_000_000_000
POPULAR_TICKERS = {
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "GOOG",
    "IBM", "ORCL", "INTC", "QCOM", "NFLX", "DIS", "COIN", "ELF", "TLRY",
}
SEC_MIN_INTERVAL = 0.25  # 4 requests/s, below the SEC 10 requests/s ceiling.
SEC_BACKOFF = (2, 4, 8, 16, 32)
SEC_DEFAULT_USER_AGENT = "SecSeed w556087587@gmail.com"
CACHE_DIR = Path(os.environ.get("SEC_Q4_EPS_POC_CACHE", "/tmp/sec-q4-eps-poc-cache"))


class SecClient:
    """Read-only SEC transport with cache, fair-use pacing, Retry-After and bounded retries."""

    def __init__(self, cache_dir=CACHE_DIR, opener=None, sleeper=time.sleep, user_agent=None):
        self.cache_dir = Path(cache_dir)
        self.opener = opener or urllib.request.urlopen
        self.sleeper = sleeper
        self.user_agent = user_agent or os.environ.get("SEC_USER_AGENT") or SEC_DEFAULT_USER_AGENT
        self.requests = 0
        self.bytes_received = 0
        self.wire_bytes_received = 0
        self.cache_hits = 0
        self.retries = 0
        self._last_request = 0.0

    @staticmethod
    def _retry_seconds(header, fallback):
        if header:
            try:
                return max(0.0, float(header.strip()))
            except (TypeError, ValueError):
                try:
                    retry_at = email.utils.parsedate_to_datetime(header)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        return fallback

    def fetch(self, url, *, cache=True):
        if not url.startswith(("https://www.sec.gov/", "https://data.sec.gov/")):
            raise ValueError("SEC client only accepts the official www.sec.gov and data.sec.gov hosts")
        cache_key = sha256_bytes(url.encode("utf-8"))
        body_path = self.cache_dir / f"{cache_key}.body"
        meta_path = self.cache_dir / f"{cache_key}.json"
        if cache and body_path.exists() and meta_path.exists():
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            self.cache_hits += 1
            data = body_path.read_bytes()
            if data.startswith(b"\x1f\x8b"):
                data = gzip.decompress(data)
                body_path.write_bytes(data)
            return {**metadata, "body": data, "cache_hit": True, "url": url}

        last_result = None
        attempt_count = 0
        for attempt in range(len(SEC_BACKOFF) + 1):
            now = time.monotonic()
            wait = SEC_MIN_INTERVAL - (now - self._last_request)
            if self._last_request and wait > 0:
                self.sleeper(wait)
            request = urllib.request.Request(url, headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json, text/html, application/zip, */*",
            })
            self.requests += 1
            attempt_count += 1
            self._last_request = time.monotonic()
            try:
                response = self.opener(request, timeout=60)
                with response:
                    status = response.status
                    headers = dict(response.headers.items())
                    data = response.read()
            except urllib.error.HTTPError as error:
                status, headers = error.code, dict(error.headers.items()) if error.headers else {}
                try:
                    data = error.read()
                except OSError:
                    data = b""
            except (OSError, TimeoutError) as error:
                last_result = {"status": None, "headers": {}, "body": b"", "wire_bytes": 0,
                               "error": f"{type(error).__name__}: {error}"}
                if attempt >= len(SEC_BACKOFF):
                    break
                self.retries += 1
                self.sleeper(SEC_BACKOFF[attempt])
                continue
            self.wire_bytes_received += len(data)
            wire_bytes = len(data)
            encoding = str(headers.get("Content-Encoding", "")).lower().strip()
            try:
                if encoding == "gzip":
                    data = gzip.decompress(data)
                elif encoding == "deflate":
                    try:
                        data = zlib.decompress(data)
                    except zlib.error:
                        data = zlib.decompress(data, -zlib.MAX_WBITS)
                elif data.startswith(b"\x1f\x8b"):
                    # SEC occasionally serves a gzip body without a
                    # Content-Encoding header through the managed proxy.
                    data = gzip.decompress(data)
            except (OSError, EOFError, zlib.error) as error:
                last_result = {"status": status, "headers": headers, "body": b"",
                               "wire_bytes": wire_bytes, "error": f"invalid {encoding} HTTP body: {error}"}
                if attempt >= len(SEC_BACKOFF):
                    break
                self.retries += 1
                self.sleeper(SEC_BACKOFF[attempt])
                continue
            self.bytes_received += len(data)
            last_result = {"status": status, "headers": headers, "body": data,
                           "wire_bytes": wire_bytes, "error": None}
            if status == 200 or status == 404 or status == 403 or status < 500 and status != 429:
                break
            if attempt >= len(SEC_BACKOFF):
                break
            self.retries += 1
            self.sleeper(self._retry_seconds(headers.get("Retry-After"), SEC_BACKOFF[attempt]))

        last_result = last_result or {"status": None, "headers": {}, "body": b"", "wire_bytes": 0,
                                      "error": "no response"}
        metadata = {
            "url": url,
            "status": last_result["status"],
            "error": last_result.get("error"),
            "headers": {key: value for key, value in last_result["headers"].items()
                        if key.lower() in {"content-type", "content-length", "last-modified", "retry-after"}},
            "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "bytes": len(last_result["body"]),
            "wire_bytes": last_result.get("wire_bytes", len(last_result["body"])),
            "cache_hit": False,
            "attempts": attempt_count,
        }
        if cache and last_result["status"] == 200 and not last_result.get("error"):
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            body_path.write_bytes(last_result["body"])
            meta_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        return {**metadata, "body": last_result["body"], "url": url}


class TitleParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)


class LinkParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def fetch_official_dataset_docs():
    client = SecClient()
    pages = {
        "financial_statement_data_sets": "https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets",
        "financial_statement_data_sets_layout": "https://www.sec.gov/dera/data/financial-statement-data-sets.html",
        "financial_statement_notes_data_sets": "https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets",
        "financial_statement_notes_layout": "https://www.sec.gov/dera/data/financial-statement-and-notes-data-set.html",
    }
    result = {}
    for key, url in pages.items():
        response = client.fetch(url)
        parser = TitleParser()
        parser.feed(response["body"].decode("utf-8", errors="replace"))
        text = response["body"].decode("utf-8", errors="replace")
        link_parser = LinkParser()
        link_parser.feed(text)
        links = sorted({urllib.parse.urljoin("https://www.sec.gov", href)
                        for href in link_parser.hrefs if ".zip" in href.lower()})
        result[key] = {
            "url": url,
            "status": response["status"],
            "title": " ".join(parser.parts).strip(),
            "retrieved_at": response["retrieved_at"],
            "bytes": response["bytes"],
            "sha256": sha256_bytes(response["body"]),
            "zip_links_found": len(links),
            "sample_zip_links": links[:8],
            "lock_interstitial": "lock" in " ".join(parser.parts).lower(),
        }
    pdfs = {
        "financial_statement_data_sets_pdf": "https://www.sec.gov/files/financial-statement-data-sets.pdf",
        "financial_statement_notes_data_sets_pdf": "https://www.sec.gov/files/aqfsn_1.pdf",
    }
    for key, url in pdfs.items():
        response = client.fetch(url)
        result[key] = {
            "url": url,
            "status": response["status"],
            "retrieved_at": response["retrieved_at"],
            "content_type": response.get("headers", {}).get("content-type"),
            "bytes": len(response["body"]),
            "wire_bytes": response.get("wire_bytes"),
            "sha256": sha256_bytes(response["body"]),
        }
    return result, client


def resolve_filing_map(sample_path=None, output_path=None):
    sample_path = Path(sample_path or ROOT / "docs/report/sec-q4-eps-poc/sample.json")
    output_path = Path(output_path or ROOT / "docs/report/sec-q4-eps-poc/filing-map.json")
    if not sample_path.is_absolute():
        sample_path = ROOT / sample_path
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    sample = load_json(sample_path)
    if len(sample.get("sample", [])) != 10 or sample.get("freeze_sha256") != freeze_hash(sample):
        raise ValueError("sample is not the committed frozen 10-ticker sample")
    client = SecClient()
    by_cik = defaultdict(list)
    for issuer in sample["sample"]:
        by_cik[issuer["cik"]].append(issuer)
    mapped = []
    for cik, issuers in sorted(by_cik.items()):
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        response = client.fetch(url)
        row = {
            "cik": cik,
            "tickers": sorted(item["ticker"] for item in issuers),
            "url": url,
            "http_status": response["status"],
            "retrieved_at": response["retrieved_at"],
            "response_bytes": len(response["body"]),
            "cache_hit": response.get("cache_hit", False),
            "error": response.get("error"),
            "filings": [],
            "periods": [],
        }
        if response["status"] != 200 or response.get("error"):
            mapped.append(row)
            continue
        try:
            payload = json.loads(response["body"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            row["error"] = f"submissions JSON parse failure: {error}"
            mapped.append(row)
            continue
        row["entity_name"] = payload.get("name")
        row["tickers_reported_by_sec"] = payload.get("tickers", [])
        row["sic"] = payload.get("sic")
        row["sic_description"] = payload.get("sicDescription")
        recent = (payload.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        for index, form in enumerate(forms):
            if form not in ("10-K", "10-K/A", "8-K", "8-K/A"):
                continue
            def at(name):
                items = recent.get(name) or []
                return items[index] if index < len(items) else None
            filing = {
                "form": form,
                "filing_date": at("filingDate"),
                "report_date": at("reportDate"),
                "accession": at("accessionNumber"),
                "primary_document": at("primaryDocument"),
                "is_xbrl": at("isXBRL"),
                "is_inline_xbrl": at("isInlineXBRL"),
            }
            row["filings"].append(filing)
        for issuer in issuers:
            for period in issuer["periods"]:
                end = period["q4_end"]
                exact = [filing for filing in row["filings"]
                         if filing["form"] in ("10-K", "10-K/A") and filing["report_date"] == end]
                if not exact:
                    approximate = [filing for filing in row["filings"]
                                   if filing["form"] in ("10-K", "10-K/A")
                                   and filing["report_date"] and abs((date_from_string(filing["report_date"])-date_from_string(end)).days) <= 7]
                    exact = approximate
                source_doc = load_json(ROOT / period["financials_path"])
                qsec = source_doc.get("quarterly") or {}
                q3_ends = [qend for qend, qfy, qfq, qsrc in zip(
                    qsec.get("end", []), qsec.get("fy", []), qsec.get("fq", []),
                    qsec.get("src", ["yahoo"] * len(qsec.get("end", [])))
                ) if qfy == period["fy"] and qfq == 3 and qsrc == "sec"]
                q4_start = None
                if len(q3_ends) == 1:
                    from datetime import timedelta
                    q4_start = (date_from_string(q3_ends[0]) + timedelta(days=1)).isoformat()
                annual = source_doc.get("annual") or {}
                annual_ends = annual.get("end", [])
                annual_src = annual.get("src", ["yahoo"] * len(annual_ends))
                annual_fys = annual.get("fy", [])
                local_annual_dates = [aend for aend, afy, asrc in zip(annual_ends, annual_fys, annual_src)
                                      if afy == period["fy"] and asrc == "sec"]
                annual_matches = []
                for annual_end in local_annual_dates:
                    annual_matches.extend(filing for filing in row["filings"]
                                          if filing["form"] in ("10-K", "10-K/A")
                                          and filing["report_date"] == annual_end)
                eightks = [filing for filing in row["filings"]
                           if filing["form"] in ("8-K", "8-K/A") and filing["filing_date"]
                           and (date_from_string(end).toordinal() - 60 <= date_from_string(filing["filing_date"]).toordinal()
                                <= date_from_string(end).toordinal() + 60)]
                row["periods"].append({
                    "ticker": issuer["ticker"],
                    "fy": period["fy"],
                    "q4_end": end,
                    "q4_start_estimate": q4_start,
                    "local_annual_report_dates": local_annual_dates,
                    "annual_10k_matches": annual_matches,
                    "q4_end_matches_local_annual_end": end in local_annual_dates,
                    "target_10k_matches": exact,
                    "eight_k_candidates_within_60d_of_period_end": eightks,
                    "eight_k_candidates_within_60d_of_local_annual_end": [
                        filing for filing in row["filings"]
                        if filing["form"] in ("8-K", "8-K/A") and filing["filing_date"]
                        and any(date_from_string(annual_end).toordinal() - 60
                                <= date_from_string(filing["filing_date"]).toordinal()
                                <= date_from_string(annual_end).toordinal() + 60
                                for annual_end in local_annual_dates)
                    ],
                    "match_status": "exact_report_date" if any(x["report_date"] == end for x in exact)
                    else "near_report_date_candidate" if exact else "no_10k_match_in_submissions_recent",
                })
        mapped.append(row)
    result = {
        "version": 1,
        "sample_id": sample.get("sample_id"),
        "sample_freeze_sha256": sample.get("freeze_sha256"),
        "source": "SEC submissions JSON metadata; used only to map target periods to filings, not as EPS evidence",
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "issuers": mapped,
        "metrics": {
            "sec_http_requests": client.requests,
            "wire_bytes_received": client.wire_bytes_received,
            "decoded_bytes_received": client.bytes_received,
            "cache_hits": client.cache_hits,
            "retries": client.retries,
            "rate_limit_requests_per_second": 1 / SEC_MIN_INTERVAL,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def freeze_hash(sample):
    payload = dict(sample)
    recorded = payload.pop("freeze_sha256", None)
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(canonical)


def date_from_string(value):
    return datetime.strptime(value, "%Y-%m-%d").date()




def filing_quarter(filing_date):
    parsed = date_from_string(filing_date)
    return f"{parsed.year}q{(parsed.month - 1) // 3 + 1}"


def read_dataset_member(archive, names, expected_basename, wanted_adshs=None,
                        wanted_tag_pairs=None, wanted_dimhs=None):
    member = next((name for name in names if name.rsplit("/", 1)[-1].lower() == expected_basename), None)
    if member is None:
        return None, None
    rows = []
    with archive.open(member) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        reader = csv.DictReader(text, delimiter="\t")
        for item in reader:
            if wanted_adshs is not None and item.get("adsh") not in wanted_adshs:
                continue
            if wanted_tag_pairs is not None and (item.get("tag"), item.get("version")) not in wanted_tag_pairs:
                continue
            if wanted_dimhs is not None and item.get("dimh") not in wanted_dimhs:
                continue
            rows.append(item)
    return member, rows


def possible_eps_fact(fact, tag):
    """Wide candidate filter only; this is never the verification rule."""
    unit = (fact.get("uom") or "").lower().replace(" ", "")
    label = " ".join(str(tag.get(key) or "") for key in ("tag", "tlabel", "doc")).lower()
    compact = re.sub(r"[^a-z0-9]", "", label)
    return (any(token in label for token in (
        "earnings per share", "income per share", "loss per share", "profit per share",
        "diluted eps", "basic eps", "per-share", "per share", "eps",
    )) or any(token in compact for token in (
        "earningspershare", "netincomeperdilutedshare", "incomelossperdilutedshare",
        "earningsperdilutedshare", "netincomeperbasicshare", "earningsperbasicshare",
    )) or ("usd" in unit and "share" in unit))


def process_dataset_zip(body, accessions, source):
    if not zipfile.is_zipfile(io.BytesIO(body)):
        raise zipfile.BadZipFile("SEC dataset response is not a ZIP archive")
    archive = zipfile.ZipFile(io.BytesIO(body))
    if source == "fsds":
        bad_member = archive.testzip()
        if bad_member:
            raise zipfile.BadZipFile(f"CRC failure in {bad_member}")
    names = archive.namelist()
    extension = "txt" if source == "fsds" else "tsv"
    target_adshs = set(accessions)
    sub_member, sub_rows = read_dataset_member(archive, names, f"sub.{extension}", target_adshs)
    if source == "notes" and not sub_rows:
        # No target accession row means no filing facts to inspect. Avoid
        # inflating the multi-gigabyte NUM/TXT tables for unrelated filers.
        return {
            "members": {"sub": sub_member, "num": None, "tag": None, "pre": None, "dim": None, "txt": None},
            "submission_rows": [],
            "candidate_facts_by_accession": {accession: [] for accession in target_adshs},
            "note_text_by_accession": {accession: [] for accession in target_adshs},
            "row_counts": {"sub_target": 0, "num_target": 0, "tag_target": 0,
                           "pre_target": 0, "dim_target": 0, "txt_target": 0},
        }
    num_member, num_rows = read_dataset_member(archive, names, f"num.{extension}", target_adshs)
    tag_pairs = {(row.get("tag"), row.get("version")) for row in (num_rows or [])}
    tag_member, tag_rows = read_dataset_member(archive, names, f"tag.{extension}", wanted_tag_pairs=tag_pairs)
    tags = {(row.get("tag"), row.get("version")): row for row in (tag_rows or [])}
    pre_member, pre_rows = (None, None)
    facts = {accession: [] for accession in target_adshs}
    for fact in num_rows or []:
        accession = fact.get("adsh")
        tag = tags.get((fact.get("tag"), fact.get("version")), {})
        if not possible_eps_fact(fact, tag):
            continue
        key = (accession, fact.get("tag"), fact.get("version"))
        facts.setdefault(accession, []).append({
            "tag": fact.get("tag"),
            "version": fact.get("version"),
            "tag_label": tag.get("tlabel"),
            "tag_documentation": tag.get("doc"),
            "custom_tag": tag.get("custom"),
            "datatype": tag.get("datatype"),
            "date": fact.get("ddate"),
            "quarters": fact.get("qtrs"),
            "unit": fact.get("uom"),
            "value": fact.get("value"),
            "segments": fact.get("segments"),
            "coreg": fact.get("coreg"),
            "dimension_hash": fact.get("dimh"),
            "dimension_members": [],
            "presentation": [],
            "classification": "candidate_unverified",
            "source_file": "NUM",
        })
    notes = {accession: [] for accession in target_adshs}
    dim_member, dim_rows, txt_member, txt_rows = (None, None, None, None)
    dimensions = defaultdict(list)
    if source == "notes":
        wanted_dimhs = {fact.get("dimension_hash") for facts_for_accession in facts.values()
                        for fact in facts_for_accession if fact.get("dimension_hash")}
        dim_member, dim_rows = read_dataset_member(archive, names, f"dim.{extension}", wanted_dimhs=wanted_dimhs)
        for row in dim_rows or []:
            dimensions[row.get("dimh")].append(row)
        candidate_tag_keys = {(acc, fact.get("tag"), fact.get("version"))
                              for acc, facts_for_accession in facts.items() for fact in facts_for_accession}
        pre_member, pre_rows = read_dataset_member(
            archive, names, f"pre.{extension}", wanted_adshs=target_adshs,
            wanted_tag_pairs={(tag, version) for _acc, tag, version in candidate_tag_keys},
        )
        pre_by_tag = defaultdict(list)
        for row in pre_rows or []:
            pre_by_tag[(row.get("adsh"), row.get("tag"), row.get("version"))].append(row)
        for accession, facts_for_accession in facts.items():
            for fact in facts_for_accession:
                fact["dimension_members"] = dimensions.get(fact.get("dimension_hash"), [])
                fact["presentation"] = [{
                    "statement": row.get("stmt"), "report": row.get("report"),
                    "line": row.get("line"), "label": row.get("plabel"),
                } for row in pre_by_tag.get((accession, fact.get("tag"), fact.get("version")), [])]
        txt_member, txt_rows = read_dataset_member(archive, names, f"txt.{extension}", wanted_adshs=target_adshs)
    for row in txt_rows or []:
        accession = row.get("adsh")
        tag = tags.get((row.get("tag"), row.get("version")), {})
        value = row.get("value") or ""
        label = " ".join(str(tag.get(key) or "") for key in ("tag", "tlabel", "doc"))
        if not re.search(r"earnings? per share|income per share|loss per share|diluted|basic eps|adjusted eps|three months ended|quarterly financial", label + " " + value, re.I):
            continue
        notes.setdefault(accession, []).append({
            "tag": row.get("tag"), "version": row.get("version"),
            "tag_label": tag.get("tlabel"), "tag_documentation": tag.get("doc"),
            "date": row.get("ddate"), "quarters": row.get("qtrs"),
            "dimension_hash": row.get("dimh"),
            "dimension_members": dimensions.get(row.get("dimh"), []),
            "text_length": len(value), "text": value[:8000],
            "source_file": "TXT",
        })
    return {
        "members": {"sub": sub_member, "num": num_member, "tag": tag_member,
                    "pre": pre_member, "dim": dim_member, "txt": txt_member},
        "submission_rows": sub_rows or [],
        "candidate_facts_by_accession": facts,
        "note_text_by_accession": notes,
        "row_counts": {"sub_target": len(sub_rows or []), "num_target": len(num_rows or []),
                       "tag_target": len(tag_rows or []), "pre_target": len(pre_rows or []),
                       "dim_target": len(dim_rows or []), "txt_target": len(txt_rows or [])},
    }


def dataset_accessions(filing_map):
    """Map frozen periods to their annual filing accessions for A/B inspection."""
    out = {}
    for issuer in filing_map.get("issuers", []):
        for period in issuer.get("periods", []):
            # If the local Q4 date is inconsistent with its annual SEC end, the
            # annual accession is retained for diagnosis; acceptance still
            # requires the frozen Q4 date and matching fiscal context.
            filings = period.get("annual_10k_matches") or period.get("target_10k_matches") or []
            for filing in filings:
                accession = filing.get("accession")
                if not accession:
                    continue
                out.setdefault(accession, {
                    "accession": accession,
                    "ticker": period["ticker"],
                    "cik": issuer["cik"],
                    "fy": period["fy"],
                    "target_q4_end": period["q4_end"],
                    "local_annual_report_dates": period.get("local_annual_report_dates", []),
                    "q4_end_matches_local_annual_end": period.get("q4_end_matches_local_annual_end", False),
                    "form": filing.get("form"),
                    "filing_date": filing.get("filing_date"),
                    "report_date": filing.get("report_date"),
                    "primary_document": filing.get("primary_document"),
                    "annual_accession_used_for_diagnostic": bool(period.get("annual_10k_matches"))
                    and filing not in period.get("target_10k_matches", []),
                })
    return out


def run_source_a(filing_map_path=None, output_path=None):
    filing_map_path = Path(filing_map_path or ROOT / "docs/report/sec-q4-eps-poc/filing-map.json")
    output_path = Path(output_path or ROOT / "docs/report/sec-q4-eps-poc/source-a.json")
    if not filing_map_path.is_absolute():
        filing_map_path = ROOT / filing_map_path
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    filing_map = load_json(filing_map_path)
    accessions = dataset_accessions(filing_map)
    grouped = defaultdict(list)
    for accession, record in accessions.items():
        grouped[filing_quarter(record["filing_date"])].append(accession)
    client = SecClient()
    zip_results = {}
    accession_results = {
        accession: {"accession": accession, "metadata": record, "sec_zip_rows": 0,
                    "eps_candidates": [], "dataset_status": "pending"}
        for accession, record in accessions.items()
    }
    for quarter, target_accessions in sorted(grouped.items()):
        url = f"https://www.sec.gov/files/dera/data/financial-statement-data-sets/{quarter}.zip"
        response = client.fetch(url)
        zip_result = {
            "url": url,
            "http_status": response["status"],
            "retrieved_at": response["retrieved_at"],
            "cache_hit": response.get("cache_hit", False),
            "download_bytes": len(response["body"]),
            "wire_bytes": response.get("wire_bytes"),
            "sha256": sha256_bytes(response["body"]),
            "target_accessions": target_accessions,
        }
        if response["status"] == 200 and not response.get("error"):
            try:
                processed = process_dataset_zip(response["body"], target_accessions, "fsds")
            except (zipfile.BadZipFile, KeyError, OSError, ValueError) as error:
                zip_result.update({"parse_status": "invalid_zip_or_schema", "parse_error": str(error)})
            else:
                zip_result.update({"parse_status": "parsed", "members": processed["members"],
                                   "row_counts": processed["row_counts"]})
                sub_by_accession = {row.get("adsh"): row for row in processed["submission_rows"]}
                for accession in target_accessions:
                    sub = sub_by_accession.get(accession)
                    facts = processed["candidate_facts_by_accession"].get(accession, [])
                    item = accession_results[accession]
                    item.update({"dataset_status": "accession_found" if sub else "accession_absent_from_sub",
                                 "sub_row": sub,
                                 "sec_zip_rows": processed["row_counts"]["num_target"],
                                 "eps_candidates": facts})
                    item["accession_matches_metadata"] = bool(sub) and (
                        str(sub.get("cik", "")).zfill(10) == item["metadata"]["cik"]
                        and str(sub.get("filed", "")).replace("-", "") == item["metadata"]["filing_date"].replace("-", "")
                        and str(sub.get("period", "")).replace("-", "") == item["metadata"]["report_date"].replace("-", "")
                        and sub.get("form") == item["metadata"]["form"]
                    )
        else:
            zip_result.update({"parse_status": "fetch_failed", "error": response.get("error")})
            for accession in target_accessions:
                accession_results[accession]["dataset_status"] = (
                    "permission_error" if response["status"] == 403 else "technical_failure"
                )
                accession_results[accession]["http_status"] = response["status"]
                accession_results[accession]["error"] = response.get("error")
        zip_results[quarter] = zip_result
    result = {
        "version": 1,
        "source": "SEC Financial Statement Data Sets (Source A)",
        "documentation": "https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets",
        "data_scope_note": "Quarter key is filing-calendar-quarter only; accession, CIK, form, report date and qtrs are matched separately.",
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "zip_count": len(zip_results),
        "zips": zip_results,
        "accessions": accession_results,
        "metrics": {"sec_http_requests": client.requests, "wire_bytes_received": client.wire_bytes_received,
                    "decoded_bytes_received": client.bytes_received, "cache_hits": client.cache_hits,
                    "retries": client.retries, "rate_limit_requests_per_second": 1 / SEC_MIN_INTERVAL},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def run_source_b(filing_map_path=None, output_path=None):
    filing_map_path = Path(filing_map_path or ROOT / "docs/report/sec-q4-eps-poc/filing-map.json")
    output_path = Path(output_path or ROOT / "docs/report/sec-q4-eps-poc/source-b.json")
    if not filing_map_path.is_absolute():
        filing_map_path = ROOT / filing_map_path
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    filing_map = load_json(filing_map_path)
    accessions = dataset_accessions(filing_map)
    grouped = defaultdict(list)
    for accession, record in accessions.items():
        grouped[filing_quarter(record["filing_date"])].append(accession)
    client = SecClient()
    zip_results = {}
    accession_results = {
        accession: {"accession": accession, "metadata": record, "sec_zip_rows": 0,
                    "eps_candidates": [], "note_text_candidates": [], "dataset_status": "pending"}
        for accession, record in accessions.items()
    }
    for quarter, target_accessions in sorted(grouped.items()):
        url = f"https://www.sec.gov/files/dera/data/financial-statement-notes-data-sets/{quarter}_notes.zip"
        # Notes ZIPs are 400–700 MB each. Keep the earlier 2012q3 schema probe
        # cached; process later quarters sequentially without retaining their
        # whole-market archives in /tmp.
        response = client.fetch(url, cache=(quarter == "2012q3"))
        zip_result = {
            "url": url,
            "http_status": response["status"],
            "retrieved_at": response["retrieved_at"],
            "cache_hit": response.get("cache_hit", False),
            "download_bytes": len(response["body"]),
            "wire_bytes": response.get("wire_bytes"),
            "sha256": sha256_bytes(response["body"]),
            "target_accessions": target_accessions,
        }
        if response["status"] == 200 and not response.get("error"):
            try:
                processed = process_dataset_zip(response["body"], target_accessions, "notes")
            except (zipfile.BadZipFile, KeyError, OSError, ValueError) as error:
                zip_result.update({"parse_status": "invalid_zip_or_schema", "parse_error": str(error)})
            else:
                zip_result.update({"parse_status": "parsed", "members": processed["members"],
                                   "row_counts": processed["row_counts"]})
                sub_by_accession = {row.get("adsh"): row for row in processed["submission_rows"]}
                for accession in target_accessions:
                    sub = sub_by_accession.get(accession)
                    item = accession_results[accession]
                    item.update({
                        "dataset_status": "accession_found" if sub else "accession_absent_from_sub",
                        "sub_row": sub,
                        "sec_zip_rows": processed["row_counts"]["num_target"],
                        "eps_candidates": processed["candidate_facts_by_accession"].get(accession, []),
                        "note_text_candidates": processed["note_text_by_accession"].get(accession, []),
                    })
                    item["accession_matches_metadata"] = bool(sub) and (
                        str(sub.get("cik", "")).zfill(10) == item["metadata"]["cik"]
                        and str(sub.get("filed", "")).replace("-", "") == item["metadata"]["filing_date"].replace("-", "")
                        and str(sub.get("period", "")).replace("-", "") == item["metadata"]["report_date"].replace("-", "")
                        and sub.get("form") == item["metadata"]["form"]
                    )
        else:
            zip_result.update({"parse_status": "fetch_failed", "error": response.get("error")})
            for accession in target_accessions:
                accession_results[accession]["dataset_status"] = (
                    "permission_error" if response["status"] == 403 else "technical_failure"
                )
                accession_results[accession]["http_status"] = response["status"]
                accession_results[accession]["error"] = response.get("error")
        zip_results[quarter] = zip_result
        print(f"[Source B] {quarter} HTTP {zip_result['http_status']} "
              f"bytes={zip_result['download_bytes']} parse={zip_result['parse_status']}",
              file=sys.stderr, flush=True)
    result = {
        "version": 1,
        "source": "SEC Financial Statement and Notes Data Sets (Source B)",
        "documentation": "https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets",
        "data_scope_note": "Includes primary statements plus tagged numeric and nonnumeric note facts; accession and fiscal context are matched separately.",
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "zip_count": len(zip_results),
        "zips": zip_results,
        "accessions": accession_results,
        "metrics": {"sec_http_requests": client.requests, "wire_bytes_received": client.wire_bytes_received,
                    "decoded_bytes_received": client.bytes_received, "cache_hits": client.cache_hits,
                    "retries": client.retries, "rate_limit_requests_per_second": 1 / SEC_MIN_INTERVAL},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


EPS_CONCEPT_RE = re.compile(
    r"earnings?pershare|income(loss)?per(diluted|basic|common)share|"
    r"(diluted|basic)eps|eps(diluted|basic)", re.I,
)
EPS_TEXT_RE = re.compile(
    r"earnings?\s+(?:\(loss\)\s+)?per\s+(?:(?:basic|diluted|common)\s+)?share|"
    r"(?:basic\s+and\s+)?diluted\s+(?:net\s+)?(?:earnings?|loss)(?:\s*\([^)]*\))?\s+per\s+(?:common\s+)?share|"
    r"per\s+(?:fully\s+)?diluted\s+share|"
    r"net\s+(?:income|loss)(?:\s*\([^)]*\))?\s+per\s+(?:(?:basic|diluted)\s+(?:and\s+(?:basic|diluted)\s+)?)?(?:common\s+)?share|"
    r"(?:basic|diluted)\s+eps|eps\s+(?:basic|diluted)|"
    r"quarterly\s+financial\s+data|fourth\s+quarter|three\s+months\s+ended", re.I,
)
EPS_KEYWORD_RE = re.compile(
    r"quarterly financial information|quarterly financial data|selected quarterly|"
    r"earnings per share|diluted earnings per share|diluted net income per share|"
    r"fourth quarter results|(?:basic|diluted)\s+eps|eps\s+(?:basic|diluted)|"
    r"(?:basic\s+and\s+)?diluted\s+(?:net\s+)?(?:earnings?|loss)(?:\s*\([^)]*\))?\s+per\s+(?:common\s+)?share|"
    r"net\s+(?:income|loss)(?:\s*\([^)]*\))?\s+per\s+(?:(?:basic|diluted)\s+(?:and\s+(?:basic|diluted)\s+)?)?(?:common\s+)?share|"
    r"per\s+fully\s+diluted\s+share", re.I,
)


def is_nontext_payload(name, body, content_type=None):
    """Keep binary exhibits out of the HTML parser, even when their filename says EX-99."""
    extension = Path(str(name or "")).suffix.lower()
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    if mime.startswith("image/") or mime in {"application/pdf", "application/octet-stream"}:
        return True
    signature = bytes(body[:16])
    if signature.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM")):
        return True
    if extension in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".pdf"}:
        return True
    return False


def html_plain_text(element):
    value = " ".join(part.strip() for part in element.itertext() if part.strip())
    return re.sub(r"\s+", " ", value).strip()


def parse_inline_filing(body):
    """Extract raw XBRL contexts, per-share facts and EPS table text from a SEC filing."""
    from lxml import html as lxml_html

    root = lxml_html.fromstring(body)
    contexts = {}
    for node in (item for item in root.iter() if etree_local_name(item) == "context"):
        context_id = node.get("id")
        if not context_id:
            continue
        start = descendant_text(node, "startdate") or None
        end = descendant_text(node, "enddate") or None
        instant = descendant_text(node, "instant") or None
        dimensions = []
        for member in (item for item in node.iterdescendants()
                       if etree_local_name(item) in ("explicitmember", "typedmember")):
            dimensions.append({
                "kind": etree_local_name(member),
                "axis": member.get("dimension"),
                "member": " ".join(part.strip() for part in member.itertext() if part.strip()),
            })
        contexts[context_id] = {
            "start": start, "end": end, "instant": instant,
            "dimensions": dimensions,
        }
    units = {}
    for node in (item for item in root.iter() if etree_local_name(item) == "unit"):
        unit_id = node.get("id")
        if unit_id:
            units[unit_id] = [
                " ".join(part.strip() for part in measure.itertext() if part.strip())
                for measure in node.iterdescendants() if etree_local_name(measure) == "measure"
            ]

    facts = []
    for fact in (item for item in root.iter()
                 if etree_local_name(item) in ("nonfraction", "fraction")):
        concept = fact.get("name") or ""
        if not EPS_CONCEPT_RE.search(re.sub(r"[^a-z0-9]", "", concept.lower())):
            continue
        raw_value = re.sub(r"\s+", "", "".join(fact.itertext())).replace(",", "")
        value = None
        try:
            number = Decimal(raw_value)
            scale = int(fact.get("scale") or 0)
            number *= Decimal(10) ** scale
            if fact.get("sign") == "-":
                number = -number
            value = float(number)
        except (InvalidOperation, ValueError, OverflowError):
            pass
        row = next((parent for parent in fact.iterancestors() if etree_local_name(parent) == "tr"), None)
        table = next((parent for parent in fact.iterancestors() if etree_local_name(parent) == "table"), None)
        facts.append({
            "concept": concept,
            "context_ref": fact.get("contextref"),
            "context": contexts.get(fact.get("contextref")),
            "unit_ref": fact.get("unitref"),
            "unit_measures": units.get(fact.get("unitref")),
            "decimals": fact.get("decimals"),
            "scale": fact.get("scale"),
            "sign": fact.get("sign"),
            "raw_value": raw_value,
            "value": value,
            "fact_text": html_plain_text(fact)[:300],
            "row_text": html_plain_text(row)[:1600] if row is not None else None,
            "table_text": html_plain_text(table)[:8000] if table is not None else None,
            "row_xpath": row.getroottree().getpath(row) if row is not None else None,
            "fact_xpath": fact.getroottree().getpath(fact),
        })

    eps_tables = []
    for table in (item for item in root.iter() if etree_local_name(item) == "table"):
        table_text = html_plain_text(table)
        if EPS_TEXT_RE.search(table_text):
            eps_tables.append({
                "text": table_text[:12000],
                "xpath": table.getroottree().getpath(table),
                "contains_q4_language": bool(re.search(r"fourth\s+quarter|three\s+months\s+ended", table_text, re.I)),
                "contains_adjusted_language": bool(re.search(r"adjusted|non.?gaap|non.?GAAP", table_text, re.I)),
            })
    document_text = re.sub(r"\s+", " ", root.text_content())
    keyword_hits = []
    for match in EPS_KEYWORD_RE.finditer(document_text):
        start, end = max(0, match.start() - 180), min(len(document_text), match.end() + 420)
        keyword_hits.append({"term": match.group(0), "offset": match.start(), "excerpt": document_text[start:end]})
        if len(keyword_hits) >= 200:
            break
    quarterly_section_excerpts = []
    for match in re.finditer(r"quarterly\s+financial\s+(?:data|information)|selected\s+quarterly\s+financial", document_text, re.I):
        start = max(0, match.start() - 180)
        end = min(len(document_text), match.end() + 10000)
        quarterly_section_excerpts.append({
            "term": match.group(0), "offset": match.start(),
            "text": document_text[start:end],
            "truncated": end < len(document_text),
        })
        if len(quarterly_section_excerpts) >= 10:
            break
    return {
        "contexts": contexts,
        "units": units,
        "eps_facts": facts,
        "eps_tables": eps_tables,
        "keyword_hits": keyword_hits,
        "quarterly_section_excerpts": quarterly_section_excerpts,
        "document_text_length": len(document_text),
    }


def descendant_text(node, name):
    for item in node.iterdescendants():
        if etree_local_name(item) == name:
            value = " ".join(part.strip() for part in item.itertext() if part.strip())
            if value:
                return value
    return ""


def etree_local_name(node):
    tag = getattr(node, "tag", "")
    if not isinstance(tag, str):  # lxml HTML comments and processing-instruction nodes
        return ""
    return tag.rsplit("}", 1)[-1].split(":")[-1].lower()


def assess_inline_eps_fact(fact, target, *, expected_class=None, split_adjustment_verified=True):
    """Conservative gate: raw SEC facts are candidates until every period/basis check passes."""
    context = fact.get("context") or {}
    start, end = context.get("start"), context.get("end")
    issues = []
    if not target.get("q4_end_matches_local_annual_end", False):
        issues.append("local_fiscal_period_mapping_mismatch")
    if end != target.get("q4_end"):
        issues.append("end_date_mismatch")
    if not start or not end:
        issues.append("duration_context_missing")
    else:
        duration_days = (date_from_string(end) - date_from_string(start)).days
        if not 65 <= duration_days <= 110:
            issues.append("not_single_quarter_duration")
        expected_start = target.get("q4_start_estimate")
        if expected_start and start != expected_start:
            issues.append("quarter_start_mismatch")
    concept = re.sub(r"[^a-z0-9]", "", (fact.get("concept") or "").lower())
    diluted = "diluted" in concept or "basicanddiluted" in concept
    if not diluted:
        issues.append("basic_only_or_dilution_unverified")
    unit_measures = fact.get("unit_measures")
    unit = str(fact.get("unit") or "").lower()
    if unit_measures is not None:
        currency_present = any("usd" in str(measure).lower() for measure in unit_measures)
        share_denominator_present = any("share" in str(measure).lower() for measure in unit_measures)
        if not currency_present or not share_denominator_present:
            issues.append("not_usd_per_share_unit")
    elif unit and unit == "usd":
        # FSDS/Notes NUM uses the conventional USD unit for per-share concepts.
        pass
    else:
        issues.append("per_share_unit_unverified")
    if not concept.startswith("usgaap") and not fact.get("gaap_evidence"):
        issues.append("gaap_tag_or_statement_basis_unverified")
    row_text = fact.get("row_text") or ""
    table_text = fact.get("table_text") or ""
    basis_text = f"{row_text} {table_text}"
    if re.search(r"adjusted|non.?gaap|non.?GAAP", basis_text, re.I):
        issues.append("non_gaap_context_possible")
    if fact.get("value") is None:
        issues.append("numeric_value_unavailable")
    dimensions = context.get("dimensions") or []
    class_dimensions = [item for item in dimensions if re.search(r"class|stock|share", (item.get("axis") or "") + " " + (item.get("member") or ""), re.I)]
    if class_dimensions and not expected_class:
        issues.append("share_class_unresolved")
    elif class_dimensions and expected_class and not any(expected_class.lower() in (item.get("member") or "").lower() for item in class_dimensions):
        issues.append("wrong_share_class")
    if not split_adjustment_verified:
        issues.append("split_basis_unresolved")
    verified = not issues
    return {
        "classification": "A_verified_direct_q4_eps" if verified else "B_candidate_needs_verification",
        "verified": verified,
        "diluted": diluted,
        "value": fact.get("value"),
        "issues": issues,
    }


def select_release_eps_column(disclosure, target):
    """Select a single three-month column by issuer fiscal dates, excluding FY columns."""
    matches = []
    for index, column in enumerate(disclosure.get("period_columns", [])):
        start, end = column.get("start"), column.get("end")
        if end != target.get("q4_end") or not start:
            continue
        try:
            duration_days = (date_from_string(end) - date_from_string(start)).days
        except (TypeError, ValueError):
            continue
        if not 65 <= duration_days <= 110:
            continue
        expected_start = target.get("q4_start_estimate")
        if expected_start and start != expected_start:
            continue
        matches.append((index, duration_days))
    return matches


def assess_release_eps_disclosure(disclosure, target):
    """Conservative adjudication for a normalized SEC earnings-release table/text candidate."""
    issues = []
    if not target.get("q4_end_matches_local_annual_end", False):
        issues.append("local_fiscal_period_mapping_mismatch")
    selected = select_release_eps_column(disclosure, target)
    if len(selected) != 1:
        issues.append("single_q4_column_not_resolved")
        value = None
    else:
        column_index, _ = selected[0]
        row_values = disclosure.get("row_values", [])
        value = row_values[column_index] if column_index < len(row_values) else None
        if value is None:
            issues.append("numeric_value_unavailable")
        else:
            try:
                numeric = Decimal(str(value).replace("$", "").replace(",", "").replace("(", "-").replace(")", ""))
                value = float(numeric)
            except (InvalidOperation, ValueError):
                value = None
                issues.append("numeric_value_unavailable")

    label = disclosure.get("measure_label") or ""
    context = f"{label} {disclosure.get('statement_scope', '')}"
    diluted = bool(re.search(r"diluted", label, re.I))
    if not diluted:
        issues.append("basic_only_or_dilution_unverified")
    if re.search(r"adjusted|non.?gaap", context, re.I):
        issues.append("non_gaap_context_possible")
    if not disclosure.get("gaap_statement_basis_verified"):
        issues.append("gaap_tag_or_statement_basis_unverified")
    if not re.search(r"per\s+(?:(?:basic|diluted|fully)\s+)*(?:and\s+(?:basic|diluted)\s+)?(?:common\s+)?share", label, re.I):
        issues.append("per_share_unit_unverified")
    if disclosure.get("share_class") != "common":
        issues.append("share_class_unresolved")
    if not disclosure.get("consolidated_scope_verified"):
        issues.append("consolidated_scope_unverified")
    if not disclosure.get("split_review_complete"):
        issues.append("split_basis_unresolved")
    if not disclosure.get("accession") or not str(disclosure.get("source_url", "")).startswith("https://www.sec.gov/Archives/"):
        issues.append("source_identity_unverified")
    return {
        "classification": "A_verified_direct_q4_eps" if not issues else "B_candidate_needs_verification",
        "verified": not issues,
        "value": value,
        "selected_q4_column": selected[0][0] if len(selected) == 1 else None,
        "issues": issues,
    }


def compare_positive_control(sec_value, database_value, *, tolerance=0.005):
    """Compare a raw SEC Q4 fact to the local value already on its recorded split basis."""
    if sec_value is None or database_value is None:
        return {"status": "unverifiable_missing_value", "difference": None}
    difference = abs(float(sec_value) - float(database_value))
    return {"status": "consistent" if difference <= tolerance else "mismatch",
            "difference": difference, "tolerance": tolerance}


def eps_on_current_split_basis(value, end, events):
    """Apply the repository's per-share split convention to an as-filed EPS value."""
    factor = 1.0
    for day, ratio in events:
        if day > end and (ratio >= financials.MIN_SPLIT_RATIO or ratio <= 1 / financials.MIN_SPLIT_RATIO):
            factor *= float(ratio)
    return float(value) / factor if factor else None


def compare_filing_versions(original_facts, amendment_facts, target):
    """Flag conflicting direct Q4 facts across a 10-K and its 10-K/A."""
    def values(facts):
        return sorted({round(float(fact["value"]), 8) for fact in facts
                       if fact.get("value") is not None
                       and (fact.get("context") or {}).get("end") == target.get("q4_end")
                       and (fact.get("context") or {}).get("start") == target.get("q4_start_estimate")
                       and "diluted" in re.sub(r"[^a-z0-9]", "", (fact.get("concept") or "").lower())})
    base, amendment = values(original_facts), values(amendment_facts)
    if not base or not amendment:
        return {"status": "one_version_missing_direct_q4_fact", "original_values": base,
                "amendment_values": amendment}
    if base != amendment:
        return {"status": "version_conflict", "original_values": base,
                "amendment_values": amendment}
    return {"status": "versions_agree", "original_values": base,
            "amendment_values": amendment}


def dataset_fallback_status(http_status, parse_status, accession_present):
    """Keep an empty SEC dataset accession distinct from transport/parser failure."""
    if http_status != 200:
        return "E_permission_error" if http_status == 403 else "E_technical_failure"
    if parse_status != "parsed":
        return "E_technical_failure"
    return "accession_found" if accession_present else "fallback_to_next_source"


def run_source_c(filing_map_path=None, output_path=None):
    """Fetch the original SEC 10-K/10-K/A documents selected before data inspection."""
    filing_map_path = Path(filing_map_path or ROOT / "docs/report/sec-q4-eps-poc/filing-map.json")
    output_path = Path(output_path or ROOT / "docs/report/sec-q4-eps-poc/source-c.json")
    if not filing_map_path.is_absolute():
        filing_map_path = ROOT / filing_map_path
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    filing_map = load_json(filing_map_path)
    accessions = dataset_accessions(filing_map)
    client = SecClient()
    results = {}
    for accession, metadata in sorted(accessions.items(), key=lambda item: (item[1]["filing_date"], item[0])):
        cik_no_zeros = str(int(metadata["cik"]))
        accession_path = accession.replace("-", "")
        document = metadata.get("primary_document")
        if not document:
            results[accession] = {"metadata": metadata, "http_status": None,
                                  "parse_status": "technical_failure", "error": "no primary document in SEC submissions"}
            continue
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/"
               f"{accession_path}/{urllib.parse.quote(document)}")
        response = client.fetch(url)
        result = {
            "metadata": metadata,
            "url": url,
            "http_status": response["status"],
            "retrieved_at": response["retrieved_at"],
            "cache_hit": response.get("cache_hit", False),
            "download_bytes": len(response["body"]),
            "wire_bytes": response.get("wire_bytes"),
            "sha256": sha256_bytes(response["body"]),
            "error": response.get("error"),
        }
        if response["status"] == 200 and not response.get("error"):
            try:
                parsed = parse_inline_filing(response["body"])
            except Exception as error:  # preserve actual filing response and distinguish parser failure
                result.update({"parse_status": "parser_failure", "parse_error": f"{type(error).__name__}: {error}"})
            else:
                result.update({"parse_status": "parsed", **parsed})
        else:
            result["parse_status"] = "permission_error" if response["status"] == 403 else "technical_failure"
        results[accession] = result
        print(f"[Source C] {accession} {metadata['form']} HTTP {result['http_status']} "
              f"bytes={result['download_bytes']} parse={result['parse_status']}",
              file=sys.stderr, flush=True)
    output = {
        "version": 1,
        "source": "SEC original 10-K/10-K/A filings (Source C)",
        "documentation": "https://www.sec.gov/Archives/",
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "accession_count": len(results),
        "accessions": results,
        "metrics": {"sec_http_requests": client.requests, "wire_bytes_received": client.wire_bytes_received,
                    "decoded_bytes_received": client.bytes_received, "cache_hits": client.cache_hits,
                    "retries": client.retries, "rate_limit_requests_per_second": 1 / SEC_MIN_INTERVAL},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def candidate_eight_k_filings(filing_map):
    """Select only sample issuers' 8-Ks filed within 120 days after the frozen/SEC annual ends."""
    selected = {}
    for issuer in filing_map.get("issuers", []):
        for period in issuer.get("periods", []):
            basis_dates = {period["q4_end"], *(period.get("local_annual_report_dates") or [])}
            for filing in issuer.get("filings", []):
                if filing.get("form") not in ("8-K", "8-K/A") or not filing.get("filing_date"):
                    continue
                filed = date_from_string(filing["filing_date"])
                matched_dates = [basis for basis in basis_dates
                                 if 0 <= (filed - date_from_string(basis)).days <= 120]
                if not matched_dates:
                    continue
                accession = filing.get("accession")
                if not accession:
                    continue
                item = selected.setdefault(accession, {**filing, "cik": issuer["cik"],
                                                         "tickers": [], "period_windows": []})
                if period["ticker"] not in item["tickers"]:
                    item["tickers"].append(period["ticker"])
                for basis in matched_dates:
                    window = {
                        "ticker": period["ticker"], "fy": period["fy"],
                        "frozen_q4_end": period["q4_end"], "window_end": basis,
                        "diagnostic_only": basis != period["q4_end"],
                    }
                    if window not in item["period_windows"]:
                        item["period_windows"].append(window)
    return selected


def likely_results_exhibit(name, document_type="", description=""):
    """Select releases even when SEC labels the item generically or omits EX-99.1 numbering."""
    document_type = str(document_type or "").upper()
    compact_name = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    return bool(
        document_type.startswith("EX-99")
        or re.search(r"earnings|financial results|quarterly results|press release|year.end results",
                     str(description or ""), re.I)
        or re.search(r"ex(?:hibit)?99", compact_name, re.I)
        or re.search(r"press|earnings|financialresults|quarterlyresults", compact_name, re.I)
    )


RESULTS_LINK_TEXT_RE = re.compile(
    r"earnings|financial results|quarterly results|press release|earnings release|"
    r"fourth quarter|year.end results|corporate presentation", re.I,
)


def linked_results_exhibits(primary_body, index_items):
    """Follow release links from an 8-K body when EDGAR uses a generic exhibit filename/type."""
    from lxml import html as lxml_html

    indexed = {str(item.get("name", "")).lower(): item for item in index_items if item.get("name")}
    try:
        root = lxml_html.fromstring(primary_body)
    except (ValueError, TypeError):
        return []
    selected = []
    seen = set()
    for anchor in root.xpath("//a[@href]"):
        href = urllib.parse.unquote(urllib.parse.urlsplit(anchor.get("href", "")).path)
        name = Path(href).name
        item = indexed.get(name.lower())
        if not item or name.lower() in seen:
            continue
        anchor_text = html_plain_text(anchor)
        if (likely_results_exhibit(name, item.get("type"), item.get("description"))
                or RESULTS_LINK_TEXT_RE.search(anchor_text)):
            selected.append({"name": item["name"], "type": item.get("type"),
                             "description": item.get("description", ""),
                             "size": item.get("size"), "linked_from_primary": anchor_text[:300]})
            seen.add(name.lower())
    return selected


def run_source_d(filing_map_path=None, output_path=None):
    """Inspect nearby SEC 8-K filing indexes and earnings/financial-result exhibits."""
    filing_map_path = Path(filing_map_path or ROOT / "docs/report/sec-q4-eps-poc/filing-map.json")
    output_path = Path(output_path or ROOT / "docs/report/sec-q4-eps-poc/source-d.json")
    if not filing_map_path.is_absolute():
        filing_map_path = ROOT / filing_map_path
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    filing_map = load_json(filing_map_path)
    candidates = candidate_eight_k_filings(filing_map)
    client = SecClient()
    filings = {}
    for accession, metadata in sorted(candidates.items(), key=lambda item: (item[1]["filing_date"], item[0])):
        cik_no_zeros = str(int(metadata["cik"]))
        accession_path = accession.replace("-", "")
        base_url = f"https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_path}/"
        index_url = base_url + "index.json"
        index_response = client.fetch(index_url)
        record = {
            "metadata": metadata,
            "index_url": index_url,
            "index_http_status": index_response["status"],
            "index_retrieved_at": index_response["retrieved_at"],
            "index_bytes": len(index_response["body"]),
            "index_wire_bytes": index_response.get("wire_bytes"),
            "index_cache_hit": index_response.get("cache_hit", False),
            "index_sha256": sha256_bytes(index_response["body"]),
            "index_error": index_response.get("error"),
            "documents": [],
        }
        if index_response["status"] != 200 or index_response.get("error"):
            record["status"] = "permission_error" if index_response["status"] == 403 else "technical_failure"
            filings[accession] = record
            print(f"[Source D] {accession} index HTTP {record['index_http_status']} status={record['status']}",
                  file=sys.stderr, flush=True)
            continue
        try:
            index_payload = json.loads(index_response["body"].decode("utf-8"))
            index_items = (index_payload.get("directory") or {}).get("item") or []
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError) as error:
            record.update({"status": "parser_failure", "index_parse_error": str(error)})
            filings[accession] = record
            print(f"[Source D] {accession} index HTTP 200 status=parser_failure", file=sys.stderr, flush=True)
            continue
        record["index_item_count"] = len(index_items)
        primary_document = metadata.get("primary_document")
        chosen = []
        for item in index_items:
            name = item.get("name")
            if not name or name == primary_document:
                continue
            document_type = str(item.get("type") or "").upper()
            description = str(item.get("description") or "")
            if likely_results_exhibit(name, document_type, description):
                chosen.append({"name": name, "type": item.get("type"),
                               "description": description, "size": item.get("size")})
        # Always inspect the 8-K body itself; SEC index metadata can be generic, so also
        # follow explicit earnings/results exhibit links from that body.
        documents = []
        if primary_document:
            documents.append({"name": primary_document, "type": metadata.get("form"),
                              "description": "SEC submissions primary 8-K document"})
        documents.extend(chosen)
        prefetched = {}
        if primary_document:
            primary_url = base_url + urllib.parse.quote(primary_document)
            primary_response = client.fetch(primary_url)
            prefetched[primary_url] = primary_response
            if primary_response["status"] == 200 and not primary_response.get("error"):
                documents.extend(linked_results_exhibits(primary_response["body"], index_items))
        seen_names = set()
        for document_meta in documents:
            name = document_meta["name"]
            if name in seen_names:
                continue
            seen_names.add(name)
            url = base_url + urllib.parse.quote(name)
            response = prefetched.get(url) or client.fetch(url)
            document_record = {
                **document_meta,
                "url": url,
                "http_status": response["status"],
                "retrieved_at": response["retrieved_at"],
                "cache_hit": response.get("cache_hit", False),
                "bytes": len(response["body"]),
                "wire_bytes": response.get("wire_bytes"),
                "sha256": sha256_bytes(response["body"]),
                "error": response.get("error"),
            }
            if response["status"] == 200 and not response.get("error"):
                content_type = response.get("headers", {}).get("Content-Type")
                if is_nontext_payload(name, response["body"], content_type):
                    document_record.update({
                        "parse_status": "unsupported_nontext",
                        "content_type": content_type,
                        "parse_note": "Binary SEC attachment retained by URL/hash; HTML parser skipped it. See source-d-ocr.json for OCR review.",
                    })
                else:
                    try:
                        parsed = parse_inline_filing(response["body"])
                    except Exception as error:
                        document_record.update({"parse_status": "parser_failure",
                                                "parse_error": f"{type(error).__name__}: {error}"})
                    else:
                        # Keep only directly useful passages and EPS-like tagged facts, not whole exhibits.
                        eps_tables = [table for table in parsed["eps_tables"]
                                      if re.search(r"earnings?\s+per|diluted.{0,30}share|basic.{0,30}share|eps", table["text"], re.I)]
                        keyword_hits = [hit for hit in parsed["keyword_hits"]
                                        if EPS_KEYWORD_RE.search(hit["excerpt"])]
                        document_record.update({
                            "parse_status": "parsed",
                            "eps_facts": parsed["eps_facts"],
                            "eps_tables": eps_tables,
                            "quarterly_section_excerpts": parsed["quarterly_section_excerpts"],
                            "keyword_hits": keyword_hits[:30],
                            "document_text_length": parsed["document_text_length"],
                        })
            else:
                document_record["parse_status"] = "permission_error" if response["status"] == 403 else "technical_failure"
            record["documents"].append(document_record)
        failure_docs = [doc for doc in record["documents"]
                        if doc["parse_status"] not in {"parsed", "unsupported_nontext"}]
        unsupported_docs = [doc for doc in record["documents"]
                            if doc["parse_status"] == "unsupported_nontext"]
        record["unsupported_nontext_attachment_count"] = len(unsupported_docs)
        record["status"] = (
            "technical_failure" if failure_docs else
            "searched_with_unsupported_attachments" if unsupported_docs else "searched"
        )
        filings[accession] = record
        print(f"[Source D] {accession} {metadata['filing_date']} docs={len(record['documents'])} status={record['status']}",
              file=sys.stderr, flush=True)
    output = {
        "version": 1,
        "source": "SEC original 8-K and likely EX-99 earnings/results exhibits (Source D)",
        "documentation": "https://www.sec.gov/Archives/",
        "search_window": "0–120 days after frozen Q4 end; when local Q4 end differs from SEC annual report date, both are searched and the latter is diagnostic only",
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "filing_count": len(filings),
        "filings": filings,
        "metrics": {"sec_http_requests": client.requests, "wire_bytes_received": client.wire_bytes_received,
                    "decoded_bytes_received": client.bytes_received, "cache_hits": client.cache_hits,
                    "retries": client.retries, "rate_limit_requests_per_second": 1 / SEC_MIN_INTERVAL},
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def col(section, name, length, default=None):
    values = section.get(name)
    if not isinstance(values, list):
        return [default] * length
    return values[:length] + [default] * max(0, length - len(values))


def source_map(progress):
    """Current ticker→CIK map from the repo's SEC submissions evidence."""
    mapped = defaultdict(list)
    for key, issuer in progress.get("issuers", {}).items():
        evidence = issuer.get("screeningEvidence") or {}
        cik = str(evidence.get("cik") or issuer.get("cik") or key).zfill(10)
        for ticker in evidence.get("submissionsTickers") or []:
            mapped[str(ticker).upper()].append({
                "cik": cik,
                "name": evidence.get("entityName"),
                "sic": evidence.get("sic"),
                "sicDescription": evidence.get("sicDescription"),
            })
    return mapped


def document_year_rows(doc, ticker, cik, company, sic, sic_description, path):
    annual = doc.get("annual") or {}
    quarterly = doc.get("quarterly") or {}
    qends = quarterly.get("end") if isinstance(quarterly.get("end"), list) else []
    aends = annual.get("end") if isinstance(annual.get("end"), list) else []
    qsrc = col(quarterly, "src", len(qends), "yahoo")
    asrc = col(annual, "src", len(aends), "yahoo")
    qeps = col(quarterly.get("values") or {}, "epsDiluted", len(qends))
    aeps = col(annual.get("values") or {}, "epsDiluted", len(aends))
    qni = col(quarterly.get("values") or {}, "netIncome", len(qends))
    qshares = col(quarterly.get("values") or {}, "dilutedShares", len(qends))
    fy = col(quarterly, "fy", len(qends))
    fq = col(quarterly, "fq", len(qends))
    derived = col(quarterly, "derived", len(qends))

    annual_by_fy = defaultdict(list)
    for index, year in enumerate(col(annual, "fy", len(aends))):
        if asrc[index] == "sec":
            annual_by_fy[year].append({
                "index": index,
                "end": aends[index],
                "epsDiluted": aeps[index],
                "values": {field: values[index] for field, values in (annual.get("values") or {}).items()
                           if isinstance(values, list) and len(values) > index},
            })

    sec_q_by_fy_q = defaultdict(list)
    for index, (year, quarter) in enumerate(zip(fy, fq)):
        if qsrc[index] == "sec" and quarter in (1, 2, 3, 4):
            sec_q_by_fy_q[(year, quarter)].append(index)

    split = doc.get("split") if isinstance(doc.get("split"), dict) else {}
    split_events = split.get("events") if isinstance(split.get("events"), list) else []
    quality = doc.get("quality") if isinstance(doc.get("quality"), dict) else {}
    out = []
    for index, (end, year, quarter, source, eps) in enumerate(zip(qends, fy, fq, qsrc, qeps)):
        if quarter != 4 or source != "sec" or eps is not None:
            continue
        try:
            year_number = int(year)
        except (TypeError, ValueError):
            continue
        if not 2012 <= year_number <= 2025:
            continue
        annual_rows = annual_by_fy.get(year, [])
        annual_row = annual_rows[0] if len(annual_rows) == 1 else None
        q_indices = [
            sec_q_by_fy_q.get((year, number), [None])[0]
            if len(sec_q_by_fy_q.get((year, number), [])) == 1 else None
            for number in (1, 2, 3)
        ]
        q_items = []
        for qindex in q_indices:
            if qindex is None:
                q_items.append(None)
                continue
            q_items.append({
                "end": qends[qindex],
                "epsDiluted": qeps[qindex],
                "netIncome": qni[qindex],
                "dilutedShares": qshares[qindex],
                "shareBasis": financials.share_basis(qshares[qindex], qni[qindex], qeps[qindex]),
            })

        if qni[index] is None:
            reason = "q4_net_income_missing"
            details = ["Q4 SEC row has no netIncome"]
        elif annual_row is None or annual_row["epsDiluted"] is None:
            reason = "annual_diluted_eps_missing"
            details = ["no unique SEC annual row for this FY" if annual_row is None
                       else "SEC annual epsDiluted is null"]
        elif any(item is None or item["epsDiluted"] is None or item["shareBasis"] is None for item in q_items):
            reason = "q1_q3_incomplete"
            details = []
            for number, item in enumerate(q_items, 1):
                if item is None:
                    details.append(f"Q{number} has no unique SEC period")
                elif item["epsDiluted"] is None:
                    details.append(f"Q{number} epsDiluted is null")
                elif item["shareBasis"] is None:
                    details.append(f"Q{number} has no usable diluted share basis")
        else:
            reason, details = "derivation_rejected", []
            annual_values = annual_row["values"]
            if financials.eps_inconsistent(annual_values):
                details.append("annual EPS does not agree with annual net income / diluted shares")
            annual_basis = financials.share_basis(
                annual_values.get("dilutedShares"), annual_values.get("netIncome"), annual_row["epsDiluted"]
            )
            bases = [annual_basis] + [item["shareBasis"] for item in q_items]
            if any(not basis for basis in bases):
                details.append("annual or quarterly diluted share basis unavailable")
            elif max(bases) / min(bases) > 1 + financials.SPLIT_MATCH:
                details.append("annual and Q1–Q3 share bases differ beyond split tolerance")
            else:
                value = financials.q4_eps(annual_row["epsDiluted"], [item["epsDiluted"] for item in q_items], bases)
                income = qni[index]
                if value is None:
                    details.append("annual less Q1–Q3 EPS could not be evaluated")
                elif value and (value > 0) != (income > 0):
                    details.append("candidate EPS sign disagrees with Q4 net income")
                elif abs(value - income / bases[0]) > (
                    financials.EPS_TOLERANCE * abs(value) + financials.Q4_EPS_ROUNDING
                ):
                    details.append("candidate EPS disagrees with Q4 net income / annual share basis")
                else:
                    details.append("existing production rule still leaves this Q4 EPS empty")

        out.append({
            "ticker": ticker,
            "company_name": company,
            "cik": cik,
            "sic": sic,
            "sic_description": sic_description,
            "category": reason,
            "fy": year_number,
            "q4_end": end,
            "filing_period_source": source,
            "current_q4_eps_diluted": None,
            "current_q4_net_income": qni[index],
            "current_annual_eps_diluted": annual_row["epsDiluted"] if annual_row else None,
            "q1_q3": q_items,
            "category_evidence": details,
            "quarterly_derived_flag": derived[index],
            "financials_path": str(path.relative_to(ROOT)),
            "financials_sha256": sha256_file(path),
            "split_adjusted": bool(split.get("adjusted")),
            "split_events": split_events,
            "existing_quality_level": quality.get("level"),
            "existing_quality_issues": quality.get("issues", []),
        })
    return out, annual_by_fy, qends, qeps, qsrc, derived, split_events


def median_sec_revenue(doc):
    annual = doc.get("annual") or {}
    ends = annual.get("end") if isinstance(annual.get("end"), list) else []
    values = annual.get("values") or {}
    revenue = col(values, "revenue", len(ends))
    src = col(annual, "src", len(ends), "yahoo")
    years = col(annual, "fy", len(ends))
    selected = [value for year, source, value in zip(years, src, revenue)
                if source == "sec" and value is not None and 2019 <= int(year) <= 2025]
    return statistics.median(selected) if selected else None


def seed_rank(seed, category, ticker):
    return sha256_bytes(f"{seed}|{category}|{ticker}".encode("utf-8"))


def diversity_select(candidates, count, seed, category):
    ordered = sorted(candidates, key=lambda item: (seed_rank(seed, category, item["ticker"]), item["ticker"]))
    chosen, used_ciks, used_industries = [], set(), set()
    for enforce_diversity in (True, False):
        for candidate in ordered:
            industry = str(candidate.get("sic") or "")[:2]
            if candidate in chosen or candidate["cik"] in used_ciks:
                continue
            if enforce_diversity and industry in used_industries:
                continue
            chosen.append(candidate)
            used_ciks.add(candidate["cik"])
            used_industries.add(industry)
            if len(chosen) == count:
                return chosen
    return chosen


def freeze_sample(seed="20261018", output=None):
    report_dir = ROOT / "docs" / "report" / "sec-q4-eps-poc"
    output = Path(output) if output else report_dir / "sample.json"
    if not output.is_absolute():
        output = ROOT / output
    if output.exists():
        raise FileExistsError(f"refusing to replace frozen sample: {output}")

    snapshot_path = ROOT / "docs/report/fundamentals/us-market-snapshot.json"
    progress_path = ROOT / "docs/report/fundamentals/us-market-progress-summary.json"
    financial_dir = ROOT / "docs/report/financials"
    progress = load_json(progress_path)
    snapshot = load_json(snapshot_path)
    ticker_map = source_map(progress)
    current_symbols = {item["symbol"]: item for item in snapshot.get("eligibleSymbols", [])}

    docs = {}
    missing_periods = []
    all_us_missing = 0
    for path in sorted(financial_dir.glob("*.json")):
        try:
            doc = load_json(path)
        except (OSError, ValueError):
            continue
        if doc.get("market") != "US":
            continue
        quarterly = doc.get("quarterly") or {}
        ends = quarterly.get("end") if isinstance(quarterly.get("end"), list) else []
        fy = col(quarterly, "fy", len(ends))
        fq = col(quarterly, "fq", len(ends))
        eps = col(quarterly.get("values") or {}, "epsDiluted", len(ends))
        for year, quarter, value in zip(fy, fq, eps):
            if quarter == 4 and value is None:
                all_us_missing += 1
        docs[doc.get("symbol", path.stem)] = (path, doc)

    classified_by_category = defaultdict(list)
    source_eligible_rows = 0
    for ticker, (path, doc) in docs.items():
        maps = ticker_map.get(ticker, [])
        if doc.get("market") != "US" or ticker not in current_symbols or len({x["cik"] for x in maps}) != 1:
            continue
        mapping = maps[0]
        company = mapping["name"] or current_symbols[ticker].get("name")
        records, annual_by_fy, qends, qeps, qsrc, derived, split_events = document_year_rows(
            doc, ticker, mapping["cik"], company, mapping["sic"], mapping["sicDescription"], path
        )
        source_eligible_rows += len(records)
        for record in records:
            classified_by_category[record["category"]].append(record)
        # Positive control rows use the local SEC series only, and are selected
        # without looking at any SEC response from this POC.
        q = doc.get("quarterly") or {}
        fy = col(q, "fy", len(qends))
        fq = col(q, "fq", len(qends))
        for index, (end, year, quarter, source, eps) in enumerate(zip(qends, fy, fq, qsrc, qeps)):
            if (quarter != 4 or source != "sec" or eps is None or derived[index] is not None
                    or not (2012 <= int(year) <= 2025)):
                continue
            record = {
                "ticker": ticker,
                "company_name": company,
                "cik": mapping["cik"],
                "sic": mapping["sic"],
                "sic_description": mapping["sicDescription"],
                "fy": int(year),
                "q4_end": end,
                "existing_q4_eps_diluted": eps,
                "existing_q4_source": source,
                "quarterly_derived_flag": derived[index],
                "financials_path": str(path.relative_to(ROOT)),
                "financials_sha256": sha256_file(path),
                "split_adjusted": bool((doc.get("split") or {}).get("adjusted")),
                "split_events": split_events,
                "existing_quality_level": (doc.get("quality") or {}).get("level"),
            }
            classified_by_category["direct_sec_controls"].append(record)

    by_ticker = defaultdict(list)
    for category, records in classified_by_category.items():
        for record in records:
            by_ticker[(category, record["ticker"])].append(record)

    def period_pair(records):
        early = sorted((r for r in records if EARLY[0] <= r["fy"] <= EARLY[1]), key=lambda r: (r["fy"], r["q4_end"]))
        recent = sorted((r for r in records if RECENT[0] <= r["fy"] <= RECENT[1]), key=lambda r: (r["fy"], r["q4_end"]))
        return (early[0], recent[0]) if early and recent else None

    candidates = {category: [] for category in CLASSIFICATIONS}
    for category in CLASSIFICATIONS:
        tickers = sorted({r["ticker"] for r in classified_by_category[category]})
        for ticker in tickers:
            rows = by_ticker[(category, ticker)]
            pair = period_pair(rows)
            path, doc = docs[ticker]
            size = median_sec_revenue(doc)
            ciks = {row["cik"] for row in rows}
            if pair and len(ciks) == 1 and size is not None and size <= SIZE_LIMIT and ticker not in POPULAR_TICKERS:
                candidates[category].append({
                    "ticker": ticker,
                    "cik": rows[0]["cik"],
                    "company_name": rows[0]["company_name"],
                    "sic": rows[0]["sic"],
                    "sic_description": rows[0]["sic_description"],
                    "median_2019_2025_sec_revenue_proxy": size,
                    "periods": list(pair),
                })

    selected = []
    candidate_counts = {}
    for category in CLASSIFICATIONS:
        candidate_counts[category] = {
            "eligible_tickers_with_early_and_recent_periods": len(candidates[category]),
            "eligible_periods": sum(len(item["periods"]) for item in candidates[category]),
        }
        chosen = diversity_select(candidates[category], 2, seed, category)
        candidate_counts[category]["selected_tickers"] = [item["ticker"] for item in chosen]
        for candidate in chosen:
            selected.append({
                **{key: candidate[key] for key in (
                    "ticker", "cik", "company_name", "sic", "sic_description",
                    "median_2019_2025_sec_revenue_proxy",
                )},
                "sample_role": "missing_q4_eps",
                "missing_reason": category,
                "selection_rank_sha256": seed_rank(seed, category, candidate["ticker"]),
                "selection_rule": "seeded SHA-256 rank, then greedily prefer an unused two-digit SIC group",
                "periods": candidate["periods"],
            })

    control_candidates = []
    control_records = classified_by_category["direct_sec_controls"]
    control_by_ticker = defaultdict(list)
    for row in control_records:
        control_by_ticker[row["ticker"]].append(row)
    for ticker, rows in control_by_ticker.items():
        pair = period_pair(rows)
        if not pair:
            continue
        path, doc = docs[ticker]
        size = median_sec_revenue(doc)
        if size is None or size > SIZE_LIMIT or ticker in POPULAR_TICKERS:
            continue
        control_candidates.append({
            "ticker": ticker,
            "cik": rows[0]["cik"],
            "company_name": rows[0]["company_name"],
            "sic": rows[0]["sic"],
            "sic_description": rows[0]["sic_description"],
            "median_2019_2025_sec_revenue_proxy": size,
            "periods": list(pair),
        })
    # Prefer smaller issuers, with the fixed seed as the tie breaker. One issuer
    # per CIK and two-digit SIC diversity are enforced by diversity_select.
    ordered_controls = sorted(
        control_candidates,
        key=lambda item: (item["median_2019_2025_sec_revenue_proxy"],
                          seed_rank(seed, "direct_sec_controls", item["ticker"]), item["ticker"]),
    )
    control_selected, used_ciks, used_sic = [], set(), set()
    for enforce_diversity in (True, False):
        for item in ordered_controls:
            industry = str(item.get("sic") or "")[:2]
            if item["cik"] in used_ciks or (enforce_diversity and industry in used_sic):
                continue
            control_selected.append(item)
            used_ciks.add(item["cik"])
            used_sic.add(industry)
            if len(control_selected) == 2:
                break
        if len(control_selected) == 2:
            break
    candidate_counts["direct_sec_controls"] = {
        "eligible_tickers_with_early_and_recent_periods_and_sec_revenue_le_1bn": len(control_candidates),
        "selected_tickers": [item["ticker"] for item in control_selected],
        "historical_grade_a_ledger_available": False,
    }
    for candidate in control_selected:
        selected.append({
            **{key: candidate[key] for key in (
                "ticker", "cik", "company_name", "sic", "sic_description",
                "median_2019_2025_sec_revenue_proxy",
            )},
            "sample_role": "positive_control_candidate",
            "missing_reason": None,
            "historical_grade_a_status": "not_auditable: acceptance ledger and original classifier absent from main",
            "selection_rule": "direct SEC Q4 epsDiluted exists locally with no per-row derived flag; smallest revenue proxy, then fixed seed, with SIC diversity",
            "periods": candidate["periods"],
        })

    # The categories are required to have two selected tickers; fail closed rather
    # than silently making a post-query substitution.
    gaps = {category: candidate_counts[category]["selected_tickers"] for category in CLASSIFICATIONS
            if len(candidate_counts[category]["selected_tickers"]) != 2}
    if gaps or len(control_selected) != 2:
        raise RuntimeError(f"frozen sample incomplete; no SEC query permitted: {gaps or 'positive control gap'}")

    classification_counts = {category: len(classified_by_category[category]) for category in CLASSIFICATIONS}
    payload = {
        "version": 1,
        "sample_id": "sec-q4-diluted-eps-poc-20261018",
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_commit": "dd7ac76821976ea42f6291bc09eb5e63b28c2ecc",
        "selection_seed": seed,
        "selection_constraints": {
            "current_directory_and_cik": "ticker must exist in main's US eligibleSymbols and map to one SEC CIK in us-market-progress-summary.json",
            "financial_rows": "US financials; SEC-sourced Q4 row; fiscal year 2012–2025; Q4 epsDiluted null for missing cases",
            "annual_revenue_proxy": "median of SEC-sourced annual revenue for FY 2019–2025 <= USD 1B",
            "periods_per_ticker": "earliest fiscal year in 2012–2018 and earliest fiscal year in 2019–2025; no SEC response is used in selection",
            "industry_diversity": "one CIK per category; greedily prefer unused two-digit SIC industry",
            "sorting": "missing-category tickers ranked by SHA-256(seed|category|ticker); controls ranked by revenue proxy, then SHA-256; dates sorted by FY and end date",
            "popular_ticker_exclusions": sorted(POPULAR_TICKERS),
            "positive_control_predicate": "existing local Q4 row has source sec, non-null epsDiluted, null quarterly derived flag; original Grade A acceptance ledger is absent, so the historical Grade A label cannot be asserted",
        },
        "input_audit": {
            "missing_inputs": [
                "docs/report/q4-eps-missing-classification.json",
                "docs/report/q4-eps-acceptance-ledger.json",
                "atlas/scripts/classify_q4_eps_missing.py",
            ],
            "temporary_classifier": "in-memory deterministic classification implemented in this POC from main's financials; no input report regenerated or overwritten",
            "financials_dir": "docs/report/financials/",
            "financials_files": len(docs),
            "us_financials_missing_q4_eps_all_sources_all_years": all_us_missing,
            "sec_sourced_current_directory_missing_q4_eps_periods_fy2012_2025": source_eligible_rows,
            "temporary_classification_counts": classification_counts,
            "candidate_pool": candidate_counts,
            "snapshot_sha256": sha256_file(snapshot_path),
            "progress_summary_sha256": sha256_file(progress_path),
        },
        "sample": selected,
        "freeze_note": "Selection is locked before SEC submissions, data-set ZIPs, filing documents, or earnings releases are queried. Any post-freeze retrieval failure remains E/incomplete and will not trigger replacement.",
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["freeze_sha256"] = sha256_bytes(canonical)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze-sample", help="select and freeze a deterministic 10-ticker sample from local main data")
    freeze.add_argument("--seed", default="20261018")
    freeze.add_argument("--out", default="docs/report/sec-q4-eps-poc/sample.json")
    fetch_docs = sub.add_parser("fetch-docs", help="read the two official SEC data-set documentation pages")
    fetch_docs.add_argument("--out", default="docs/report/sec-q4-eps-poc/official-docs.json")
    filing_map = sub.add_parser("resolve-submissions", help="map frozen periods to SEC 10-K and 8-K submissions")
    filing_map.add_argument("--sample", default="docs/report/sec-q4-eps-poc/sample.json")
    filing_map.add_argument("--out", default="docs/report/sec-q4-eps-poc/filing-map.json")
    source_a = sub.add_parser("source-a", help="download and inspect only FSDS ZIPs for frozen sample 10-K filing dates")
    source_a.add_argument("--filing-map", default="docs/report/sec-q4-eps-poc/filing-map.json")
    source_a.add_argument("--out", default="docs/report/sec-q4-eps-poc/source-a.json")
    source_b = sub.add_parser("source-b", help="download and inspect Notes ZIPs for frozen sample 10-K filing dates")
    source_b.add_argument("--filing-map", default="docs/report/sec-q4-eps-poc/filing-map.json")
    source_b.add_argument("--out", default="docs/report/sec-q4-eps-poc/source-b.json")
    source_c = sub.add_parser("source-c", help="fetch and inspect original SEC 10-K/10-K/A filings for frozen periods")
    source_c.add_argument("--filing-map", default="docs/report/sec-q4-eps-poc/filing-map.json")
    source_c.add_argument("--out", default="docs/report/sec-q4-eps-poc/source-c.json")
    source_d = sub.add_parser("source-d", help="inspect only sample issuers' nearby 8-Ks and earnings/result exhibits")
    source_d.add_argument("--filing-map", default="docs/report/sec-q4-eps-poc/filing-map.json")
    source_d.add_argument("--out", default="docs/report/sec-q4-eps-poc/source-d.json")
    args = parser.parse_args(argv)
    if args.command == "freeze-sample":
        result = freeze_sample(args.seed, args.out)
        print(json.dumps({
            "sample": str(Path(args.out)),
            "freeze_sha256": result["freeze_sha256"],
            "symbols": [f"{row['ticker']}:{row['sample_role']}" for row in result["sample"]],
            "missing_category_counts": result["input_audit"]["temporary_classification_counts"],
            "candidate_pool": result["input_audit"]["candidate_pool"],
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "fetch-docs":
        docs, client = fetch_official_dataset_docs()
        successful = sum(item.get("status") == 200 for item in docs.values())
        status_counts = dict(Counter(str(item.get("status")) for item in docs.values()))
        payload = {"source": "Official SEC data-set documentation", "docs": docs, "metrics": {
            "sec_http_requests": client.requests,
            "bytes_received": client.bytes_received,
            "wire_bytes_received": client.wire_bytes_received,
            "cache_hits": client.cache_hits,
            "retries": client.retries,
            "min_interval_seconds": SEC_MIN_INTERVAL,
            "rate_limit_requests_per_second": 1 / SEC_MIN_INTERVAL,
        }, "end_to_end_metrics": {
            "actual_http_requests": client.requests,
            "successful_http_responses": successful,
            "http_status_counts": status_counts,
            "wire_bytes_received": client.wire_bytes_received,
            "decoded_bytes_received": client.bytes_received,
            "retries": client.retries,
            "rate_limit_requests_per_second": 1 / SEC_MIN_INTERVAL,
        }}
        output_path = Path(args.out)
        if not output_path.is_absolute():
            output_path = ROOT / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output_path), "status_counts": status_counts,
                          "metrics": payload["metrics"]}, ensure_ascii=False, indent=2))
        return 0 if successful == len(docs) else 1
    if args.command == "resolve-submissions":
        result = resolve_filing_map(args.sample, args.out)
        compact = []
        for issuer in result["issuers"]:
            compact.extend({
                "ticker": period["ticker"], "fy": period["fy"], "end": period["q4_end"],
                "match": period["match_status"],
                "10k": [(item["form"], item["filing_date"], item["accession"])
                        for item in period["target_10k_matches"]],
                "8k_candidates": len(period["eight_k_candidates_within_60d_of_period_end"]),
            } for period in issuer.get("periods", []))
        print(json.dumps({"periods": compact, "metrics": result["metrics"]}, ensure_ascii=False, indent=2))
        return 0 if all(item["http_status"] == 200 for item in result["issuers"]) else 1
    if args.command == "source-a":
        result = run_source_a(args.filing_map, args.out)
        compact = []
        for item in result["accessions"].values():
            compact.append({
                "ticker": item["metadata"]["ticker"], "fy": item["metadata"]["fy"],
                "form": item["metadata"]["form"], "filed": item["metadata"]["filing_date"],
                "accession": item["accession"], "zip": filing_quarter(item["metadata"]["filing_date"]),
                "dataset_status": item["dataset_status"], "sub_matches": item.get("accession_matches_metadata"),
                "candidate_count": len(item.get("eps_candidates", [])),
                "candidates": [{key: fact.get(key) for key in (
                    "tag", "tag_label", "tag_documentation", "date", "quarters", "unit", "value", "segments", "coreg"
                )} for fact in item.get("eps_candidates", [])],
            })
        print(json.dumps({"zip_statuses": {quarter: {
            "http_status": item["http_status"], "download_bytes": item["download_bytes"],
            "parse_status": item["parse_status"], "candidate_accessions": len(item["target_accessions"]),
        } for quarter, item in result["zips"].items()}, "accessions": compact,
            "metrics": result["metrics"]}, ensure_ascii=False, indent=2))
        return 0 if all(item["http_status"] == 200 for item in result["zips"].values()) else 1
    if args.command == "source-b":
        result = run_source_b(args.filing_map, args.out)
        compact = []
        for item in result["accessions"].values():
            compact.append({
                "ticker": item["metadata"]["ticker"], "fy": item["metadata"]["fy"],
                "form": item["metadata"]["form"], "filed": item["metadata"]["filing_date"],
                "accession": item["accession"], "zip": filing_quarter(item["metadata"]["filing_date"]),
                "dataset_status": item["dataset_status"], "sub_matches": item.get("accession_matches_metadata"),
                "candidate_numeric_facts": len(item.get("eps_candidates", [])),
                "note_text_candidates": len(item.get("note_text_candidates", [])),
            })
        print(json.dumps({"zip_statuses": {quarter: {
            "http_status": item["http_status"], "download_bytes": item["download_bytes"],
            "parse_status": item["parse_status"], "candidate_accessions": len(item["target_accessions"]),
        } for quarter, item in result["zips"].items()}, "accessions": compact,
            "metrics": result["metrics"]}, ensure_ascii=False, indent=2))
        return 0 if all(item["http_status"] == 200 for item in result["zips"].values()) else 1
    if args.command == "source-c":
        result = run_source_c(args.filing_map, args.out)
        compact = [{
            "ticker": item["metadata"]["ticker"], "fy": item["metadata"]["fy"],
            "form": item["metadata"]["form"], "filed": item["metadata"]["filing_date"],
            "accession": accession, "http_status": item["http_status"],
            "parse_status": item["parse_status"], "eps_facts": len(item.get("eps_facts", [])),
            "eps_tables": len(item.get("eps_tables", [])),
        } for accession, item in result["accessions"].items()]
        print(json.dumps({"accessions": compact, "metrics": result["metrics"]}, ensure_ascii=False, indent=2))
        return 0 if all(item["http_status"] == 200 and item["parse_status"] == "parsed"
                        for item in result["accessions"].values()) else 1
    if args.command == "source-d":
        result = run_source_d(args.filing_map, args.out)
        compact = [{
            "accession": accession,
            "ticker": ",".join(item["metadata"]["tickers"]),
            "filing_date": item["metadata"]["filing_date"],
            "index_http_status": item["index_http_status"],
            "status": item["status"], "document_count": len(item.get("documents", [])),
            "eps_fact_count": sum(len(doc.get("eps_facts", [])) for doc in item.get("documents", [])),
        } for accession, item in result["filings"].items()]
        print(json.dumps({"filing_count": result["filing_count"], "filings": compact,
                          "metrics": result["metrics"]}, ensure_ascii=False, indent=2))
        accepted_statuses = {"searched", "searched_with_unsupported_attachments"}
        return 0 if all(item["index_http_status"] == 200 and item["status"] in accepted_statuses
                        and all(doc.get("http_status") == 200 for doc in item.get("documents", []))
                        for item in result["filings"].values()) else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
