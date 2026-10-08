# SEC companyfacts fixture validation

These checked-in files are test-only inputs for the Phase 2 financial normalizer. The app does not load them as offline financial data. JPM, WMT and the current SEC-mapped XOM companyfacts were fetched through the existing `SecClient`, which resolves official SEC ticker mappings, declares the Atlas prototype User-Agent, honors inherited proxy settings, serializes requests at 500 ms intervals, and uses its URL cache and in-flight deduplication. No paid service or key was used.

The four new fixtures retain only mapped US-GAAP concepts and unit rows whose `end` is on or after 2022-01-01. The raw values, units, dates, accessions, forms and filing focus fields are preserved. Their source URLs, retrieval times, source-object hashes and fixture hashes are in [v1-provenance.json](../tests/fixtures/sec/v1-provenance.json). The source-object hash is SHA-256 of compact `JSON.stringify` output for the parsed response object supplied to the filter; `SecClient` normalizes digit-string CIKs to numbers. It is not a hash of SEC wire bytes. The original four fixture entries remain in [provenance.json](../tests/fixtures/sec/provenance.json).

The SEC ticker map currently resolves XOM to CIK `0002115436`, ExxonMobil Holdings Corp. Its companyfacts response uses a digit-string CIK; `SecClient` accepts it only after numeric CIK identity matches the official ticker mapping. The current mapped source has 94 US-GAAP concepts and normalizes to two quarterly records and no annual records in this fixture window. A separate `XOM.json` uses the verified historical SEC companyfacts endpoint for CIK `0000034088`, Exxon Mobil Corporation, to exercise a longer energy-company history. These SEC entity names and CIKs are distinct; the historical fixture does not claim that the current ticker resolver serves those facts. The script checks both identities before writing either fixture.

## Normalized fixture results

Counts below are the actual number of annual and quarterly records produced from the filtered checked-in fixture on 2026-10-06. Values are latest period revenue in USD; no values are interpolated for display in this table.

| Symbol                     | Annual / quarterly records | Latest annual period |  Annual revenue | Latest quarter        | Quarterly revenue |
| -------------------------- | -------------------------: | -------------------- | --------------: | --------------------- | ----------------: |
| AAPL                       |                     4 / 18 | FY2025, 2025-09-27   | 416,161,000,000 | FY2026 Q3, 2026-06-27 |   109,417,000,000 |
| MSFT                       |                     5 / 18 | FY2026, 2026-06-30   | 331,839,000,000 | FY2026 Q4, 2026-06-30 |    90,007,000,000 |
| NVDA                       |                     5 / 18 | FY2026, 2026-01-25   | 215,938,000,000 | FY2027 Q2, 2026-07-26 |    96,221,000,000 |
| TSLA                       |                     4 / 18 | FY2025, 2025-12-31   |  94,827,000,000 | FY2026 Q2, 2026-06-30 |    28,236,000,000 |
| JPM                        |                     4 / 18 | FY2025, 2025-12-31   | 182,447,000,000 | FY2026 Q2, 2026-06-30 |    57,347,000,000 |
| WMT                        |                     5 / 18 | FY2026, 2026-01-31   | 706,413,000,000 | FY2027 Q2, 2026-07-31 |   186,100,000,000 |
| XOM (historical CIK 34088) |                     4 / 17 | FY2025, 2025-12-31   | 332,238,000,000 | FY2026 Q1, 2026-03-31 |    85,138,000,000 |

The separate current-map snapshot is [XOM-current.json](../tests/fixtures/sec/XOM-current.json): 0 annual records and 2 quarterly records. Its latest quarter is FY2026 Q2, 2026-06-30, with revenue 116,017,000,000 and diluted EPS 3.48. Operating cash flow, CapEx and FCF remain null for that record.

`tests/sec-expanded.test.ts` pins each row's record counts and annual/quarterly revenue and diluted EPS values. It also validates every normalized record against the public financial schema.

The live `node scripts/sec-v1-smoke.mjs` run on 2026-10-06 produced these per-company record counts through current ticker resolution. It normalizes XOM from CIK 0002115436; the separate historical XOM fixture is not included in these live counts.

| Symbol | Resolved SEC entity      | Annual / quarterly records | Latest annual      | Latest quarter        |
| ------ | ------------------------ | -------------------------: | ------------------ | --------------------- |
| AAPL   | Apple Inc.               |                    19 / 73 | FY2025, 2025-09-27 | FY2026 Q3, 2026-06-27 |
| MSFT   | MICROSOFT CORPORATION    |                    19 / 76 | FY2026, 2026-06-30 | FY2026 Q4, 2026-06-30 |
| NVDA   | NVIDIA CORP              |                    19 / 77 | FY2026, 2026-01-25 | FY2027 Q2, 2026-07-26 |
| TSLA   | Tesla, Inc.              |                    17 / 66 | FY2025, 2025-12-31 | FY2026 Q2, 2026-06-30 |
| JPM    | JPMORGAN CHASE & CO      |                    19 / 73 | FY2025, 2025-12-31 | FY2026 Q2, 2026-06-30 |
| WMT    | WALMART INC.             |                    20 / 77 | FY2026, 2026-01-31 | FY2027 Q2, 2026-07-31 |
| XOM    | ExxonMobil Holdings Corp |                      0 / 2 | none               | FY2026 Q2, 2026-06-30 |

## Audit findings

- JPM's FY2025 annual revenue is reported by `us-gaap:Revenues`; its Q2 2026 standalone total is `us-gaap:RevenuesNetOfInterestExpense`, USD 57,347,000,000, filed in accession `0001628280-26-054343` on 2026-08-06. The audit exposes that concept on the value. The normalizer does not add overlapping interest and noninterest components. Gross profit, operating expenses, CapEx, cash and FCF remain null where mapped facts are absent.
- MSFT's FY2026 Q4 revenue is derived from the FY total less the aligned nine-month fact: 331,839,000,000 − 241,832,000,000 = 90,007,000,000. Both inputs use the same concept and fiscal start, and both filings appear in provenance.
- AAPL's FY2026 Q3 operating cash flow is derived from aligned YTD facts: 116,996,000,000 − 82,627,000,000 = 34,369,000,000. The fixture tests retain both input periods, filings and accessions.
- Instant balance-sheet facts use USD and have no start date. Duration values use USD; EPS uses USD/shares. Standalone Q2/Q3 EPS values are taken directly from filings when present. Missing or unsupported fields remain null, including MSFT's Q4 diluted EPS, WMT's total liabilities, and XOM's gross profit and operating expenses.

Refresh the current and historical company fixtures and run the seven-company live normalization summary with:

```sh
node scripts/sec-v1-smoke.mjs --write-fixtures
node scripts/sec-v1-smoke.mjs
npm test -- tests/sec-expanded.test.ts
```

Fixture refresh leaves the original four-company provenance file untouched. The new manifest separately hashes `XOM-current.json` and the historical `XOM.json`; the historical source is fetched from the fixed official CIK URL after checking the current SEC ticker mapping. The script writes scratch raw/normalized smoke outputs under `scratch/sec-v1/` and keeps the checked-in data test-only.

## Final production-backend check

After the clean V1 production build, the restarted local preview backend returned current quarterly records for AAPL, JPM, WMT and XOM without route mocks. Issuer CIK, latest fiscal period, revenue provenance and accession were checked through the actual /api/fundamentals endpoint. The response summary and retrieval time are recorded in [v1-live-backend-smoke.json](v1-live-backend-smoke.json). This is availability/provenance smoke evidence, not an assertion that every SEC metric or issuer pattern is universally supported.
