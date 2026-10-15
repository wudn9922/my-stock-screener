# US market financial-history backfill

As of 2026-10-15, the 15-calendar-year cutoff is 2011-10-15.
The frozen NasdaqTrader snapshot contains 5,347 directory-filtered symbols across 5,289 SEC-mapped CIK groups. It was fetched at 2026-10-15T05:41:29Z and has source-pair SHA-256 `e730480bf89129e5a649e4ddec68b992655fed757df8d9438c0f0ca1947742ba`.
There are currently 3,121 financial files among those listing symbols: 3,081 SEC-backed and 40 Yahoo-only. The whole cohort is not yet financially refreshed.

## Listing and issuer screening

The NasdaqTrader filters accepted 5,347 unique listing rows and excluded 7,940 rows. The 506 SPAC-name matches are a string heuristic, separate from confirmed blank-check issuers.
SEC submissions confirmed 62 SIC 6770 blank-check issuers and identified 3,391 active-directory operating candidates. 48 issuer decisions remain unknown or failed. An SIC other than 6770 is a queueing basis and does not certify legal non-shell status; entityType alone is not treated as shell evidence.
The current resume queue has 1,206 new issuer groups pending and 583 existing SEC-seeded issuer groups still awaiting a submissions screen. Existing financial files remain available while that metadata queue is pending.
Financial index reconciliation pending for 0 symbol files.

## Current batch and resume

Batch 7 (financial) attempted 500 issuer groups; status: completed. SEC requests this batch: 981; raw SEC bytes received: 70,164,481.
The batch checkpoint has 1206 new issuer groups pending and 583 existing metadata screens pending.
Split-check recheck for batch 2: stopped_split_unavailable; 1/174 symbols checked, 0 chart responses, 1 chart failures, 0 split events recorded, 0 files adjusted, 174 still pending, and 0 SEC wire requests. Stop reason: Yahoo connection failed: SplitUnavailable: curl_cffi is not installed (pip install curl_cffi).
Split-check recheck for batch 2: completed; 174/174 symbols checked, 174 chart responses, 0 chart failures, 236 split events recorded, 74 files adjusted, 0 still pending, and 0 SEC wire requests. Interpreter: `/tmp/tw-mops-backfill-venv/bin/python`.

Run `python atlas/scripts/backfill_financials_us_market.py --resume --batch-size 500` to continue the next stable batch. Add `--retry` only after reviewing failure and unknown-metadata decisions. Each resume fetches both current NasdaqTrader directories again and records any frozen symbol that is no longer present as `not_current_directory`; that label does not assert legal delisting.

## Financial metric coverage

These are observed financial-file rows across the full frozen listing cohort. They do not imply every issuer has 15 years of history or every metric input. Target counts require at least 15 annual or 60 quarterly rows; cost of revenue keeps raw reported and display-only derived values separate.

| Window | Period type | Rows | Symbols with target rows | Symbols contiguous at target | Symbols complete on all 7 metrics | Target |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Calendar cutoff | annual | 35,872 | 1,515 | 1,457 | 403 | 15 |
| Calendar cutoff | quarterly | 140,084 | 128 | 128 | 21 | 60 |
| Latest records | annual | 36,022 | 1,671 | 1,576 | 458 | 15 |
| Latest records | quarterly | 141,990 | 1,603 | 1,429 | 128 | 60 |

### Calendar-window metric cells

| Period | Metric | Available | Raw reported | Derived display | Missing | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| annual | eps | 34,394 | — | — | 1,478 | 95.88% |
| annual | revenue | 30,270 | — | — | 5,602 | 84.38% |
| annual | costOfRevenue | 19,547 | 17828 | 1719 | 16,325 | 54.49% |
| annual | nonOperatingIncome | 25,235 | — | — | 10,637 | 70.35% |
| annual | grossMargin | 19,298 | — | — | 16,574 | 53.80% |
| annual | operatingIncome | 28,623 | — | — | 7,249 | 79.79% |
| annual | netMargin | 29,444 | — | — | 6,428 | 82.08% |
| quarterly | eps | 129,194 | — | — | 10,890 | 92.23% |
| quarterly | revenue | 115,051 | — | — | 25,033 | 82.13% |
| quarterly | costOfRevenue | 73,448 | 66087 | 7361 | 66,636 | 52.43% |
| quarterly | nonOperatingIncome | 91,045 | — | — | 49,039 | 64.99% |
| quarterly | grossMargin | 72,186 | — | — | 67,898 | 51.53% |
| quarterly | operatingIncome | 109,799 | — | — | 30,285 | 78.38% |
| quarterly | netMargin | 110,864 | — | — | 29,220 | 79.14% |

The seven fields follow the existing coverage reporter and financial display rules: EPS, revenue, cost of revenue, non-operating income, gross margin, operating income, and net margin. Derived cost of revenue is a report-only revenue-minus-gross-profit calculation; source financial JSON is not rewritten with that estimate.

Machine-readable report: `docs/report/fundamentals/us-market-backfill.json`. The immutable listing snapshot and per-issuer checkpoints are `docs/report/fundamentals/us-market-snapshot.json` and `docs/report/fundamentals/us-market-progress.json`. The SEC raw-body cache is `/tmp/us-15y-sec-raw (cache loss causes re-fetch; progress and snapshot are committed)`; losing it causes a paced refetch, while queue progress remains committed.
