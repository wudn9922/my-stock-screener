# Atlas Research Terminal — V1

A Web-first stock research terminal with an original interface, one Lightweight Charts 5.2.1 implementation, and a separate drawing engine. V1 extends the accepted Phase 1/2 workspace; it does not replace it. Core development, offline Demo mode and tests require no paid subscription, API key, credit card or commercial chart license. This project does not use TradingView proprietary source or Advanced Charts.

Earlier V1 and Pages verification results are historical. The latest SEC snapshot, Taiwan market, and price-axis extension is deployed and passed public HTTPS and iPhone WebKit verification. Commit [3feb6337aeab4e686c0f61e259bece7250be3d40](https://github.com/wudn9922/lightweight-drawing-lab/commit/3feb6337aeab4e686c0f61e259bece7250be3d40) completed [Actions run 37785146220](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37785146220) successfully at 2026-10-08T13:36:11Z. All 16 seeded SEC packs are served and validate; NVO, QQQ, SPY, and TSM remain unavailable after SEC 403/cooldown, with no fresh runner data claimed for them. Physical-device UAT and independent Gemini/Claude review remain pending. See the current checkpoint below.

## GitHub Pages

The same Atlas source supports the project path `/lightweight-drawing-lab/`. The current deployed source is commit `3feb6337aeab4e686c0f61e259bece7250be3d40`; Actions run 37785146220 completed deployment at 2026-10-08T13:36:11Z. **Open [Atlas](https://wudn9922.github.io/lightweight-drawing-lab/) in your browser** for the deployed app. The Pages build is separate from the normal local build:

```sh
npm ci
npm run build:pages
npm run test:pages
```

`dist-pages/` contains only static files. `.github/workflows/pages.yml` builds and deploys it after a commit to `main`; the repository is already configured with **Settings → Pages → Source → GitHub Actions**. The verified URL is `https://wudn9922.github.io/lightweight-drawing-lab/`. To preview locally, run `VITE_STATIC_HOSTING=1 VITE_PUBLIC_BASE=/lightweight-drawing-lab/ npx vite preview --outDir dist-pages --port 4175` and open that project path. `npm run test:pages` starts its own isolated preview, so stop a manual server on 4175 before running the tests.

GitHub Pages cannot run the existing live Node Yahoo/SEC proxies. The deployed build serves delayed Yahoo market snapshots (including SMCI/NFLX); live Yahoo requests still require a backend. The earlier run 37764054644 received SEC company-ticker HTTP 403 and cooldown, so all 20 symbols were unavailable and AAPL returned 404; the 16 versioned seed packs resolved that missing-pack condition in the current release. TLS-verified public checks at 2026-10-08T13:38:04.608649+00:00 confirmed all assets byte-equal to the deployed build, the font license, and all 16 SEC packs with valid schema, audit lineage, source, nullable fields, and original 09:19 fetched timestamps. The public iPhone WebKit smoke passed at 2026-10-08T13:37:56.193Z with zero API requests or runtime errors. Four unseeded SEC symbols—NVO, QQQ, SPY, and TSM—remain unavailable after SEC 403/cooldown; no fresh runner data is claimed for them. Future refresh is best effort, and SEC packs are marked stale after 24 hours. The HTTP record reports partial Taiwan coverage because `0050.TW` lacks 1W/1M. The latest hosted 26-symbol market refresh in Actions run 37785146220 reports 20 available, 2 partial (`0050.TW` lacks 1W/1M; AMZN 1M was omitted after source quote agreement failed), and 4 retained-stale (`NVO`, `XOM`, `SPY`, `TSM`). Retained market packs preserve and display their original observation dates and are not presented as current quotes. XOM's retained market quote state is separate from its SEC seed, which has two quarterly facts and zero annual facts. WebKit verified TWD and native Taiwan weekly/monthly values; AAPL quarterly and annual Revenue audit UI; persistence/reload/export v4 for three timeframe-owned locked SMAs and a future horizontal drawing. Pack raw/derived provenance, including FCF lineage, passed HTTP validation; WebKit did not assert the FCF UI. Root inspected public chart and financial-panel screenshots and found axis labels including 2550/2600 and date labels coherent. See the [live HTTP evidence](docs/taiwan-financial-live-http.json) and [live browser evidence](docs/taiwan-financial-live-browser.json). Demo, drawings, indicators, Watchlist, local persistence, research backtests, foreground alerts and settings backup remain available on Pages. Yahoo backend-unavailable behavior does not silently switch providers or use fabricated prices. The normal `npm run dev` / `npm run build` / `npm run preview` paths retain their existing provider behavior.

The manifest, icons, assets, worker scope and offline caches follow the project path. Cache cleanup is confined to this registration scope; other GitHub Pages apps' caches are preserved. PWA installation requires HTTPS and a first online load. IndexedDB belongs to the hosting **origin**, not the path: another Atlas app on the same `username.github.io` origin shares its database; local Cloud settings are not transferred automatically. Export locally and Import on Pages to transfer them. See [hosting ADR](docs/architecture/ADR-010-static-hosting.md) and [hosting verification](docs/PAGES_VERIFICATION.md).

## Current workspace extension: SEC snapshots, Taiwan symbols, and price axis — engineering and public verification complete; external UAT pending

The current extension source commit [3feb6337aeab4e686c0f61e259bece7250be3d40](https://github.com/wudn9922/lightweight-drawing-lab/commit/3feb6337aeab4e686c0f61e259bece7250be3d40) was published non-force with 269 reviewed paths. [Actions run 37785146220](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37785146220) deployed successfully at 2026-10-08T13:36:11Z. The preceding run 37764054644 had SEC company-ticker HTTP 403/cooldown and AAPL 404; the current deployment serves 16 validated seeded packs. TLS-verified HTTPS at 2026-10-08T13:38:04.608649+00:00 confirmed all deployed assets byte-equal, the font license, and all 16 SEC packs' schema, audit, source, nulls, and original 09:19 timestamps. The HTTP report is `PASS_WITH_REPORTED_PARTIAL_TAIWAN_COVERAGE`: `0050.TW` still lacks 1W/1M. NVO, QQQ, SPY, and TSM remain unavailable after runner SEC 403/cooldown; no fresh runner facts are claimed for them. The public iPhone WebKit smoke passed at 2026-10-08T13:37:56.193Z with zero API requests and runtime errors. It verified TWD and native weekly/monthly Taiwan values, AAPL quarterly and annual Revenue audit UI, and persistence/reload/export v4 for three timeframe-owned locked SMAs and a future horizontal drawing. Pack raw/derived provenance, including FCF lineage, passed HTTP validation; public WebKit did not assert the FCF UI. Root inspected the public chart and financial-panel screenshots; axis values including 2550 and 2600 and the date labels appeared coherent. See [HTTP evidence](docs/taiwan-financial-live-http.json) and [WebKit evidence](docs/taiwan-financial-live-browser.json).

The collector reuses `FinancialNormalizer` without changing quarter derivation, EPS handling, nullable metrics, provenance, or audit lineage. Snapshot schema v1 is separate from the version-4 user settings/export format. Generated per-symbol output and the collector manifest live in `public/financial-data/` and remain Git-ignored. The 16 normalized public SEC packs in `data/financial-snapshots/` are intentionally versioned bootstrap facts: AAPL, MSFT, NVDA, TSLA, AMD, META, GOOGL, AMZN, SMCI, NFLX, JPM, WMT, XOM, AVGO, PLTR, and MU. Four allowlist entries remain unavailable. XOM uses current CIK `0002115436` and has two quarterly facts and zero annual facts in the seed; the pack does not claim annual coverage.

The collector validates seed packs and uses one only when the matching cache entry is missing or invalid, or when the seed's `fetchedAt` is strictly newer. It preserves the original `fetchedAt`, `generatedAt`, null values, and audit metadata, and never downgrades a newer valid cache. When the 24-hour TTL expires, the cache is marked stale and refresh is attempted; a failed refresh retains the last valid pack. A seed-write failure is isolated per issuer, and later issuers continue; the `EISDIR` plus SEC-403 continuation case has been exercised. A local mocked-403 run also confirmed 16 seed-backed symbols remain `fresh`, the four unseeded symbols (`NVO`, `QQQ`, `SPY`, `TSM`) remain `unavailable`, only four SEC calls are made, and seed timestamps compare deeply equal after collection. This is local mocked-response evidence, not hosted SEC access. The collector does not rotate User-Agents or IPs and does not use a proxy workaround. The fixed SEC client continues to identify the project truthfully; no metrics are synthesized.

Pages loads validated same-origin packs through `FinancialSnapshotProvider`; its disposable IndexedDB cache is bounded to 32 symbols and reports stale/offline state. The SEC collector filters the mixed market-symbol list to the 20 US-profile symbols, excluding Taiwan from SEC requests and the SEC manifest. Issuers or ETFs without supported SEC companyfacts remain `unsupported`; per-symbol outcomes remain `fresh`, `retained`, `unavailable`, or `unsupported`. Live SEC API use remains behind the existing backend.

Taiwan coverage keeps the existing 20 US symbols and adds delayed market snapshots for `2330.TW`, `0050.TW`, `2317.TW`, `2454.TW`, `6488.TWO`, and `8069.TWO`. The official TWSE/TPEx symbol directory has 2,251 entries. The latest hosted 26-symbol market refresh reports 20 available, 2 partial (`0050.TW` lacks 1W/1M; AMZN 1M was omitted after source quote agreement failed), and 4 retained-stale (`NVO`, `XOM`, `SPY`, `TSM`). Retained packs preserve and display their original observation dates and do not imply a current quote. The older local snapshot check had reported 25 available and `0050.TW` partial. XOM's retained market state is separate from its SEC seed, which has two quarterly facts and zero annual facts. Five selected Taiwan symbols have all seven timeframes. A bare Taiwan ticker resolves only when the directory identifies one listing; enter `.TW` or `.TWO` when a code is ambiguous. Taiwan uses TWD, `Asia/Taipei`, and a 09:00–13:30 session; no holiday calendar, Taiwan SEC facts, or TSM ADR alias is fabricated. See [ADR-013](docs/architecture/ADR-013-static-fundamentals-and-taiwan.md) and the [Taiwan/SEC snapshot verification checkpoint](docs/TAIWAN_FINANCIAL_SNAPSHOT_VERIFICATION.md).

Yahoo may provide an actual Taiwan terminal-auction sample exactly at 13:30. When validated, it is marked as a `sessionCloseObservations` entry, a flat-OHLC/zero-volume `CLOSED INSTANT` sample, not an ordinary fabricated interval. Research includes it only when the mark is valid and `asOf` is at or after its timestamp. Taiwan intraday bar counts may include this close observation; US calendars and counts are unchanged.

The axis design uses `Atlas Narrow Axis`, a 70%-horizontal-scale Barlow Condensed Regular derivative under SIL Open Font License 1.1 ([license](src/assets/fonts/OFL.txt)). It targets minimum width zero, 11px labels, `minMove: 0.01`, and redundant trailing-zero trimming only. The corrected font scales glyph advances and outlines (SHA-256 `5e465e809833ef0fa73c5a65827e921c0e02aba1facc263d606838c1bd126d1f`); Sol independently passed all 694 glyph geometries. The corrected integrated axis check passed all four profiles, and fresh read-only measurement showed at least 30.8% width reduction for ordinary samples. The earlier overlapping-font measurements are superseded. The font also affects chart time-axis dates; company UI fonts remain unchanged. Startup preloads the font and waits up to five seconds before falling back to the system font, where the width reduction is not guaranteed.

Latest gates: 225 unit tests across 29 files; lint, typecheck, normal/Pages builds; targeted collector/provider unit suite 18; normal browser 126 passed / 10 original skips in 12.8 minutes; Pages browser 32 passed / 0 failed in 3.3 minutes. Combined browser result: 158 passed / 10 original expected skips. Corrected axis measurement remains at least 30.8% width reduction for ordinary samples across four profiles; all 694 glyph geometries passed. Bootstrap execution used 16 seed-backed `fresh` SEC packs and left NVO/QQQ/SPY/TSM unavailable after SEC 403; no fresh runner facts are claimed for those four. The latest hosted 26-symbol market refresh reports 20 available, 2 partial (`0050.TW` lacks 1W/1M; AMZN 1M was omitted after source quote agreement failed), and 4 retained-stale (`NVO`, `XOM`, `SPY`, `TSM`); retained packs preserve their observation dates and are not current-quote claims. The earlier local market check had reported 25 available and `0050.TW` partial. The official Taiwan directory has 2,251 entries. BOOT-01/02 are accepted and closed. Extension engineering, deployment, and public proof are complete; physical-device UAT and independent Gemini/Claude review remain pending. Next: open the existing public URL and try the financial and Taiwan flows. Reopen the site if cached, but do not clear site data. No V2 work is authorized.

## Monthly/timeframe ownership and real prices (2026-10-07)

Select **Yahoo · 延遲快照** in Market data source to view actual Yahoo prices on Pages; **Demo is fictional, not current market data**. Snapshots are refreshed by the existing free public GitHub Actions workflow on commits/manual dispatch and best-effort hourly weekday schedules. They are not streaming or guaranteed current. Source, quote timestamp, retrieval timestamp and split-adjusted price basis remain visible; aged/offline data is marked. Yahoo already adjusts quote OHLC for splits: SMCI/NFLX are never divided a second time. Dividend-adjusted `adjclose` is not used. Native weekly/monthly history remains unchanged; only the current in-progress period is rebuilt from validated daily OHLCV and carries derivation provenance (`本週 K由日 K彙總` / `本月 K由日 K彙總`). A latest-session daily row is never added again to native aggregate volume.

Covered tickers: AAPL, MSFT, NVDA, TSLA, AMD, META, GOOGL, AMZN, NVO, SMCI, NFLX, JPM, WMT, XOM, SPY, QQQ, AVGO, PLTR, MU, TSM. Unsupported tickers or failed intervals show unavailable rather than invented prices. Maintain `scripts/market-symbols.json` to extend the public allowlist. To collect locally before building Pages:

```sh
node scripts/refresh-market.mjs
npm run build:pages
```

Generated `public/market-data/` files are not committed; the workflow collects and publishes them, retaining previous successful packs via its cache when daily refresh fails. A failed interval is omitted, and a pack never mixes new adjustment data with old intervals. No key/account/card is required. This is an unofficial prototype; production availability and redistribution rights are not guaranteed.

**1M is calendar-month data**, including future whitespace and conservative monthly closed-bar research. MA/EMA/Volume, drawings, presets, alerts and undo history are independently owned by symbol + timeframe. Version3 migration preserves every ID/anchor/style/lock: old global MAs go to the last preferred timeframe (their creation timeframe was never stored), and drawings go to the first anchor's timeframe. The panel explains the inferred MA home; all buckets remain in Export. Version4 adds optional ATR width intent while retaining pixel fallbacks and does not rewrite saved widths. JSON1/2/3 imports are validated then atomically upgraded; invalid imports leave current settings intact. Do not clear browser site data. See [ADR-011](docs/architecture/ADR-011-timeframe-ownership-and-snapshot-prices.md), [ADR-012](docs/architecture/ADR-012-drawing-picker-and-atr-strokes.md), and [timeframe verification](docs/TIMEFRAME_PRICE_VERIFICATION.md).

## Drawing picker and ATR strokes — deployment and remote verification complete; external UAT pending

The drawing side rail has been removed. A 44×44px **Drawing Tools** button with an icon and **繪圖** in the chart toolbar opens a categorized modal outside the plot. At widths up to 1099px, the duplicate delayed-snapshot CTA is hidden while the provider selector remains visible. Categories provide access to all ten existing drawing tools. The modal also exposes Select/Pan, magnet, undo/redo, zoom, future area, and reset view through their existing actions. Opening it cancels an unfinished gesture; choosing a tool closes it. The chart/controller stay mounted and the modal does not resize the chart. On mobile, category buttons and the selected category's tools use a safe-area-aware sheet with bounded scrolling.

New drawings and new SMA/EMA instances default to **0.02 × Wilder ATR(14)** stroke width, measured in price space and converted through the chart's public price scale. Insufficient, invalid, or flat OHLC data uses the stored pixel fallback. The UI offers ATR mode and fixed 1–4 CSS-pixel mode. Completed drawing strokes are rendered between 0.5 and 4 CSS pixels (and at least one raster pixel); native SMA/EMA widths round to 1–4 pixels. Preview strokes remain 1px and selected strokes at least 2.5px; edit handles are unchanged. These limits mean rendered width is not exactly 0.02 ATR at every zoom. Saved drawings, MAs, locks, and defaults retain their existing widths unless the user changes modes. Volume stays in pixel units and does not use ATR.

Historical deployment checkpoint: at that time, the public URL served commit `0e4317a108fb9e6acc4b688355d2c55ac7dbaaf1`; Actions run 37741318516 succeeded, and HTTPS asset/hash verification passed. The public iPhone WebKit smoke passed at 2026-10-08T07:09:15.433Z. The 178-test unit suite, lint/typecheck, both builds and 24 targeted browser cases passed. Focused mobile-uat and v1-journey coverage passed 20 cases across all four profiles. The final normal browser gate passed 122 cases with 10 original expected skips and no failures; the Pages gate passed all 20 cases. Combined: 142 passed / 10 original expected skips / 0 failed. Read the [HTTP evidence](docs/picker-atr-live-http.json) and [browser evidence](docs/picker-atr-live-browser.json). The picker, ATR stroke settings/defaults, and compact mobile chart controls are documented in [the picker and ATR verification record](docs/DRAWING_PICKER_ATR_VERIFICATION.md). Existing saved widths/settings were not forced to ATR; ATR could be selected in a drawing or SMA/EMA setting. Independent external review and physical-device UAT remained pending at this checkpoint.

## Install, run and verify

Node 22.12+; this Cloud session uses Node 24.19.0.

```sh
npm ci
npm run dev
```

Open http://localhost:5173. In Codex Cloud use the existing environment's Preview/port-forward control for port 5173 if available. No external deployment is assumed.

```sh
npm test
npm run lint
npm run build
npm run preview
npx playwright install --with-deps chromium webkit
npm run test:browser
```

Playwright covers desktop Chromium, mobile Chromium, iPhone WebKit and iPad WebKit. Production offline-shell tests expect `npm run preview -- --port 4173 --strictPort` to be running as well as the development server. WebKit offline tests briefly start their own isolated preview on 4174 and stop it after caching the shell.

In this Cloud session the browsers and temporary WebKit libraries use:

```sh
PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1 \
PLAYWRIGHT_BROWSERS_PATH=/tmp/pw-browsers \
LD_LIBRARY_PATH=/tmp/pw-libs/extracted/usr/lib/x86_64-linux-gnu \
npm run test:browser
```

The downloaded WebKit launcher preserves that library path. Skipping its host check avoids an ldconfig-only false negative for the extracted libraries; the actual browser still launches and runs tests. A normal system installation does not need this Cloud workaround. See [V1 verification](docs/V1_VERIFICATION.md), [SEC validation](docs/SEC_V1_VALIDATION.md), and the retained [Phase 1](docs/VERIFICATION.md) / [Phase 2](docs/PHASE2_VERIFICATION.md) reports. Headless RAF scheduling is not physical-device FPS.

## Daily use

Phone chart controls: **Enter chart fullscreen** expands the same chart and **Exit chart fullscreen** returns to the workspace. Safari versions without element fullscreen use a chart focus layout; browser chrome remains. Add to Home Screen for a standalone PWA. Open **Drawing Tools** from the chart toolbar for tool selection and drawing utilities; the picker stays outside the plot. The compact legend shows SMA/EMA values for the crosshair candle, or the latest candle when no real candle is selected. **Manage indicators** opens the existing per-symbol controls. Volume bars match candle direction (green close ≥ open, red close < open) with a fixed **20-bar volume SMA**; its first19bars are N/A, not zero. An explicit Volume indicator's color styles the average line, and Hide controls both series.

1. Enter a normalized ticker in **Symbol search** or select a Watchlist entry. Recent symbols and the Watchlist appear as suggestions; optional known company names and available cached quote/change labels are shown. Manage Watchlist provides add/remove/reorder with persistent order.
2. For AAPL, open **Indicators** (phone bottom **SMA**), choose SMA, add periods **24** and **58**. Switch to NVDA and add **43** and **56**. Each symbol AND timeframe owns independent indicator instances, styles, locks and drawings. Select 1D/1W/1M first, then configure that bucket; 1D never shows 1W settings. Chart preferences are restored per timeframe. SMA and EMA support open/high/low/close; Volume uses provider volume. Eye remains available while locked; period/source/style/removal require unlock.
3. Save a named indicator preset, then apply it to another ticker. Applying creates new instances rebound to the selected symbol and timeframe; changing one bucket cannot mutate the preset or another bucket. Zero, one or many indicators are supported.
4. Open **Drawing Tools**, choose a category, then select one of its tools. **Press → Drag → Release** defines each control point: one release for Horizontal/Vertical Line, two for Trend, Ray, Rectangle, Fib and measurements, three for Parallel Channel. The third channel point defines width. Selected endpoints/corners/width handles edit anchors; dragging the body moves the immutable original snapshot. Touch shows a precision loupe and uses large invisible hitboxes.
5. Lock in the floating drawing toolbar or Drawings panel. Locked drawings stay selectable; dragging them pans the chart. Settings exposes ATR or fixed pixel line width, style, opacity and visibility, rectangle/channel fill and Fib levels/labels. Workspace Settings saves per-tool defaults for new drawings. Defaults are copied rather than shared. Existing locked styles remain unchanged until unlocked.
6. **Show future area** reveals whitespace to the right of actual candles. Draw there without fake OHLC. Magnet applies only to placement/anchor edits near actual candle O/H/L/C, never whole-object moves or future space. Undo/redo: Ctrl/Cmd+Z and Ctrl/Cmd+Shift+Z; Escape cancels, Delete removes an unlocked selection. Input fields keep their ordinary keyboard behavior.
7. Open **Financials**, choose Quarterly/Annual and fiscal period. Overview, Income Statement, Cash Flow and Balance Sheet expose Revenue/EPS/Net Income/FCF histories, margins and fiscal YoY/QoQ. Select a value for concept/unit/form/filing/accession/period and raw/derived calculation lineage. US financials are actual SEC facts even when prices are visibly simulated Demo data. Taiwan SEC financials are unsupported and are not inferred from another company or source.
8. Open **Research → Backtest**. Choose MA Cross or Price Cross MA, SMA/EMA periods, long/short direction, capital and optional commission/slippage. Signals are formed on closed bars and fills occur at the **next bar open**. Results show trades, win rate, total return, drawdown, average trade, profit factor, exposure, date range and chart entry/exit markers. This is research, not brokerage execution.
9. Open **Research → Alerts**. Create a level, moving-average or Horizontal Line/Ray cross alert; enable/disable/delete as needed. Alerts evaluate the **selected symbol/timeframe only**, once per minute while the page is visible. Initial history establishes a baseline without replaying historical alerts. Moving a referenced drawing resets its baseline; deletion invalidates the alert. Closing the app stops evaluation; there is no background monitoring or push service.
10. **Export settings** backs up every symbol plus Watchlist, drawings/styles/defaults, indicators/presets, locks, alerts and workspace/chart preferences. **Import settings** validates first and atomically replaces settings. Export a backup first. Invalid imports and failed migrations preserve existing stored data. SEC facts, market cache and backtest results are excluded.
11. Install the PWA from a supported browser or iOS Safari **Share → Add to Home Screen**. HTTPS/localhost is required. Production caches its shell; local settings and Demo work offline. Missing live data shows unavailable; an expired cached response is explicitly stale.

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

IndexedDB `atlas-terminal` **version 4** keeps `symbols` and `app` stores. The v1/v2/v3 migrations preserve drawings, indicators, locks, order, preferences and saved widths, then assign legacy global objects a timeframe owner. Version4 adds optional `widthMode` for ATR vs pixels while retaining `lineWidth` as a fallback. Failed upgrades abort; failed import does not clear current data. Commit writes are serialized; export awaits writes and refuses unresolved storage errors. Version4 JSON backups also accept validated legacy v1/v2/v3 envelopes. Market snapshot schema remains version3 and is separate. Drawings and their session-local 200-command histories are isolated by symbol and timeframe; indicators and volume preferences are scoped the same way. There is no localStorage settings store. Clearing browser data removes the workspace; JSON export is the portable backup.

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

## Attribution

[TradingView Lightweight Charts™](https://www.tradingview.com/lightweight-charts/) is Apache-2.0; its attribution link remains visible. Atlas assets and drawing interaction code are original. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
