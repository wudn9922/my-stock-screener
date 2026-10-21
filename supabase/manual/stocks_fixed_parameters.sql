-- Apply before deploying the user-settings build that accepts group_id = 'ungrouped'
-- (Atlas fixed per-stock MA preferences, ADR-015).
-- Production carried UNIQUE (line_user_id, ticker): one row per ticker across all groups.
-- An ungrouped preference row would collide with the ticker's grouped row in either order,
-- blocking both the preference save and later group membership.
-- Keep "one group per ticker" for real groups and allow one dormant preference beside it;
-- uniqueness of the preference row itself stays covered by (line_user_id, ticker, group_id).
begin;
create unique index if not exists stocks_line_user_id_ticker_grouped_key
  on public.stocks (line_user_id, ticker)
  where group_id <> 'ungrouped';
alter table public.stocks drop constraint if exists stocks_line_user_id_ticker_key1;
commit;
