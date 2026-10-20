# Isolated Codex review

## Review scope and disposition

This is the isolated Codex cross-review used because the Cloud runtime did not expose Gemini or model delegation. I reviewed the data normalizer, feature/event builder, return labeling, chronological splits, portfolio accounting, cost scenarios, inference, multiple-testing adjustment, cache key, generated reports, and unit tests against the source snapshot `7e0a1fa12ac74d3d848fa88ea7ad7d47369645b2`.

**Disposition:** suitable to submit as a separated research prototype and reproducibility pipeline. It is not evidence of a deployable strategy, an unbiased full-market backtest, or a completed 2016–2025 study. No strategy passes the stated acceptance rule.

## Checks

- Feature inputs use the current close and lagged rolling references; 20/60/120/252-session breakout references exclude the current bar. Future-row invariance has a unit test.
- Events are observable after the close and returns begin at the next available symbol-file open. The code does not use the signal-day close as an assumed fill. A unit test checks that timing.
- Generic price/volume/trend events are counted for equities only; the OTC index contributes only to its dedicated index-return family. Statistics report equity and index counts separately.
- Large (>50%) gap, intraday, close-to-close discontinuities are excluded from event signals, affected event-return horizons, market breadth inputs, and strategy holding windows. This catches some split/data-error cases only; it does not establish split adjustment for ordinary-sized moves.
- The train/validation/test split is chronological. Selection uses training net Sharpe only. The test window overlaps a report created and inspected during development, so the final manifest and result rows mark it exposed; no result is called untouched, and exposure blocks the pass flag.
- Event inference uses a fixed-seed, date-clustered moving-block bootstrap with 2,000 replicates and Benjamini–Hochberg FDR within market across event definitions and horizons. Definitions with fewer than 10 distinct event dates remain descriptive only. This preserves same-date cross-sectional clusters and some time dependence, but does not capture every repeated-symbol dependence or eliminate low power in a one-year sample.
- Strategy costs are explicit scenarios, with 25 bps ranking cost for US and 50 bps for Taiwan; Taiwan sensitivity includes 30/50/100/150 bps plus a zero-cost reference. These are modeled costs, not verified broker fills, spread, tax class, or capacity.
- The local test suite covers forward leakage, event timing, market breadth, suspected corporate-action jumps, data cleaning/time zones, announcement timestamps, cost arithmetic, chronological splits, FDR, and resumable imports.

## Blocking research limits

1. Current equity history is roughly one year, not 2016–2025. The final test portions are only 52 US and 57 Taiwan market dates.
2. There is no point-in-time constituent universe, delisting return set, or reliable listing-status history. All results, including breadth events computed from the current repo snapshot, have survivor/universe selection bias.
3. Source prices use `auto_adjust=False` and `actions=False`; splits and dividends are not verified or modeled. The >50% filter cannot repair subtler actions.
4. No exchange calendar or suspension/fill data is available. “Next available symbol bar” is an explicit fill assumption, not a confirmed executable quote.
5. Only an OTC index series is present. Broad US and Taiwan listed benchmarks, full-market regimes, point-in-time size, and sector classifications are absent; unavailable excess-return and regime fields remain blank.
6. Financial snapshots lack verified point-in-time announcement timestamps, so earnings events and analyst-surprise tests are disabled.
7. Event statistics are exploratory and many definitions/horizons are correlated. FDR and the block bootstrap reduce selected inference risks but cannot validate an edge from this short sample.

## Verification

The final execution manifest, per-symbol inventory, quality flags, result tables, and testing-exposure record are the evidence source for exact output counts and dates. The review accepts the code and outputs only with the above limits carried into any later use.
