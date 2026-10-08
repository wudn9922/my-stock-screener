# Atlas V1 final verification

Completed: 2026-10-06. Status: **COMPLETE WITH EXTERNAL UAT PENDING**. The existing /workspace project remains the only source of truth. Phase 1/2 were extended, not recreated; Lightweight Charts remains pinned to 5.2.1. No V2 work is authorized or started.

## Clean final gate

Executed in order after all source changes: `npm ci` → `npm test` → `npm run lint` → `npm run build` → `npm run test:browser -- --reporter=list,json`.

| Check | Actual result |
| --- | --- |
| npm ci | PASS, root lockfile clean install |
| Unit/regression | PASS, 135 tests across 17 files |
| Lint | PASS, zero lint warnings/errors |
| TypeScript + production build | PASS |
| Desktop Chromium | 21 passed, 1 expected skip, 0 failed |
| Mobile Chromium | 19 passed, 3 expected skips, 0 failed |
| iPad WebKit | 19 passed, 3 expected skips, 0 failed |
| iPhone WebKit | 19 passed, 3 expected skips, 0 failed |
| Full browser suite | 78 passed, 10 expected skips, 0 unexpected failures, 0 flaky cases; 88 cases total |

Final browser duration: 520,930 ms (approximately 8.7 minutes). [Machine-readable case results](v1-browser-summary.json) retain every project/test/status/duration. The raw runner report is `scratch/v1-browser-results.json`; the final run used JSON/list reporters, so an older HTML report must not be mistaken for the final gate.

The ten skips are intentional: one touch-only case on desktop, plus the three controlled desktop load/measurement scenarios on each of the three mobile/tablet projects. All core drawing, isolation, reload, backups, financial, strategy, alert and offline journeys run on all four projects. Existing Phase 1/2 tests remain enabled and pass; no failed interaction gate was bypassed.

Nonblocking dependency/build notices: the existing locked ESLint 9 package emits a deprecation notice during install; isolated native tooling emits a transitive uuid deprecation notice; Rollup removes upstream Zod comment annotations. These are not lint errors or failed builds. Dependency major versions were not changed to silence notices.

## Accepted functionality

- One candlestick chart, pan/zoom/crosshair/OHLC header, public Primitive rendering and whitespace future mapping.
- Ten shared-engine drawing tools: Trend Line, Horizontal Line, Horizontal Ray, Rectangle, Fibonacci Retracement, Parallel Channel, Price Range, Date Range, Price + Date Range, Vertical Line. Anchor/corner/channel-width edits, snapshot body moves, lock, magnet, loupe, history, styles/defaults, visibility, future anchors, symbol ownership and backup persistence.
- Symbol-owned SMA/EMA/Volume, multiple instances, locks, source/style/visibility, copied presets. AAPL 24/58 and NVDA 43/56 regression retains isolation and reload locks.
- Watchlist order, recent ticker search, provider capabilities, seven Demo timeframes, six direct Yahoo timeframes, bounded TTL cache and visible delayed/simulated/stale metadata.
- SEC ticker/CIK backend, conservative fiscal normalization, financial statements/trends/margins/growth and value-level audit. Actual filing/split/dividend markers; unavailable events remain explicit.
- Pure strategy/backtest engines, MA Cross and Price Cross MA, long/short research signals, deterministic metrics and no-lookahead/next-open executions.
- Foreground selected-chart alerts, persisted definitions/trigger history, crossing re-arm/deduplication, safe drawing-reference reset/invalidation.
- Version-2 IndexedDB migration and transactional validated import/export; failed migrations/imports preserve previous data.
- Responsive desktop/phone/iPad panels, keyboard guards, unified errors/debug, installable production PWA and offline Demo/local settings.

## Drawing bugs fixed and verified

The stricter locked-pan tests exposed a latent Phase 1 restoration bug: LWC `chart.options()` returns a live object, so disabling gestures also mutated the saved reference. The controller now copies only scroll/scale values before disabling and releases capture using the actual completed pointer ID.

Real Chromium Touch Events additionally proved that a precision drawing hold could start LWC's parallel native long-press tracking, causing the next drag to move crosshair rather than pan. The existing controller suppresses compatibility Touch Events through the owned drawing cycle; geometry remains Pointer-only. Held-placement regression now exceeds the native long-press threshold. A secondary finger cancels drawing and native pinch measurably changes the visible range without committing drawing changes.

