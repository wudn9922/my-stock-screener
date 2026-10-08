# ADR-008 — V1 workspace migration and backup
Status: accepted for V1 (2026-10-06); timeframe ownership extension accepted (2026-10-07).

Keep the existing atlas-terminal IndexedDB symbols/app stores. Database version 2 added validated workspace fields (defaults, presets, alerts, recent symbols, contextual panel/debug preferences). Database version 3 assigns every drawing and indicator a single timeframe owner and adds per-timeframe legacy Volume overrides. Upgrades from versions 1 and 2 validate and write in the versionchange transaction. A validation failure aborts it, preserving the old database rather than silently clearing records.

Export envelope version 3 includes all symbol settings and workspace models. Legacy version 1 and 2 envelopes are validated, then migrated in memory before any import write. Unknown versions, malformed ownership/ids/styles/levels and enabled orphan or out-of-scope drawing alerts fail before writes. V3 imports reject global or multi-timeframe drawings and indicators without a timeframe. Import replaces app and symbols in one transaction; publication occurs only after completion. Export waits for queued writes and refuses to claim success when persistence failed. Presets contain the current timeframe's instances; application deep-copies them, creates fresh instance IDs and rebinds each instance to the target symbol's current timeframe.

## Timeframe ownership migration (2026-10-07)

An existing single-timeframe drawing scope is preserved. Legacy global or multi-timeframe drawings are assigned to the timeframe of their first canonical anchor. A legacy indicator with no timeframe is assigned to its symbol's saved preferred timeframe because its creation timeframe is unavailable. Migrated symbol preferences retain `ownershipMigration` metadata (`fromVersion`, `indicatorHome`, `drawingRule`) to explain those choices. IDs, anchors, styles and locks are preserved.

An enabled drawing alert whose timeframe does not match its migrated drawing is disabled with a visible reason; it is never left as an enabled orphan. Visibility does not affect a drawing alert reference. Legacy `legacyVolume: false` becomes a false override on every supported timeframe; a legacy true or missing value keeps the overlay enabled on other timeframes. Adding or removing explicit Volume disables only its own timeframe. The v3 symbol record allows up to 100 indicator instances per timeframe and 800 total.

Drawing history is keyed by symbol and timeframe, remains session-local, and stores only the edited timeframe bucket. Undo and redo merge that bucket into the latest symbol state so an edit in one timeframe cannot restore stale drawing records from another timeframe.

Market data is disposable and bounded in a separate atlas-market-cache database with provider/symbol/timeframe/range identity and TTL; it is excluded from settings backup. Stale offline results are explicitly labelled. User settings persistence errors stay visible; cache quota failures never erase settings or hide a successful provider fetch.

## Superseding workspace extension — v4 ATR width mode (2026-10-08)

[ADR-012](ADR-012-drawing-picker-and-atr-strokes.md) extends settings storage from v3 to v4 to preserve optional `widthMode: 'pixels' | 'atr'` for drawings and SMA/EMA. Existing `lineWidth` remains the pixel fallback, and legacy fixed widths are not rewritten. IndexedDB v1/v2/v3 upgrades and settings v1/v2/v3 imports validate and replace data atomically; v3 timeframe ownership, IDs, anchors, prices, styles, presets, locks and preferences remain intact. Market snapshot schema version 3 is independent and unchanged. Full v4 migration and picker verification remains pending in the current workspace checkpoint.
