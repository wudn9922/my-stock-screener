# US market financial-history backfill

As of 2026-10-15, the 15-calendar-year cutoff is 2011-10-15.
The frozen NasdaqTrader snapshot contains 5,347 directory-filtered symbols across 5,289 SEC-mapped CIK groups. It was fetched at 2026-10-15T05:41:29Z and has source-pair SHA-256 `e730480bf89129e5a649e4ddec68b992655fed757df8d9438c0f0ca1947742ba`.
There are currently 3,946 financial files among those listing symbols: 3,906 SEC-backed and 40 Yahoo-only. The whole cohort is not yet financially refreshed.

## Listing and issuer screening

The NasdaqTrader filters accepted 5,347 unique listing rows and excluded 7,940 rows. The 506 SPAC-name matches are a string heuristic, separate from confirmed blank-check issuers.
SEC submissions confirmed 85 SIC 6770 blank-check issuers and identified 5,056 active-directory operating candidates. 67 issuer decisions remain unknown or failed. An SIC other than 6770 is a queueing basis and does not certify legal non-shell status; entityType alone is not treated as shell evidence.
The current resume queue has 0 new issuer groups pending and 83 existing SEC-seeded issuer groups still awaiting a submissions screen. Existing financial files remain available while that metadata queue is pending.
Financial index reconciliation pending for 0 symbol files.

## Current batch and resume

Batch 11 (metadata_only) attempted 500 issuer groups; status: completed. SEC requests this batch: 500; raw SEC bytes received: 13,356,420.
The batch checkpoint has 0 new issuer groups pending and 83 existing metadata screens pending.
Split-check recheck for batch 2: stopped_split_unavailable; 1/174 symbols checked, 0 chart responses, 1 chart failures, 0 split events recorded, 0 files adjusted, 174 still pending, and 0 SEC wire requests. Stop reason: Yahoo connection failed: SplitUnavailable: curl_cffi is not installed (pip install curl_cffi).
Split-check recheck for batch 2: completed; 174/174 symbols checked, 174 chart responses, 0 chart failures, 236 split events recorded, 74 files adjusted, 0 still pending, and 0 SEC wire requests. Interpreter: `/tmp/tw-mops-backfill-venv/bin/python`.

Run `python atlas/scripts/backfill_financials_us_market.py --resume --batch-size 500` to continue the next stable batch. Add `--retry` only after reviewing failure and unknown-metadata decisions. Each resume fetches both current NasdaqTrader directories again and records any frozen symbol that is no longer present as `not_current_directory`; that label does not assert legal delisting.

## Financial metric coverage

These are observed financial-file rows across the full frozen listing cohort. They do not imply every issuer has 15 years of history or every metric input. Target counts require at least 15 annual or 60 quarterly rows; cost of revenue keeps raw reported and display-only derived values separate.

| Window | Period type | Rows | Symbols with target rows | Symbols contiguous at target | Symbols complete on all 7 metrics | Target |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Calendar cutoff | annual | 45,191 | 1,878 | 1,798 | 490 | 15 |
| Calendar cutoff | quarterly | 176,588 | 154 | 154 | 22 | 60 |
| Latest records | annual | 45,380 | 2,074 | 1,948 | 551 | 15 |
| Latest records | quarterly | 178,959 | 1,990 | 1,771 | 153 | 60 |

### Calendar-window metric cells

| Period | Metric | Available | Raw reported | Derived display | Missing | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| annual | eps | 43,271 | — | — | 1,920 | 95.75% |
| annual | revenue | 38,071 | — | — | 7,120 | 84.24% |
| annual | costOfRevenue | 24,726 | 22419 | 2307 | 20,465 | 54.71% |
| annual | nonOperatingIncome | 31,904 | — | — | 13,287 | 70.60% |
| annual | grossMargin | 24,402 | — | — | 20,789 | 54.00% |
| annual | operatingIncome | 36,237 | — | — | 8,954 | 80.19% |
| annual | netMargin | 36,985 | — | — | 8,206 | 81.84% |
| quarterly | eps | 162,406 | — | — | 14,182 | 91.97% |
| quarterly | revenue | 144,821 | — | — | 31,767 | 82.01% |
| quarterly | costOfRevenue | 92,843 | 82934 | 9909 | 83,745 | 52.58% |
| quarterly | nonOperatingIncome | 114,963 | — | — | 61,625 | 65.10% |
| quarterly | grossMargin | 91,233 | — | — | 85,355 | 51.66% |
| quarterly | operatingIncome | 139,084 | — | — | 37,504 | 78.76% |
| quarterly | netMargin | 139,333 | — | — | 37,255 | 78.90% |

The seven fields follow the existing coverage reporter and financial display rules: EPS, revenue, cost of revenue, non-operating income, gross margin, operating income, and net margin. Derived cost of revenue is a report-only revenue-minus-gross-profit calculation; source financial JSON is not rewritten with that estimate.

Machine-readable report: `docs/report/fundamentals/us-market-backfill.json`. The immutable listing snapshot and per-issuer checkpoints are `docs/report/fundamentals/us-market-snapshot.json` and `docs/report/fundamentals/us-market-progress.json`. The SEC raw-body cache is `/tmp/us-15y-sec-raw (cache loss causes re-fetch; progress and snapshot are committed)`; losing it causes a paced refetch, while queue progress remains committed.
