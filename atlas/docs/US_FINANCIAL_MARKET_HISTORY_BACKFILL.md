# US market financial-history backfill

As of 2026-10-15, the 15-calendar-year cutoff is 2011-10-15.
The frozen NasdaqTrader snapshot contains 5,347 directory-filtered symbols across 5,289 SEC-mapped CIK groups. It was fetched at 2026-10-15T05:41:29Z and has source-pair SHA-256 `e730480bf89129e5a649e4ddec68b992655fed757df8d9438c0f0ca1947742ba`.
There are currently 2,057 financial files among those listing symbols: 2,017 SEC-backed and 40 Yahoo-only. The whole cohort is not yet financially refreshed.

## Listing and issuer screening

The NasdaqTrader filters accepted 5,347 unique listing rows and excluded 7,940 rows. The 506 SPAC-name matches are a string heuristic, separate from confirmed blank-check issuers.
SEC submissions confirmed 39 SIC 6770 blank-check issuers and identified 1,936 active-directory operating candidates. 25 issuer decisions remain unknown or failed. An SIC other than 6770 is a queueing basis and does not certify legal non-shell status; entityType alone is not treated as shell evidence.
The current resume queue has 2,706 new issuer groups pending and 583 existing SEC-seeded issuer groups still awaiting a submissions screen. Existing financial files remain available while that metadata queue is pending.
Financial index reconciliation pending for 0 symbol files.

## Current batch and resume

Batch 4 (financial) attempted 500 issuer groups; status: completed. SEC requests this batch: 975; raw SEC bytes received: 73,582,372.
The batch checkpoint has 2706 new issuer groups pending and 583 existing metadata screens pending.
Split-check recheck for batch 2: stopped_split_unavailable; 1/174 symbols checked, 0 chart responses, 1 chart failures, 0 split events recorded, 0 files adjusted, 174 still pending, and 0 SEC wire requests. Stop reason: Yahoo connection failed: SplitUnavailable: curl_cffi is not installed (pip install curl_cffi).
Split-check recheck for batch 2: completed; 174/174 symbols checked, 174 chart responses, 0 chart failures, 236 split events recorded, 74 files adjusted, 0 still pending, and 0 SEC wire requests. Interpreter: `/tmp/tw-mops-backfill-venv/bin/python`.

Run `python atlas/scripts/backfill_financials_us_market.py --resume --batch-size 500` to continue the next stable batch. Add `--retry` only after reviewing failure and unknown-metadata decisions. Each resume fetches both current NasdaqTrader directories again and records any frozen symbol that is no longer present as `not_current_directory`; that label does not assert legal delisting.

## Financial metric coverage

These are observed financial-file rows across the full frozen listing cohort. They do not imply every issuer has 15 years of history or every metric input. Target counts require at least 15 annual or 60 quarterly rows; cost of revenue keeps raw reported and display-only derived values separate.

| Window | Period type | Rows | Symbols with target rows | Symbols contiguous at target | Symbols complete on all 7 metrics | Target |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Calendar cutoff | annual | 23,963 | 1,053 | 1,016 | 271 | 15 |
| Calendar cutoff | quarterly | 93,386 | 96 | 96 | 15 | 60 |
| Latest records | annual | 24,061 | 1,154 | 1,094 | 311 | 15 |
| Latest records | quarterly | 94,641 | 1,111 | 1,008 | 89 | 60 |

### Calendar-window metric cells

| Period | Metric | Available | Raw reported | Derived display | Missing | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| annual | eps | 23,010 | — | — | 953 | 96.02% |
| annual | revenue | 20,425 | — | — | 3,538 | 85.24% |
| annual | costOfRevenue | 13,013 | 11894 | 1119 | 10,950 | 54.30% |
| annual | nonOperatingIncome | 16,777 | — | — | 7,186 | 70.01% |
| annual | grossMargin | 12,848 | — | — | 11,115 | 53.62% |
| annual | operatingIncome | 18,806 | — | — | 5,157 | 78.48% |
| annual | netMargin | 19,979 | — | — | 3,984 | 83.37% |
| quarterly | eps | 86,426 | — | — | 6,960 | 92.55% |
| quarterly | revenue | 77,547 | — | — | 15,839 | 83.04% |
| quarterly | costOfRevenue | 48,897 | 44081 | 4816 | 44,489 | 52.36% |
| quarterly | nonOperatingIncome | 60,921 | — | — | 32,465 | 65.24% |
| quarterly | grossMargin | 48,098 | — | — | 45,288 | 51.50% |
| quarterly | operatingIncome | 71,920 | — | — | 21,466 | 77.01% |
| quarterly | netMargin | 75,175 | — | — | 18,211 | 80.50% |

The seven fields follow the existing coverage reporter and financial display rules: EPS, revenue, cost of revenue, non-operating income, gross margin, operating income, and net margin. Derived cost of revenue is a report-only revenue-minus-gross-profit calculation; source financial JSON is not rewritten with that estimate.

Machine-readable report: `docs/report/fundamentals/us-market-backfill.json`. The immutable listing snapshot and per-issuer checkpoints are `docs/report/fundamentals/us-market-snapshot.json` and `docs/report/fundamentals/us-market-progress.json`. The SEC raw-body cache is `/tmp/us-15y-sec-raw (cache loss causes re-fetch; progress and snapshot are committed)`; losing it causes a paced refetch, while queue progress remains committed.
