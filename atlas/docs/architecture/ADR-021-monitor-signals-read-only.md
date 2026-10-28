# ADR-021 — Read-only Discord monitor signal board

Status: implemented for review; no deployment, migration or independent sign-off.

The watchlist quote board gains a source beside LINE 熱門. It reads the existing
account-owned monitor state through a new, isolated `monitor-signals` function.
Existing login, user-settings, chart-drawings and monitor producers remain unchanged.
Shared server identity/grant helpers authenticate each read; the verified owner is
the only query scope. Only last successful Discord `last_alert_*` fields are projected.
This is a rolling 30-day last-state view, not a new append-only notification history.

The client binds its original storage context before asynchronous initialization,
aborts on context changes, and never persists the returned personal groups/signals.
Server and browser responses use no-store. None/device modes do not read cloud data.
Pagination is bounded and truncation explicit; errors never fabricate an empty success.

Group details coalesce each symbol's triggered MAs into a single vertically scrolling
card. Existing MiniChart / Lightweight Charts 5.2.1 supplies daily candles, volume and
zoom; native vertical page gestures are preserved. No new engine, chart primitive,
coordinate model or stock-owned default MA is introduced. Charts only mount near the
viewport and release canvases when scrolled away; fixed-height placeholders prevent jumps.

Screener virtualization retains its own bounded scroll container. The enclosing page
uses intrinsic height so its following data-health disclosure cannot overlap overflowing
list content on short phones. The app shell and navigation are untouched.

See [the Chinese runbook](../../../docs/ATLAS_MONITOR_SIGNALS.md) for the privacy model,
manual enablement sequence, source limits and actual validation. Tests use synthetic
signals and mocked services. No SQL, deployment or outbound notifications are performed.