The full gate also verifies fixes for mobile selected controls blocking pan, WebKit ticker Enter submission, and one-anchor Vertical Line object-list rendering. No Phase 1 regression remains in the final suite.

## Performance evidence

The V1 controlled desktop stress case imports 2,500 Demo bars, eight SMA/EMA instances, and 100 objects mixed across all ten tools (50 locked). It verifies crosshair, pan/zoom, creating/editing a 101st drawing, deleting it back to 100, and zero runtime errors. [Actual measurement](v1-performance-measurement.json): 89 RAF samples, median gap 16.7 ms, p95 16.8 ms. These are **headless scheduling gaps**, not device FPS, GPU paint performance or finger/loupe latency. Old Phase 1/2 load cases also pass.

## SEC and market evidence

Official fixtures and current live normalization cover AAPL, MSFT, NVDA, TSLA, JPM, WMT and XOM. See [expanded SEC validation](SEC_V1_VALIDATION.md). Current XOM CIK 2115436 and historical energy CIK 34088 are separately identified, never silently substituted.

The rebuilt production preview backend returned current AAPL/JPM/WMT/XOM quarterly records without mocks, preserving issuer CIK, fiscal period, revenue lineage and accession: [backend smoke](v1-live-backend-smoke.json). AAPL Yahoo 5m/1H/1D endpoints returned HTTP 200 and actual normalized delayed bars: [market smoke](v1-live-market-smoke.json). These are availability snapshots, not provider uptime, real-time or execution-quality guarantees.

Raw SEC values are matching reported standalone-duration or instant facts with concept/unit/form/filed/accession/period. EPS is raw only; balance-sheet values are instant. Derived values include compatible Q2/Q3/Q4 cumulative subtraction, negative CapEx outflow sign normalization, missing Gross Profit from aligned Revenue − Cost, and FCF = OCF − CapEx. Derivation preserves inputs/calculation. Missing or unsupported values stay null/N/A; margins require nonzero Revenue, growth matches fiscal keys.

Backtests use explicitly closed bars, signals at close and fills at next bar open. Defaults are one position/current-equity notional/zero costs, with optional fees/slippage. Open positions are marked rather than fabricated exits; short insolvency truncates with actual equity and a warning. Deterministic tests cover prefix/no-lookahead, gaps, long/short/costs, metrics and finite arithmetic.

Alerts evaluate only the selected symbol/timeframe while the page is visible, once per minute. New definitions/re-enable baseline history; new closed bars and crossing transitions trigger, high-water marks deduplicate. Drawing movement resets baseline; deletion invalidates. There is no background monitoring/push guarantee.

## Native configuration

`apps/native` pins Capacitor Core/CLI/iOS/Android 8.5.2 and reuses the root Web build. Isolated `npm ci`, CLI config parsing, config typecheck, script syntax and `build:web` all passed. The native-flag bundle omits service-worker registration; the final root build restored the normal Web PWA bundle. Safe-area injected variables have browser env fallbacks. No IPA/APK, SDK build, signing or physical wrapper result is claimed. Bundled Demo/local settings work by architecture; native SEC/Yahoo needs a deployed HTTPS backend/API base/CORS before live use. See [runbook](../apps/native/README.md).

## External review and device UAT

**External Review Pending.** Gemini/Claude reviewer tools are unavailable. [REVIEW_PACKAGE.md](../REVIEW_PACKAGE.md) records scope, source references and explicit primary accept/reject triage. There is no independent sign-off; accepted external findings must return to the primary engineer for fixes/tests.

**Physical Device UAT Pending** is the remaining user-device acceptance: actual iPhone/iPad drawing feel, loupe latency, touch accuracy, scrolling/pinch, sustained mixed load, PWA installation, safe areas and long-session stability. Mobile Chromium uses real CDP Touch Events for touch/pinch cases. WebKit runs iPhone/iPad viewports and pointer capture with touch-modality overrides for drawing; this does not establish physical Safari finger behavior or FPS.

No external blocker remains for the V1 Web/PWA engineering acceptance. No paid key, card or commercial chart license is needed. Unofficial Yahoo availability/adjustment, custom/IFRS SEC coverage and absent holiday/early-close calendars remain documented data limits, not fabricated functionality.
