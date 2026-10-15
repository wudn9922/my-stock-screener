# US market financial-history backfill

As of 2026-10-15, the 15-calendar-year cutoff is 2011-10-15.
The frozen NasdaqTrader snapshot contains 5,347 directory-filtered symbols across 5,289 SEC-mapped CIK groups. It was fetched at 2026-10-15T05:41:29Z and has source-pair SHA-256 `e730480bf89129e5a649e4ddec68b992655fed757df8d9438c0f0ca1947742ba`.
There are currently 2,759 financial files among those listing symbols: 2,719 SEC-backed and 40 Yahoo-only. The whole cohort is not yet financially refreshed.

## Listing and issuer screening

The NasdaqTrader filters accepted 5,347 unique listing rows and excluded 7,940 rows. The 506 SPAC-name matches are a string heuristic, separate from confirmed blank-check issuers.
SEC submissions confirmed 56 SIC 6770 blank-check issuers and identified 2,907 active-directory operating candidates. 38 issuer decisions remain unknown or failed. An SIC other than 6770 is a queueing basis and does not certify legal non-shell status; entityType alone is not treated as shell evidence.
The current resume queue has 1,706 new issuer groups pending and 583 existing SEC-seeded issuer groups still awaiting a submissions screen. Existing financial files remain available while that metadata queue is pending.
Financial index reconciliation pending for 0 symbol files.

## Current batch and resume

Batch 6 (financial) attempted 500 issuer groups; status: completed. SEC requests this batch: 983; raw SEC bytes received: 67,927,473.
The batch checkpoint has 1706 new issuer groups pending and 583 existing metadata screens pending.
Split-check recheck for batch 2: stopped_split_unavailable; 1/174 symbols checked, 0 chart responses, 1 chart failures, 0 split events recorded, 0 files adjusted, 174 still pending, and 0 SEC wire requests. Stop reason: Yahoo connection failed: SplitUnavailable: curl_cffi is not installed (pip install curl_cffi).
Split-check recheck for batch 2: completed; 174/174 symbols checked, 174 chart responses, 0 chart failures, 236 split events recorded, 74 files adjusted, 0 still pending, and 0 SEC wire requests. Interpreter: `/tmp/tw-mops-backfill-venv/bin/python`.

Run `python atlas/scripts/backfill_financials_us_market.py --resume --batch-size 500` to continue the next stable batch. Add `--retry` only after reviewing failure and unknown-metadata decisions. Each resume fetches both current NasdaqTrader directories again and records any frozen symbol that is no longer present as `not_current_directory`; that label does not assert legal delisting.

## Financial metric coverage

These are observed financial-file rows across the full frozen listing cohort. They do not imply every issuer has 15 years of history or every metric input. Target counts require at least 15 annual or 60 quarterly rows; cost of revenue keeps raw reported and display-only derived values separate.

| Window | Period type | Rows | Symbols with target rows | Symbols contiguous at target | Symbols complete on all 7 metrics | Target |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Calendar cutoff | annual | 31,779 | 1,357 | 1,301 | 356 | 15 |
| Calendar cutoff | quarterly | 124,090 | 116 | 116 | 19 | 60 |
| Latest records | annual | 31,917 | 1,501 | 1,414 | 407 | 15 |
| Latest records | quarterly | 125,783 | 1,443 | 1,295 | 116 | 60 |

### Calendar-window metric cells

| Period | Metric | Available | Raw reported | Derived display | Missing | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| annual | eps | 30,479 | — | — | 1,300 | 95.91% |
| annual | revenue | 26,905 | — | — | 4,874 | 84.66% |
| annual | costOfRevenue | 17,355 | 15802 | 1553 | 14,424 | 54.61% |
| annual | nonOperatingIncome | 22,375 | — | — | 9,404 | 70.41% |
| annual | grossMargin | 17,140 | — | — | 14,639 | 53.93% |
| annual | operatingIncome | 25,298 | — | — | 6,481 | 79.61% |
| annual | netMargin | 26,203 | — | — | 5,576 | 82.45% |
| quarterly | eps | 114,611 | — | — | 9,479 | 92.36% |
| quarterly | revenue | 102,263 | — | — | 21,827 | 82.41% |
| quarterly | costOfRevenue | 65,217 | 58542 | 6675 | 58,873 | 52.56% |
| quarterly | nonOperatingIncome | 80,876 | — | — | 43,214 | 65.18% |
| quarterly | grossMargin | 64,126 | — | — | 59,964 | 51.68% |
| quarterly | operatingIncome | 97,008 | — | — | 27,082 | 78.18% |
| quarterly | netMargin | 98,712 | — | — | 25,378 | 79.55% |

The seven fields follow the existing coverage reporter and financial display rules: EPS, revenue, cost of revenue, non-operating income, gross margin, operating income, and net margin. Derived cost of revenue is a report-only revenue-minus-gross-profit calculation; source financial JSON is not rewritten with that estimate.

Machine-readable report: `docs/report/fundamentals/us-market-backfill.json`. The immutable listing snapshot and per-issuer checkpoints are `docs/report/fundamentals/us-market-snapshot.json` and `docs/report/fundamentals/us-market-progress.json`. The SEC raw-body cache is `/tmp/us-15y-sec-raw (cache loss causes re-fetch; progress and snapshot are committed)`; losing it causes a paced refetch, while queue progress remains committed.
