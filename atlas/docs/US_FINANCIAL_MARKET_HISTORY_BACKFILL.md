# US market financial-history backfill

As of 2026-10-15, the 15-calendar-year cutoff is 2011-10-15.
The frozen NasdaqTrader snapshot contains 5,347 directory-filtered symbols across 5,289 SEC-mapped CIK groups. It was fetched at 2026-10-15T05:41:29Z and has source-pair SHA-256 `e730480bf89129e5a649e4ddec68b992655fed757df8d9438c0f0ca1947742ba`.
There are currently 3,819 financial files among those listing symbols: 3,779 SEC-backed and 40 Yahoo-only. The whole cohort is not yet financially refreshed.

## Listing and issuer screening

The NasdaqTrader filters accepted 5,347 unique listing rows and excluded 7,940 rows. The 506 SPAC-name matches are a string heuristic, separate from confirmed blank-check issuers.
SEC submissions confirmed 78 SIC 6770 blank-check issuers and identified 4,358 active-directory operating candidates. 65 issuer decisions remain unknown or failed. An SIC other than 6770 is a queueing basis and does not certify legal non-shell status; entityType alone is not treated as shell evidence.
The current resume queue has 206 new issuer groups pending and 583 existing SEC-seeded issuer groups still awaiting a submissions screen. Existing financial files remain available while that metadata queue is pending.
Financial index reconciliation pending for 0 symbol files.

## Current batch and resume

Batch 9 (financial) attempted 500 issuer groups; status: completed. SEC requests this batch: 976; raw SEC bytes received: 71,069,332.
The batch checkpoint has 206 new issuer groups pending and 583 existing metadata screens pending.
Split-check recheck for batch 2: stopped_split_unavailable; 1/174 symbols checked, 0 chart responses, 1 chart failures, 0 split events recorded, 0 files adjusted, 174 still pending, and 0 SEC wire requests. Stop reason: Yahoo connection failed: SplitUnavailable: curl_cffi is not installed (pip install curl_cffi).
Split-check recheck for batch 2: completed; 174/174 symbols checked, 174 chart responses, 0 chart failures, 236 split events recorded, 74 files adjusted, 0 still pending, and 0 SEC wire requests. Interpreter: `/tmp/tw-mops-backfill-venv/bin/python`.

Run `python atlas/scripts/backfill_financials_us_market.py --resume --batch-size 500` to continue the next stable batch. Add `--retry` only after reviewing failure and unknown-metadata decisions. Each resume fetches both current NasdaqTrader directories again and records any frozen symbol that is no longer present as `not_current_directory`; that label does not assert legal delisting.

## Financial metric coverage

These are observed financial-file rows across the full frozen listing cohort. They do not imply every issuer has 15 years of history or every metric input. Target counts require at least 15 annual or 60 quarterly rows; cost of revenue keeps raw reported and display-only derived values separate.

| Window | Period type | Rows | Symbols with target rows | Symbols contiguous at target | Symbols complete on all 7 metrics | Target |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Calendar cutoff | annual | 43,718 | 1,826 | 1,749 | 477 | 15 |
| Calendar cutoff | quarterly | 170,825 | 150 | 150 | 22 | 60 |
| Latest records | annual | 43,903 | 2,016 | 1,895 | 536 | 15 |
| Latest records | quarterly | 173,130 | 1,933 | 1,722 | 147 | 60 |

### Calendar-window metric cells

| Period | Metric | Available | Raw reported | Derived display | Missing | Coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| annual | eps | 41,890 | — | — | 1,828 | 95.82% |
| annual | revenue | 36,787 | — | — | 6,931 | 84.15% |
| annual | costOfRevenue | 23,827 | 21628 | 2199 | 19,891 | 54.50% |
| annual | nonOperatingIncome | 30,759 | — | — | 12,959 | 70.36% |
| annual | grossMargin | 23,523 | — | — | 20,195 | 53.81% |
| annual | operatingIncome | 34,999 | — | — | 8,719 | 80.06% |
| annual | netMargin | 35,747 | — | — | 7,971 | 81.77% |
| quarterly | eps | 157,269 | — | — | 13,556 | 92.06% |
| quarterly | revenue | 139,934 | — | — | 30,891 | 81.92% |
| quarterly | costOfRevenue | 89,520 | 80072 | 9448 | 81,305 | 52.40% |
| quarterly | nonOperatingIncome | 110,791 | — | — | 60,034 | 64.86% |
| quarterly | grossMargin | 87,993 | — | — | 82,832 | 51.51% |
| quarterly | operatingIncome | 134,326 | — | — | 36,499 | 78.63% |
| quarterly | netMargin | 134,701 | — | — | 36,124 | 78.85% |

The seven fields follow the existing coverage reporter and financial display rules: EPS, revenue, cost of revenue, non-operating income, gross margin, operating income, and net margin. Derived cost of revenue is a report-only revenue-minus-gross-profit calculation; source financial JSON is not rewritten with that estimate.

Machine-readable report: `docs/report/fundamentals/us-market-backfill.json`. The immutable listing snapshot and per-issuer checkpoints are `docs/report/fundamentals/us-market-snapshot.json` and `docs/report/fundamentals/us-market-progress.json`. The SEC raw-body cache is `/tmp/us-15y-sec-raw (cache loss causes re-fetch; progress and snapshot are committed)`; losing it causes a paced refetch, while queue progress remains committed.
