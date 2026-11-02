# ADR-004 — Free market data and provider boundary

Status: Accepted / frozen for Phase 1.

MarketDataProvider defines normalized `getBars(symbol, timeframe, range?, signal?)` and `getQuote(symbol, signal?)`. BarResult carries bars, provenance, session, delay and adjustment metadata. UI and chart never depend on a Yahoo response shape. Capabilities distinguish the implemented 5m/1H/1D from future 1m/15m/30m/4H/1W. Only implemented timeframes are visible. Extended hours are not fabricated.

DemoProvider is default: 2,500 deterministic simulated regular-session candles per ticker/timeframe, fixed as-of 2026-10-05. It works without a market-data network and clearly identifies SIMULATED data. Generated timeline caching avoids rebuilding session calendars on each symbol change. Demo price values are fictional and do not represent historical market prices.

YahooProvider is an isolated **unofficial prototype**, no key/credit card/paid SDK. A Vite dev/preview middleware proxies a fixed Yahoo URL, validates ticker/timeframe, filters null OHLC entries, applies a 12-second request timeout and caches for 60 seconds. Server requests use Undici EnvHttpProxyAgent to honor the managed environment’s inherited proxy and CA settings. Provider normalization validates the response and sorts/deduplicates timestamps. It never accepts arbitrary destination URLs. It exposes raw/unadjusted OHLC; volume and quote are normalized. Rate limits, endpoint availability, licensing for production redistribution and split-adjustment semantics need a production provider decision. Static deployment has no `/api/yahoo` backend: Demo remains functional; Yahoo fails explicitly instead of fabricating fallback prices.

Prototype failure is visible with a button to switch to Demo. Switching data providers is explicit and keeps symbol drawings; old anchors may be outside the new provider's history. No API key is a core dependency. A production backend may replace the prototype with Polygon/Massive/Finnhub/broker providers without replacing chart/drawing models.

## V1 extension (2026-10-06)
Demo supports 5m/15m/30m/1H/4H/1D/1W scheduled simulated data. Yahoo exposes only supported direct intervals; 4H is unavailable for this prototype. Capability metadata controls the UI. A bounded IndexedDB TTL cache includes provider, symbol, timeframe and exact range. Source, last bar, delayed/simulated and cache state remain visible. Closed-bar research uses conservative regular-session end times (including DST and final shortened intraday bars); a production trading holiday/early-close calendar remains out of scope. Provider corporate events are actual split ratios/dividend amounts or explicitly unavailable.

## Authorized UAT extension (2026-10-07)

[ADR-011](ADR-011-timeframe-ownership-and-snapshot-prices.md) supersedes historical cross-timeframe defaults and Demo-only static prices. 1M is calendar-aware. Drawings/indicators/history/volume now belong to symbol+timeframe with validated atomic v3 migration. Yahoo quote OHLC is source-split-adjusted, never divided again; real static delayed snapshots and as-of metadata are published by the existing hourly weekday Actions workflow. SEC still needs a backend. Previous unadjusted wording is historical and must not guide new price normalization.

## Single-symbol manual refresh (2026-10-27)

The optional `MarketDataProvider.refreshBars(symbol, timeframe, signal)` stays behind the provider boundary.
The screener router refreshes the selected source only: Yahoo requests use an isolated raw-response cache,
a unique query value and `cache: no-store`; calendar periods also refetch that symbol's daily bars.
The outer cache validates and replaces the selected bars/quote only after success, with revision guards
preventing older downloads from replacing refreshed data. Manual refresh never falls back to stale success.
The workspace retains its current chart on error and cancels the update on navigation/timeframe changes;
canonical drawings, indicators and view preferences remain owned by the existing store and chart engine.
Source timestamps and delayed/static provenance remain authoritative; this does not add a real-time feed.

## Read-only world assets in the chart workspace (2026-10-31)

The screener router resolves the 12 registered world research identities before checking stock/index
providers. `WorldAssetProvider` validates the published world-assets index and daily candles together,
offers daily/weekly/monthly bars and quotes, and explicitly has no corporate events. A missing or invalid
registered asset never falls back to a similarly named stock. BTC retains the existing absent-index-only
archive fallback and the explicit BTC/USDT identity check. No producer or durable input is changed.

The registry reuses the inline charts' deterministic `^R…` storage symbols, so drawings and indicator
ownership remain in the existing account-scoped AppStore; there is no migration or second chart engine.
Source dates remain date labels, with the existing continuous-UTC mapper for BTC and the existing daily
aggregator for weeks/months. Asset display units and price precision come from the registered instrument;
they are not stock currency/volume assumptions. Indices and assets do not acquire stock financials.

The optional below-chart P/E plot reuses the existing financial-history loader, weekly provider prices
and audited historical EPS calculation. It does not change the provider contracts or calculate old P/E
from current EPS. Data is requested near the viewport and missing/invalid histories fail locally.

Header/screener valuation helpers live in `fundamentals/peFigures.ts` and are re-exported by the site's
data facade. Shared screener conditions and the Node collector import the pure module directly, so
the collector does not bundle browser market providers or Vite-only raw BTC assets. The valuation
formulas and collector decisions are unchanged; a real esbuild entry-point test enforces this boundary.
