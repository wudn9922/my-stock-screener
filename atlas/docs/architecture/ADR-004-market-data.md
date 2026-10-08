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
