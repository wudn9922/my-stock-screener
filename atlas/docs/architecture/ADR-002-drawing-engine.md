# ADR-002 — Drawing model and gesture ownership

Status: Accepted / frozen for Phase 1.

Drawing is a first-class domain outside React. DrawingModel persists `{symbol, type, points, locked, visible, scope, style}`. Each point stores `{time, logical, price, timeframe}`. Time is canonical across timeframe/history changes; logical is the original chart position for audit. No pixel is persisted. ChartTransform projects anchors through public `logicalToCoordinate` and `priceToCoordinate` APIs.

TimeMapper binary-searches the actual bar timeline. It interpolates fractional logical values across adjacent timestamps, including session gaps. 500 **time-only whitespace points** extend the timeline for future drawing; no fake OHLC or volume is added. Logical conversion also extrapolates beyond the padded timeline. Future dates are estimates based on regular sessions and weekdays, with US Eastern DST handling for intraday bars; holidays are not yet an exchange calendar. This limitation affects date labels, not the ability to draw at N + 30.

DrawingStateMachine owns mutable transient state. Trend Line uses two distinct Press → Drag → Release gestures. P1/P2 are release positions. P2 movement renders P1 → candidate immediately at the next frame. Horizontal Line uses one gesture. Esc, pointercancel, lost capture, symbol/timeframe/provider switch and destroy discard uncommitted work. A second pointer cancels drawing ownership and restores chart handlers.

HitTester is independent of visuals. Selected unlocked handles have a 26px touch / 10px mouse hit radius; line tolerance is 12px touch / 7px mouse. Visual handles are 5px, preview strokes 1px, completed lines 2px, selected lines 2.5px. Selected handles take precedence. Editing uses a 2px mouse / 4px touch movement threshold, so a tap only selects. Endpoint dragging preserves the grab offset inside its hitbox; the loupe starts at the actual endpoint and follows the exact candidate. Preview ids are stable during a gesture. Locked objects can be selected, show no draggable handles and return gesture ownership to native chart pan.

Whole-line moves derive all points from the immutable drag-start snapshot and logical/price deltas. Magnet is forbidden on whole-line movement; endpoint magnet uses pixel distance to O/H/L/C within 18px, and is disabled beyond the last real bar. History stores bounded before/after commands per symbol (200); undo/redo covers create, endpoint edit, move, delete and lock/unlock. History is session-local, while resulting drawings are persistent.

DrawingController captures only owned pointers, temporarily disables chart pan/zoom, coalesces pointer moves into one RAF, and restores controls on every exit. Pointer Up computes the exact final candidate synchronously even if the latest RAF has not run. Persistence occurs only at commit. `touch-action:none` applies only to the chart; there is no window scroll correction. Renderer runs as an official series primitive using `PluginBase`, one cached view/renderer, bitmap coordinates and context save/restore.

Loupe is 180×125 CSS px, 2.75×, opposite the finger. Its centre is computed from the final post-magnet anchor. It uses the public chart screenshot API once per RAF during touch endpoint placement/edit, with a precision crosshair. Full chart screenshot cost is a known profiling target before declaring physical-device FPS.

Future extensions add tool geometry, hit parts and multi-stage placement behind this engine. Horizontal Ray, Rectangle, Fibonacci and Parallel Channel are **not Phase 1**.

## Phase 2 authorized extension — Horizontal Ray

Phase 2 was explicitly authorized by the user on 2026-10-06. Horizontal Ray extends the same drawing union, state machine, primitive, hit testing, history and schema; no second pointer/render/persistence system. Two release gestures set timestamps. P1 establishes the baseline; placement P2 is constrained to that price (including the loupe candidate). P2's timestamp chooses left/right extension. Editing either anchor changes the shared baseline and that anchor's timestamp. Both canonical points retain equal price. The visible ray is clipped to the pane edge; hit testing covers its extension, not just P1–P2. Whole-object movement remains snapshot-based and bypasses magnet. Existing schema version 1 records stay compatible; validation adds the ray enum and horizontal invariant without changing IndexedDB stores.

### Rectangle

