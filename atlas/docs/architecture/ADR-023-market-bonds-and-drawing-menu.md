# ADR-023 — Market sections, bond reference bands and drawing quick actions

Status: implemented for review; primary self-review only, no independent sign-off.

## Full-market breadth and chart controls follow-up (2026-10-30)

The Japan view's short label is 日債. US and Japan headline titles stay on one
line without removing the selected Fed bound. Stock view and analysis triggers
share the existing StockTabs group. Recommendation and volatility details remain
floating menus; volume analysis contains only its results. The chart engine,
canonical drawing models and account storage boundaries are unchanged.

Production breadth now belongs to the universe generated owner and consumes the
same complete listing/cache reader and coverage gates as full-market entry
success rates. Daily publication no longer produces partial-list breadth. The
legacy breadth CLI and full-market entry engine remain available unchanged.
Screener chart review reuses StockChartList and saved-parameter precedence.
Research displays the published full-market RS percentile before its existing
price-return comparison. Theme charts enable the existing ChartEngine volume
series only for observed ETF/constituent OHLCV, with real ticker volume units;
all other research chart callers retain their original default. See
[coverage, safety and tests](../MARKET_COVERAGE_CHARTS.md).

The market content navigator has three sections: 大盤, 全球 and 題材. 大盤
retains every existing country tab and remembers the last country while visiting
another section. Route aliases remain compatible. The application shell and
bottom navigation are unchanged.

The global page adds a separate Japan sovereign spread view. It reuses TimeChart
and the existing rates loader/cache pattern. Only same-origin, small generated
reports are fetched by the browser. MOF/FRED collection runs in the existing
daily/rates workflows; no extra writer, server function or notification is added.
The new collector writes only jp-rates.json and yield-band-params.json. Existing
rates reports, permanent prices, financial records and the K-line archive are
untouched. Failed sources retain dated observations/coefficients; a missing report
shows an explicitly dated attachment baseline rather than claiming live data.

The user-supplied yield-band formulas and coefficient snapshot are versioned.
US and Japan models are validated independently, with full-sample completed-month
OLS and sample spread estimates. New complete samples can refit each country
monthly; failed, truncated or regressive samples cannot advance the saved fit date.
US uses the Fed target upper bound and Japan uses the BOJ target. Source/fit dates,
sample range and the historical interpretation of the band remain available.

New drawings without a saved custom default use 2 CSS pixels. Existing drawing
styles, ATR options and saved user defaults are preserved. The gesture state
machine emits an ephemeral host-local click/release point only when selecting or
finishing an edit. A small DOM toolbar positions itself within the plot, including
the indicator legend's offset, and prefers outside the projected shape to keep
its other handles/body reachable. It offers lock/delete/settings/deselect. Editing
uses the existing settings component in a dialog. Mobile no longer needs a second
action row below the chart. Active-gesture instructions also float inside the
plot so entering/completing a tool cannot resize the price scale. The official
primitive, canonical time/price anchors,
gesture ownership, native locked pan, history and persistence are unchanged;
pointer movement does not update React state or write storage.

See [sources, rollout and verification](../MARKET_BONDS_DRAWING_CONTROLS.md).

## UI follow-up, 2026-10-30

US and JP share the three-number summary and summary/band/spread ordering; JP's
published point target is never expanded into an invented range. Compact world
labels fit a five-column phone row. Temporary screener conditions now belong to
the explicit URL, with group/fresh-visit resets; saved strategies and legacy
preferences/imports remain intact. The stock drawing dialog focuses Cancel and
closes after a successful settings commit, without changing the shared dialog,
gesture model or persistence schema. See [behavior and verification](../BOND_FILTER_EDITOR_FOLLOWUPS.md).
