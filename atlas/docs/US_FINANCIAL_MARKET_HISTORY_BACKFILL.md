# US market financial-history backfill

As of 2026-10-15, the 15-calendar-year cutoff is 2011-10-15.
The frozen NasdaqTrader snapshot contains 5,347 directory-filtered symbols across 5,289 SEC-mapped CIK groups. It was fetched at 2026-10-15T05:41:29Z and has source-pair SHA-256 `e730480bf89129e5a649e4ddec68b992655fed757df8d9438c0f0ca1947742ba`.
There are currently 1,345 financial files among those listing symbols: 1,305 SEC-backed and 40 Yahoo-only. The whole cohort is not yet financially refreshed.

## Listing and issuer screening

The NasdaqTrader filters accepted 5,347 unique listing rows and excluded 7,940 rows. The 506 SPAC-name matches are a string heuristic, separate from confirmed blank-check issuers.
SEC submissions confirmed 23 SIC 6770 blank-check issuers and identified 965 active-directory operating candidates. 12 issuer decisions remain unknown or failed. An SIC other than 6770 is a queueing basis and does not certify legal non-shell status; entityType alone is not treated as shell evidence.
The current resume queue has 3,706 new issuer groups pending and 583 existing SEC-seeded issuer groups still awaiting a submissions screen. Existing financial files remain available while that metadata queue is pending.
Financial index reconciliation pending for 0 symbol files.

## Current batch and resume

Batch 2 (financial) attempted 500 issuer groups; status: completed. SEC requests this batch: 972; raw SEC bytes received: 68,346,580.
The batch checkpoint has 3706 new issuer groups pending and 583 existing metadata screens pending.
Split-check recheck for batch 2: stopped_split_unavailable; 1/174 symbols checked, 0 chart responses, 1 chart failures, 0 split events recorded, 0 files adjusted, 174 still pending, and 0 SEC wire requests. Stop reason: Yahoo connection failed: SplitUnavailable: curl_cffi is not installed (pip install curl_cffi).
Split-check recheck for batch 2: completed; 174/174 symbols checked, 174 chart responses, 0 chart failures, 236 split events recorded, 74 files adjusted, 0 still pending, and 0 SEC wire requests. Interpreter: `/tmp/tw-mops-backfill-venv/bin/python`.

Run `python atlas/scripts/backfill_financials_us_market.py --resume --batch-size 500` to continue the next stable batch. Add `--retry` only after reviewing failure and unknown-metadata decisions. Each resume fetches both current NasdaqTrader directories again and records any frozen symbol that is no longer present as `not_current_directory`; that label does not assert legal delisting.

## Financial metric coverage

These are observed financial-file rows across the full frozen listing cohort. They do not imply every issuer has 15 years of history or every metric input. Target counts require at least 15 annual or 60 quarterly rows; cost of revenue keeps raw reported and display-only derived values separate.

| Window | Period type | Rows | Symbols with target rows | Symbols contiguous at target | Symbols complete on all 7 metrics | Target |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Calendar cutoff | annual | 15,931 | 727 | 703 | 197 | 15 |
| Calendar cutoff | quarterly | 61,877 | 74 | 74 | 10 | 60 |
| Latest records | annual | 15,991 | 788 | 753 | 225 | 15 |
| Latest records | quarterly | 62,711 | 760 | 699 | 63 | 60 |

### Calendar-window metric cells

| Period | Metric | Available | Raw reported | Derived display | Missing | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| annual | eps | 15,402 | — | — | 529 | 96.68% |
| annual | revenue | 13,972 | — | — | 1,959 | 87.70% |
| annual | costOfRevenue | 8,940 | 8206 | 734 | 6,991 | 56.12% |
| annual | nonOperatingIncome | 11,516 | — | — | 4,415 | 72.29% |
| annual | grossMargin | 8,839 | — | — | 7,092 | 55.48% |
| annual | operatingIncome | 12,707 | — | — | 3,224 | 79.76% |
| annual | netMargin | 13,661 | — | — | 2,270 | 85.75% |
| quarterly | eps | 57,875 | — | — | 4,002 | 93.53% |
| quarterly | revenue | 53,023 | — | — | 8,854 | 85.69% |
| quarterly | costOfRevenue | 33,514 | 30352 | 3162 | 28,363 | 54.16% |
| quarterly | nonOperatingIncome | 42,022 | — | — | 19,855 | 67.91% |
| quarterly | grossMargin | 33,026 | — | — | 28,851 | 53.37% |
| quarterly | operatingIncome | 48,433 | — | — | 13,444 | 78.27% |
| quarterly | netMargin | 51,322 | — | — | 10,555 | 82.94% |

The seven fields follow the existing coverage reporter and financial display rules: EPS, revenue, cost of revenue, non-operating income, gross margin, operating income, and net margin. Derived cost of revenue is a report-only revenue-minus-gross-profit calculation; source financial JSON is not rewritten with that estimate.

Machine-readable report: `docs/report/fundamentals/us-market-backfill.json`. The immutable listing snapshot and per-issuer checkpoints are `docs/report/fundamentals/us-market-snapshot.json` and `docs/report/fundamentals/us-market-progress.json`. The SEC raw-body cache is `/tmp/us-15y-sec-raw (cache loss causes re-fetch; progress and snapshot are committed)`; losing it causes a paced refetch, while queue progress remains committed.
