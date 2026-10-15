# Financial history coverage audit

Generated: 2026-10-15T19:02:33Z
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
Latest SEC retry batch: 83 issuers, 83 SEC requests, 1,931,108 response bytes; decisions {'seeded_existing_screened_operating_candidate': 83}.

No raw-source cross-check output is recorded.
Technical audit issues: P0 0, P1 4822, P2 6029.
Input hashes and data-source limits are recorded in `source-provenance.json`. Financial JSON bytes: 50,712,608; GitHub API repository size at baseline: 322540 KB; local Git directory: 34741715 bytes (shallow=True).
Rough incremental JSON storage projection to fill every target window: US 13,453,243–24,984,594 bytes; TW 19,607,452–36,413,839 bytes. See `capacity.json` for assumptions.
