# Financial history coverage audit

Generated: 2026-10-15T19:02:39Z
Git commit: `52a95f27b654db54b9a544082b4eb0625c7ed5de`
Fiscal window: FY2016–FY2025 (40 fiscal quarters)

This report measures periods using stored fiscal labels. File count, year coverage, quarterly coverage, field completeness, and point-in-time eligibility are separate measures.

## Market coverage

| Market | Directory companies | Financial files | 10 FY periods | 40 fiscal quarters | Core annual complete | Core annual + quarter complete | Quality + PIT passed |
|---|---:|---:|---:|---:|---:|---:|---:|
| US | 5349 | 4963 (92.8%) | 2824 (52.8%) | 2344 (43.8%) | 1866 (34.9%) | 491 (9.2%) | 0 (0.0%) |
| TW | 1982 | 510 (25.7%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |

## Scope and limitations

Current directory counts: US 5349; TW 1982. The scanner universe reports US 5349 and TW 1979; directory and scanner scopes differ.
Tenure-adjusted completeness is not calculated because the checked-in sources do not supply reliable listing/incorporation dates. Index-only symbols are listed as unlisted candidates; they are not asserted to be a complete delisted-company census.
Legacy files do not carry filing dates, accepted timestamps, accession numbers, or restatement vintages. Therefore they are not certified for point-in-time historical backtests.
For field-level figures, see `field-completeness.csv`; null may indicate either an unavailable or inapplicable value.

FY2026 data present: US annual 551 companies, quarterly 3959; TW annual 0, quarterly 494. Latest stored period ends: US 2026-09-30, TW 2026-06-30.
Latest SEC retry batch: 66 issuers, 69 SEC requests, 1,194,320 response bytes; decisions {'unknownmetadata': 63, 'failure': 2, 'no-us-gaap': 1}.
Follow-up SEC resume probe: no_pending_issuers after validating 5350 NasdaqTrader symbols; no financial company files changed.
SEC Company Facts source cross-checks: 30 current mapped companies verified across 20,346 values; 0 mismatches. Historical-CIK identity P0s: 1. Taiwan raw-value cross-checks: 0 (MOPS 503).
Technical audit issues: P0 1, P1 4822, P2 6029.
Input hashes and data-source limits are recorded in `source-provenance.json`. Financial JSON bytes: 50,712,608; GitHub API repository size at baseline: 322540 KB; local Git directory: 34741715 bytes (shallow=True).
Rough incremental JSON storage projection to fill every target window: US 13,453,243–24,984,594 bytes; TW 19,607,452–36,413,839 bytes. See `capacity.json` for assumptions.

## Data changes since baseline revision

Compared with `52a95f27b654db54b9a544082b4eb0625c7ed5de` across 0 changed financial company files.

| Market | Annual periods added | Quarterly periods added | New non-null value cells | Changed existing values | Removed values | Net bytes |
|---|---:|---:|---:|---:|---:|---:|
| US | 0 | 0 | 0 | 0 | 0 | 0 |
| TW | 0 | 0 | 0 | 0 | 0 | 0 |
