## Architecture

```text
src/app/          React shell, workspace preferences, commit-only AppStore
src/chart/        one ChartEngine, coordinate transforms, Future TimeMapper, StrokeWidth
src/drawing/      controller/state machine/primitive renderer/hit testing,
                  magnet/snapshot movement/history/loupe/persistence
src/tools/        tool-specific line/ray/rectangle/Fib/channel/measurement geometry
src/indicators/   registry/engine, SMA/EMA and Volume instances
src/market-data/  normalized providers, Taiwan market profiles/symbol catalog,
                  deterministic Demo, Yahoo proxy, TTL cache
src/fundamentals/ SEC backend/client and static snapshot provider, CIK resolution,
                  concept/period normalization, lineage and filing events
src/strategy/     pure StrategyEngine and BacktestEngine, signals and metrics
src/alerts/       local definitions, closed-bar evaluation and drawing references
src/storage/      versioned schemas, IndexedDB migration, atomic backup/import
src/errors/       scoped user errors; panel boundaries isolate render failures
src/ui/           accessible panels, drawing picker, controls, charts and mobile/tablet drawers
apps/native/      optional Capacitor wrapper configuration for the same Web build
```

[ADRs 001–009](docs/architecture/) record engine, drawings, indicators, providers, financial correctness, backtest execution, alerts, storage and PWA/native boundaries. Official chart and primitive-authoring skills are vendored under `.agent-skills/` because Cloud `.agents` is protected. The installed 5.2.1 typings and public Primitive/marker APIs are the implementation reference.

### Drawing and rendering model

Anchors `{time, logical, price, timeframe}` use timestamp and price as canonical coordinates; logical is advisory across timeframe/history changes. Every object owns symbol, visible/locked state, scope, style and tool anchors. Fib has one level list; channel has three anchors, measurements two. Screen pixels exist only in transient projections. Timeframe changes remap timestamps rather than interpreting old logical indices as new bars.

Pointer movement updates mutable interaction state, coalesced into at most one RAF primitive redraw; it does not rerender React or write storage. Releases commit app state. Native chart handlers are restored on all exits. Whole-object moves always use drag-start snapshots and preserve shape; commit rechecks locks. Endpoint visuals are small while touch hitboxes are independent. The 180×125 CSS-pixel, 2.75× loupe centres on the actual constrained/snapped release candidate. Chart `touch-action:none` and pointer capture own gestures without page-scroll corrections. OHLC/crosshair updates stay outside React pointer hot paths.

### Indicators and storage

Indicator instances are keyed by id and owned by symbol plus a required timeframe: SMA/EMA/Volume, period/source, visible/locked, color/width. EMA is SMA-seeded; source changes recompute only the affected series. Explicit Volume instances take control from the legacy overlay; hiding/removing Volume cannot silently restore that overlay.

Standalone IndexedDB `atlas-terminal` **version 4** keeps `symbols` and `app` stores. The v1/v2/v3 migrations preserve drawings, indicators, locks, order, preferences and saved widths, then assign legacy global objects a timeframe owner. Version4 adds optional `widthMode` for ATR vs pixels while retaining `lineWidth` as a fallback. Failed upgrades abort; failed import does not clear current data. Commit writes are serialized; export awaits writes and refuses unresolved storage errors. Version4 JSON backups also accept validated legacy v1/v2/v3 envelopes. Market snapshot schema remains version3 and is separate. Drawings and their session-local 200-command histories are isolated by symbol and timeframe; indicators and volume preferences are scoped the same way. In `/my-stock-screener/atlas/`, IndexedDB (`atlas-terminal-screener`) and preferences (`atlas-account-screener`) are scoped by verified LINE account and storage mode; `none` stays in RAM. Legacy unscoped data remains untouched until an authorized user explicitly imports it in the console. Clearing browser data removes the device copy; cloud-authorized data also syncs through Supabase.

### Market data and events

Providers expose normalized OHLCV/quotes/capabilities/events rather than raw responses. Demo supplies **2,500 deterministic simulated bars**, fixed as-of 2026-10-05, for 5m/15m/30m/1H/4H/1D/1W, and600 calendar-based completed 1M bars. Yahoo exposes available direct timeframes through capability metadata; unsupported 4H is hidden. Yahoo is an unofficial free prototype: **Prototype only，不保證 production availability。** Vite dev/preview includes a fixed-destination proxy, no paid key. Static hosting uses the distinct SnapshotProvider for real delayed data; the live API/native mode needs an equivalent backend. There is no silent Demo substitution.

The separate bounded `atlas-market-cache` database keys provider identity/symbol/timeframe/range, deduplicates concurrent requests, retains at most 150 responses and uses provider TTL (Yahoo 60 seconds, Demo one day). Offline fallback is marked stale; quote source/as-of/delayed/simulated state stays visible. Watchlist fetches cached available quotes without polling. Manual refresh updates the chart snapshot. Bars are not represented as a guaranteed real-time or certified adjusted feed.

Reliable provider splits/dividends use markers and recorded raw values/ratios; Demo reports events unavailable. SEC 10-Q/10-K filing markers are labelled **SEC Filing**, not invented earnings announcements. They are added when financial records load. Marker kinds coexist with backtest entry/exit markers without creating another chart engine.

### Fundamentals and correctness

SEC EDGAR uses generic ticker/CIK mapping, identification User-Agent, 2 requests/second throttling, caching and request deduplication. Only the backend reads SEC response shapes. For standalone SEC service:

