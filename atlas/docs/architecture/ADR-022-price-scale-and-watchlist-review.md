# ADR-022 — Price-axis context and watchlist chart review

Status: implemented for review; no independent sign-off or deployment.

The shared ChartEngine must not carry a manual native price range into another
symbol/timeframe. Re-enable candle price-scale autoscaling on context changes and
on explicit Reset View, using the pinned library's public applyOptions API.
Same-context refresh retains manual scaling; saved logical views, canonical
drawings and symbol/timeframe-owned indicators remain unchanged. A browser test
renders synthetic 20/2,000-price datasets and checks real candle pixels after a
manual axis drag, catching the original empty-chart regression.

The watchlist board owns its source tabs, top-level saved-list selector and
read-only chart-review toggle. ConsolePage still owns list definitions, evaluation,
history and synchronization, with route IDs selecting a list exactly as before.
The recent-search source is removed from this board without deleting search history.

StockChartList reuses MiniChart and the existing provider; fixed-height cards mount
charts only near the viewport, dispose offscreen charts, and append 20 rows at a
time. Native vertical/wheel page scrolling is preserved. No default stock MA or
new persistence layer is introduced. Existing Discord signal details are unchanged.

All four stock tabs use shared controls styling in both workspace and financials,
independent of which lazy route is loaded first. Financial controls remain compact
and scroll with their data. No API, account, navigation-shell or data-format change.

See [implementation and verification](../CHART_WATCHLIST_FOLLOWUPS.md).
