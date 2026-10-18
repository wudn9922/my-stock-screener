# SEC Q4 Diluted EPS POC

This is a read-only, 10-ticker proof of concept against SEC EDGAR and the two official SEC financial data-set families. It tests whether those free sources can expose direct, as-filed GAAP diluted EPS for the frozen Q4 periods in `sample.json`. It does not backfill production data.

## Findings

- The frozen sample contains 16 missing Q4 EPS periods and 4 positive-control candidates. The original classifier and acceptance ledger are absent from `main`, so the two control tickers cannot be certified as historical Grade A.
- Six direct Q4 diluted EPS values were verified in the 16 frozen missing periods: ASTC FY2014 `-0.04`, FGC FY2016 `+0.27`, OFG FY2017 `+0.30`, OFG FY2020 `+0.42`, FGC FY2022 `-0.03`, and WRAP FY2022 `-0.09`.
- FSDS and Notes data sets produced no verified direct Q4 EPS in this sample. Their two diluted EPS candidates were TDW facts for March 31, which the original filings identify as Q1 because TDW's fiscal year ends December 31.
- Original 10-K/10-K/A records contributed two direct values (OFG FY2017 and FY2020). Source D found six direct period disclosures, independently corroborated both OFG periods, and added four unique periods (ASTC FY2014, FGC FY2016 and FY2022, WRAP FY2022).
- The outcome is **PARTIAL**. See `feasibility-report.md` for the required tables, failure categories, selection limitations, and next-step recommendation.

## Frozen sample and source evidence

`sample.json` is frozen before any SEC submissions, quarterly ZIP, 10-K, or 8-K was queried. Its seed, SHA-256 rank, CIKs, local Q4 dates, missing reasons, source-file checksums, and input-audit limitations are embedded in the file. The four Q4 date / SEC report-date mismatches stay in the sample and are classified E; none was replaced after retrieval.

The requested files `docs/report/q4-eps-missing-classification.json`, `docs/report/q4-eps-acceptance-ledger.json`, and `atlas/scripts/classify_q4_eps_missing.py` are not present on `main`. The POC therefore made a deterministic temporary classification from the current local financial files, and its positive selections are labeled candidates rather than historical Grade A facts. Its missing-Q4 count is 7,831 across all local U.S. sources and years, versus the roughly 7,755 stated in the task; the SEC-sourced FY2012–2025 subset is 7,404. This difference cannot be reconciled without the missing original artifacts.

`source-ledger.jsonl` records source URLs, filing accessions, status, filing dates, response sizes, hashes, parser outcomes, and the direct EPS adjudications. `results.json` has a row for each frozen ticker/FY. The `source-a*.json`, `source-b*.json`, `source-c*.json`, and `source-d*.json` files keep the detailed matched facts, tables, text excerpts, filing windows, and diagnostic runs.

## Re-run

The retrieval commands below make live SEC requests and reproduce the frozen sample's evidence windows. They may download the 38 selected whole-market ZIPs (about 11.17 GB total), one at a time. The report renderer uses the committed frozen sample and applies the adjudication rules documented in the script; it does not rewrite production data.

```sh
python scripts/sec_q4_eps_poc.py fetch-docs --out docs/report/sec-q4-eps-poc/official-docs.json
python scripts/sec_q4_eps_poc.py resolve-submissions --sample docs/report/sec-q4-eps-poc/sample.json --out docs/report/sec-q4-eps-poc/filing-map.json
python scripts/sec_q4_eps_poc.py source-a --filing-map docs/report/sec-q4-eps-poc/filing-map.json --out docs/report/sec-q4-eps-poc/source-a.json
python scripts/sec_q4_eps_poc.py source-b --filing-map docs/report/sec-q4-eps-poc/filing-map.json --out docs/report/sec-q4-eps-poc/source-b.json
python scripts/sec_q4_eps_poc.py source-c --filing-map docs/report/sec-q4-eps-poc/filing-map.json --out docs/report/sec-q4-eps-poc/source-c.json
python scripts/sec_q4_eps_poc.py source-d --filing-map docs/report/sec-q4-eps-poc/filing-map.json --out docs/report/sec-q4-eps-poc/source-d.json
python scripts/ocr_sec_q4_eps_poc_assets.py --source-d docs/report/sec-q4-eps-poc/source-d.json --out docs/report/sec-q4-eps-poc/source-d-ocr.json
python scripts/render_sec_q4_eps_poc_report.py
```

The SEC client uses an explicit User-Agent, a maximum 4-request/second pacing interval, `Retry-After`, bounded exponential backoff, a local response cache, and an SEC host allowlist. Final unique URLs all returned HTTP 200. Two intermediate retry response statuses from this run were not retained, so this report does not claim those attempts had no 403/429 response.

SEC FSDS and Notes ZIPs are whole-market archives by design, not ticker-specific downloads. This run downloaded only the 19 relevant filing-calendar-quarter ZIPs for each family, one at a time; it did not fetch all available historical quarters. The parser retained only the 10 sample CIKs and their filing accessions. The 38 ZIPs totaled 11,166,993,468 bytes and are not committed. `source-d-ocr.json` reuses cached, SHA-256-verified image bodies; all 64 images were OCR reviewed with Tesseract 5.5.0. The image bodies themselves are not committed.

The SEC directory's filing calendar quarter is not an issuer's fiscal quarter. Always compare accession/CIK, fiscal report date, XBRL context dates, duration, tag, unit, dimensions, and original filing text. A `Fourth Quarter` heading alone may refer to stock-price High/Low data; an exact date match alone may still be TDW Q1. The POC does not create a Q4 value by subtracting Q1–Q3 EPS from annual EPS.

## Tests and report rendering

```sh
python -m unittest discover -s tests -p 'test_sec_q4_eps_poc.py' -v
python scripts/render_sec_q4_eps_poc_report.py
```

Fixtures under `tests/fixtures/sec_q4_eps_poc/` contain partial normalized excerpts from real SEC filings, with the official source URL, accession number, filing date, source location, and fiscal context. They do not vendor the original SEC documents. The original classifier and Grade A acceptance ledger were not present on `main`, so NMTC and TDW remain positive-control candidates; the original Grade A labels could not be independently verified.

## Safety boundary

The run used SEC's public free resources only. It did not use Massive, a crawler, or an API key. It did not write production DB rows, alter `docs/report/financials/*.json`, change growth/TTM/P-E/front-end logic, or modify the frozen S3/S4 process. The repo dry-run repair command remained read-only. This branch is intended for review; it should not be merged as a production backfill without a separately validated, broader study.
