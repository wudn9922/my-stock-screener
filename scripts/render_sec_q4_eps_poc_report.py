#!/usr/bin/env python3
"""Render the frozen SEC Q4 EPS POC from persisted SEC responses."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/report/sec-q4-eps-poc"
FINANCIALS = ROOT / "docs/report/financials"

# Each A result is tied to a reviewed SEC exhibit.  Values are as-filed values;
# this renderer never derives Q4 EPS from annual and Q1-Q3 figures.
DISCLOSURES = {
    ("ASTC", 2014): {
        "source": "SEC 8-K EX-99.1",
        "url": "https://www.sec.gov/Archives/edgar/data/1001907/000157104914004870/t1401849_ex99-1.htm",
        "accession": "0001571049-14-004870", "filing_date": "2014-09-30",
        "q4_end": "2014-06-30", "eps": -0.04, "raw_tag": None,
        "location": "Exhibit 99.1, ‘Fourth Quarter Results’; sentence for the quarter ended June 30, 2014.",
        "gaap_diluted_basis": "Release labels results GAAP and explicitly reports the Q4 loss per diluted share; no adjusted qualifier applies.",
        "split_basis": "As-filed basis; no adjustment applied. Later reverse splits were in 2017 and 2022.",
        "database_match": "No same-period local Q4 value; not comparable.",
    },
    ("FGC", 2016): {
        "source": "SEC 8-K EX-99.1",
        "url": "https://www.sec.gov/Archives/edgar/data/1591890/000138713117001425/ex99-1.htm",
        "accession": "0001387131-17-001425", "filing_date": "2017-03-16",
        "q4_end": "2016-12-31", "eps": 0.27, "raw_tag": None,
        "location": "Exhibit 99.1, unaudited consolidated income statement; Three Months Ended December 31, 2016, Diluted row.",
        "gaap_diluted_basis": "Primary consolidated statement labels the row Diluted. The annual ‘before catastrophe events’ EPS is adjusted/non-GAAP and excluded.",
        "split_basis": "As-filed FY2016 basis; no adjustment applied. Later reverse splits were 0.04 (2024) and 0.20 (2026).",
        "database_match": "No same-period local Q4 value; not comparable.",
    },
    ("FGC", 2022): {
        "source": "SEC 8-K EX-99.1",
        "url": "https://www.sec.gov/Archives/edgar/data/1591890/000149315223008865/ex99-1.htm",
        "accession": "0001493152-23-008865", "filing_date": "2023-03-24",
        "q4_end": "2022-12-31", "eps": -0.03, "raw_tag": None,
        "location": "Exhibit 99.1, Q4 earnings narrative and consolidated statement; Three Months Ended December 31, 2022 column.",
        "gaap_diluted_basis": "Narrative says fully diluted Q4 EPS; primary statement says Basic and diluted net earnings (loss) per common share. The separate annual column is not used.",
        "split_basis": "As-filed FY2022 basis; no adjustment applied. Later reverse splits were 0.04 (2024) and 0.20 (2026).",
        "database_match": "No same-period local Q4 value; not comparable.",
    },
    ("WRAP", 2022): {
        "source": "SEC 8-K EX-99.1",
        "url": "https://www.sec.gov/Archives/edgar/data/1702924/000143774923005259/ex_483215.htm",
        "accession": "0001437749-23-005259", "filing_date": "2023-03-03",
        "q4_end": "2022-12-31", "eps": -0.09, "raw_tag": None,
        "location": "Exhibit 99.1, consolidated statements of operations; first column is Three Months Ended December 31, 2022.",
        "gaap_diluted_basis": "Primary statement row is Net loss per basic and diluted common share; Q4 column is distinct from the twelve-month columns.",
        "split_basis": "As-filed FY2022 basis; no post-period split adjustment applied.",
        "database_match": "No same-period local Q4 value; not comparable.",
    },
    ("ULBI", 2021): {
        "source": "SEC 8-K EX-99.1",
        "url": "https://www.sec.gov/Archives/edgar/data/875657/000143774922002307/ex_331663.htm",
        "accession": "0001437749-22-002307", "filing_date": "2022-02-03",
        "q4_end": "2021-12-31", "eps": -0.07, "raw_tag": None,
        "location": "Exhibit 99.1, unaudited Q4 table; Three-Month Period Ended December 31, 2021, Net (Loss) Income Attributable row, Per Diluted Share column.",
        "gaap_diluted_basis": "The table reports the unadjusted net loss attributable to Ultralife and its per diluted share amount. The separate adjusted net income row is not used.",
        "split_basis": "As-filed basis; no adjustment applied and no later split identified for this period.",
        "database_match": "No same-period local Q4 EPS value; the local Q4 EPS is null.",
    },
}

ADJUDICATION = {
    ("BNC", 2014): ("C", None, "年度 EPS 與部分季度資料可用；10-K/8-K 搜尋範圍未找到可直接採用的 Q4 diluted EPS。"),
    ("BNC", 2024): ("E", None, "凍結期末 2025-01-31 與 SEC FY2024 年報期末 2024-12-31 不同，無法把年報當作該凍結期別。"),
    ("QXL", 2014): ("C", None, "年報的 Fourth Quarter 表是股價 High/Low，不是 EPS；僅有年度/部分季度 EPS 線索。"),
    ("QXL", 2019): ("C", None, "年報的 Fourth Quarter 表是股價 High/Low，不是 EPS；僅有年度/部分季度 EPS 線索。"),
    ("FGC", 2016): ("A", 0.27, "SEC 8-K EX-99.1 明確列示 GAAP diluted Q4 EPS。"),
    ("FGC", 2022): ("A", -0.03, "SEC 8-K EX-99.1 明確分開單季和年度欄，GAAP 表及敘述一致。"),
    ("WRAP", 2017): ("C", None, "年度/部分季度資料可用；10-K/8-K 搜尋範圍未找到可直接採用的 Q4 diluted EPS。"),
    ("WRAP", 2022): ("A", -0.09, "SEC 8-K EX-99.1 明確列示 GAAP basic-and-diluted Q4 EPS。"),
    ("PCSA", 2012): ("E", None, "目前 CIK 的 SEC submissions 沒有可連結至 FY2012 的申報；歷史 CIK/公司承接映射不完整，不能判定沒有披露。"),
    ("PCSA", 2022): ("C", None, "10-K 只找到年度 basic-and-diluted EPS；SEC 8-K 文字與已完成的圖片 OCR 均未找到單季 Q4 EPS。"),
    ("ULBI", 2014): ("B", None, "8-K 報告 Q4 每股 0.05 美元，但未說明是 basic 或 diluted，故不接受為 diluted EPS。"),
    ("ULBI", 2021): ("A", -0.07, "SEC 8-K EX-99.1 表格明確列示 GAAP Q4 per diluted share。"),
    ("ASTC", 2014): ("A", -0.04, "SEC 8-K EX-99.1 ‘Fourth Quarter Results’ 明確列示 GAAP diluted EPS。"),
    ("ASTC", 2019): ("C", None, "有年度 EPS 和財報資料；已查來源範圍未找到直接 Q4 EPS。"),
    ("IGC", 2012): ("C", None, "年報有年度 diluted EPS 與前一曆年季度 pro forma 表，並非該財年 Q4。"),
    ("IGC", 2019): ("C", None, "有年度與部分季度 EPS 線索；已查來源範圍未找到直接 Q4 EPS。"),
    ("PZG", 2016): ("B", None, "Notes 與年報季度表列示 +0.32，但 main Grade A fact 為 -0.32；原始表格符號基準有衝突，暫不採用。"),
    ("PZG", 2019): ("A", -0.09, "SEC 年報季度 EPS fact 為 -0.09，並與 main Grade A as-filed value 一致；原始表格以 net loss 語境列示 0.09。"),
    ("PTN", 2018): ("A", 0.06, "SEC 年報季度 diluted EPS fact +0.06，與 main Grade A as-filed value 一致。"),
    ("PTN", 2019): ("A", 0.23, "SEC 年報季度 diluted EPS fact +0.23，與 main Grade A as-filed value 一致。"),
}

FISCAL_MISMATCH_EVIDENCE = {
    "TDW": {
        2018: ("2018-12-31", "0001564590-19-005545", "2019-02-28"),
        2019: ("2019-12-31", "0001564590-20-008191", "2020-03-02"),
        2020: ("2020-12-31", "0001437749-21-005037", "2021-03-04"),
        2021: ("2021-12-31", "0001437749-22-005725", "2022-03-09"),
        2022: ("2022-12-31", "0001437749-23-004829", "2023-02-28"),
        2023: ("2023-12-31", "0001437749-24-006108", "2024-02-29"),
        2024: ("2024-12-31", "0001437749-25-005487", "2025-02-27"),
        2025: ("2025-12-31", "0001437749-26-006391", "2026-03-02"),
    },
    "NMTC": {
        2019: ("2019-09-30", "0001213900-19-026720", "2019-12-20"),
        2020: ("2020-09-30", "0001213900-20-041661", "2020-12-09"),
        2021: ("2021-09-30", "0001213900-21-065463", "2021-12-15"),
        2022: ("2022-09-30", "0001213900-22-081897", "2022-12-22"),
        2023: ("2023-09-30", "0001213900-23-096120", "2023-12-15"),
        2024: ("2024-09-30", "0001213900-24-109524", "2024-12-17"),
        2025: ("2025-09-30", "0001213900-25-122390", "2025-12-17"),
    },
    "BNC": {2024: ("2024-12-31", "0001641172-25-000913", "2025-03-27")},
}


def read_json(name: str):
    return json.loads((REPORT / name).read_text(encoding="utf-8"))


def write_json(name: str, value):
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def all_records(source):
    return source.get("accessions", source.get("filings", {}))


def source_status(source, ticker, fy, end):
    """Locate target-period data facts; preserve candidates for audit."""
    end_compact = end.replace("-", "")
    records = all_records(source)
    hits = []
    related = []
    for accession, item in records.items():
        meta = item.get("metadata", {})
        tickers = [meta.get("ticker")] if meta.get("ticker") else meta.get("tickers", [])
        if ticker not in tickers:
            continue
        if meta.get("fy") not in (fy, fy + 1, None):
            continue
        related.append((accession, item))
        for fact in item.get("eps_candidates", []):
            if (fact.get("date") == end_compact and fact.get("quarters") == "1"
                    and "Diluted" in fact.get("tag", "")
                    and fact.get("unit") == "USD" and not fact.get("dimension_members")):
                hits.append({"accession": accession, "tag": fact.get("tag"), "value": fact.get("value"),
                             "unit": fact.get("unit"), "date": fact.get("date"),
                             "quarters": fact.get("quarters"), "dimensions": fact.get("dimension_members", [])})
    hits.sort(key=lambda row: (row["accession"], row["tag"], row["value"] or ""))
    return {
        "status": "HTTP 200 / parsed" if related else "no target accession linked to this CIK/period",
        "related_accessions": [acc for acc, _ in related],
        "eps_candidate_count": len(hits), "direct_q4_facts": hits,
        "dataset_statuses": sorted({item.get("dataset_status") for _, item in related if item.get("dataset_status")}),
    }


def source_c_records(c, ticker, fy):
    found = []
    for accession, item in all_records(c).items():
        meta = item.get("metadata", {})
        if meta.get("ticker") == ticker and meta.get("fy") == fy:
            found.append({"accession": accession, "form": meta.get("form"),
                          "filing_date": meta.get("filing_date"), "report_date": meta.get("report_date"),
                          "url": item.get("url"), "http_status": item.get("http_status"),
                          "parse_status": item.get("parse_status"),
                          "eps_table_count": len(item.get("eps_tables", [])),
                          "quarterly_excerpt_count": len(item.get("quarterly_section_excerpts", []))})
    return sorted(found, key=lambda x: (x["form"] == "10-K/A", x["filing_date"] or ""))


def source_d_windows(d, ocr, ticker, fy):
    ocr_by_url = {item.get("url"): item for item in ocr}
    windows = []
    for accession, filing in d.get("filings", {}).items():
        matching = [w for w in filing.get("metadata", {}).get("period_windows", [])
                    if w.get("ticker") == ticker and int(w.get("fy", -1)) == fy]
        if not matching:
            continue
        documents = []
        for doc in filing.get("documents", []):
            ocr_item = ocr_by_url.get(doc.get("url"), {})
            unsupported = doc.get("parse_status") == "unsupported_nontext"
            documents.append({"name": doc.get("name"), "url": doc.get("url"),
                              "http_status": doc.get("http_status"), "parse_status": doc.get("parse_status"),
                              "ocr_status": ocr_item.get("ocr_status") if unsupported else None,
                              "ocr_hash_matches": (ocr_item.get("sha256") == doc.get("sha256")) if unsupported else None})
        complete = filing.get("index_http_status") == 200 and all(
            doc["http_status"] == 200 and doc["parse_status"] in {"parsed", "unsupported_nontext"}
            and (doc["parse_status"] != "unsupported_nontext" or
                 (doc["ocr_status"] == "ok" and doc["ocr_hash_matches"]))
            for doc in documents)
        windows.append({"accession": accession, "filing_date": filing.get("metadata", {}).get("filing_date"),
                        "index_url": filing.get("index_url"), "index_http_status": filing.get("index_http_status"),
                        "complete": complete, "period_windows": matching, "documents": documents})
    return sorted(windows, key=lambda x: (x["filing_date"] or "", x["accession"]))


def quarter_for_date(value):
    date = datetime.strptime(value, "%Y-%m-%d")
    return f"{date.year}q{(date.month - 1) // 3 + 1}"


def archive_observation(source, fact_status):
    zips = source.get("zips", {})
    quarters = sorted({quarter_for_date(item.get("metadata", {}).get("filing_date"))
                       for acc in fact_status.get("related_accessions", [])
                       for item in [all_records(source).get(acc, {})]
                       if item.get("metadata", {}).get("filing_date")})
    matches = [zips.get(q) for q in quarters if q in zips]
    if not fact_status.get("related_accessions"):
        return {"calendar_quarters": quarters, "http_status": None, "parse_status": "target accession unavailable"}
    return {"calendar_quarters": quarters,
            "http_status": 200 if matches and all(z.get("http_status") == 200 for z in matches) else None,
            "parse_status": "parsed" if matches and all(z.get("parse_status") == "parsed" for z in matches) else "missing archive record"}


def main_grade_fact(company, period):
    return period.get("main_grade_a_audit", {}).get("sec_fact") or {}


def period_results(sample, a, b, c, d, ocr):
    rows = []
    for company in sample["sample"]:
        for period in company["periods"]:
            ticker, fy = company["ticker"], int(period["fy"])
            classification, eps, explanation = ADJUDICATION[(ticker, fy)]
            status_a = source_status(a, ticker, fy, period["q4_end"])
            status_b = source_status(b, ticker, fy, period["q4_end"])
            c_filings = source_c_records(c, ticker, fy)
            d_filings = source_d_windows(d, ocr, ticker, fy)
            grade_fact = main_grade_fact(company, period)
            unverified_candidate = None
            if (ticker, fy) == ("ULBI", 2014):
                unverified_candidate = {"value": 0.05, "unit": "USD/share", "source": "SEC 8-K EX-99.1",
                                        "accession": "0000875657-15-000003", "filing_date": "2015-02-12",
                                        "reason": "per-share amount is not identified as basic or diluted"}
            elif (ticker, fy) == ("PZG", 2016):
                unverified_candidate = {"value": 0.32, "unit": "USD/share", "source": "SEC Notes / FY2016 10-K quarterly table",
                                        "accession": "0001564590-16-025274", "filing_date": "2016-09-16",
                                        "reason": "source positive magnitude conflicts with main Grade A reportedValue -0.32"}
            row = {
                "ticker": ticker, "company_name": company["company_name"], "cik": company["cik"],
                "sample_role": company["sample_role"], "fiscal_year": fy,
                "frozen_q4_end": period["q4_end"],
                "missing_reason": company.get("missing_reason"),
                "classification": classification, "verified_q4_eps": eps,
                "unverified_candidate": unverified_candidate,
                "result": explanation,
                "source_a_fsds": {**status_a, **archive_observation(a, status_a)},
                "source_b_notes": {**status_b, **archive_observation(b, status_b)},
                "source_c_10k": {"status": "HTTP 200 / parsed" if c_filings and all(x["http_status"] == 200 and x["parse_status"] == "parsed" for x in c_filings)
                                           else ("no accession linked to current CIK/period" if not c_filings else "incomplete"),
                                  "filings": c_filings},
                "source_d_8k": {"status": "HTTP 200 / parsed; OCR hash verified" if d_filings and all(x["complete"] for x in d_filings)
                                         else (("historical CIK/filing window unavailable" if (ticker, fy) == ("PCSA", 2012)
                                                else "no 8-K matched the requested search window") if not d_filings else "incomplete"),
                                "filings": d_filings},
                "main_grade_a_audit": ({"eps_quality": period.get("main_grade_a_audit", {}).get("main_eps_quality"),
                                        "is_main_grade_a": period.get("main_grade_a_audit", {}).get("is_main_grade_a"),
                                        "as_filed_value": grade_fact.get("reportedValue"),
                                        "current_basis_value": grade_fact.get("value"),
                                        "split_factor": grade_fact.get("splitFactor"),
                                        "accession": grade_fact.get("accession"),
                                        "filing_date": grade_fact.get("filed"),
                                        "url": grade_fact.get("filingUrl")}
                                       if company["sample_role"] == "positive_control_grade_a" else None),
            }
            rows.append(row)
    return rows


def fiscal_mismatches():
    rows = []
    for ticker, years in FISCAL_MISMATCH_EVIDENCE.items():
        d = json.loads((FINANCIALS / f"{ticker}.json").read_text(encoding="utf-8"))
        annual = {int(fy): end for fy, end in zip(d["annual"]["fy"], d["annual"]["end"])}
        q = d["quarterly"]
        for idx, (fy, fq, local_end) in enumerate(zip(q["fy"], q["fq"], q["end"])):
            fy = int(fy)
            if int(fq) != 4:
                continue
            if fy in years:
                sec_end, accession, filing_date = years[fy]
                if sec_end != local_end:
                    rows.append({"ticker": ticker, "fy": fy, "local_fq": 4, "local_q4_end": local_end,
                                 "sec_report_end": sec_end, "accession": accession, "filing_date": filing_date,
                                 "status": "confirmed_mismatch"})
            elif fy == 2026 and ticker == "TDW":
                rows.append({"ticker": ticker, "fy": fy, "local_fq": 4, "local_q4_end": local_end,
                             "sec_report_end": annual.get(fy), "accession": None, "filing_date": None,
                             "status": "unmatched_no_same_fy_annual_row"})
            elif fy == 2025 and ticker == "BNC":
                rows.append({"ticker": ticker, "fy": fy, "local_fq": 4, "local_q4_end": local_end,
                             "sec_report_end": "2026-04-30 (latest retrieved 10-K; fiscal-year label unresolved)",
                             "accession": "0001482541-26-000019", "filing_date": "2026-06-23",
                             "status": "unmatched_local_row_requires_period_mapping"})
            elif fy == 2018 and ticker == "NMTC":
                rows.append({"ticker": ticker, "fy": fy, "local_fq": 4, "local_q4_end": local_end,
                             "sec_report_end": None, "accession": None, "filing_date": None,
                             "status": "unmatched_no_same_fy_annual_row"})
    return rows


def source_summary(rows, a, b, c, d):
    # Twenty targets were planned for each source (16 missing rows plus 4
    # positive-control periods). PCSA FY2012 has no accession linked to the
    # current CIK, so every source has one incomplete target and 19 completed
    # source checks. Unique contribution columns count only the 16 missing rows.
    return [
        {"source": "SEC Financial Statement Data Sets", "planned_targets": 20,
         "completed_periods": 19, "incomplete_periods": 1,
         "found_candidate": 3, "verified_direct": 3, "unverifiable": 0, "failure": 0,
         "single_source_unique_missing": 0, "cumulative_unique_missing": 0,
         "candidate_periods": ["PZG:2019-06-30", "PTN:2018-06-30", "PTN:2019-06-30"],
         "verified_missing_periods": [], "verified_controls": ["PZG:2019-06-30", "PTN:2018-06-30", "PTN:2019-06-30"]},
        {"source": "SEC Financial Statement and Notes Data Sets", "planned_targets": 20,
         "completed_periods": 19, "incomplete_periods": 1,
         "found_candidate": 4, "verified_direct": 3, "unverifiable": 1, "failure": 0,
         "single_source_unique_missing": 0, "cumulative_unique_missing": 0,
         "candidate_periods": ["PZG:2016-06-30", "PZG:2019-06-30", "PTN:2018-06-30", "PTN:2019-06-30"],
         "verified_missing_periods": [], "verified_controls": ["PZG:2019-06-30", "PTN:2018-06-30", "PTN:2019-06-30"],
         "unverifiable_periods": ["PZG:2016-06-30"]},
        {"source": "原始 10-K / 10-K/A", "planned_targets": 20,
         "completed_periods": 19, "incomplete_periods": 1,
         "found_candidate": 4, "verified_direct": 3, "unverifiable": 1, "failure": 0,
         "single_source_unique_missing": 0, "cumulative_unique_missing": 0,
         "candidate_periods": ["PZG:2016-06-30", "PZG:2019-06-30", "PTN:2018-06-30", "PTN:2019-06-30"],
         "verified_missing_periods": [], "verified_controls": ["PZG:2019-06-30", "PTN:2018-06-30", "PTN:2019-06-30"],
         "unverifiable_periods": ["PZG:2016-06-30"]},
        {"source": "原始 8-K / EX-99.1", "planned_targets": 20,
         "completed_periods": 19, "incomplete_periods": 1,
         "found_candidate": 6, "verified_direct": 5, "unverifiable": 1, "failure": 0,
         "single_source_unique_missing": 5, "cumulative_unique_missing": 5,
         "candidate_periods": ["ASTC:2014-06-30", "FGC:2016-12-31", "FGC:2022-12-31", "WRAP:2022-12-31", "ULBI:2014-12-31", "ULBI:2021-12-31"],
         "verified_missing_periods": ["ASTC:2014-06-30", "FGC:2016-12-31", "FGC:2022-12-31", "WRAP:2022-12-31", "ULBI:2021-12-31"],
         "unverifiable_periods": ["ULBI:2014-12-31"]},
    ]


def controls_audit(sample):
    old = sample["input_audit"].get("previous_positive_control_rows_checked_against_main", [])
    return {
        "selected_control_companies": [x["ticker"] for x in sample["sample"] if x["sample_role"] == "positive_control_grade_a"],
        "selected_controls": [
            {"ticker": co["ticker"], "fy": p["fy"], "q4_end": p["q4_end"],
             "main_grade_a": p["main_grade_a_audit"],
             "poc_status": ADJUDICATION[(co["ticker"], int(p["fy"]))][0],
             "poc_eps_as_filed": ADJUDICATION[(co["ticker"], int(p["fy"]))][1],
             "poc_note": ADJUDICATION[(co["ticker"], int(p["fy"]))][2]}
            for co in sample["sample"] if co["sample_role"] == "positive_control_grade_a" for p in co["periods"]
        ],
        "prior_suggested_controls_nm_tc_tdw": old,
        "ledger_scope_note": "Main classifier/report now exists. Main acceptance ledger has aggregate verifiedA=5009 but no per-row accepted-A list; the selected Grade A controls were audited using main financial-row epsQuality plus secQ4EpsFacts and their SEC accession. NMTC/TDW rows have local values but no row-level A fact, no missing-classification entry, and no blocked/overridden/deferred ledger entries.",
    }


def build_results(sample, a, b, c, d, ocr, filing_map, official, supplement):
    periods = period_results(sample, a, b, c, d, ocr)
    missing = [r for r in periods if r["sample_role"] == "missing_q4_eps"]
    counts = Counter(r["classification"] for r in missing)
    sources = source_summary(periods, a, b, c, d)
    mismatches = fiscal_mismatches()
    current_direct = [f"{r['ticker']}:{r['frozen_q4_end']}" for r in missing if r["classification"] == "A"]
    old_audit = sample["input_audit"].get("previous_freeze_membership", {})
    prior_source_a = {"sec_http_requests": 19, "wire_bytes_received": 1841375190}
    prior_source_b = {"sec_http_requests": 18, "wire_bytes_received": 8864151660}
    current_source_a = a.get("metrics", {})
    current_source_b = b.get("metrics", {})
    current_a_increment = current_source_a.get("incremental", {})
    current_b_increment = current_source_b.get("incremental", {})
    # The artifacts retain acquisition counters across the earlier and
    # refrozen samples. Keep those deltas separate from the last invocation's
    # incremental counters and from the bytes of ZIPs referenced by this sample.
    refresh_metrics = {
        "source_a_fsds": {
            "recorded_request_delta_from_prior_poc": current_source_a.get("sec_http_requests", 0) - prior_source_a["sec_http_requests"],
            "recorded_wire_byte_delta_from_prior_poc": current_source_a.get("wire_bytes_received", 0) - prior_source_a["wire_bytes_received"],
            "last_invocation_requests": current_a_increment.get("sec_http_requests", 0),
            "last_invocation_wire_bytes": current_a_increment.get("wire_bytes_received", 0),
            "current_sample_unique_zip_payload_bytes": sum(x.get("wire_bytes", 0) or 0 for x in a.get("zips", {}).values()),
            "current_sample_unique_zip_count": len(a.get("zips", {})),
        },
        "source_b_notes": {
            "recorded_request_delta_from_prior_poc": current_source_b.get("sec_http_requests", 0) - prior_source_b["sec_http_requests"],
            "recorded_wire_byte_delta_from_prior_poc": current_source_b.get("wire_bytes_received", 0) - prior_source_b["wire_bytes_received"],
            "last_invocation_requests": current_b_increment.get("sec_http_requests", 0),
            "last_invocation_wire_bytes": current_b_increment.get("wire_bytes_received", 0),
            "current_sample_unique_zip_payload_bytes": sum(x.get("wire_bytes", 0) or 0 for x in b.get("zips", {}).values()),
            "current_sample_unique_zip_count": len(b.get("zips", {})),
        },
        "source_c_10k": {"requests": c.get("metrics", {}).get("sec_http_requests", 0),
                          "wire_bytes": c.get("metrics", {}).get("wire_bytes_received", 0)},
        "source_d_8k": {"requests": d.get("metrics", {}).get("sec_http_requests", 0),
                         "wire_bytes": d.get("metrics", {}).get("wire_bytes_received", 0)},
        "submissions": {"requests": filing_map.get("end_to_end_metrics", {}).get("actual_http_requests", 0),
                        "wire_bytes": filing_map.get("end_to_end_metrics", {}).get("wire_bytes_received", 0)},
        "positive_control_supplement": {"requests": supplement.get("metrics", {}).get("sec_http_requests", 0),
                                         "wire_bytes": supplement.get("metrics", {}).get("wire_bytes_received", 0)},
    }
    # Sum only the explicitly delimited refresh deltas. It excludes the older
    # mixed-scope total in the prior report and the unique ZIP payload sizes.
    refresh_requests = (
        refresh_metrics["source_a_fsds"]["recorded_request_delta_from_prior_poc"]
        + refresh_metrics["source_b_notes"]["recorded_request_delta_from_prior_poc"]
        + refresh_metrics["source_c_10k"]["requests"]
        + refresh_metrics["source_d_8k"]["requests"]
        + refresh_metrics["submissions"]["requests"]
        + refresh_metrics["positive_control_supplement"]["requests"]
    )
    refresh_wire_bytes = (
        refresh_metrics["source_a_fsds"]["recorded_wire_byte_delta_from_prior_poc"]
        + refresh_metrics["source_b_notes"]["recorded_wire_byte_delta_from_prior_poc"]
        + refresh_metrics["source_c_10k"]["wire_bytes"]
        + refresh_metrics["source_d_8k"]["wire_bytes"]
        + refresh_metrics["submissions"]["wire_bytes"]
        + refresh_metrics["positive_control_supplement"]["wire_bytes"]
    )
    net = {
        "rate_limit_requests_per_second": 4,
        "previous_poc_artifact_snapshot": "229ca59f0 (pre-refreeze SEC POC artifacts)",
        "refresh_delta_since_previous_poc_artifacts": {
            "requests": refresh_requests,
            "wire_bytes": refresh_wire_bytes,
            "by_source": refresh_metrics,
            "note": "Source A/B are cumulative artifact-counter deltas versus the previous POC snapshot; C/D, submissions, and the PZG supplement are the recorded acquisition-stage counters. Do not add their cumulative metrics fields again. The current report/render invocation is offline.",
        },
        "current_sample_unique_archive_payload_bytes": {
            "source_a_fsds": refresh_metrics["source_a_fsds"]["current_sample_unique_zip_payload_bytes"],
            "source_b_notes": refresh_metrics["source_b_notes"]["current_sample_unique_zip_payload_bytes"],
            "note": "Sum of the 16 unique quarterly ZIP payload sizes referenced by the frozen sample, including reused responses; not bytes downloaded during this refresh.",
        },
        "legacy_initial_run_reported_counters": {
            "requests": 406,
            "reported_total_bytes": 11190612664,
            "note": "Previous report total is preserved for traceability only. Its byte total includes a cached Notes ZIP's payload size and is not an exact actual-wire-byte baseline; it is not added to the refresh delta.",
        },
        "source_metrics": {
            "official_docs": official.get("end_to_end_metrics", {}),
            "submissions_end_to_end": filing_map.get("end_to_end_metrics", filing_map.get("metrics", {})),
            "submissions_current_replay": filing_map.get("metrics", {}),
            "source_a_fsds": a.get("metrics", {}), "source_b_notes": b.get("metrics", {}),
            "source_c_10k": c.get("metrics", {}), "source_d_8k": d.get("metrics", {}),
            "positive_control_supplement": supplement.get("metrics", {}),
        },
        "observed_collection_window": {"sample_frozen_at": sample.get("frozen_at"),
                                       "last_sec_source_response": supplement.get("retrieved_at"),
                                       "elapsed_after_freeze_seconds": 1260,
                                       "elapsed_note": "約 21 分鐘至最後一筆 SEC source artifact；包括快取重用和解析，不含後續 report/test。"},
        "responses_note": "主來源 artifact 的實際 SEC HTTP request/byte 數分開呈現；快取讀取不重複計為網路請求。沒有提交 SEC 原始 ZIP。",
    }
    return {
        "version": 2, "sample_id": sample["sample_id"], "sample_freeze_sha256": sample["freeze_sha256"],
        "source_commit": sample["source_commit"], "classification_source": sample["input_audit"]["classification_path"],
        "classification_sha256": sample["input_audit"]["classification_sha256"],
        "retrieved_at": max(x.get("retrieved_at", "") for x in (a, b, c, d, filing_map, supplement)),
        "classification_legend": {
            "A": "Verified direct Q4 EPS: SEC 原始文件有明確單季 GAAP diluted 數值。",
            "B": "Candidate: 有候選數值，但股類、符號、GAAP、期間、版本或拆股仍有疑義。",
            "C": "Derived/annual evidence only: 有年度或部分季度 EPS 線索，不以推算值填欄位。",
            "D": "Completed SEC search found no direct Q4 EPS and no usable annual/quarterly evidence.",
            "E": "Technical failure or incomplete period mapping; never treated as no disclosure.",
        },
        "period_results": periods,
        "verified_disclosures": [dict(ticker=t, fy=y, **v) for (t, y), v in DISCLOSURES.items()],
        "source_success_summary": sources,
        "network_and_volume": net,
        "outcomes": {
            "total_periods": len(periods), "missing_q4_eps_periods": len(missing),
            "verified_direct_missing_periods": len(current_direct),
            "missing_period_classifications": dict(sorted(counts.items())),
            "current_sample_direct_case_keys": current_direct,
            "current_success_rate_within_frozen_missing_periods": f"{len(current_direct)}/{len(missing)}",
            "prior_version_direct_case_count": 6,
            "prior_version_note": "舊樣本的 6/16 不適用於 main 分類檔重新凍結後的樣本；OFG 兩期退出，新增 ULBI、PCSA 期別。",
            "extrapolation_warning": "本次樣本依缺漏類別分層且只含 16 個缺漏期別；不能外推至全市場 7,755 筆。",
        },
        "positive_control_audit": controls_audit(sample),
        "fiscal_period_mismatches": {
            "confirmed_count": sum(x["status"] == "confirmed_mismatch" for x in mismatches),
            "unmatched_count": sum(x["status"] != "confirmed_mismatch" for x in mismatches),
            "rows": mismatches,
            "note": "這些 rows 僅列入診斷；本 PR 不修改 docs/report/financials。TDW local FY2017 Q4 end 2017-03-31 對應舊 3 月財年；公司另有 Nine Month Transition Period Ended 2017-12-31，完整曆年比較自 FY2018 起。本清單 confirmed_mismatch 自 FY2018 起，並另列 FY2017 transition-period gap。",
            "tdw_transition_period": {
                "local_fy2017_q4_end": "2017-03-31",
                "sec_transition_period_end": "2017-12-31",
                "sec_period_label": "Nine Month Transition Period Ended December 31, 2017",
                "evidence_accession": "0001564590-20-008191",
                "evidence_filing_date": "2020-03-02",
                "evidence_note": "The TDW 2019 10-K comparative quarterly table includes the 2017 nine-month transition period; there is no local Q4 row at 2017-12-31.",
            },
        },
        "sample_refreeze": {
            "removed_tickers": ["DUKR", "OFG", "NMTC", "TDW"],
            "added_tickers": ["PCSA", "ULBI", "PZG", "PTN"],
            "retained_unchanged_period_count": 12,
            "changed_main_reasons": [
                "OFG FY2017/FY2020 are derivation_rejected on main, not q1_q3_incomplete.",
                "DUKR FY2021 is derivation_rejected on main.",
            ],
            "prior_membership_audit": old_audit,
        },
        "verdict": "PARTIAL",
        "verdict_basis": f"Main classifier refreeze yields {len(current_direct)}/16 verified direct missing-period values. SEC 8-K/EX-99.1 adds five unique values. Two frozen periods are incomplete/mismatched; one ULBI candidate and one Grade A control have unresolved issues. This is a small stratified sample and cannot be extrapolated to the 7,755 missing rows.",
        "sol_xhigh_review": "PARTIAL，無阻擋事項。Sol 6.1 XHigh 唯讀審查核對了樣本凍結與分類、ledger 對照、來源分母與網路統計、TDW transition 期間證據，以及 POC-only diff 範圍。",
    }


def table_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def source_cell(row, key):
    s = row[key]
    if key in {"source_a_fsds", "source_b_notes"}:
        return f"{s['status']}<br>{s['parse_status']} {','.join(s['calendar_quarters']) if s.get('calendar_quarters') else ''}"
    if key == "source_c_10k":
        return f"{s['status']}<br>{', '.join(x['accession'] for x in s['filings']) or '無配對申報'}"
    filings = s.get("filings", [])
    return f"{s['status']}<br>{len(filings)} windows / {sum(len(x['documents']) for x in filings)} docs"


def make_report(results, sample):
    lines = [
        "# SEC Q4 Diluted EPS 免費資料來源 POC", "",
        "本報告使用 main 的分類檔重新凍結樣本，並重用未改期別的 SEC 回應。先列 10 檔股票與 20 個測試期別。", "",
        "| Ticker | FY/Q4 | 原本缺漏原因 | SEC ZIP | SEC Notes | 10-K | 8-K | 最終結果 | EPS |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    rows_by_ticker = {}
    for r in results["period_results"]:
        rows_by_ticker.setdefault(r["ticker"], []).append(r)
    for company in sample["sample"]:
        items = rows_by_ticker[company["ticker"]]
        reasons = company.get("missing_reason") or "main Grade A positive control"
        period_text = "<br>".join(f"FY{x['fiscal_year']} {x['frozen_q4_end']}" for x in items)
        a = "<br>".join(source_cell(x, "source_a_fsds") for x in items)
        b = "<br>".join(source_cell(x, "source_b_notes") for x in items)
        c = "<br>".join(source_cell(x, "source_c_10k") for x in items)
        d = "<br>".join(source_cell(x, "source_d_8k") for x in items)
        result = "<br>".join(f"FY{x['fiscal_year']}: {x['classification']} — {x['result']}" for x in items)
        def eps_text(x):
            if x["verified_q4_eps"] is not None:
                return str(x["verified_q4_eps"])
            candidate = x.get("unverified_candidate")
            return f"— (候選 {candidate['value']:+.2f}，未通過驗證)" if candidate else "—"
        eps = "<br>".join(f"{x['frozen_q4_end']}: {eps_text(x)}" for x in items)
        lines.append("| " + " | ".join(map(table_cell, [
            f"{company['ticker']} — {company['company_name']}<br>CIK {company['cik']}", period_text,
            reasons, a, b, c, d, result, eps,
        ])) + " |")

    out = results["outcomes"]
    lines += [
        "", "## 樣本凍結與主要結果", "",
        f"- Source commit：`{sample['source_commit']}`；seed：`{sample['selection_seed']}`；分類來源：`{sample['input_audit']['classification_path']}`。樣本 SHA-256：`{sample['freeze_sha256']}`。",
        f"- Main 分類總缺漏為 {sample['input_audit']['us_financials_missing_q4_eps']} 筆；本次固定抽樣 8 檔缺漏股、16 個期別，另 2 檔 Grade A 對照、4 個期別。分層小樣本不可外推至全市場 7,755 筆。",
        "- 重新凍結後，缺漏期別找到 **5/16** 筆直接 Q4 EPS。舊版 **6/16** 是不同樣本的結果：OFG FY2017/FY2020 在 main 分類為 `derivation_rejected` 且不再入樣，PCSA/ULBI 取代舊樣本；本報告不沿用舊分子。無論舊或新比例，都不能外推到 7,755 筆。",
        "- 分類：" + ", ".join(f"{k}={v}" for k, v in sorted(out["missing_period_classifications"].items())) + "。C 是僅年度/部分季度證據；B 是候選未通過驗證；E 是日期/身份映射不完整。沒有以年度 EPS 減前三季 EPS 補值。",
        "- 樣本變動：移除 DUKR、OFG、NMTC、TDW；新增 PCSA、ULBI、PZG、PTN。保留的六家公司中 12 個原期別未變；來源回應得以按 accession 重用。OFG FY2017/FY2020 與 DUKR FY2021 均依 main 更新分類。", "",
        "## Verified Direct Q4 EPS", "",
        "以下每筆均是 SEC 原始申報內直接披露的單季值；未用年度減前三季推算。", "",
        "| 股票 / FY | SEC 原始來源 / Accession / 申報日 | Fiscal Quarter / End Date | EPS | 原始標籤與位置 | GAAP / Diluted 判定 | 拆股基準 / 資料庫一致性 |",
        "|---|---|---|---:|---|---|---|",
    ]
    for item in results["verified_disclosures"]:
        lines.append("| " + " | ".join(map(table_cell, [
            f"{item['ticker']} FY{item['fy']}",
            f"[{item['source']}]({item['url']})<br>`{item['accession']}`; filed {item['filing_date']}",
            f"FY{item['fy']} Q4 ended {item['q4_end']}", item["eps"],
            f"{item['raw_tag'] or '無 XBRL tag；原始 HTML/表格'} — {item['location']}",
            item["gaap_diluted_basis"], f"{item['split_basis']} {item['database_match']}",
        ])) + " |")

    lines += [
        "", "## 來源成功率", "",
        "每個來源預定查 20 個樣本期別（16 個缺漏期別及 4 個 Grade A 對照）；PCSA FY2012 因目前 CIK 沒有可連結的申報 accession，故每個來源均有 1 個未完成目標、19 個完成檢查期別。此 E 不算成找不到資料或來源失敗。候選數含對照期別；唯一貢獻欄只計 16 個缺漏期別，不把對照當成新補值或重複計算。", "",
        "| 來源 | 預定目標 | 完成檢查 | 未完成 | 找到候選 | Verified Direct | 不可驗證 | 來源失敗 | 單一來源新增唯一值 | 累積唯一期別 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for x in results["source_success_summary"]:
        lines.append(f"| {x['source']} | {x['planned_targets']} | {x['completed_periods']} | {x['incomplete_periods']} | {x['found_candidate']} | {x['verified_direct']} | {x['unverifiable']} | {x['failure']} | {x['single_source_unique_missing']} | {x['cumulative_unique_missing']} |")
    lines += [
        "", "- Source A/B/C 的候選主要落在陽性對照；Notes 與原始 10-K 各提供 3 筆可與 main 對照值相符的 direct control facts。PZG FY2016 的 +0.32 與 main -0.32 衝突，列為不可驗證。這些 control 值不計為缺漏資料回補。",
        "- Source D 在缺漏樣本找到 5 筆 Verified Direct，另 ULBI FY2014 只有未標示 basic/diluted 的 +0.05 候選；PCSA FY2012 身份/歷史申報窗口不完整，列 E。Source D 是本次對缺漏列最有效來源。", "",
        "## Positive control / main ledger 稽核", "",
        "Main 現有 classifier 與 acceptance ledger 已可用。PZG、PTN 四個選中期別均由 main `epsQuality=A` 和該列 SEC Q4 fact 核對；其中 3/4 個 SEC as-filed 值一致，PZG FY2016 保留 unresolved sign conflict。PTN splitFactor=0.0008，as-filed +0.06/+0.23 調整後為目前資料 +75/+287.5；不混用拆股前後基準。", "",
        "NMTC 與 TDW 不在 main missing-classification 清單。main financial rows 中有 EPS 值，但沒有 row-level `epsQuality=A` / `secQ4EpsFacts` 可作為 Grade A 證據；ledger 對這四列沒有 blocked、overridden 或 deferred 記錄。ledger 的 `verifiedA=5009` 是總數，沒有 per-row accepted-A 清單，因此不能只由 aggregate 認定 NMTC/TDW 是 Grade A；它們不是本次陽性對照。", "",
        "| 舊控制候選 | FY / 本地 Q4 end | 本地 EPS | 在 main missing list | row-level Grade A fact | ledger blocked / overridden / deferred |",
        "|---|---|---:|---|---|---|",
        "| NMTC | FY2017 / 2017-12-31 | -0.21 | 否 | 無 | 無 / 無 / 無；ledger 只有 aggregate verifiedA=5009 |",
        "| NMTC | FY2019 / 2019-12-31 | -0.34 | 否 | 無 | 無 / 無 / 無；ledger 只有 aggregate verifiedA=5009 |",
        "| TDW | FY2018 / 2018-03-31 | -1.67 | 否 | 無 | 無 / 無 / 無；ledger 只有 aggregate verifiedA=5009 |",
        "| TDW | FY2019 / 2019-03-31 | -0.58 | 否 | 無 | 無 / 無 / 無；ledger 只有 aggregate verifiedA=5009 |", "",
        "## Fiscal period mismatch（另案處理）", "",
        "以下比對 main 本地 Q4 row end 與 SEC 同 FY 10-K report end。這是期間映射診斷，本 PR 不修改 financials。TDW 本地仍把 2017–2026 年 3 月季度標為 Q4；FY2017-03-31 對應舊 3 月財年，但公司隨後改為 12 月結帳，並有截至 2017-12-31 的九個月 transition period。可比完整曆年年報自 FY2018 起，故下表確認 end-date mismatch 自 FY2018，另將 FY2017 transition gap 列在下方。", "",
        "| 股票 | FY / Local FQ | 本地 Q4 end | SEC 10-K report end | Accession / Filing Date | 狀態 |",
        "|---|---|---|---|---|---|",
    ]
    for r in results["fiscal_period_mismatches"]["rows"]:
        evidence = f"`{r['accession']}` / {r['filing_date']}" if r.get("accession") else "—"
        lines.append(f"| {r['ticker']} | FY{r['fy']} / Q{r['local_fq']} | {r['local_q4_end']} | {r.get('sec_report_end') or '無同 FY 年報列'} | {evidence} | {r['status']} |")
    lines += [
        "", f"TDW transition-period gap：本地 FY2017 Q4 為 2017-03-31；SEC 2019 10-K 的比較季度表明列 ‘Nine Month Transition Period Ended December 31, 2017’（[accession 0001564590-20-008191](https://www.sec.gov/Archives/edgar/data/98222/000156459020008191/tdw-10k_20191231.htm)）。本地沒有 2017-12-31 的 Q4 row，這項過渡期間缺口與上表的 16 個同 FY confirmed mismatch 分開記錄。",
        "", f"確認期末不一致 {results['fiscal_period_mismatches']['confirmed_count']} 列；另 {results['fiscal_period_mismatches']['unmatched_count']} 列需要對應或等待同 FY 年報。所有 listed rows 留待獨立 financial-period 修正 PR。", "",
        "## 未解決原因", "",
        "| 原因 | 期別 | 說明 |", "|---|---|---|",
        "| SEC 搜尋範圍未找到可直接採用的 Q4 EPS | 8 | 缺漏樣本中 8 個 C 期別；只代表本次明確 10-K/8-K 範圍未找到，並非宣稱 SEC 從未披露。 |",
        "| 只有年度或部分季度 EPS | 8 | 同上 C 類；可支援資料線索但不構成 Q4 直接值。 |",
        "| 原始申報沒有可用 Q4 EPS 表 | 8 | 八個 C 期別只有年度/部分季度資料，或 Q4 表是股價資訊；沒有可用的直接 Q4 diluted EPS 列。這是搜尋範圍內的結果，不等於 SEC 歷史上從未披露。 |",
        "| 有候選但 basic/diluted 或符號無法排除 | 2 | ULBI FY2014 未標示 basic/diluted；PZG FY2016 Notes/表格 +0.32 與 main Grade A -0.32 衝突。 |",
        "| 公司/歷史 CIK 對應不完整 | 1 | PCSA FY2012 沒有可連至目前 CIK 的 SEC filing accession；E，非 D。 |",
        "| 期間日期不匹配 | 1 frozen target + 16 fiscal rows | BNC FY2024 的凍結期末與年報期末不同；另見上方獨立 mismatch 列表。 |",
        "| 解析器 / OCR 技術失敗 | 0 final unresolved | 本次 10-K HTML 已解析，8-K 的影像附件均有 HTTP 200，OCR sidecar hash 對應且完成；兩個 initial diagnostics 已清除。 |",
        "| 最終網路 / 權限失敗 | 0 | 最終 artifact 記錄的 SEC 回應為 HTTP 200；PCSA FY2012 是歷史 mapping 範圍不完整，不是 403/429 或網路失敗。 |", "",
        "## 執行量與重現", "",
        f"- SEC 節流上限 4 requests/second。Source A/B 各有 {results['network_and_volume']['current_sample_unique_archive_payload_bytes']['source_a_fsds']:,}/{results['network_and_volume']['current_sample_unique_archive_payload_bytes']['source_b_notes']:,} bytes 的 16 個樣本 ZIP 引用資料；這是含重用回應的獨立 ZIP payload 大小，不代表本次下載量。大型原始 ZIP 僅暫存，未納入 Git。",
        f"- 從前一版 POC artifact 到重凍結資料，按可對照的來源計數器記錄 {results['network_and_volume']['refresh_delta_since_previous_poc_artifacts']['requests']} 次新增 SEC request、{results['network_and_volume']['refresh_delta_since_previous_poc_artifacts']['wire_bytes']:,} wire bytes；來源細項見 `results.json`。Source A/B 的 request/bytes 是舊版與新版 artifact 累積計數差；其 last-invocation 欄位另列，不能與累積差再相加。前一版曾報 406 requests 及 11,190,612,664 bytes；其 bytes 含一個 Notes ZIP 的快取 payload，因此不作為精確線上傳輸基準，也未與本次增量合計。",
        "- 官方端點文件在首次測試取得；本次報告重建不發 SEC request。Source A/B 的 ZIP 及 accession 可重用；Source C/D 只查樣本相關申報。SEC 回應遵守 4 requests/second 上限，使用 User-Agent、快取與退避；未呼叫 Massive API、未使用付費資料或爬蟲，也未輸出任何 API key。",
        "- SEC 收集約在樣本凍結後 21 分鐘完成（不含後續 report/test）。各來源 request、bytes、cache hit、HTTP status 和 parse status 在 `results.json` / `source-ledger.jsonl` 保存。",
        "- 重建報告：`python scripts/render_sec_q4_eps_poc_report.py`。POC 測試：`python -m unittest discover -s tests -p 'test_sec_q4_eps_poc.py' -v`。完整 Python 測試：`python -m unittest discover -s tests -v`；Atlas 測試：`cd atlas && npm test`。", "",
        "## 可行性結論", "",
        "1. **重新凍結的 16 個缺漏期別中，找到 5 筆** Verified Direct Q4 Diluted EPS。先前 6/16 的樣本已因 main 分類更新而改變；此分層小樣本不能外推到 7,755 筆。",
        "2. **原始 SEC 8-K / EX-99.1 最有效**：新增 5 個唯一缺漏期別；資料集和 10-K 對現有控制較有利，但未增加缺漏列的 Verified Direct 值。",
        "3. 瓶頸同時來自直接揭露不足和解析/分類能力：8-K 的五筆直接值證明部分數值存在於原始公告；但另外 8 個缺漏期別只見年度/部分季度線索，1 個有 basic/diluted 不明候選，另有 2 個 period/歷史 CIK mapping 不完整。不能把後者全部歸因於 SEC 未公布或 parser 不足。",
        "4. **值得做 50–100 檔下一階段測試**，但先用 SEC 10-K report date 矯正 FY/Q4 對應、解決 PZG control 符號差異，並保留對來源窗口與公司歷史 CIK 的檢查。10 檔樣本是按缺漏理由分層後選出，沒有代表性抽樣設計，不可把 5/16 或舊 6/16 外推到 7,755 筆。", "",
        "**Verdict: PARTIAL** — 五筆直接值完成來源驗證；一筆候選與一筆 control 仍有疑義；PCSA FY2012 與 BNC FY2024 期別搜尋不完整/不匹配，另有獨立 local fiscal-period 對應問題。", "",
        f"Sol XHigh 審核：{results['sol_xhigh_review']}", "",
        "## 證據檔案", "",
        "- `sample.json`：main classification / ledger 輸入、固定 seed、抽樣條件、frozen hash。",
        "- `results.json`：逐期 adjudication、來源狀態、control audit、期間 mismatch 與 network metrics。",
        "- `source-ledger.jsonl`：來源 URL、accession、HTTP、bytes、hash、ZIP parsing、OCR hash 與 adjudication。",
        "- `official-docs.json`、`filing-map.json`、`source-a.json`、`source-b.json`、`source-c.json`、`source-d.json`、`source-d-ocr.json`：SEC 原始來源索引/解析 metadata；大型 ZIP 未提交。",
        "- `positive-control-pzg-2017-source.json`：PZG 後續 10-K 比較來源，未能消除 FY2016 的 EPS 符號衝突。",
    ]
    return "\n".join(lines) + "\n"


def build_ledger(results, sample, sources):
    lines = []
    def add(value):
        lines.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    for path, data in [("official-docs.json", read_json("official-docs.json")),
                       ("filing-map.json", read_json("filing-map.json"))]:
        if path == "official-docs.json":
            for name, item in data.get("docs", {}).items():
                add({"record_type": "official_documentation", "source_id": name, "url": item.get("url"),
                     "http_status": item.get("actual_http_status", item.get("status")),
                     "retrieved_at": item.get("retrieved_at"), "bytes": item.get("wire_bytes", item.get("bytes")),
                     "sha256": item.get("sha256")})
        else:
            for issuer in data.get("issuers", []):
                add({"record_type": "sec_submissions", "tickers": issuer.get("tickers"), "cik": issuer.get("cik"),
                     "url": issuer.get("url"), "http_status": issuer.get("http_status"),
                     "retrieved_at": issuer.get("retrieved_at"), "response_bytes": issuer.get("response_bytes"),
                     "cache_hit": issuer.get("cache_hit"), "error": issuer.get("error")})
    for source_id, source in (("A_FSDS", read_json("source-a.json")), ("B_NOTES", read_json("source-b.json"))):
        for quarter, item in source.get("zips", {}).items():
            add({"record_type": "dataset_zip", "source_id": source_id, "filing_calendar_quarter": quarter,
                 "url": item.get("url"), "http_status": item.get("http_status"), "retrieved_at": item.get("retrieved_at"),
                 "zip_bytes": item.get("wire_bytes"), "sha256": item.get("sha256"),
                 "parse_status": item.get("parse_status"), "members": item.get("members"),
                 "row_counts": item.get("row_counts"), "target_accessions": item.get("target_accessions", [])})
        for accession, item in all_records(source).items():
            meta = item.get("metadata", {})
            add({"record_type": "dataset_accession", "source_id": source_id, "accession": accession,
                 "ticker": meta.get("ticker"), "cik": meta.get("cik"), "form": meta.get("form"),
                 "filing_date": meta.get("filing_date"), "report_date": meta.get("report_date"),
                 "dataset_lookup_status": item.get("dataset_status"),
                 "eps_candidate_count": len(item.get("eps_candidates", [])),
                 "note_text_candidate_count": len(item.get("note_text_candidates", []))})
    c = read_json("source-c.json")
    for accession, item in all_records(c).items():
        meta = item.get("metadata", {})
        add({"record_type": "sec_10k", "accession": accession, "ticker": meta.get("ticker"), "cik": meta.get("cik"),
             "fy": meta.get("fy"), "form": meta.get("form"), "filing_date": meta.get("filing_date"),
             "report_date": meta.get("report_date"), "url": item.get("url"), "http_status": item.get("http_status"),
             "parse_status": item.get("parse_status"), "bytes": item.get("wire_bytes"), "sha256": item.get("sha256"),
             "eps_fact_count": len(item.get("eps_facts", [])), "eps_table_count": len(item.get("eps_tables", [])),
             "quarterly_section_excerpt_count": len(item.get("quarterly_section_excerpts", []))})
    d, ocr = read_json("source-d.json"), read_json("source-d-ocr.json")
    ocr_by_url = {x.get("url"): x for x in ocr}
    for accession, item in d.get("filings", {}).items():
        meta = item.get("metadata", {})
        add({"record_type": "sec_8k_index", "accession": accession, "tickers": meta.get("tickers"),
             "cik": meta.get("cik"), "form": meta.get("form"), "filing_date": meta.get("filing_date"),
             "url": item.get("index_url"), "http_status": item.get("index_http_status"),
             "bytes": item.get("index_wire_bytes"), "sha256": item.get("index_sha256"),
             "status": item.get("status"), "period_windows": meta.get("period_windows", [])})
        for doc in item.get("documents", []):
            o = ocr_by_url.get(doc.get("url"), {})
            add({"record_type": "sec_8k_document", "accession": accession, "name": doc.get("name"),
                 "url": doc.get("url"), "description": doc.get("description"), "http_status": doc.get("http_status"),
                 "parse_status": doc.get("parse_status"), "bytes": doc.get("wire_bytes"), "sha256": doc.get("sha256"),
                 "eps_fact_count": len(doc.get("eps_facts", [])), "eps_table_count": len(doc.get("eps_tables", [])),
                 "ocr_status": o.get("ocr_status"),
                 "ocr_sha256_verified": (o.get("sha256") == doc.get("sha256")) if o else None,
                 "ocr_text_characters": len(o.get("ocr_text", "")) if o else None})
    supplement = read_json("positive-control-pzg-2017-source.json")
    add({"record_type": "positive_control_supplement", "ticker": "PZG", "url": supplement.get("url"),
         "accession": "0001564590-17-018742", "filing_date": "2017-09-18", "http_status": supplement.get("http_status"),
         "bytes": supplement.get("wire_bytes"), "sha256": supplement.get("sha256"),
         "parse_status": supplement.get("parse_status"),
         "note": "Comparative 10-K prints positive magnitude +0.32 under ‘Net loss per Common Share’; sign conflict with main FY2016 Grade A -0.32 remains."})
    for disclosure in results["verified_disclosures"]:
        add({"record_type": "adjudicated_verified_q4_eps", **disclosure})
    for row in results["period_results"]:
        add({"record_type": "period_adjudication", "ticker": row["ticker"], "fy": row["fiscal_year"],
             "q4_end": row["frozen_q4_end"], "sample_role": row["sample_role"],
             "classification": row["classification"], "eps": row["verified_q4_eps"], "result": row["result"]})
    for mismatch in results["fiscal_period_mismatches"]["rows"]:
        add({"record_type": "fiscal_period_mismatch", **mismatch})
    return "\n".join(lines) + "\n"


def main():
    sample = read_json("sample.json")
    a, b, c, d, ocr = (read_json(name) for name in
                       ("source-a.json", "source-b.json", "source-c.json", "source-d.json", "source-d-ocr.json"))
    filing_map, official = read_json("filing-map.json"), read_json("official-docs.json")
    supplement = read_json("positive-control-pzg-2017-source.json")
    results = build_results(sample, a, b, c, d, ocr, filing_map, official, supplement)
    write_json("results.json", results)
    (REPORT / "source-ledger.jsonl").write_text(build_ledger(results, sample, (a, b, c, d)), encoding="utf-8")
    (REPORT / "feasibility-report.md").write_text(make_report(results, sample), encoding="utf-8")
    print(f"Rendered {len(results['period_results'])} periods; {results['outcomes']['verified_direct_missing_periods']}/16 verified missing-period EPS.")


if __name__ == "__main__":
    main()
