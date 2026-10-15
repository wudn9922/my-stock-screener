# US market financial-history backfill

As of 2026-10-15, the 15-calendar-year cutoff is 2011-10-15.
The frozen NasdaqTrader snapshot contains 5,347 directory-filtered symbols across 5,289 SEC-mapped CIK groups. It was fetched at 2026-10-15T05:41:29Z and has source-pair SHA-256 `e730480bf89129e5a649e4ddec68b992655fed757df8d9438c0f0ca1947742ba`.
There are currently 998 financial files among those listing symbols: 958 SEC-backed and 40 Yahoo-only. The whole cohort is not yet financially refreshed.

## Listing and issuer screening

The NasdaqTrader filters accepted 5,347 unique listing rows and excluded 7,940 rows. The 506 SPAC-name matches are a string heuristic, separate from confirmed blank-check issuers.
SEC submissions confirmed 10 SIC 6770 blank-check issuers and identified 486 active-directory operating candidates. 4 issuer decisions remain unknown or failed. An SIC other than 6770 is a queueing basis and does not certify legal non-shell status; entityType alone is not treated as shell evidence.
The current resume queue has 4,206 new issuer groups pending and 583 existing SEC-seeded issuer groups still awaiting a submissions screen. Existing financial files remain available while that metadata queue is pending.
Financial index reconciliation pending for 0 symbol files.

## Current batch and resume

Batch 1 (financial) attempted 500 issuer groups; status: completed. SEC requests this batch: 977; raw SEC bytes received: 67,955,730.
The batch checkpoint has 4206 new issuer groups pending and 583 existing metadata screens pending.

Run `python atlas/scripts/backfill_financials_us_market.py --resume --batch-size 500` to continue the next stable batch. Add `--retry` only after reviewing failure and unknown-metadata decisions. Each resume fetches both current NasdaqTrader directories again and records any frozen symbol that is no longer present as `not_current_directory`; that label does not assert legal delisting.

## Financial metric coverage

These are observed financial-file rows across the full frozen listing cohort. They do not imply every issuer has 15 years of history or every metric input. Target counts require at least 15 annual or 60 quarterly rows; cost of revenue keeps raw reported and display-only derived values separate.

| Window | Period type | Rows | Symbols with target rows | Symbols contiguous at target | Symbols complete on all 7 metrics | Target |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Calendar cutoff | annual | 12,111 | 579 | 561 | 164 | 15 |
| Calendar cutoff | quarterly | 46,926 | 61 | 61 | 9 | 60 |
| Latest records | annual | 12,156 | 623 | 599 | 187 | 15 |
| Latest records | quarterly | 47,572 | 601 | 552 | 55 | 60 |

### Calendar-window metric cells

| Period | Metric | Available | Raw reported | Derived display | Missing | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| annual | eps | 11,768 | — | — | 343 | 97.17% |
| annual | revenue | 10,930 | — | — | 1,181 | 90.25% |
| annual | costOfRevenue | 7,010 | 6483 | 527 | 5,101 | 57.88% |
| annual | nonOperatingIncome | 9,135 | — | — | 2,976 | 75.43% |
| annual | grossMargin | 6,936 | — | — | 5,175 | 57.27% |
| annual | operatingIncome | 9,897 | — | — | 2,214 | 81.72% |
| annual | netMargin | 10,718 | — | — | 1,393 | 88.50% |
| quarterly | eps | 44,421 | — | — | 2,505 | 94.66% |
| quarterly | revenue | 41,533 | — | — | 5,393 | 88.51% |
| quarterly | costOfRevenue | 26,277 | 23968 | 2309 | 20,649 | 56.00% |
| quarterly | nonOperatingIncome | 33,327 | — | — | 13,599 | 71.02% |
| quarterly | grossMargin | 25,920 | — | — | 21,006 | 55.24% |
| quarterly | operatingIncome | 37,622 | — | — | 9,304 | 80.17% |
| quarterly | netMargin | 40,407 | — | — | 6,519 | 86.11% |

The seven fields follow the existing coverage reporter and financial display rules: EPS, revenue, cost of revenue, non-operating income, gross margin, operating income, and net margin. Derived cost of revenue is a report-only revenue-minus-gross-profit calculation; source financial JSON is not rewritten with that estimate.

Machine-readable report: `docs/report/fundamentals/us-market-backfill.json`. The immutable listing snapshot and per-issuer checkpoints are `docs/report/fundamentals/us-market-snapshot.json` and `docs/report/fundamentals/us-market-progress.json`. The SEC raw-body cache is `/tmp/us-15y-sec-raw (cache loss causes re-fetch; progress and snapshot are committed)`; losing it causes a paced refetch, while queue progress remains committed.
