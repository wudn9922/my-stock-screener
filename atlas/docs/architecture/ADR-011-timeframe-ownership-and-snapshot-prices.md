# ADR-011 — Monthly candles, timeframe ownership, and static delayed prices

Status: accepted by the primary engineer, 2026-10-07, following explicit physical-UAT requirements. Supersedes the cross-timeframe defaults in ADR-002/003 and the Demo-only market-data restriction in ADR-010. Same engine, primitives, pointer system and stores remain.

## Calendar months

1M means a calendar month. Native Yahoo `1mo` data uses a ten-year range and canonical UTC first-of-month timestamps; OHLCV is preserved. Demo provides 600 completed simulated months rather than 2,500 months extending into negative Unix time. TimeMapper advances actual months and interpolates between calendar neighbors, including extrapolation beyond buffered future whitespace. The nominal 30-day interval is only a Demo volatility parameter.

Monthly closed-bar research conservatively waits until the next month's midnight in Eastern time, including DST. This intentionally avoids guessing the exchange holiday/early-close schedule. The forming month cannot generate closed-bar signals/fills/alerts. Events in monthly views map to their containing month, not the next monthly candle. Intraday clock labels are hidden for monthly views.

## Ownership and migration

New and persisted indicators require `scope.timeframe`; each drawing has exactly one `scope.timeframes` entry. Panels, legends, hit testing, counts, alerts, explicit-volume suppression and history operate on the active symbol/timeframe. Presets snapshot only that bucket and create new IDs rebound to the destination bucket. Chart commit callbacks include the captured timeframe. History records bucket snapshots and merges undo/redo into the latest other buckets, preventing cross-timeframe overwrite.

IndexedDB and JSON export advance to version3. Legacy v1/v2 is validated before atomic migration. Old global MAs have no creation-timeframe information: their deterministic home is the symbol's last preferred timeframe. Old global/multi-scope drawings use the first canonical anchor's timeframe. Existing singleton scopes, IDs, anchors, prices, visibility, styles and locks are preserved. Preferences retain `ownershipMigration` explaining the inference; UI displays the indicator home. No object is duplicated into every timeframe. Old global `legacyVolume:false` remains false for all timeframes to preserve the previous explicit intent; subsequent Volume changes update only the active timeframe. Invalid v3 global scopes are rejected; invalid migration aborts without clearing settings. Disabled mismatched drawing alerts remain auditable with a reason. Indicator capacity is100 per bucket,800 per symbol.

## Prices and split basis

Public Pages previously supplied deterministic Demo prices, including fictional SMCI/NFLX hash-based prices. Those are not historical market data and cannot be repaired by applying split ratios. Demo remains available and explicitly simulated; changing to actual data is an explicit provider choice and does not erase settings or drawings.

Actual Yahoo chart responses captured for SMCI and NFLX show their 10:1 split events and already split-adjusted OHLC. The provider retains quote OHLC and volume unchanged, ignores dividend-adjusted `adjclose`, and never divides by split events again. Symbol and USD identity are validated. Normalized records expose `priceBasis:split-adjusted`; malformed/missing metadata is not presented as verified adjustment. Current available quote uses `regularMarketPrice/regularMarketTime` with actual timestamps, not `chartPreviousClose` (the range-start price). Old normalization caches are separated by a provider cache version. This is an unofficial research prototype, not a certified execution feed or redistribution license determination.

## Free static snapshots

SnapshotProvider fetches validated same-origin normalized files under the deployment base. It works on Pages without a Node server, secret, paid service or external CORS proxy. The fixed public ticker allowlist lives in `scripts/market-symbols.json` and includes SMCI/NFLX plus20 total tickers. Other tickers report unavailable and never silently substitute Demo. Seven native Yahoo intervals are requested; unavailable4H is hidden. A failed interval is omitted rather than invented.

The existing Pages workflow collects on commits, manual dispatch and hourly weekday schedules. GitHub schedules are best-effort and may delay. Requests are serialized/throttled with timeouts and the inherited environment proxy. Successful snapshots contain actual bars, current available quote, split/dividend records, source and retrieval timestamps. New files contain only newly collected intervals so a pre-split cache is never mixed with newly adjusted intervals. A failed daily refresh retains the entire previous valid snapshot with its original timestamps through an Actions cache. No verified snapshots causes refresh/deployment failure instead of publishing an empty feed.

UI labels delayed snapshots, exact market/retrieval time and non-real-time status; snapshots older than2hours are marked aged. The ordinary bounded local TTL cache allows labelled stale offline fallback. Market JSON is not service-worker shell content. SEC still requires a backend and stays explicitly unavailable on Pages. Snapshot generation artifacts are ignored by Git; fixtures preserve a small audited actual payload for deterministic tests.

## Verification

Unit cases cover monthly calendar/leap/future/closure, strict v3 legacy migration, lock/history/preset/alert/volume isolation, actual SMCI/NFLX split-window preservation, quote metadata, dividend distinction, snapshot validation/missing/range/dedup and cache basis identity. Browser gates retain all previous interactions and add1D/1W/1M ownership/reload/backup plus static actual-fixture price tests on all four profiles. Physical-device UAT and independent external review remain separate from headless verification.

## Current-period correction — 2026-10-08

Live inspection found Yahoo native weekly/monthly aggregates still through October 6 while their appended session and metadata represented October 7. Keeping the aggregate prevented volume loss, but left its close stale. The daily response also contained an explicit null latest close. This supersedes aggregate retention for the current period only; native historical bars remain unchanged.

The latest daily explicit null close may use `regularMarketPrice` only with a matching session date, bounded market timestamp, valid OHLC bounds and real volume. Historical nulls and missing array entries are not filled. The normalized record retains the method, original bar time and quote time.

Current weekly/monthly OHLCV is reconstructed from unique validated daily sessions: first open, maximum high, minimum low, last close and summed volume. Never add an appended session to a native aggregate: revised source volume can overlap. Require matching issuer/USD/split basis, non-stale market timestamps, compatible quote prices and native opening price, and all provider-supplied rows in that bucket to be valid. History coverage guards the first period. This checks supplied observations, not a certified exchange holiday calendar. Failure omits the interval; an inconsistent daily refresh preserves the previous entire snapshot with its old timestamps.

The reconstruction includes its method, period, daily range/count, quote time and original native bar in audit provenance. Snapshot schema and disposable market cache move to version 3; workspace/IndexedDB/export version 3 is unchanged. Daily collection runs last to avoid aligning newer native metadata with an older daily quote. Snapshot validation and live smoke compare daily/weekly/monthly latest closes with the pack quote, and verify current-period volume from its daily rows. These checks address the gap in the initial pack-internal browser assertions.
