# ADR-015: Automatic watchlist writes and dormant stock parameters

## 2026-10-29 修訂：保留原分組股票列

此修訂取代下文舊的「無分組優先／加入分組另建一筆」決策。原有分組的個股參數優先於無分組固定列及清單預設；多筆分組保留最小 ID 的原筆為共同來源。圖表讀取並補入已保存的日線 SMA。編輯先顯示原值，使用既有 ID 更新；若使用者確認畫面明列的無分組重複筆，先更新原筆成功再刪重複筆。取消、載入失敗及主要寫入失敗均不刪資料，不做背景遷移。詳見 [STOCK_PARAMETER_CONSISTENCY](../STOCK_PARAMETER_CONSISTENCY.md)。沒有新增 SQL 或 Edge Function 變更。

以下保留原始決策與歷史部署背景。

Status: implemented; production deployment and account acceptance pending.

Watchlist definitions remain in the validated local v1 document and the existing atlas_watchlists API. Saving selected-list settings schedules an authenticated cloud write. A per-list queue serializes settings, opening snapshots and deletion; a returned version advances metadata while preserving edits made during the request. Deletion removes the remote record first and retains the local copy on failure or conflict. An unlinked record is checked for a remote copy before deletion to cover a timed-out create. No other-account link is automatically moved or deleted. Backups remain local operations and do not delete remote lists omitted from an import.

Permanent per-stock MA preferences reuse the existing stocks unique key with group_id='ungrouped'. This reserved value never confers report or alert membership. The main report and breakout service read verified/configured-user preferences and apply them only to active grouped stocks or enabled watchlist members. Precedence is fixed stock preference, then existing per-group stock values or watchlist defaults, then group fallback. Removing a member, group membership or watchlist never deletes its preference; explicit deletion of the ungrouped record restores fallback behavior. Adding a grouped stock through the console reuses any fixed preference.

The existing user-settings handler validates fixed stock writes through the same LINE verification, user predicates, ticker/MA limits and unique key as grouped records. Updates remain possible at the 500-record limit. A missing owned row returns 404 rather than claiming an edit/deletion succeeded.

Older breakout builds infer unfamiliar groups as active. Deploy both consumers before enabling ATLAS_FIXED_PARAMETERS_ENABLED=1 on the Edge Function; until enabled, ungrouped saves fail closed with 503. No new schema migration is required, subject to checking any production-only group constraint. This rollout is separate from the existing watchlist table/collector deployment.

The console uses a native dialog for browser focus trapping and background isolation. Editing and deletion are explicit confirm/cancel operations; failures stay in the dialog. Existing parameter records retain text search and group filters in a bounded scrolling region. The chart engine, canonical drawing model and IndexedDB schema are unchanged.

Offline coverage exercises the actual Edge handler with stubbed LINE/database dependencies, queued rapid edits and conflicts, dormant preferences in the actual daily report loader and breakout configuration, and desktop/phone/WebKit interactions. Mock tests do not establish live Supabase deployment or delivery of real notifications.
