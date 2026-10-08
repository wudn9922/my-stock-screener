# ADR-003 — Per-symbol indicator and storage isolation

Status: Accepted / frozen for Phase 1.

IndicatorInstance is not MA1/MA2: `{id, symbol, type, period, source, visible, locked, lineWidth, color, scope}`. First implementation is SMA, registered in IndicatorRegistry; EMA/WMA can be added later. A symbol can have zero or many instances. Defaults are empty, so no unrequested settings are silently imposed.

SMA applies across timeframes using the same lookback period. `scope.timeframe` reserves per-timeframe overrides, while Phase 1 UI only exposes symbol scope. IndicatorEngine owns LineSeries by instance id and only recomputes when data revision/period/source changes. Locked instances allow visibility or unlock updates; period/source/style edits and removal are rejected at AppStore as well as disabled in UI.

IndexedDB database `atlas-terminal` has `symbols` (keyPath `symbol`) and `app` (settings key). Each symbol record contains drawings, indicators, magnet setting, preferred timeframe and view ranges by timeframe. App settings contain active symbol, watchlist order and provider. Drawing history is session-local and keyed by symbol and timeframe.

AppStore publishes immutable snapshots on commits and serializes writes. A pending counter prevents premature SAVED status. Write failures are tracked per record until that record is successfully saved; storage failures remain visible and export refuses to claim a complete backup. No persistence occurs per drawing frame. Stored view ranges include the source bar count; restoring against shorter/longer provider history adjusts relative to the data tail, preventing an empty chart after switching providers. Drawing anchors remain canonical timestamps independently of view preferences. Pan/zoom view persistence occurs at gesture completion or wheel inactivity, never on each range-change frame.

Export awaits queued writes and produces a versioned JSON for all records. Import validates bounds, finite anchors, symbols, point counts, periods, unique ids and symbol ownership via Zod, then atomically replaces both stores in a single transaction. Import is replacement, not merge; UI guidance recommends exporting first. A future schema upgrade must supply an explicit IndexedDB migration and import-version adapter. No localStorage state is used.

Unit + browser regressions require AAPL 24/58 and NVDA 43/56 with locked states to survive switching and reload without contamination. AAPL drawings must disappear on NVDA and return on AAPL. Data loading uses AbortController and generation guards to discard stale requests.

## V1 extension (2026-10-06)

### Physical-UAT correction (2026-10-07)

Volume rendering pairs direction-colored HistogramSeries with a fixed full-window MA20 LineSeries on the same overlay scale. Existing Volume period1 records and schema remain valid; the stored MA lookback is ignored for Volume. Instance color/width styles the volume average; visibility and removal apply to both. Timeframe isolation assigns each explicit Volume instance to one timeframe and disables the legacy overlay only there. Per-timeframe overrides preserve the prior intent of a legacy `false` value across all timeframes; removing explicit Volume does not resurrect the overlay in its timeframe. Time-keyed cached values support a passive numerical legend through the existing crosshair RAF, with N/A before warmup and no React hover updates or frame persistence. Indicator actions remain in the per-symbol panel.
SMA, SMA-seeded EMA and Volume are id-based symbol-owned instances. Volume ignores the MA lookback and uses actual provider volume. Legacy workspaces retain their always-visible volume overlay until an explicit Volume instance is added; its visibility and lock are then symbol-owned. Presets snapshot configuration, never IDs or symbol references; applying appends deep-copied new instances. Existing locked instances remain intact.

### V1 timeframe isolation (2026-10-07)

V3 settings require every indicator to own one timeframe; a symbol can keep 100 instances per timeframe and 800 total. New instances bind to the symbol's active timeframe. Indicator presets save only that timeframe and rebind fresh copies to the target symbol's active timeframe. Legacy unscoped symbol indicators bind to the saved preferred timeframe during the v1/v2-to-v3 migration. V3 preserves existing locked states and still permits locked instances to change visibility or unlock only.

## Superseding workspace extension — ATR widths and v4 settings (2026-10-08)

[ADR-012](ADR-012-drawing-picker-and-atr-strokes.md) adds optional indicator `widthMode: 'pixels' | 'atr'` while retaining `lineWidth` as the pixel fallback. Newly created SMA/EMA instances default to Wilder ATR(14) × 0.02 mapped through the public price scale. Existing instances, styles, locks and saved defaults keep their previous widths unless changed. Volume remains in pixel mode with its 2px default; its width never uses ATR. IndexedDB and export version 4 persist this preference while migrations preserve prior v3 timeframe ownership. Full extension verification remains pending.
