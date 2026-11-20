-- Per-list notification settings (Atlas 我的 › 清單 › 通知). Run once in the Supabase SQL Editor
-- after account_storage_permissions.sql. Additive: no existing table, row or alert state changes.
-- No row means "所有清單都通知" (today's behaviour), so nothing stops before a user opts in.
begin;
create table if not exists public.atlas_alert_settings (
  line_user_id text primary key check (line_user_id ~ '^U[0-9a-f]{32}$'),
  settings jsonb not null check (jsonb_typeof(settings) = 'object'),
  updated_at timestamptz not null default clock_timestamp()
);
alter table public.atlas_alert_settings enable row level security;
revoke all on public.atlas_alert_settings from public, anon, authenticated;
grant select, insert, update, delete on public.atlas_alert_settings to service_role;
-- LINE tokens are verified by user-settings; no browser role receives a table policy.
create or replace function public.atlas_alert_settings_updated_at()
returns trigger language plpgsql set search_path = public as $$
begin
  new.updated_at = clock_timestamp();
  return new;
end;
$$;
drop trigger if exists atlas_alert_settings_updated_at on public.atlas_alert_settings;
create trigger atlas_alert_settings_updated_at before update on public.atlas_alert_settings
for each row execute function public.atlas_alert_settings_updated_at();
-- Same mutation-time cloud check as the other personal tables (function from account_storage_permissions.sql).
drop trigger if exists atlas_require_cloud_storage on public.atlas_alert_settings;
create trigger atlas_require_cloud_storage before insert or update or delete on public.atlas_alert_settings
for each row execute function public.atlas_require_cloud_storage();
commit;
