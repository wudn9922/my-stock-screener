# US market financial-history backfill

As of 2026-10-15, the 15-calendar-year cutoff is 2011-10-15.
The frozen NasdaqTrader snapshot contains 5,347 directory-filtered symbols across 5,289 SEC-mapped CIK groups. It was fetched at 2026-10-15T05:41:29Z and has source-pair SHA-256 `e730480bf89129e5a649e4ddec68b992655fed757df8d9438c0f0ca1947742ba`.
There are currently 3,478 financial files among those listing symbols: 3,438 SEC-backed and 40 Yahoo-only. The whole cohort is not yet financially refreshed.

## Listing and issuer screening

The NasdaqTrader filters accepted 5,347 unique listing rows and excluded 7,940 rows. The 506 SPAC-name matches are a string heuristic, separate from confirmed blank-check issuers.
SEC submissions confirmed 67 SIC 6770 blank-check issuers and identified 3,877 active-directory operating candidates. 57 issuer decisions remain unknown or failed. An SIC other than 6770 is a queueing basis and does not certify legal non-shell status; entityType alone is not treated as shell evidence.
The current resume queue has 706 new issuer groups pending and 583 existing SEC-seeded issuer groups still awaiting a submissions screen. Existing financial files remain available while that metadata queue is pending.
Financial index reconciliation pending for 0 symbol files.

## Current batch and resume

Batch 8 (financial) attempted 500 issuer groups; status: completed. SEC requests this batch: 984; raw SEC bytes received: 68,818,959.
The batch checkpoint has 706 new issuer groups pending and 583 existing metadata screens pending.
Split-check recheck for batch 2: stopped_split_unavailable; 1/174 symbols checked, 0 chart responses, 1 chart failures, 0 split events recorded, 0 files adjusted, 174 still pending, and 0 SEC wire requests. Stop reason: Yahoo connection failed: SplitUnavailable: curl_cffi is not installed (pip install curl_cffi).
Split-check recheck for batch 2: completed; 174/174 symbols checked, 174 chart responses, 0 chart failures, 236 split events recorded, 74 files adjusted, 0 still pending, and 0 SEC wire requests. Interpreter: `/tmp/tw-mops-backfill-venv/bin/python`.

Run `python atlas/scripts/backfill_financials_us_market.py --resume --batch-size 500` to continue the next stable batch. Add `--retry` only after reviewing failure and unknown-metadata decisions. Each resume fetches both current NasdaqTrader directories again and records any frozen symbol that is no longer present as `not_current_directory`; that label does not assert legal delisting.

## Financial metric coverage

These are observed financial-file rows across the full frozen listing cohort. They do not imply every issuer has 15 years of history or every metric input. Target counts require at least 15 annual or 60 quarterly rows; cost of revenue keeps raw reported and display-only derived values separate.

| Window | Period type | Rows | Symbols with target rows | Symbols contiguous at target | Symbols complete on all 7 metrics | Target |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Calendar cutoff | annual | 39,749 | 1,659 | 1,594 | 434 | 15 |
| Calendar cutoff | quarterly | 155,293 | 138 | 138 | 21 | 60 |
| Latest records | annual | 39,921 | 1,831 | 1,727 | 491 | 15 |
| Latest records | quarterly | 157,407 | 1,758 | 1,566 | 132 | 60 |

### Calendar-window metric cells

| Period | Metric | Available | Raw reported | Derived display | Missing | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| annual | eps | 38,128 | — | — | 1,621 | 95.92% |
| annual | revenue | 33,537 | — | — | 6,212 | 84.37% |
| annual | costOfRevenue | 21,627 | 19677 | 1950 | 18,122 | 54.41% |
| annual | nonOperatingIncome | 27,857 | — | — | 11,892 | 70.08% |
| annual | grossMargin | 21,355 | — | — | 18,394 | 53.72% |
| annual | operatingIncome | 31,719 | — | — | 8,030 | 79.80% |
| annual | netMargin | 32,626 | — | — | 7,123 | 82.08% |
| quarterly | eps | 143,105 | — | — | 12,188 | 92.15% |
| quarterly | revenue | 127,616 | — | — | 27,677 | 82.18% |
| quarterly | costOfRevenue | 81,250 | 72866 | 8384 | 74,043 | 52.32% |
| quarterly | nonOperatingIncome | 100,469 | — | — | 54,824 | 64.70% |
| quarterly | grossMargin | 79,859 | — | — | 75,434 | 51.42% |
| quarterly | operatingIncome | 121,702 | — | — | 33,591 | 78.37% |
| quarterly | netMargin | 122,993 | — | — | 32,300 | 79.20% |

The seven fields follow the existing coverage reporter and financial display rules: EPS, revenue, cost of revenue, non-operating income, gross margin, operating income, and net margin. Derived cost of revenue is a report-only revenue-minus-gross-profit calculation; source financial JSON is not rewritten with that estimate.

Machine-readable report: `docs/report/fundamentals/us-market-backfill.json`. The immutable listing snapshot and per-issuer checkpoints are `docs/report/fundamentals/us-market-snapshot.json` and `docs/report/fundamentals/us-market-progress.json`. The SEC raw-body cache is `/tmp/us-15y-sec-raw (cache loss causes re-fetch; progress and snapshot are committed)`; losing it causes a paced refetch, while queue progress remains committed.
