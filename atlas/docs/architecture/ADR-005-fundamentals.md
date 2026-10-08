# ADR-005 — Auditable SEC fundamentals

Status: Phase 2 implementation accepted by primary engineer, 2026-10-06. User explicitly authorized Phase 2 after accepting Phase 1. Independent review remains pending; see REVIEW_PACKAGE.md.

## Boundaries

SEC data remains behind `FundamentalsProvider`. `SecClient` is server-only; it resolves any supported US ticker from the official `www.sec.gov/files/company_tickers.json` mapping (24-hour cache, dot/hyphen aliases) and fetches `data.sec.gov/api/xbrl/companyfacts/CIK….json` (6-hour cache). Verify response CIK, never associate another company's facts with the requested symbol. Fixed destinations, no caller-supplied URLs. Missing ticker or supported taxonomy gives an empty normalized list, not invented numbers.

The client declares `AtlasResearchTerminal/0.2 (local research prototype)` as its local User-Agent. `SEC_USER_AGENT` can supply the operator's real organization/contact; configure it for deployment, never a fabricated contact. One serialized queue spaces request starts by at least 500ms (2/s, below SEC's published 10/s maximum). Same-URL concurrent requests deduplicate; successful raw responses cache up to 32 entries, normalized output caches by raw-object identity. HTTP 403/429 sets a 60-second cooldown, failures are not cached as financial values. Each instance enforces its own budget; multiple server replicas need a shared rate limiter/cache before horizontal deployment. Requests honor inherited proxy/CA trust through Undici EnvHttpProxyAgent and have a 20-second timeout.

`createSecBackend()` returns a reusable HTTP middleware and cleanup. Vite dev/preview and the standalone Node backend use the same middleware. `npm run build` builds browser assets and `dist-server/fundamentals.mjs`; `npm run serve:fundamentals` starts the API on 127.0.0.1:8788 (SEC_HOST/SEC_PORT configurable). Deploy static assets and reverse-proxy `/api/fundamentals` to that backend. No paid key, card or SDK. React receives only normalized JSON validated by `financialSchema`; it never imports raw SEC fixtures or response structures. Browser cache is per symbol/period for one hour, returns independent snapshots, and rejects mismatched records. API errors leave chart/drawing state independent.

## Normalized records and audit

`CompanyFundamentals` has symbol, quarterly/annual period, fiscal year/quarter, actual periodStart/End, a representative duration filing's form/date/accession, nullable metrics and `sourceConcepts`. The unused Phase 1 draft `audit` contract was extended/replaced before any financial consumers or persisted records existed. No Phase 1 storage migration is needed: financial facts are not settings and are not written to the drawing/SMA IndexedDB stores.

Each metric provenance carries `derived`, calculation text and raw inputs. Every input has namespace/concept, unit, raw value, duration/instant kind, actual start/end, filing form/date/accession, SEC companyfacts URL, filing directory URL and original filing fiscal focus. The representative record heading prefers revenue's duration source; each value's own source may have a different filing date. Audit details are authoritative. Raw filing `fy/fp` refers to the filing focus, not necessarily a comparative value's own year. Derived FCF retains underlying raw cash-flow/CapEx inputs and intermediate quarter formulas.

## Concept mapping

`FinancialConceptMapper` gives explicit ordered alternatives per metric and expected unit/kind. Only USD and USD/shares are normalized; there is no invented currency conversion. Revenue supports contract revenue excluding/including assessed tax, Revenues, SalesRevenueNet, SalesRevenueGoodsNet, RevenueFromSaleOfGoods. Other fallbacks cover cost of sales, net income, combined EPS, continuing operating cash flow, productive-asset spending, cash and equity variants. Audit preserves the actual chosen concept; alternatives need not have identical economic definitions across issuers.

Broad totals are intentionally not synonyms: CostsAndExpenses is not OperatingExpenses; debt is not total liabilities; cash including restricted cash is not unrestricted cash. Company-specific extension tags and IFRS are not yet mapped. Missing metrics remain null/N/A.

## Fiscal periods, duplicates and derivation