```sh
npm run build
SEC_USER_AGENT='Your organization your-real-contact@example.com' npm run serve:fundamentals
```

Use your real identification/contact. Default local identification truthfully describes Atlas research. The standalone service listens on 127.0.0.1:8788; configure SEC_HOST/SEC_PORT and reverse-proxy `/api/fundamentals`. SEC CORS prevents a static frontend from directly replacing this backend. Multi-instance production needs shared cache/rate limiting. No secrets are bundled. SEC errors do not interrupt drawings/chart.

All metrics may be null (shown N/A). Concept priority mappings include revenue alternatives, bank revenue net of interest expense, cost/net income/EPS/OCF/CapEx/cash/equity fallbacks, without guessing overlapping totals. Balance-sheet facts are instant; income/cash flow are duration. Compatible Q2/Q3/Q4 cumulative subtraction records derived lineage; ambiguous periods remain missing. EPS is never obtained by subtracting cumulative EPS. FCF is derived **OCF − CapEx**, only when both exist. Margins require nonzero revenue. YoY/QoQ use fiscal keys, not array position. Forms, filing dates, accession, raw concept/unit/period and derived calculations remain auditable.

Official fixtures and live smoke cover AAPL, MSFT, NVDA, TSLA, JPM, WMT and XOM. Current XOM resolves to ExxonMobil Holdings CIK 2115436, with limited holding-company history; the historical CIK 34088 fixture is separately labelled and never overrides current ticker resolution. See [expanded SEC validation](docs/SEC_V1_VALIDATION.md). Optional network smoke: `npm run test:sec-live` and `node scripts/sec-v1-smoke.mjs`. Ordinary regression tests use auditable official test fixtures without network or fabricated financials.

### Backtests and alerts

Strategy/backtest modules operate on explicitly closed bars, use no future values for signals and fill at next-bar open. One position uses current equity notional, no leverage/pyramiding; fees/slippage are configurable. Final open positions are marked to the last closed close, never given a fabricated exit. Short insolvency truncates with an explicit warning rather than invented liquidation. Profit factor without losses is N/A; marked total return/drawdown include open positions, while win rate/average trade use closed trades. Yahoo adjustment/execution quality is not certified.

Alert definitions persist; evaluation is active-page-only, current symbol/timeframe, closed-bar/high-water deduplicated, with baseline initialization and transition re-arming. Drawing moves reset baselines; deletion disables invalid references. Future server-side alerts can implement the same contract. V1 sends no orders and provides no always-on notifications.

## Mobile, PWA and native

The screener site's 自選 page now offers 清單 / 持股. Holdings use browser-only CSV/XLSX
or pasted-table import, editable previews, account snapshot replacement and dated
market prices for P&L. Guest data is RAM-only; device and cloud follow existing
account authorization. See [the holdings runbook](../docs/ATLAS_HOLDINGS.md) for
format limits and the maintainer-only SQL → user-settings → frontend rollout.
MEXC read-only API synchronization follows in PR B after #199, with server-encrypted
credentials, cost review and visible-page polling. See [its manual setup runbook](../docs/ATLAS_MEXC_SYNC.md)
before enabling the website key-entry form; no deployment or SQL application is automatic.

Desktop supports chart with contextual panels; iPhone uses bottom sheets/tabs without permanently shrinking the chart. iPad uses a wider contextual panel while retaining chart pan/drawing access. Forms/actions have accessible labels, keyboard focus and 44px targets; controls are not hover-only. Safe-area CSS handles notches/home indicators. Debug is OFF by default and exposes source, symbol/timeframe, counts, storage and errors when enabled.

The production-only versioned service worker caches shell/assets and excludes API responses. HTTPS/localhost is required for installation. Native configuration/documentation under [apps/native](apps/native/) reuses the same Web build; Xcode, signing, device testing and Android SDK are separate platform tasks, not Cloud Web blockers.

## Limitations and next work

- **Physical Device UAT Pending:** real iPhone/iPad drawing feel, loupe latency, touch accuracy, scroll/pinch, long-session performance, PWA install and safe-area behavior. Headless WebKit is not a physical Safari verification.
- Exchange schedules handle Eastern DST/weekends, not exchange holidays/early closes. Future whitespace horizon is 500 bars; eventual holiday/session dates can differ. Intraday partial sessions are conservative.
- Timestamp anchors stay fixed across timeframe/provider changes, but different history windows may leave objects outside the viewport. Manual vertical price-scale range is not persisted.
- Undo is session-local; settings/locks persist. Import replaces the workspace and clears history.
- Yahoo is an unofficial delayed research source with no availability or adjustment guarantee. Corporate events may be unavailable; never guessed.
- SEC custom extensions/IFRS/unusual fiscal transitions are conservative N/A. Q4 EPS frequently remains N/A. Derived cumulative values depend on compatible reported inputs; inspect lineage for restatement differences.
- Alerts stop when inactive/closed and monitor the selected chart only. Backtests have simplified execution, no borrow/margin/holiday/split correction engine, and are research signals rather than trading advice or brokerage orders.
- Gemini/Claude independent review remains external; [REVIEW_PACKAGE.md](REVIEW_PACKAGE.md) includes scopes, primary triage and reviewer instructions. No independent sign-off is claimed.

V1 retains all Phase 1/2 regression gates. Further development requires explicit V2 authorization; no brokerage, auto trading, accounts/cloud sync, screener, news, options, payments or Pine clone is added here.
