# US market financial-history backfill

As of 2026-10-15, the 15-calendar-year cutoff is 2011-10-15.
The frozen NasdaqTrader snapshot contains 5,347 directory-filtered symbols across 5,289 SEC-mapped CIK groups. It was fetched at 2026-10-15T05:41:29Z and has source-pair SHA-256 `e730480bf89129e5a649e4ddec68b992655fed757df8d9438c0f0ca1947742ba`.
There are currently 2,406 financial files among those listing symbols: 2,366 SEC-backed and 40 Yahoo-only. The whole cohort is not yet financially refreshed.

## Listing and issuer screening

The NasdaqTrader filters accepted 5,347 unique listing rows and excluded 7,940 rows. The 506 SPAC-name matches are a string heuristic, separate from confirmed blank-check issuers.
SEC submissions confirmed 49 SIC 6770 blank-check issuers and identified 2,419 active-directory operating candidates. 33 issuer decisions remain unknown or failed. An SIC other than 6770 is a queueing basis and does not certify legal non-shell status; entityType alone is not treated as shell evidence.
The current resume queue has 2,206 new issuer groups pending and 583 existing SEC-seeded issuer groups still awaiting a submissions screen. Existing financial files remain available while that metadata queue is pending.
Financial index reconciliation pending for 0 symbol files.

## Current batch and resume

Batch 5 (financial) attempted 500 issuer groups; status: completed. SEC requests this batch: 978; raw SEC bytes received: 67,576,861.
The batch checkpoint has 2206 new issuer groups pending and 583 existing metadata screens pending.
Split-check recheck for batch 2: stopped_split_unavailable; 1/174 symbols checked, 0 chart responses, 1 chart failures, 0 split events recorded, 0 files adjusted, 174 still pending, and 0 SEC wire requests. Stop reason: Yahoo connection failed: SplitUnavailable: curl_cffi is not installed (pip install curl_cffi).
Split-check recheck for batch 2: completed; 174/174 symbols checked, 174 chart responses, 0 chart failures, 236 split events recorded, 74 files adjusted, 0 still pending, and 0 SEC wire requests. Interpreter: `/tmp/tw-mops-backfill-venv/bin/python`.

Run `python atlas/scripts/backfill_financials_us_market.py --resume --batch-size 500` to continue the next stable batch. Add `--retry` only after reviewing failure and unknown-metadata decisions. Each resume fetches both current NasdaqTrader directories again and records any frozen symbol that is no longer present as `not_current_directory`; that label does not assert legal delisting.

## Financial metric coverage

These are observed financial-file rows across the full frozen listing cohort. They do not imply every issuer has 15 years of history or every metric input. Target counts require at least 15 annual or 60 quarterly rows; cost of revenue keeps raw reported and display-only derived values separate.

| Window | Period type | Rows | Symbols with target rows | Symbols contiguous at target | Symbols complete on all 7 metrics | Target |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Calendar cutoff | annual | 27,828 | 1,194 | 1,150 | 305 | 15 |
| Calendar cutoff | quarterly | 108,574 | 108 | 108 | 18 | 60 |
| Latest records | annual | 27,953 | 1,320 | 1,248 | 351 | 15 |
| Latest records | quarterly | 110,058 | 1,270 | 1,146 | 101 | 60 |

### Calendar-window metric cells

| Period | Metric | Available | Raw reported | Derived display | Missing | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| annual | eps | 26,723 | — | — | 1,105 | 96.03% |
| annual | revenue | 23,658 | — | — | 4,170 | 85.02% |
| annual | costOfRevenue | 15,195 | 13884 | 1311 | 12,633 | 54.60% |
| annual | nonOperatingIncome | 19,539 | — | — | 8,289 | 70.21% |
| annual | grossMargin | 15,012 | — | — | 12,816 | 53.95% |
| annual | operatingIncome | 22,012 | — | — | 5,816 | 79.10% |
| annual | netMargin | 23,131 | — | — | 4,697 | 83.12% |
| quarterly | eps | 100,403 | — | — | 8,171 | 92.47% |
| quarterly | revenue | 89,923 | — | — | 18,651 | 82.82% |
| quarterly | costOfRevenue | 57,098 | 51434 | 5664 | 51,476 | 52.59% |
| quarterly | nonOperatingIncome | 70,716 | — | — | 37,858 | 65.13% |
| quarterly | grossMargin | 56,185 | — | — | 52,389 | 51.75% |
| quarterly | operatingIncome | 84,346 | — | — | 24,228 | 77.69% |
| quarterly | netMargin | 87,109 | — | — | 21,465 | 80.23% |

The seven fields follow the existing coverage reporter and financial display rules: EPS, revenue, cost of revenue, non-operating income, gross margin, operating income, and net margin. Derived cost of revenue is a report-only revenue-minus-gross-profit calculation; source financial JSON is not rewritten with that estimate.

Machine-readable report: `docs/report/fundamentals/us-market-backfill.json`. The immutable listing snapshot and per-issuer checkpoints are `docs/report/fundamentals/us-market-snapshot.json` and `docs/report/fundamentals/us-market-progress.json`. The SEC raw-body cache is `/tmp/us-15y-sec-raw (cache loss causes re-fetch; progress and snapshot are committed)`; losing it causes a paced refetch, while queue progress remains committed.
