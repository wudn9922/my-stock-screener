# Atlas interface refinements

This change implements the twelve requested interface adjustments without deploying or merging.

- Financials use a compact scrolling header. Quality and search controls sit at the upper right; external queries and explanatory notes expand on demand.
- Opening stocks or indices no longer creates report-prescribed SMA indicators. Mini charts also show candles without preset moving averages. Explicitly saved indicators remain owned by their symbol and timeframe.
- The full-market entry-success card offers the latest calculation and saved snapshot dates. An index generated from the existing immutable archives makes those snapshots discoverable. The existing archive interval is **20 calendar days**, and each result retains its own calculation time, market data date, parameters and outcomes.
- Secondary explanations and provenance expand on demand across the report pages and chart panels; notes use quieter styling. Errors and data availability remain visible.
- Screener volume uses the latest complete 20-session average. Taiwan quantities display lots (1 lot = 1,000 shares), including fractional lots; US quantities display shares. Raw volume and calculations stay in shares. Unknown or insufficient volume is unavailable rather than estimated from daily volume or monetary turnover.
- The retired daily-change condition is ignored in old URLs and saved filters. Other change displays and sorting remain available.
- MA distance uses the smallest explicitly configured per-stock SMA, or EMA20 when no fixed parameters exist. Report-group parameters do not become personal fixed parameters. Interactive and scheduled watchlist evaluation use the same references and preserve observations when required references cannot be obtained.
- EPS acceleration selects quarterly YoY, annual YoY, or both. Each compares consecutive YoY observations within its own period.
- Chart sheets reserve space above the mobile navigation, including its safe area, so their final controls remain reachable.
- Price-volume research retains abnormal RVOL and the four rising/falling, expanding/shrinking volume ratios, with their thresholds and observation window.

## Review triage

Delegated read-only code review identified six findings. The primary engineer accepted all six: stabilize empty screener items; keep notes readable on touch screens; force-add the newly ignored history index in the legacy publishing workflow; preserve unknown volume in averages; retain that lineage through source/cache processing; and preserve watchlist observations when a configured MA reference is missing. The fixes and associated regressions are included.

This review is an internal Codex review. It does not claim external review or physical-device Safari verification.

## Data and release boundaries

No synthetic production snapshots are added. Existing generated-branch workflows must regenerate the new volume field and history index before those data are available on the hosted site. Older reports without the average expose it as unavailable. A pre-existing cache that already replaced unknown volume with zero cannot be reliably reconstructed; genuine zeros are not retrospectively relabeled. Historical dates are offered only when real archive files exist. No production deployment, merge or auto-merge is part of this change.

## Validation

- Clean `npm ci` completed from the lockfile.
- `npm test -- --maxWorkers=1`: 98 files, 1,148 tests passed. A preceding concurrent run had one SEC fixture test exceed its existing five-second test budget; the complete serial run passed without changing its assertions or timeout.
- `npm run lint`, `npm run build`, and the screener-path Pages build passed.
- Python: report metrics 41, full-market universe/cache 52, K-line integrity 12, full-market breakout/history 15, and generated-branch publishing 22 tests passed.
- Financials and entry-success browsers: 129 cases passed across desktop Chromium, mobile Chromium and iPhone WebKit. Site navigation and the added workspace regressions: 29 passed, one desktop-only sheet case intentionally skipped.
- Research, lots, explanations, chips, rates and themes: 104 browser cases passed, with four existing modality-specific skips. One main-page 404 caused by parallel builds sharing an output directory passed after isolating that output; the test server now supports `SITE_OUT_DIR`.
- Chart symbol persistence, layout and timeframe ownership: 12 browser cases passed across desktop Chromium, mobile Chromium, iPad WebKit and iPhone WebKit. The long iPad round trip was rerun serially with a 120-second case budget after concurrent execution exhausted its 45-second case deadline; the successful run completed in 35 seconds without changing assertions.
- Screener, fundamental periods, fixed MA references, saved strategies and console: 99 browser cases passed across desktop Chromium, mobile Chromium and iPhone WebKit. Three theme-rotation assertions were updated to target the outer disclosure after nested explanations were added, and those three cases passed on rerun.
