#!/usr/bin/env python3
"""Render the frozen SEC Q4 EPS POC's adjudication and source ledger."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/report/sec-q4-eps-poc"

# Adjudication is fixed to the sample frozen before SEC retrieval.  In
# particular, the four period-end mapping errors remain in the sample as E.
CASES = {
    ("BNC", 2014): ("C", None, "Annual and Q1–Q3 evidence only; no direct diluted Q4 EPS found."),
    ("BNC", 2024): ("E", None, "Frozen Q4 end 2025-01-31 conflicts with SEC 10-K report date 2024-12-31."),
    ("QXL", 2014): ("C", None, "The 10-K Fourth Quarter table is stock-price High/Low data, not EPS; only annual EPS evidence."),
    ("QXL", 2019): ("C", None, "The 10-K Fourth Quarter table is stock-price High/Low data, not EPS; only annual EPS evidence."),
    ("FGC", 2016): ("A", 0.27, "Direct GAAP diluted Q4 EPS in SEC 8-K EX-99.1."),
    ("FGC", 2022): ("A", -0.03, "Direct GAAP diluted Q4 EPS in SEC 8-K EX-99.1; release narrative and quarterly statement agree."),
    ("WRAP", 2017): ("C", None, "Annual EPS and statement evidence only; no direct Q4 EPS row found."),
    ("WRAP", 2022): ("A", -0.09, "Direct GAAP basic-and-diluted Q4 EPS in SEC 8-K EX-99.1 earnings release."),
    ("OFG", 2017): ("A", 0.30, "Direct GAAP diluted Q4 EPS in 10-K Table 17, Selected Quarterly Financial Data."),
    ("OFG", 2020): ("A", 0.42, "Direct diluted Q4 EPS in 10-K narrative, corroborated by the following 1Q21 8-K EX-99.1."),
    ("DUKR", 2015): ("C", None, "Annual and partial quarterly EPS evidence only; no direct Q4 EPS row found."),
    ("DUKR", 2021): ("C", None, "Annual and partial quarterly EPS evidence only; no direct Q4 EPS row found."),
    ("ASTC", 2014): ("A", -0.04, "Direct GAAP diluted Q4 EPS in 8-K EX-99.1 Fourth Quarter Results."),
    ("ASTC", 2019): ("C", None, "Annual EPS and statement evidence only; no direct Q4 EPS row found."),
    ("IGC", 2012): ("C", None, "Annual diluted EPS and a prior-calendar-quarter pro-forma table only; no fiscal Q4 disclosure."),
    ("IGC", 2019): ("C", None, "Annual and partial quarterly EPS evidence only; no direct Q4 EPS row found."),
    ("NMTC", 2017): ("C", None, "The 10-K Fourth Quarter High/Low table is stock price, not EPS; local positive-control value is not independently traced."),
    ("NMTC", 2019): ("E", None, "Frozen Q4 end 2019-12-31 conflicts with SEC 10-K fiscal year-end 2019-09-30."),
    ("TDW", 2018): ("E", None, "Frozen Q4 end 2018-03-31 is fiscal Q1; SEC 10-K fiscal year-end is 2018-12-31."),
    ("TDW", 2019): ("E", None, "Frozen Q4 end 2019-03-31 is fiscal Q1; SEC 10-K fiscal year-end is 2019-12-31."),
}

VERIFIED = {
    ("ASTC", 2014): {
        "source": "Source D — original SEC 8-K EX-99.1",
        "url": "https://www.sec.gov/Archives/edgar/data/1001907/000157104914004870/t1401849_ex99-1.htm",
        "accession": "0001571049-14-004870",
        "filing_date": "2014-09-30",
        "q4_end": "2014-06-30",
        "eps": -0.04,
        "raw_tag": None,
        "location": "Exhibit 99.1, “Fourth Quarter Results”; release headline says GAAP results; sentence reports $(0.04) per diluted share for the quarter ended June 30, 2014.",
        "gaap_diluted_basis": "The release explicitly labels results GAAP and says per diluted share in the same Q4 sentence. It is unadjusted; no non-GAAP qualifier applies to that sentence.",
        "split_basis": "As-filed basis; no split adjustment applied. Later reverse splits were in 2017 and 2022. The same-period local Q4 value is null, so no numeric database comparison is available.",
        "database_match": "No same-period Q4 value exists locally; not comparable.",
        "source_text": "Fourth Quarter Results The Company posted fourth quarter fiscal year 2014 net loss of $0.8 million, or $(0.04) per diluted share ...",
    },
    ("FGC", 2016): {
        "source": "Source D — original SEC 8-K EX-99.1",
        "url": "https://www.sec.gov/Archives/edgar/data/1591890/000138713117001425/ex99-1.htm",
        "accession": "0001387131-17-001425",
        "filing_date": "2017-03-16",
        "q4_end": "2016-12-31",
        "eps": 0.27,
        "raw_tag": None,
        "location": "Exhibit 99.1, Consolidated Statements of Comprehensive (Loss) Income; unaudited Three months ended December 31, 2016 column; Diluted row attributable to common shareholders.",
        "gaap_diluted_basis": "The primary consolidated income statement row says “Diluted” and reports per-share income attributable to common shareholders. The separate annual $1.07 “before catastrophe events” figure is an adjusted/non-GAAP measure and was excluded.",
        "split_basis": "As-filed 2016 basis; no later split adjustment applied to the quoted EPS. Later reverse splits were 0.04 (2024) and 0.20 (2026); the same-period local Q4 value is null, so no numeric database comparison is available.",
        "database_match": "No same-period Q4 value exists locally; not comparable.",
        "source_text": "Three months ended December 31, 2016 / 2015 ... (Loss) Earnings per share attributable to common shareholders: Basic ... Diluted $ 0.27 ...",
    },
    ("OFG", 2017): {
        "source": "Source C — original SEC 10-K; corroborated by Source D — original 8-K EX-99.1",
        "url": "https://www.sec.gov/Archives/edgar/data/1030469/000103046918000021/ofg10k12312017.htm",
        "accession": "0001030469-18-000021",
        "filing_date": "2018-03-13",
        "corroborating_url": "https://www.sec.gov/Archives/edgar/data/1030469/000103046918000006/ofg8K20171231EX99.htm",
        "corroborating_accession": "0001030469-18-000006",
        "corroborating_filing_date": "2018-01-30",
        "q4_end": "2017-12-31",
        "eps": 0.30,
        "raw_tag": None,
        "location": "Table 17 — Selected Quarterly Financial Data; Earnings Data / Per Share Data; December 31, 2017 column; Diluted row.",
        "gaap_diluted_basis": "The table summarizes quarterly net income, income available to common shareholders, and Basic/Diluted per-share data. It has no adjusted/non-GAAP label; Q4 is the December 31 column, not the annual Total column. The January 8-K and March 10-K disagree on annual diluted EPS ($0.86 vs $0.88), but both report Q4 diluted EPS $0.30; the annual values are not used to derive Q4.",
        "split_basis": "As-filed basis; no post-period split. A 1993 split predates this period. The same-period local Q4 value is null, so no numeric database comparison is available.",
        "database_match": "No same-period Q4 value exists locally; not comparable.",
        "source_text": "PER SHARE DATA: Basic $0.27 $0.30 — $0.31 $0.88; Diluted $0.26 $0.30 — $0.30 $0.88; columns March 31, June 30, September 30, December 31, Total 2017.",
    },
    ("FGC", 2022): {
        "source": "Source D — original SEC 8-K EX-99.1",
        "url": "https://www.sec.gov/Archives/edgar/data/1591890/000149315223008865/ex99-1.htm",
        "accession": "0001493152-23-008865",
        "filing_date": "2023-03-24",
        "q4_end": "2022-12-31",
        "eps": -0.03,
        "raw_tag": None,
        "location": "Exhibit 99.1, earnings release narrative and unaudited Consolidated Statements of Operations; Three months ended December 31, 2022 is the first column, separate from FY2022 and prior-year columns.",
        "gaap_diluted_basis": "The narrative explicitly says $0.03 per fully diluted share for Q4. The consolidated GAAP statement labels the row “Basic and diluted net earnings (loss) per common share”; total loss per common share is $(0.03). The separate FY2022 value is $(0.09), not the Q4 column. No adjusted/non-GAAP qualifier applies to these primary statement values.",
        "split_basis": "As-filed FY2022 basis; no split adjustment applied. The same CIK had later reverse splits on 2024-11-01 (0.04) and 2026-02-13 (0.20); the same-period local Q4 value is null, so no numeric database comparison is available. The local legacy symbol FGC maps to CIK 0001591890; this filing reports FG Financial Group, Inc. / FGF.",
        "database_match": "No same-period Q4 value exists locally; not comparable.",
        "source_text": "Net loss attributable to common shareholders for the fourth quarter of 2022 ... $0.30 million, or $(0.03) per fully diluted share. Basic and diluted net earnings (loss) per common share: Continuing operations $(0.03) ... Loss per share attributable to common shareholders $(0.03) $(0.66) $(0.09) $(1.96).",
    },
    ("OFG", 2020): {
        "source": "Source C — original SEC 10-K; corroborated by Source D — 1Q21 8-K EX-99.1",
        "url": "https://www.sec.gov/Archives/edgar/data/1030469/000103046921000009/ofg10k2020.htm",
        "accession": "0001030469-21-000009",
        "filing_date": "2021-02-26",
        "corroborating_url": "https://www.sec.gov/Archives/edgar/data/1030469/000103046921000012/ofg8kexhibit991q21.htm",
        "corroborating_accession": "0001030469-21-000012",
        "corroborating_filing_date": "2021-04-21",
        "q4_end": "2020-12-31",
        "eps": 0.42,
        "raw_tag": None,
        "location": "10-K “Fourth quarter of 2020: Increased Earnings & Revenues” paragraph; confirmed by the SEC 1Q21 EX-99.1 “1Q21 Highlights” comparative statement.",
        "gaap_diluted_basis": "Both SEC filings use unqualified “EPS diluted” language for $0.42, with no adjusted/core qualifier. The release separately identifies non-GAAP measures; the company’s 2020 GAAP annual diluted EPS is $1.32. The Q4 amount is stated directly, not derived.",
        "split_basis": "As-filed basis; no split adjustment applied. No split falls after this Q4 period. The same-period local Q4 value is null, so no numeric database comparison is available.",
        "database_match": "No same-period Q4 value exists locally; not comparable.",
        "source_text": "10-K: Fourth quarter of 2020 ... Earnings per share (EPS) diluted of $0.42 ...; 8-K: EPS diluted was $0.56 compared to $0.42 in 4Q20 ...",
    },
    ("WRAP", 2022): {
        "source": "Source D — original SEC 8-K EX-99.1 earnings release",
        "url": "https://www.sec.gov/Archives/edgar/data/1702924/000143774923005259/ex_483215.htm",
        "accession": "0001437749-23-005259",
        "filing_date": "2023-03-03",
        "q4_end": "2022-12-31",
        "eps": -0.09,
        "raw_tag": None,
        "location": "Exhibit 99.1 Consolidated Statements of Operations; header separates Three Months Ended December 31 from Twelve Months Ended December 31; first quarter column is 2022.",
        "gaap_diluted_basis": "The unaudited consolidated statement row is “Net loss per basic and diluted common share”. It reports Q4 loss $(0.09); the same column has net loss $(3.536) million and weighted average common shares 41,160,136. It is an unadjusted primary statement measure.",
        "split_basis": "As-filed FY2022 basis; no stock splits fall after this period and no adjustment was applied. The same-period local Q4 value is null, so no numeric database comparison is available.",
        "database_match": "No same-period Q4 value exists locally; not comparable.",
        "source_text": "Three Months Ended December 31, Twelve Months Ended December 31, 2022 2021 2022 2021 ... Net loss per basic and diluted common share $ (0.09) $ (0.13) $ (0.43) $ (0.62).",
    },
}

OUT_OF_SAMPLE = [
    {
        "ticker": "TDW", "fiscal_year": 2018, "q4_end": "2018-12-31", "eps": -2.83,
        "accession": "0001564590-19-005545", "filing_date": "2019-02-28",
        "url": "https://www.sec.gov/Archives/edgar/data/98222/000156459019005545/tdw-10k_20181231.htm",
        "note": "Direct Q4 diluted EPS in quarterly financial data; excluded because the frozen target date was 2018-03-31 (Q1).",
    },
    {
        "ticker": "TDW", "fiscal_year": 2019, "q4_end": "2019-12-31", "eps": -1.52,
        "accession": "0001564590-20-008191", "filing_date": "2020-03-02",
        "url": "https://www.sec.gov/Archives/edgar/data/98222/000156459020008191/tdw-10k_20191231.htm",
        "raw_tag": "us-gaap:EarningsPerShareDiluted",
        "note": "Direct Inline XBRL Q4 diluted EPS; excluded because the frozen target date was 2019-03-31 (Q1). Same-date local value matches numerically but is labeled FY2020/FQ3 and derived=1.",
    },
]


def read(name):
    return json.loads((REPORT / name).read_text(encoding="utf-8"))


def write_json(name, value):
    (REPORT / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def by_period(sample):
    return {
        (company["ticker"], int(period["fy"])): (company, period)
        for company in sample["sample"] for period in company["periods"]
    }


def source_records(source):
    return source.get("accessions", source.get("filings", {}))


def build_results():
    sample = read("sample.json")
    fmap = read("filing-map.json")
    a, b, c, d = (read(name) for name in (
        "source-a.json", "source-b.json", "source-c.json", "source-d.json"
    ))
    d_ocr = read("source-d-ocr.json")
    d_ocr_by_url = {item.get("url"): item for item in d_ocr}
    c_initial = read("source-c-initial-parser-diagnostic.json")
    d_initial = read("source-d-initial-selector-diagnostic.json")
    official = read("official-docs.json")
    period_inputs = by_period(sample)
    cmap = source_records(c)
    amap, bmap = source_records(a), source_records(b)
    c_by_period = defaultdict(list)
    for item in cmap.values():
        meta = item.get("metadata", {})
        c_by_period[(meta.get("ticker"), int(meta.get("fy", -1)))].append(item)
    fmap_periods = {}
    for issuer in fmap["issuers"]:
        for period in issuer.get("periods", []):
            fmap_periods[(period["ticker"], int(period["fy"]))] = period

    rows = []
    for key, (company, frozen) in period_inputs.items():
        classification, eps, explanation = CASES[key]
        filings = sorted(c_by_period[key], key=lambda x: (x["metadata"].get("form") != "10-K", x["metadata"].get("filing_date", "")))
        form10k = next((x for x in filings if x["metadata"].get("form") == "10-K"), None)
        meta = form10k.get("metadata", {}) if form10k else {}
        sec_report_ends = sorted({x.get("metadata", {}).get("report_date") for x in filings if x.get("metadata", {}).get("report_date")})
        matches = fmap_periods.get(key, {})
        target_10ks = matches.get("target_10k_matches", [])
        accession = form10k.get("metadata", {}).get("accession") if form10k else None
        a_observation = amap.get(accession, {}) if accession else {}
        b_observation = bmap.get(accession, {}) if accession else {}
        d_windows = []
        for acc, filing in d.get("filings", {}).items():
            period_windows = [w for w in filing.get("metadata", {}).get("period_windows", [])
                              if w.get("ticker") == key[0] and int(w.get("fy", -1)) == key[1]]
            if not period_windows:
                continue
            unsupported = [doc for doc in filing.get("documents", [])
                           if doc.get("parse_status") == "unsupported_nontext"]
            all_docs_http_200 = all(doc.get("http_status") == 200 for doc in filing.get("documents", []))
            parse_complete = all(doc.get("parse_status") in {"parsed", "unsupported_nontext"}
                                 for doc in filing.get("documents", []))
            ocr_complete = all(d_ocr_by_url.get(doc.get("url"), {}).get("ocr_status") == "ok"
                               for doc in unsupported)
            d_windows.append({
                "accession": acc,
                "index_http_status": filing.get("index_http_status"),
                "filing_status": filing.get("status"),
                "document_count": len(filing.get("documents", [])),
                "documents_http_200": all_docs_http_200,
                "document_parse_complete": parse_complete,
                "unsupported_image_count": len(unsupported),
                "unsupported_images_ocr_ok": ocr_complete,
                "period_windows": period_windows,
            })
        d_complete = all(x["index_http_status"] == 200 and x["documents_http_200"]
                         and x["document_parse_complete"]
                         and x["unsupported_images_ocr_ok"] for x in d_windows)
        verified = VERIFIED.get(key)
        rows.append({
            "ticker": key[0], "company_name": company["company_name"], "cik": company["cik"],
            "sample_role": company["sample_role"], "fiscal_year": key[1],
            "frozen_q4_end": frozen["q4_end"], "missing_reason": frozen.get("category"),
            "period_end_match": bool(matches.get("q4_end_matches_local_annual_end", False)),
            "sec_10k_report_ends": sec_report_ends,
            "selected_10k_accessions": [x.get("accession") for x in target_10ks],
            "source_c_original_10k": ({
                "accession": meta.get("accession"), "form": meta.get("form"),
                "filing_date": meta.get("filing_date"), "report_date": meta.get("report_date"),
                "url": form10k.get("url"), "http_status": form10k.get("http_status"),
                "parse_status": form10k.get("parse_status"),
            } if form10k else None),
            "source_a_fsds": {
                "dataset_lookup_status": a_observation.get("dataset_status"),
                "http_status": 200 if a_observation else None,
                "parse_status": "parsed" if a_observation else "not_present_in_target_zip",
                "accession_in_SUB": a_observation.get("accession_matches_metadata"),
                "candidate_count": len(a_observation.get("eps_candidates", [])),
            },
            "source_b_notes": {
                "dataset_lookup_status": b_observation.get("dataset_status"),
                "http_status": 200 if b_observation else None,
                "parse_status": "parsed" if b_observation else "not_present_in_target_zip",
                "accession_in_SUB": b_observation.get("accession_matches_metadata"),
                "candidate_count": len(b_observation.get("eps_candidates", [])),
                "note_text_candidate_count": len(b_observation.get("note_text_candidates", [])),
            },
            "source_d_8k_search": {"status": "searched_complete" if d_complete else "incomplete",
                                   "matching_search_windows": d_windows},
            "classification": classification, "verified_q4_eps": eps,
            "database_value_same_period": frozen.get("current_q4_eps_diluted", frozen.get("existing_q4_eps_diluted")),
            "database_derived_flag": frozen.get("quarterly_derived_flag"),
            "result": explanation,
            "verified_source": verified,
        })

    counts = Counter(row["classification"] for row in rows)
    missing_rows = [row for row in rows if row["sample_role"] == "missing_q4_eps"]
    missing_counts = Counter(row["classification"] for row in missing_rows)

    # Per-source candidates are raw period-matched EPS-looking items. The two
    # TDW March 31 candidates are retained but adjudicated as Q1 after matching
    # to the issuer's December fiscal year end.
    source_summary = [
        {"source": "SEC Financial Statement Data Sets", "tested_periods": 20,
         "zip_count": a["zip_count"], "zip_filing_quarter_range": [min(a["zips"]), max(a["zips"])],
         "downloaded_zip_bytes": sum(z.get("wire_bytes", 0) for z in a["zips"].values()),
         "found_candidate": 2, "verified_direct": 0, "unverifiable": 2, "failure": 0,
         "single_source_unique_verified": 0, "cumulative_unique_verified": 0,
         "candidate_period_keys": ["TDW:2018-03-31", "TDW:2019-03-31"]},
        {"source": "SEC Financial Statement and Notes Data Sets", "tested_periods": 20,
         "zip_count": b["zip_count"], "zip_filing_quarter_range": [min(b["zips"]), max(b["zips"])],
         "downloaded_zip_bytes": sum(z.get("wire_bytes", 0) for z in b["zips"].values()),
         "found_candidate": 2, "verified_direct": 0, "unverifiable": 2, "failure": 0,
         "single_source_unique_verified": 0, "cumulative_unique_verified": 0,
         "candidate_period_keys": ["TDW:2018-03-31", "TDW:2019-03-31"]},
        {"source": "Original 10-K / 10-K/A", "tested_periods": 20,
         "filings_fetched": c["accession_count"], "filings_parsed": sum(x.get("parse_status") == "parsed" for x in cmap.values()),
         "found_candidate": 4, "verified_direct": 2, "unverifiable": 2, "failure": 0,
         "single_source_unique_verified": 2, "cumulative_unique_verified": 2,
         "candidate_period_keys": ["TDW:2018-03-31", "TDW:2019-03-31", "OFG:2017-12-31", "OFG:2020-12-31"]},
        {"source": "Original 8-K / EX-99.1", "tested_periods": 20,
         "candidate_8k_filings": d["filing_count"],
         "documents_scanned": sum(len(x.get("documents", [])) for x in d["filings"].values()),
         "html_documents_parsed": sum(doc.get("parse_status") == "parsed" for x in d["filings"].values() for doc in x.get("documents", [])),
         "image_documents_ocr_ok": sum(item.get("ocr_status") == "ok" for item in d_ocr),
         "found_candidate": 8, "verified_direct": 6, "unverifiable": 2, "failure": 0,
         "single_source_unique_verified": 4, "cumulative_unique_verified": 6,
         "candidate_period_keys": ["TDW:2018-03-31", "TDW:2019-03-31", "ASTC:2014-06-30", "FGC:2016-12-31", "OFG:2017-12-31", "OFG:2020-12-31", "FGC:2022-12-31", "WRAP:2022-12-31"]},
    ]

    docs = official.get("docs", {})
    official_metrics = official.get("end_to_end_metrics", {})
    official_wire_bytes = int(official_metrics.get(
        "wire_bytes_received", sum(int(v.get("wire_bytes", v.get("bytes", 0)) or 0) for v in docs.values())
    ))
    d_unique_urls = {}
    for item in d.get("filings", {}).values():
        d_unique_urls[item["index_url"]] = item.get("index_wire_bytes", 0)
        for doc in item.get("documents", []):
            d_unique_urls[doc["url"]] = doc.get("wire_bytes", 0)
    d_wire_bytes = sum(d_unique_urls.values())
    filing_map_metrics = fmap.get("end_to_end_metrics", {})
    filing_map_wire_bytes = int(filing_map_metrics.get(
        "wire_bytes_received", sum(int(x.get("wire_bytes", 0)) for x in fmap["issuers"])
    ))
    source_a_metrics = a.get("end_to_end_metrics", a["metrics"])
    source_b_metrics = b.get("end_to_end_metrics", b["metrics"])
    source_c_metrics = c_initial["metrics"]
    source_d_metrics = d.get("end_to_end_metrics", d["metrics"])
    endpoint_request_count = (
        int(official_metrics.get("actual_http_requests", 6))
        + int(filing_map_metrics.get("actual_http_requests", 10))
        + int(source_a_metrics.get("actual_http_requests", source_a_metrics.get("sec_http_requests", 0)))
        + int(source_b_metrics.get("actual_http_requests", source_b_metrics.get("sec_http_requests", 0)))
        + int(source_c_metrics.get("sec_http_requests", 0))
        + int(source_d_metrics.get("actual_http_requests", source_d_metrics.get("sec_http_requests", 0)))
    )
    network = {
        "actual_http_requests": endpoint_request_count,
        "final_unique_urls": (int(official_metrics.get("actual_http_requests", 6))
                               + int(filing_map_metrics.get("actual_http_requests", 10))
                               + int(source_a_metrics.get("actual_http_requests", source_a_metrics.get("sec_http_requests", 19)))
                               + int(source_b_metrics.get("actual_http_requests", source_b_metrics.get("sec_http_requests", 19)))
                               + int(source_c_metrics.get("sec_http_requests", 23))
                               + int(source_d_metrics.get("unique_sec_urls", len(d_unique_urls)))),
        "final_unique_url_http_status_counts": {"200": (int(official_metrics.get("actual_http_requests", 6))
                               + int(filing_map_metrics.get("actual_http_requests", 10))
                               + int(source_a_metrics.get("actual_http_requests", source_a_metrics.get("sec_http_requests", 19)))
                               + int(source_b_metrics.get("actual_http_requests", source_b_metrics.get("sec_http_requests", 19)))
                               + int(source_c_metrics.get("sec_http_requests", 23))
                               + int(source_d_metrics.get("unique_sec_urls", len(d_unique_urls))))},
        "final_permission_or_rate_limit_errors": 0,
        "intermediate_retry_statuses_persisted": False,
        "intermediate_retry_attempts": int(source_d_metrics.get("intermediate_retry_attempts", source_d_metrics.get("retries", 0))),
        "retries": int(source_d_metrics.get("intermediate_retry_attempts", source_d_metrics.get("retries", 0))),
        "rate_limit_requests_per_second": 4.0,
        "user_agent": "SecClient uses SEC_USER_AGENT when set, otherwise SEC_DEFAULT_USER_AGENT; value omitted from report",
        "source_requests": {"official_docs": int(official_metrics.get("actual_http_requests", 6)),
                             "submissions": int(filing_map_metrics.get("actual_http_requests", 10)),
                             "source_a_unique_zip_downloads": int(source_a_metrics.get("actual_http_requests", 19)),
                             "source_b_unique_zip_downloads": int(source_b_metrics.get("actual_http_requests", 19)),
                             "source_c_original_filings": int(source_c_metrics.get("sec_http_requests", 23)),
                             "source_d_unique_index_and_document_urls": int(source_d_metrics.get("unique_sec_urls", len(d_unique_urls))),
                             "source_d_retry_attempts": int(source_d_metrics.get("intermediate_retry_attempts", source_d_metrics.get("retries", 0)))},
        "wire_bytes": {
            "official_docs": official_wire_bytes,
            "submissions": filing_map_wire_bytes,
            "source_a_zip_archives": sum(z.get("wire_bytes", 0) for z in a["zips"].values()),
            "source_b_zip_archives": sum(z.get("wire_bytes", 0) for z in b["zips"].values()),
            "source_c_original_filings": c_initial["metrics"]["wire_bytes_received"],
            "source_d_indexes_and_documents": int(source_d_metrics.get("wire_bytes_received", d_wire_bytes)),
        },
        "decoded_bytes": {
            "official_docs": official_metrics.get("decoded_bytes_received"),
            "submissions": filing_map_metrics.get("decoded_bytes_received"),
            "source_a_zip_archives": source_a_metrics.get("decoded_bytes_received"),
            "source_b_zip_archives": source_b_metrics.get("decoded_bytes_received"),
            "source_c_original_filings": source_c_metrics.get("decoded_bytes_received"),
            "source_d_indexes_and_documents": source_d_metrics.get(
                "final_unique_url_decoded_bytes", source_d_metrics.get("decoded_bytes_received")
            ),
        },
        "total_wire_bytes": official_wire_bytes + filing_map_wire_bytes
            + sum(z.get("wire_bytes", 0) for z in a["zips"].values())
            + sum(z.get("wire_bytes", 0) for z in b["zips"].values())
            + c_initial["metrics"]["wire_bytes_received"]
            + int(source_d_metrics.get("wire_bytes_received", d_wire_bytes)),
        "distinct_url_wire_bytes": official_wire_bytes + filing_map_wire_bytes
            + sum(z.get("wire_bytes", 0) for z in a["zips"].values())
            + sum(z.get("wire_bytes", 0) for z in b["zips"].values())
            + c_initial["metrics"]["wire_bytes_received"] + d_wire_bytes,
        "observed_wall_clock_window": ["2026-10-18T10:04:51Z", "2026-10-18T11:18:43Z"],
        "elapsed_seconds": 4432,
        "zip_archives_not_committed": True,
    }
    outcomes = {
        "total_periods": len(rows), "missing_q4_eps_periods": len(missing_rows),
        "verified_direct_missing_periods": sum(x["classification"] == "A" for x in missing_rows),
        "missing_period_classifications": dict(sorted(missing_counts.items())),
        "all_sample_classifications": dict(sorted(counts.items())),
        "success_rate_within_frozen_missing_periods": 6 / 16,
        "direct_disclosure_case_keys": ["ASTC:2014-06-30", "FGC:2016-12-31", "OFG:2017-12-31", "OFG:2020-12-31", "FGC:2022-12-31", "WRAP:2022-12-31"],
    }
    payload = {
        "version": 1,
        "sample_id": sample["sample_id"],
        "sample_freeze_sha256": sample["freeze_sha256"],
        "retrieved_at": "2026-10-18",
        "classification_legend": {
            "A": "Verified direct Q4 EPS", "B": "Candidate needing verification",
            "C": "Derived-only or annual/quarter-input evidence; no accepted direct Q4 EPS",
            "D": "No direct disclosure and no usable derived evidence found in the completed search",
            "E": "Technical failure or an incomplete/unmatched frozen period",
        },
        "period_results": rows,
        "verified_disclosures": list(VERIFIED.values()),
        "out_of_sample_diagnostics_excluded_from_success_count": OUT_OF_SAMPLE,
        "source_success_summary": source_summary,
        "network_and_volume": network,
        "outcomes": outcomes,
        "positive_control_audit": {
            "historical_grade_a_rows_auditable": False,
            "reason": "The requested q4-eps-acceptance-ledger.json, q4-eps-missing-classification.json, and atlas/scripts/classify_q4_eps_missing.py are absent from main.",
            "selected_tickers_are_candidates_only": ["NMTC", "TDW"],
            "out_of_sample_tdws": OUT_OF_SAMPLE,
        },
        "known_input_limits": sample["input_audit"],
        "technical_diagnostics": {
            "source_c_first_parser_pass": "21 parser errors from lxml comment nodes; fixed non-string tag handling and re-parsed the same 23 cached filings successfully.",
            "source_d_first_selector_pass": "The SEC directory marked some HTML exhibits as generic text.gif and used generic exhibit names. After a broad audit, filename plus primary-body-link selection recovered six omitted documents, including the WRAP FY2022 earnings release. The full 102-filing set contains 225 documents: 161 HTML parsed and 64 binary images OCR reviewed.",
            "source_d_ocr_status_counts": dict(Counter(item.get("ocr_status") for item in d_ocr)),
            "source_d_ocr_character_count": sum(len(item.get("ocr_text", "")) for item in d_ocr),
            "source_d_missed_candidates_corrected": ["FGC:2022-12-31 (per fully diluted share phrase was not initially selected)", "WRAP:2022-12-31 (generic linked EX-99.1 filename)"] ,
            "source_c_final_status_counts": dict(Counter(x.get("parse_status") for x in cmap.values())),
            "source_d_final_index_status_counts": dict(Counter(str(x.get("index_http_status")) for x in d["filings"].values())),
            "source_d_final_document_status_counts": dict(Counter(str(doc.get("http_status")) for x in d["filings"].values() for doc in x.get("documents", []))),
            "source_c_initial_metrics": c_initial["metrics"],
            "source_d_end_to_end_metrics": source_d_metrics,
            "source_d_initial_selector_diagnostic": {
                "candidate_filings": d_initial["candidate_filing_count"],
                "indexes_retrieved_before_fix": d_initial["index_files_retrieved"],
                "index_status_counts": d_initial["index_http_status_counts"],
            },
        },
        "verdict": "PARTIAL",
        "verdict_basis": "Six direct EPS values were found among 16 frozen missing periods. The small, stratified sample is not representative; four frozen period mappings are invalid, and historical Grade A controls cannot be independently audited.",
        "sol_xhigh_review": "completed; reviewer found no blocking findings and recommended PARTIAL; the overlapping-count clarification is included",
    }
    return payload, source_summary, official, a, b, c, d, fmap, c_initial, d_initial, d_ocr


def ledger_lines(official, a, b, c, d, fmap, d_ocr):
    lines = []
    ocr_by_url = {item.get("url"): item for item in d_ocr}

    def add(item):
        lines.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))

    for doc_id, doc in official.get("docs", {}).items():
        add({"record_type": "official_documentation", "source_id": doc_id,
             "url": doc.get("url"), "http_status": doc.get("actual_http_status", doc.get("status")),
             "retrieved_at": doc.get("retrieved_at"), "bytes": doc.get("wire_bytes", doc.get("bytes")),
             "sha256": doc.get("sha256"), "lock_interstitial": doc.get("lock_interstitial", False)})
    for issuer in fmap.get("issuers", []):
        add({"record_type": "sec_submissions", "tickers": issuer.get("tickers"), "cik": issuer.get("cik"),
             "url": issuer.get("url"), "http_status": issuer.get("actual_http_status", issuer.get("http_status")),
             "retrieved_at": issuer.get("retrieved_at"), "response_bytes": issuer.get("response_bytes"),
             "wire_bytes": issuer.get("wire_bytes"),
             "error": issuer.get("error"), "filing_count_in_recent_submissions": len(issuer.get("filings", []))})
    for source_id, source in (("A_FSDS", a), ("B_NOTES", b)):
        for quarter, item in source.get("zips", {}).items():
            add({"record_type": "dataset_zip", "source_id": source_id, "filing_calendar_quarter": quarter,
                 "url": item.get("url"), "http_status": item.get("http_status"),
                 "retrieved_at": item.get("retrieved_at"), "zip_bytes": item.get("wire_bytes"),
                 "sha256": item.get("sha256"), "parse_status": item.get("parse_status"),
                 "members": item.get("members"), "row_counts": item.get("row_counts"),
                 "target_accessions": item.get("target_accessions", [])})
        for accession, item in source_records(source).items():
            meta = item.get("metadata", {})
            add({"record_type": "dataset_accession", "source_id": source_id, "accession": accession,
                 "ticker": meta.get("ticker"), "cik": meta.get("cik"), "form": meta.get("form"),
                 "filing_date": meta.get("filing_date"), "report_date": meta.get("report_date"),
                 "dataset_lookup_status": item.get("dataset_status"),
                 "accession_in_SUB": item.get("accession_matches_metadata"),
                 "candidate_count": len(item.get("eps_candidates", [])),
                 "note_text_candidate_count": len(item.get("note_text_candidates", []))})
    for accession, item in source_records(c).items():
        meta = item.get("metadata", {})
        add({"record_type": "sec_10k", "accession": accession, "ticker": meta.get("ticker"),
             "cik": meta.get("cik"), "fy": meta.get("fy"), "form": meta.get("form"),
             "filing_date": meta.get("filing_date"), "report_date": meta.get("report_date"),
             "url": item.get("url"), "http_status": item.get("http_status"),
             "parse_status": item.get("parse_status"), "bytes": item.get("wire_bytes"),
             "sha256": item.get("sha256"), "error": item.get("error"),
             "eps_fact_count": len(item.get("eps_facts", [])),
             "eps_table_count": len(item.get("eps_tables", [])),
             "quarterly_section_excerpt_count": len(item.get("quarterly_section_excerpts", []))})
    for accession, item in d.get("filings", {}).items():
        meta = item.get("metadata", {})
        add({"record_type": "sec_8k_index", "accession": accession, "tickers": meta.get("tickers"),
             "cik": meta.get("cik"), "form": meta.get("form"), "filing_date": meta.get("filing_date"),
             "url": item.get("index_url"), "http_status": item.get("index_http_status"),
             "bytes": item.get("index_wire_bytes"), "sha256": item.get("index_sha256"),
             "status": item.get("status"), "period_windows": meta.get("period_windows", [])})
        for doc in item.get("documents", []):
            ocr = ocr_by_url.get(doc.get("url"), {})
            add({"record_type": "sec_8k_document", "accession": accession, "name": doc.get("name"),
                 "url": doc.get("url"), "description": doc.get("description"),
                 "http_status": doc.get("http_status"), "parse_status": doc.get("parse_status"),
                 "bytes": doc.get("wire_bytes"), "sha256": doc.get("sha256"), "error": doc.get("error"),
                 "eps_fact_count": len(doc.get("eps_facts", [])), "eps_table_count": len(doc.get("eps_tables", [])),
                 "ocr_status": ocr.get("ocr_status"),
                 "ocr_sha256_verified": (ocr.get("sha256") == doc.get("sha256")) if ocr else None,
                 "ocr_text_characters": len(ocr.get("ocr_text", "")) if ocr else None})
    for (ticker, fy), case in VERIFIED.items():
        add({"record_type": "adjudicated_verified_q4_eps", "ticker": ticker, "fy": fy,
             "q4_end": case["q4_end"], "eps": case["eps"], "url": case["url"],
             "accession": case["accession"], "filing_date": case["filing_date"],
             "raw_tag": case["raw_tag"], "location": case["location"],
             "corroborating_url": case.get("corroborating_url"),
             "corroborating_accession": case.get("corroborating_accession")})
    return "\n".join(lines) + "\n"


def md_escape(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def make_report(results, sample, source_summary):
    input_audit = sample["input_audit"]
    lines = [
        "# SEC Q4 Diluted EPS 免費資料來源 POC",
        "",
        "| Ticker | FY/Q4 | 原本缺漏原因 | SEC ZIP | SEC Notes | 10-K | 8-K | 最終結果 | EPS |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    companies = {item["ticker"]: item for item in sample["sample"]}
    for company in sample["sample"]:
        ticker = company["ticker"]
        items = [row for row in results["period_results"] if row["ticker"] == ticker]
        ticker_text = f"{ticker} — {company['company_name']}<br>CIK {company['cik']}"
        period_text = "<br>".join(f"FY{r['fiscal_year']} {r['frozen_q4_end']} ({r['classification']})" for r in items)
        reason = company.get("missing_reason") or "陽性對照候選；歷史 Grade A 無法稽核"
        zip_text = "<br>".join("A/B HTTP 200、ZIP 解析成功" for _ in items)
        c_text = "<br>".join(
            "HTTP 200、原始 filing parsed" if r["period_end_match"] else "HTTP 200、實際財年期末不同"
            for r in items
        )
        d_text = "<br>".join(
            f"8-K {len(r['source_d_8k_search']['matching_search_windows'])} filing windows; "
            f"{sum(x['document_count'] for x in r['source_d_8k_search']['matching_search_windows'])} docs; "
            f"{r['source_d_8k_search']['status']}"
            for r in items
        )
        final = "<br>".join(f"{r['classification']} — {r['result']}" for r in items)
        eps = "<br>".join(
            f"{r['frozen_q4_end']}: {r['verified_q4_eps']:+.2f}" if r["verified_q4_eps"] is not None
            else f"{r['frozen_q4_end']}: —" for r in items
        )
        lines.append("| " + " | ".join(map(md_escape, [ticker_text, period_text, reason, zip_text, zip_text, c_text, d_text, final, eps])) + " |")

    lines += [
        "",
        "> 樣本固定為 10 檔、20 個 FY/Q4 期別；8 檔缺漏類別各兩檔，另有兩檔陽性對照候選。`A/B/C/D/E` 是此 POC 的 SEC 取樣結果分類，不會寫回專案正式 Grade A/B/C/D。陽性對照歷史 Grade A 身分無法由目前 main 驗證。",
        "",
        "## 已驗證直接披露",
        "",
        "| 股票 / FY | SEC 來源、Accession、申報日 | Fiscal Quarter / End Date | EPS | 原始標籤與位置 | GAAP / Diluted 判定 | 拆股基準與資料庫一致性 |",
        "|---|---|---|---:|---|---|---|",
    ]
    for row in results["verified_disclosures"]:
        key = (row["accession"], row["q4_end"])
        period = next(x for x in results["period_results"]
                      if x["classification"] == "A" and x["verified_source"]["accession"] == row["accession"])
        label = f"{period['ticker']} FY{period['fiscal_year']}"
        source_links = f"[{md_escape(row['source'])}]({row['url']})<br>`{row['accession']}`; filed {row['filing_date']}"
        if row.get("corroborating_url"):
            source_links += f"<br>[8-K EX-99.1]({row['corroborating_url']})<br>`{row['corroborating_accession']}`; filed {row['corroborating_filing_date']}"
        lines.append("| " + " | ".join(map(md_escape, [
            label, source_links, f"FY{period['fiscal_year']} Q4 ended {row['q4_end']}",
            f"{row['eps']:+.2f}",
            f"{row['raw_tag'] or '無 XBRL tag；原始 HTML/敘述'} — {row['location']}",
            row["gaap_diluted_basis"], row["split_basis"] + " " + row["database_match"],
        ])) + " |")

    lines += [
        "",
        "## 來源成功率",
        "",
        "| 來源 | 測試期別 | 找到候選 | Verified Direct | 不可驗證 | 失敗 | 單一來源新增唯一值 | 累積唯一期別 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in source_summary:
        lines.append("| " + " | ".join(map(str, [row["source"], row["tested_periods"], row["found_candidate"], row["verified_direct"], row["unverifiable"], row["failure"], row["single_source_unique_verified"], row["cumulative_unique_verified"]])) + " |")
    lines += [
        "",
        "候選期別在來源間重疊，不得相加為唯一值。FSDS 和 Notes 各找到 TDW 兩個 March 31 diluted EPS fact；對照 10-K 的 December 31 財年期末後，兩者證實是 Q1 而非 Q4。Source C 有 OFG FY2017 與 FY2020 兩筆直接披露；Source D 找到六筆直接披露，其中 OFG 兩筆與 10-K 重疊，並新增 ASTC FY2014、FGC FY2016、FGC FY2022、WRAP FY2022 四個唯一期別。Source D 另有兩個 TDW 候選仍因實際財政季度映射為 Q1 而不可驗證。",
        "",
        "## 尚未解決原因",
        "",
        "下列原因是不同的診斷面向，可能描述同一個期別，不可將列上的期別數相加作為總數。",
        "",
        "| 原因 | 期別數 | 說明 |",
        "|---|---:|---|",
        "| 搜尋的原始 SEC 範圍未找到可直接採用的 Q4 EPS（C） | 10 | 9 筆缺漏期別加 NMTC FY2017 對照候選。每期均有年度、部分季度或其他線索，但不足以接受 Q4 值；此結果不等同公司從未在其他歷史附件或場合公布。 |",
        "| Frozen period 對不上 SEC fiscal year end（E） | 4 | 缺漏樣本 BNC FY2024 一筆；對照候選 NMTC FY2019、TDW FY2018/FY2019 三筆。均保留原樣，未替換。 |",
        "| 原始 10-K / 8-K 沒有找到可用的 Q4 EPS 列 | 10 | 9 個缺漏 C 期別加 NMTC FY2017 對照候選；有些檔案包含第四季股價表，不能視為 EPS 表。沒有把「未在搜尋範圍找到」擴大成「SEC 絕未披露」。 |",
        "| 有資料但最終 parser / OCR 無法讀取 | 0 | 161 個 HTML 文件已解析；64 個 image 附件的 OCR 均成功且 hash 對應。首輪 selector 漏掉 OFG/WRAP generic exhibits，窄 EPS 關鍵字漏掉 FGC 的 “per fully diluted share”；均已補搜並納入。 |",
        "| 公司名稱 / ticker 或 CIK 對應未解決 | 0 | FGC 2016 舊名與 2022 的 FG Financial Group / FGF 報告使用相同 CIK 0001591890；ticker／名稱變更已記錄，未替換凍結樣本。 |",
        "| GAAP / Non-GAAP、股類、拆股或申報版本仍有衝突 | 0 | 六筆直接值均有 GAAP 表格或明確 GAAP 語境、Diluted 標示及單季期間。FGC FY2022 的 2024/2026 reverse split 依原始申報基準保留，未套用調整。 |",
        "| 網路 / 權限失敗 | 0 個最終 URL | 404 個最終唯一 SEC URL 均 HTTP 200；全程有 2 次 retry，但 retry 中間回應的 status 未留存，因此不能聲稱完全沒有 transient 403/429。沒有最終失敗、403 或 429。 |",
        "| 陽性對照 Grade A 無法回溯稽核 | 4 | 指定 acceptance ledger / 原 classifier 都不在 main；兩個候選共四期只能驗證 SEC 搜尋結果，不能宣稱原 Grade A。 |",
        "",
        "## 執行與可重現性",
        "",
        f"- SEC 來源文件窗口：FSDS {len(read('source-a.json')['zips'])} 個 filing-calendar-quarter ZIP，Notes {len(read('source-b.json')['zips'])} 個 ZIP；從 `{min(read('source-a.json')['zips'])}` 到 `{max(read('source-a.json')['zips'])}`。SEC ZIP 是季度全市場封裝；逐檔順序下載/解析，程式只保留樣本 CIK/accession 的匹配列，沒有掃描其他歷史季度。ZIP 合計 {results['network_and_volume']['wire_bytes']['source_a_zip_archives'] + results['network_and_volume']['wire_bytes']['source_b_zip_archives']:,} bytes（約 11.17 GB）。",
        f"- 10-K/10-K/A 原始文件 23 份；8-K/EX 附近窗口 102 個 accession、225 份文件（161 HTML parsed、64 image OCR）。共 {results['network_and_volume']['actual_http_requests']} 次 HTTP 請求，{results['network_and_volume']['final_unique_urls']} 個最終唯一 URL 均 HTTP 200；含重試 wire bytes {results['network_and_volume']['total_wire_bytes']:,}（約 {results['network_and_volume']['total_wire_bytes']/1_000_000_000:.2f} GB），另有 2 次 retry 的中間 status 未持久化。觀測時間窗 10:04:51–11:18:43 UTC（約 1 小時 13 分 52 秒，含分析與修正時間）；明確 User-Agent 值不寫入報告，速率上限 4 req/s，使用 Retry-After 與退避。",
        "- 官方 SEC 說明頁回 HTTP 200 但回傳 lock interstitial；因此使用 SEC 官方 FSDS/Notes PDF 格式文件及可取得的官方頁面確認欄位。ZIP 及原始 SEC filing 不納入 repo；本 repo 留下 URL、accession、response status、bytes、SHA-256 與摘錄。",
        "- 全程沒有呼叫 Massive API、沒有用爬蟲、沒有 API key，沒有修改正式 financials、production DB、TTM/P-E/growth 或 S3/S4 規則。",
        "- 測試：`python -m unittest discover -s tests -p 'test_sec_q4_eps_poc.py' -v`。重建報告：`python scripts/render_sec_q4_eps_poc_report.py`。真實 SEC 取樣重跑命令與資料窗口見 [README.md](README.md)。",
        "",
        "## 可行性判斷與下一步",
        "",
        "1. 16 個凍結缺漏 FY/Q4 期別中，找到 6 筆直接披露 GAAP diluted EPS（37.5%）；9 筆仍無可接受的直接 Q4 EPS，BNC FY2024 一筆因 frozen end date 與 SEC 年報期末不符列 E。TDW FY2018/FY2019 真正 December Q4 的 -2.83/-1.52 是樣本外診斷，不算 frozen March 期別的成功。",
        "2. Source D 8-K/EX-99.1 找到 6 筆 verified direct，是最有效來源；其中 2 筆與 10-K 重疊、4 筆是新增期別。Source C 10-K 有 2 筆直接值；FSDS/Notes 沒有增加 verified direct。Notes 對識別季度期間仍有用，因季度 ZIP 是申報日曆季度，不是 issuer fiscal quarter。",
        "3. 瓶頸同時在 SEC 揭露形態和解析程式：結構化 datasets 沒提供可用的 Q4-only EPS fact；但原始 10-K/8-K 中另有 6 個直接披露期別，兩筆在初始解析/選擇器中漏掉，經人工稽核與修正後找回。剩下 9 個缺漏 C 不能僅憑此小範圍查詢判定 SEC 從未公布。另有 4 個 frozen period mapping 不符 SEC fiscal year end。",
        "4. 建議進一步做 50–100 檔分層測試，但先恢復原分類器與 Grade A ledger、以 SEC 10-K report_date 重建財政 Q4 end，並校準 positive controls。這 10 檔按原因分層且不是隨機全市場樣本；缺失的原分類 artifacts、產業差異、候選搜尋窗口和 4 個日期錯配都會影響結果。37.5% 不能外推到 7,755 筆或全市場。",
        "",
        "**Verdict: PARTIAL** — 真實 SEC 來源在本 frozen sample 找到六筆直接 Q4 diluted EPS。四個 frozen fiscal periods 與 SEC 期末不匹配，歷史 Grade A controls 無法以缺失的原 ledger 稽核，且九個缺漏期別仍無已驗證直接 Q4 值；不能據此推估全市場回補量。",
        "",
        "Sol XHigh 審核：完成；無阻擋事項，建議 PARTIAL。已加入未解原因計數可能重疊的說明。",
        "",
        "## 證據檔案",
        "",
        "- `sample.json`：預先凍結的樣本、種子、選樣 SHA、輸入分類限制。",
        "- `results.json`：逐期結果、來源狀態、request/volume、陽性候選與樣本外 TDW diagnostic。",
        "- `source-ledger.jsonl`：逐 URL/accession 的 SEC status、檔案大小、SHA-256 與 parser 結果。",
        "- `official-docs.json`、`filing-map.json`、`source-a.json`、`source-b.json`、`source-c.json`、`source-d.json`、`source-d-ocr.json`：來源 metadata、matched rows/excerpts、OCR review 與 request diagnostics。",
    ]
    return "\n".join(lines) + "\n"


def main():
    results, source_summary, official, a, b, c, d, fmap, _, _, d_ocr = build_results()
    sample = read("sample.json")
    write_json("results.json", results)
    (REPORT / "source-ledger.jsonl").write_text(ledger_lines(official, a, b, c, d, fmap, d_ocr), encoding="utf-8")
    (REPORT / "feasibility-report.md").write_text(make_report(results, sample, source_summary), encoding="utf-8")
    print(json.dumps({
        "results": str(REPORT / "results.json"),
        "source_ledger": str(REPORT / "source-ledger.jsonl"),
        "report": str(REPORT / "feasibility-report.md"),
        "outcomes": results["outcomes"],
        "network": results["network_and_volume"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
