# ADR-007 — Active-app local alerts
Status: accepted for V1 (2026-10-06).

Definitions are workspace-owned, symbol/timeframe scoped, persisted and exported. The pure AlertEngine receives closed bars and resolves current horizontal/ray anchor prices; React only presents definitions/events. Price-level and MA conditions compare the last closed close with the reference, use strict crossing after a finite prior comparison, and re-arm when the condition clears. The same bar cannot trigger twice.

Initial chart/history load establishes the latest baseline without replaying old notifications. Re-enabling resets evaluation state. The active symbol/timeframe is evaluated while this app is visible, with an independent one-minute provider refresh while alerts are enabled. Alert refresh never replaces the chart dataset or cancels an in-progress drawing/backtest; the chart remains an explicitly dated snapshot until the user refreshes market data. This is a local research aid, not an all-symbol backend monitor, background service, push notification or execution system. The UI must say this.

Moving a referenced drawing resets the baseline rather than inventing a price crossing. Deletion disables and invalidates the alert; undo does not silently re-enable it. Missing MA warmup is not zero. Provider or strategy errors remain isolated from chart/drawing work. Future server-side monitoring can reuse the definitions and evaluation contract.
