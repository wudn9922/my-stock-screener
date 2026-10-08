# ADR-001 — Single chart engine

Status: Accepted / frozen for Phase 1 (2026-10-06).

Physical-UAT correction2026-10-07: the numeric legend lives outside the plot, replacing overlaid indicator action cards. Candle price-scale margins reserve the bottom volume region; histogram and MA20 share one overlay scale. App-shell native fullscreen keeps dialogs within the fullscreen root; unsupported/denied native fullscreen uses transient CSS focus. Neither path remounts the chart or changes canonical drawing coordinates. Zoom actions are outside the plot on the existing rail. Normal mobile spacing is reduced while active drawing controls retain44px touch targets.

Use React 19 + TypeScript + Vite and exactly one Lightweight Charts engine. Pin `lightweight-charts` to **5.2.1**. On 2026-10-06 both npm metadata and the official latest release reported 5.2.1. Its release notes describe compatible 5.x enhancements and fixes, including crosshair/marker hot-path performance and dataset replacement under a crosshair; no breaking migration was identified. Installed `dist/typings.d.ts` is authoritative. Use `addSeries(CandlestickSeries)` and `attachPrimitive`.

ChartEngine owns candles, volume, price/time scales, lifecycle, OHLC header, indicator series and chart preferences. ChartTransform delegates conversions to public chart/series APIs. React never recreates the chart for pointer movement. Native Lightweight Charts pan, zoom, pinch and crosshair remain in use.

Official `lightweight-charts` and `lightweight-charts-plugin-authoring` skills are vendored in `.agent-skills/` and read during implementation; the cloud's `.agents/` is read-only. The official `create-lwc-plugin@0.2.0` Series Primitive scaffold and the official vertical-line source were inspected. The renderer uses the same PluginBase/view/renderer lifecycle and toolkit bitmap coordinate helpers. The disposable scaffold stays outside the app; it is not a second engine. `@tradingview/lwc-toolkit@1.0.0` APIs were verified locally.

Lightweight Charts is Apache-2.0 with attribution requirements. Keep the built-in attribution logo and README link. No Advanced Charts license, proprietary code, private JS or third-party brand assets are used. Atlas icons are original.

Sources:

- https://github.com/tradingview/lightweight-charts/releases/tag/v5.2.1
- https://github.com/tradingview/lightweight-charts/tree/master/.github/skills
- https://github.com/tradingview/lightweight-charts/blob/master/packages/lwc-plugin-vertical-line/src/vertical-line.ts

Changes to engine, canonical coordinates or hot-path ownership require an explicit follow-up ADR and interaction regressions. Do not introduce KLineChart or a parallel native implementation.

## V1 extension (2026-10-06)
Keep pinned 5.2.1. Public createSeriesMarkers owns SEC filing/corporate events and research entry/exit markers in the same candle series; no second chart or private API. Marker times are aligned to an available real bar, with SEC events labelled filing dates rather than earnings announcements. IndicatorEngine adds EMA/Volume using public Line/Histogram series, preserving the original volume overlay until an explicit symbol-owned Volume instance takes control.

## Superseding workspace extension — drawing picker and natural price axis (2026-10-08)

[ADR-012](ADR-012-drawing-picker-and-atr-strokes.md) supersedes the earlier vertical drawing-rail placement described in the physical-UAT correction above. A 44px Drawing Tools launcher now opens a categorized modal outside the plot; the rail's reserved width is removed. The right price scale uses public `minimumWidth: 40`, preserving native label sizing and precision. Lightweight Charts remains pinned to 5.2.1; the picker does not remount or resize the chart, and no chart engine or coordinate API is replaced. Full verification remains pending in the current workspace checkpoint.
