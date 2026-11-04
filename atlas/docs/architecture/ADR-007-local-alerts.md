# ADR-007 — Active-app local alerts
Status: accepted for V1 (2026-10-06).

Definitions are workspace-owned, symbol/timeframe scoped, persisted and exported. The pure AlertEngine receives closed bars and resolves current horizontal/ray anchor prices; React only presents definitions/events. Price-level and MA conditions compare the last closed close with the reference, use strict crossing after a finite prior comparison, and re-arm when the condition clears. The same bar cannot trigger twice.

Initial chart/history load establishes the latest baseline without replaying old notifications. Re-enabling resets evaluation state. The active symbol/timeframe is evaluated while this app is visible, with an independent one-minute provider refresh while alerts are enabled. Alert refresh never replaces the chart dataset or cancels an in-progress drawing/backtest; the chart remains an explicitly dated snapshot until the user refreshes market data. This is a local research aid, not an all-symbol backend monitor, background service, push notification or execution system. The UI must say this.

Moving a referenced drawing resets the baseline rather than inventing a price crossing. Deletion disables and invalidates the alert; undo does not silently re-enable it. Missing MA warmup is not zero. Provider or strategy errors remain isolated from chart/drawing work. Future server-side monitoring can reuse the definitions and evaluation contract.

## 2026-11-04: existing server drawing monitor shortcut (PR-A / A5)

The local AlertEngine contract stays unchanged. A cloud-authorized user may explicitly create a
horizontal drawing at a price alert's level, through the same AppStore/history and drawing-sync
pipeline as a manually drawn line. The existing GCP monitor consumes that cloud drawing. No new
notification client, webhook, server, schedule, SQL or Supabase function is introduced.
This shortcut waits for successful drawing synchronization, checks current authorization again on
click, uses the loaded stock/timeframe's canonical bar anchor, and reuses an identical visible line.
Browser alert direction, disable and deletion apply only to the local reminder; server monitoring
follows the existing drawing rules (both crossing directions) until the line is hidden or deleted.
The panel labels both channels and keeps their different lifecycles in an expandable explanation.
