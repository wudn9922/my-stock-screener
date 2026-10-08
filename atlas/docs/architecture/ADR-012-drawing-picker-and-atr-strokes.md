# ADR-012 — Drawing picker, natural price axis and ATR stroke defaults

Status: Accepted by primary engineer for the authorized screenshot UAT extension (2026-10-08). Independent external review remains pending.

## Tool selection

Remove the vertical rail and its reserved width. One 44px Drawing Tools launcher lives in the existing chart toolbar, outside the plot and within the fullscreen app root. It opens an original categorized modal picker inspired by the supplied interaction reference. Categories expose only the ten implemented tools. Select/Pan, magnet, history, zoom, future and reset actions continue to call their existing implementations.

Opening the picker cancels unfinished gestures. Selecting a tool calls the existing selection path and closes the modal. The chart instance and drawing controller are retained. Modal backgrounds are inert; focus, Escape, backdrop dismissal, bounded scrolling and safe areas work on phone, tablet and desktop. Opening the picker must not resize the underlying chart.

## Price axis

Use the installed Lightweight Charts 5.2.1 public `rightPriceScale.minimumWidth` option with a 40px floor, replacing 64px. This is a minimum, not a maximum. Actual labels can require more space. Preserve price precision and native label sizing; do not crop canvases or abbreviate prices to manufacture a narrower axis.

## Stroke interpretation

The primary interpretation of the requested 0.02 ATR default is **price-domain thickness**, not a price-distance band or the numeric ATR value used directly as pixels. Optional clarification was requested; absent steering, proceed with this explicitly stated interpretation.

New unspecialized drawings and newly added SMA/EMA instances default to `widthMode: 'atr'`. ATR uses Wilder's 14-bar calculation on the active symbol/timeframe's available OHLC: first true range is high minus low, later true ranges include previous-close gaps; seed with the first fourteen true ranges and then apply Wilder smoothing. Cache the latest ATR once per loaded data revision. Insufficient, invalid or flat data uses the stored pixel fallback; never persist screen widths or fabricate ATR.

Map `ATR × 0.02` through public candle `priceToCoordinate` at the drawing's first-anchor price or the latest close for indicators. Completed drawing widths are bounded to 0.5–4 CSS px and rasterized to at least one physical pixel. Native SMA/EMA LineSeries accepts only integer widths 1–4, so use the nearest supported width. These visibility limits and quantization mean rendered widths are not an exact 0.02 ATR at every zoom. Preview width remains 1px, selected width remains at least 2.5px, and invisible hitboxes and handles remain unchanged.

Volume and its average use volume units and retain pixel widths. Candles, grid, crosshair and control handles also retain their existing styles. Width refresh uses existing chart visual RAF scheduling and public size/logical-range notifications, with passive native scale-gesture observations where needed; no new gesture ownership, React pointer state, data recalculation or persistent writes.

## Storage and existing preferences

Retain required `lineWidth` as the pixel fallback and add optional validated `widthMode: 'pixels' | 'atr'`. Absence means the legacy explicit pixel width. Existing drawings, MAs, locks and saved defaults are not rewritten to ATR. Settings can select 0.02 ATR or an explicit pixel width; locks still prevent style changes.

Upgrade IndexedDB and settings export to version 4. Versions 1/2 retain their existing ownership migration; version 3 keeps all existing timeframe ownership, IDs, anchors, prices, styles, presets, locks and preferences. Migration validates and commits atomically. Version 4 prevents older importers silently discarding the ATR intent. Market snapshot schema version 3 is unrelated and stays unchanged.

## Verification

Retain all existing drawing, timeframe, migration, mobile and PWA regressions. Add ATR formula/gap/warmup/invalid/fallback/quantization tests; version-3-to-4 migration and export/import integrity; picker tool reachability, focus/inert/Escape/fullscreen and stable chart height; naturally sized normal and high-price axes; ATR persistence and preserved legacy fixed-width/locked settings. Browser coverage remains Desktop Chromium, Mobile Chromium, iPhone WebKit and iPad WebKit. Automated WebKit is not physical Safari or a device FPS benchmark.
