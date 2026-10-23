-- Account storage authorization. Apply manually after reviewing the owner identity below.
-- This creates only policy metadata and enforcement; it does not move or delete user data.
-- Deploy BOTH user-settings and chart-drawings with the shared _shared/storageAccess.ts helper.
-- The deployed ADMIN_LINE_USER_ID and atlas_storage_owner_id() MUST identify the same owner.
-- Existing owner bootstrap: cloud unless explicitly changed. Every other account: none.
-- Switching to device/none retains old cloud rows but blocks access and further writes.

begin;

create table if not exists public.atlas_storage_access (
  line_user_id text primary key check (line_user_id ~ '^U[0-9a-f]{32}$'),
  storage_mode text not null check (storage_mode in ('none', 'device', 'cloud')),
  label text not null default '' check (char_length(label) <= 80),
  updated_at timestamptz not null default clock_timestamp(),
  updated_by text not null check (updated_by ~ '^U[0-9a-f]{32}$')
);
alter table public.atlas_storage_access enable row level security;
revoke all on public.atlas_storage_access from public, anon, authenticated;
grant select, insert, update, delete on public.atlas_storage_access to service_role;

-- If ADMIN_LINE_USER_ID was overridden, replace this value with that exact ID before applying.
create or replace function public.atlas_storage_owner_id()
returns text language sql immutable set search_path = pg_catalog as $$
  select 'Ua0037dbd5037b64fc76804c60520ad76'::text;
$$;
revoke all on function public.atlas_storage_owner_id() from public, anon, authenticated;
grant execute on function public.atlas_storage_owner_id() to service_role;

create or replace function public.atlas_storage_policy_change()
returns trigger language plpgsql security definer set search_path = pg_catalog, public as $$
declare
  target text;
begin
  target := case when tg_op = 'DELETE' then old.line_user_id else new.line_user_id end;
  -- A bounded metadata table, populated only by an explicit owner action (never by login).
  perform pg_advisory_xact_lock(hashtextextended('atlas-storage-policy', 0));
  perform pg_advisory_xact_lock(hashtextextended('atlas-storage-user:' || target, 0));
  if tg_op = 'UPDATE' and new.line_user_id is distinct from old.line_user_id then
    raise exception using errcode = '42501', message = 'Storage policy account cannot be changed';
  end if;
  if tg_op <> 'DELETE' then
    if new.updated_by <> public.atlas_storage_owner_id() then
      raise exception using errcode = '42501', message = 'Storage policy owner does not match ADMIN_LINE_USER_ID';
    end if;
    if tg_op = 'INSERT' and (select count(*) from public.atlas_storage_access) >= 2000 then
      raise exception using errcode = '23514', message = 'Storage policy account limit reached';
    end if;
    new.updated_at := clock_timestamp();
    return new;
  end if;
  return old;
end;
$$;
revoke all on function public.atlas_storage_policy_change() from public, anon, authenticated;
drop trigger if exists atlas_storage_policy_change on public.atlas_storage_access;
create trigger atlas_storage_policy_change before insert or update or delete on public.atlas_storage_access
for each row execute function public.atlas_storage_policy_change();

create or replace function public.atlas_require_cloud_storage()
returns trigger language plpgsql security definer set search_path = pg_catalog, public as $$
declare
  target text;
  mode text;
begin
  if tg_table_name = 'index_configs' then
    target := public.atlas_storage_owner_id();
  else
    target := case when tg_op = 'DELETE' then to_jsonb(old)->>'line_user_id' else to_jsonb(new)->>'line_user_id' end;
    if tg_op = 'UPDATE' and (to_jsonb(new)->>'line_user_id') is distinct from (to_jsonb(old)->>'line_user_id') then
      raise exception using errcode = '42501', message = 'Personal data account cannot be changed';
    end if;
  end if;
  if target is null or target !~ '^U[0-9a-f]{32}$' then
    raise exception using errcode = '42501', message = 'Invalid storage account';
  end if;
  -- The same lock is acquired by grant/revoke. Once a revoke commits, no older writer can
  -- commit later using an earlier permission check. No row lock is added here (avoids lock inversion).
  perform pg_advisory_xact_lock(hashtextextended('atlas-storage-user:' || target, 0));
  select storage_mode into mode from public.atlas_storage_access where line_user_id = target;
  mode := coalesce(mode, case when target = public.atlas_storage_owner_id() then 'cloud' else 'none' end);
  if mode <> 'cloud' then
    raise exception using errcode = '42501', message = 'Supabase persistence is not granted for this account';
  end if;
  if tg_op = 'DELETE' then return old; end if;
  return new;
end;
$$;
revoke all on function public.atlas_require_cloud_storage() from public, anon, authenticated;

-- Close old direct REST/RLS paths too. Service-role consumers must check policy for reads;
-- these triggers additionally enforce policy at mutation time, including scheduled services.
do $$
declare relation_name text;
begin
  foreach relation_name in array array['groups', 'stocks', 'chart_drawings', 'atlas_watchlists', 'breakout_alert_state'] loop
    if to_regclass('public.' || relation_name) is null then continue; end if;
    if not exists (select 1 from information_schema.columns where table_schema = 'public'
      and table_name = relation_name and column_name = 'line_user_id') then
      raise exception 'Personal table % has no line_user_id; review schema before enabling storage policy', relation_name;
    end if;
    execute format('alter table public.%I enable row level security', relation_name);
    execute format('revoke all on public.%I from public, anon, authenticated', relation_name);
    execute format('drop trigger if exists atlas_require_cloud_storage on public.%I', relation_name);
    execute format('create trigger atlas_require_cloud_storage before insert or update or delete on public.%I for each row execute function public.atlas_require_cloud_storage()', relation_name);
  end loop;
  if to_regclass('public.index_configs') is not null then
    execute 'revoke insert, update, delete on public.index_configs from public, anon, authenticated';
    execute 'drop trigger if exists atlas_require_cloud_storage on public.index_configs';
    execute 'create trigger atlas_require_cloud_storage before insert or update or delete on public.index_configs for each row execute function public.atlas_require_cloud_storage()';
  end if;
end;
$$;

commit;

-- Verification (read-only): private table privileges must be false for anon/authenticated.
select public.atlas_storage_owner_id() as owner_must_match_edge_function_admin;
select line_user_id, storage_mode, label, updated_at from public.atlas_storage_access;
select table_name, privilege_type, grantee from information_schema.role_table_grants
 where table_schema = 'public' and table_name in
   ('groups', 'stocks', 'chart_drawings', 'atlas_watchlists', 'breakout_alert_state', 'atlas_storage_access')
 and grantee in ('anon', 'authenticated', 'PUBLIC');
-- Rollback: redeploy the previous functions first, then drop only the new triggers/functions
-- if intentionally restoring the old authorization behavior. Do not drop/delete user tables.
-- Removing a grant defaults non-owners to none; it defaults the owner to cloud.
