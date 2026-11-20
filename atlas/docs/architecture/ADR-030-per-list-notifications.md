# ADR-030 — Per-list notifications

Status: implemented in the PR; SQL, Edge Function and breakout service deployment pending.

我的 › 清單 chooses which lists send server notifications: 均線突破／跌破 (MA engine), 畫線突破／跌破 (drawing alerts), both or none, or 「所有清單都通知」. One JSON document per verified LINE account lives in `atlas_alert_settings` (`supabase/manual/atlas_alert_settings.sql`): `{version: 1, mode: 'all'|'custom', lists: {'group:<id>'|'watchlist:<uuid>': {ma, drawing}}}`. It is written only through `user-settings` (`alerts.settings.get/save`) after the cloud-grant check, and the table has the same RLS lock-down and mutation-time cloud trigger as the other personal tables. Chart favourites are device-only and never notify.

No row, an undeployed table, a read failure or a malformed document all mean `all`, which is exactly the previous behaviour (every grouped stock and monitored watchlist for MA, every drawing). Switching to custom starts from what was being sent. In custom mode a symbol notifies for a type when any list containing it enables that type; a watchlist with MA enabled is monitored even if its own switch is off.

The breakout runner reads the settings once per run, filters what may alert (MA configs by list; drawing entries by the symbol's group/watchlist membership) and alerts each ticker once even when it is in several lists. Index alerts are not list-based. Filtering happens before the notifier, so Discord and Web Push follow the same choice. Orphan pruning still uses the unfiltered configs and drawings, so turning a list off never deletes alert baselines.