Rectangle uses two canonical time/price anchors and four projected corner handles. Handle identities are P1, P2, (P1.time, P2.price), (P2.time, P1.price), so they do not jump when bounds cross. Cross-corner edits replace only their respective time/price components; no screen rectangle or additional persisted anchors. The existing endpoint grab-offset, movement threshold, magnet/loupe and command history apply to all four handles. Interior hit testing supports whole-object movement; locked interiors delegate to native chart pan. Fill opacity is 5%, keeping candles visible. No edge handles in Phase 2.

### Fibonacci Retracement

One drawing persists P1/P2, levels, symbol, scope, lock and style. Defaults are 0, .236, .382, .5, .618, .786, 1. Level price is `P2.price + level * (P1.price - P2.price)`; 0 marks the leg endpoint and 1 its origin, for either upward or downward legs. Levels project prices through the shared public transform, not by interpolating stored pixels. Editing/magnet/loupe use only the two anchors. Body hit testing covers level segments and the dashed anchor guide. Snapshot movement preserves all levels. Labels outside the pane, with <16px spacing or insufficient horizontal space are omitted; narrow panes show ratios only, wider panes may include price. Levels are not independent drawing/history records. Import validates finite, distinct retracement levels in [0,1], up to 32; UI customization/extensions remain out of scope.

## V1 extension (2026-10-06)
The same state machine accepts three release anchors for Parallel Channel, two for price/date/combined measurements, and one for Vertical Line. Channel P3 determines the second parallel edge; vertical baseline width is horizontal. Measurement labels distinguish current-timeframe logical bar distance from elapsed wall time. Styles/defaults are copied on creation and committed through existing history/storage; locks guard settings and anchors, while visibility/unlock remain available. Tool geometry and labels remain outside React, with the existing primitive RAF/controller and touch hitboxes.

### V1 regression hardening — native handler restoration
Installed 5.2.1 ChartApi.options() exposes the live chart options object. Gesture ownership therefore saves a value copy of handleScroll and handleScale only before disabling them; saving the entire live options reference cannot restore the original controls. Copying only the function-free controls also avoids cloning chart formatter callbacks. Pointer-up restoration uses the actual pointer ID after the state machine clears its gesture; cancellation/destroy still restore before clearing state. Locked-pan browser gates compare an already-defined logical view range and require actual movement, rather than treating the first persisted range as pan evidence. No change to the canonical drawing model or pointer system.

Compatibility Touch Events must not start native chart long-press tracking during a Pointer-owned drawing. The same controller suppresses that touch cycle through release; a secondary touch cancels drawing and allows native pinch. No geometry is computed by Touch Events. Native chart pan receives untouched events for locked drawings. Browser regression includes a precision hold longer than the chart long-press threshold before testing actual range movement.

## Superseding workspace extension — categorized tool picker and ATR stroke mode (2026-10-08)

[ADR-012](ADR-012-drawing-picker-and-atr-strokes.md) supersedes the prior rail-based tool-selection UI, not the drawing domain or gesture engine. One toolbar launcher opens an accessible categorized modal for the ten existing tools. Opening it cancels unfinished placement; choosing a tool invokes the existing selection path. All anchors, pointer ownership, primitive rendering, hit testing, magnet rules, history and lock behavior remain unchanged. New unspecialized drawings default to price-domain Wilder ATR(14) × 0.02, projected by the public price scale. `lineWidth` remains the persisted pixel fallback; screen pixels are not added to canonical anchors. Warmup/invalid/flat history falls back to stored pixels, and the preview/selection/native-width bounds in ADR-012 remain. Existing drawings/styles/locks are not rewritten. Full gate verification is pending.

## Authorized UAT extension (2026-10-07)

[ADR-011](ADR-011-timeframe-ownership-and-snapshot-prices.md) supersedes historical cross-timeframe defaults and Demo-only static prices. 1M is calendar-aware. Drawings/indicators/history/volume now belong to symbol+timeframe with validated atomic v3 migration. Yahoo quote OHLC is source-split-adjusted, never divided again; real static delayed snapshots and as-of metadata are published by the existing hourly weekday Actions workflow. SEC still needs a backend. Previous unadjusted wording is historical and must not guide new price normalization.