1. Validate dates, finite values, accession, form, fiscal focus, unit and kind. Supported forms are 10-Q/10-Q/A and 10-K/10-K/A. Instant fields have no start; duration fields require a start. Facts are entity-wide companyfacts, not segment aggregates.
2. Infer each accession's current duration end and fiscal focus; infer actual fiscal-year boundaries from annual or matching cumulative quarter durations. Comparative facts are mapped to their own fiscal year using end-date alignment with the filing focus. Do not use calendar `frame` as fiscal truth.
3. Standard quarter ends come from observed standalone/cumulative durations within the fiscal year. Starts are the previous observed quarter end + one day, or an explicit standalone quarter start. Annuals are 330–395 days; quarters 60–120 days. This supports ordinary calendar and 52/53-week years, not arbitrary transition/stub fiscal years. No missing boundary is fabricated.
4. For the exact target start/end, pick the first available configured concept, then latest filing/accession within that concept. Identical duplicates collapse to one source; conflicting same-concept/unit/period/accession values yield null with a visible warning. Amendments and later comparative restatements remain attributable to their actual filings.
5. Prefer directly filed standalone duration values. If absent, Q2 = H1 − Q1, Q3 = 9M − H1, Q4 = FY − 9M (equivalent to subtracting the first three quarters). Require identical fiscal start, same concept/unit, exact previous quarter end and a baseline filed no later than the minuend. Record both raw inputs, their accessions, dates and formula. Do not mix differing concepts or shifted periods. This is period alignment, not a guarantee that an earlier filing's quarter was never subsequently restated; audit exposes the chosen vintage and independent review must scrutinize unusual restatements.
6. Never subtract cumulative EPS: weighted-average shares make it unsafe. Missing standalone Q4 EPS stays N/A even when annual EPS exists.
7. Positive CapEx means cash outflow. Direct negative payments are explicitly sign-normalized with derived provenance. Quarter spending uses differences of absolute cumulative payments; decreasing positive cumulative outflows are N/A, not silently turned into positive spending.
8. FCF = normalized OCF − CapEx only with both inputs. Gross profit may be derived from aligned revenue minus cost of revenue when no raw gross profit exists; conflicts are not masked. Operating expenses are not guessed from broad expense totals.
9. Margins use profit/revenue with missing, zero or non-finite denominators/results returning N/A. YoY matches previous fiscal year and the same fiscal quarter; QoQ matches the immediately preceding fiscal quarter, including Q1→prior FY Q4. Annual YoY matches prior FY. Growth uses `(current − previous) / abs(previous)`, explicitly stated in UI. No array-position comparison across missing periods.

## UI and verification

Desktop Financials shares the existing right panel; mobile uses a bottom sheet, preserving chart dimensions. Overview/Income Statement/Cash Flow/Balance Sheet, quarterly/annual toggle, fiscal-period selector, light SVG history charts, growth/margins and expandable audit details. Simulated prices and actual SEC fundamentals are visibly distinguished. Missing/error states never switch the market-data provider or mutate drawings/indicators.

Fixtures for AAPL, MSFT, NVDA and TSLA are filtered **actual SEC responses**, with hashes/source URLs in `tests/fixtures/sec/provenance.json`. They are test-only, not offline financial data shown to users. Synthetic tests isolate concept priorities, YTD/quarter boundaries, wrong units/kinds, duplicates, amendments, comparative-year labels, missing baselines, CapEx sign, FCF, margins, growth and provenance validation. Live standalone API smoke records are in docs/sec-api-smoke.json. Physical-device/mobile performance and independent financial review remain outside headless test claims.

SEC policy: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
SEC APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces

## V1 hardening (2026-10-06)
Expanded official issuer fixtures cover JPM/WMT/XOM as well as AAPL/MSFT/NVDA/TSLA. RevenuesNetOfInterestExpense is an explicit lower-priority bank total revenue fallback; overlapping interest subcomponents are never guessed/summed. SEC numeric-string CIKs are accepted only after exact numeric identity validation and normalized behind the provider. The official ticker map determines current issuer routing: XOM's current holding entity and historical Exxon CIK are distinct, audited fixture sources, never silently substituted. Normalized records expose issuer name/CIK for UI audit. All quarter/EPS/null and provenance rules remain conservative.
