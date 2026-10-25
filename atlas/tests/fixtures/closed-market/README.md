This fixture contains genuine retained Taiwan scan rows (2492.TW, 1301.TW,
3673.TW) for 2026-10-22 and the rebuilt breadth for that session. It reproduces
the 2026-10-25 incident: a market-wide zero-volume placeholder dated 2026-10-23
had emptied the daily scan and breadth, despite complete earlier trading data.

The original scan is `docs/report/latest.json` at repository commit
`1e03e3a2a3f4b4fdf1b1dde03c94f5c354f3468e`. Recovery uses
`scripts/restore_closed_market_snapshot.py` against the daily CSVs at code base
`3b51aafcaa43e93d0c94d3126b24e664113ee64b`. Saved prices and volumes are validated
against that source. Original scan metrics remain intact; only the subsequently
introduced `volumeAvg20` is calculated from the complete real 20-session window.
Breadth is recomputed after projecting out the market-wide closed dates.

The three-row excerpt is intentionally small; the full validated recovery has
92 scan rows and 1,949 active breadth constituents (494/1,947 above MA20 and
720/1,945 above MA60). No source CSV or permanent archive is rewritten. Browser
tests vary only the generation date/clock to check weekends and long closures.
