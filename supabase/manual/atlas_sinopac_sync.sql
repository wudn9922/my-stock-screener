-- Maintainer only; do not apply automatically. No positions, money or secrets here.
begin;
create table if not exists public.atlas_sinopac_sync (
 line_user_id text not null check(line_user_id ~ '^U[0-9a-f]{32}$'), account_id uuid not null,
 last_sync_at timestamptz, last_success_at timestamptz, last_error_code text,
 skipped_shorts integer not null default 0 check(skipped_shorts >= 0), lease_until timestamptz,
 primary key(line_user_id, account_id)
);
alter table public.atlas_sinopac_sync enable row level security;
revoke all on public.atlas_sinopac_sync from public, anon, authenticated;
grant select,insert,update,delete on public.atlas_sinopac_sync to service_role;
-- Reuse the existing storage-policy lock, including owner-default cloud access.
create or replace function public.atlas_sinopac_policy_ok(owner_id text)
returns boolean language plpgsql security invoker set search_path='' as $$
declare mode text;
begin
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('atlas-storage-user:' || owner_id, 0));
 select storage_mode into mode from public.atlas_storage_access where line_user_id=owner_id;
 return coalesce(mode,case when owner_id=public.atlas_storage_owner_id() then 'cloud' else 'none' end)='cloud';
end $$;
create or replace function public.atlas_sinopac_claim(owner_id text, account_uuid uuid)
returns jsonb language plpgsql security invoker set search_path='' as $$
begin
 if not public.atlas_sinopac_policy_ok(owner_id) then return jsonb_build_object('status','forbidden'); end if;
 insert into public.atlas_sinopac_sync(line_user_id,account_id) values(owner_id,account_uuid) on conflict do nothing;
 update public.atlas_sinopac_sync set last_sync_at=clock_timestamp(),lease_until=clock_timestamp()+interval '180 seconds'
 where line_user_id=owner_id and account_id=account_uuid
 and (last_sync_at is null or last_sync_at<=clock_timestamp()-interval '60 seconds')
 and (lease_until is null or lease_until<=clock_timestamp());
 return jsonb_build_object('status',case when found then 'ok' else 'rate' end);
end $$;
create or replace function public.atlas_sinopac_finish(owner_id text, account_uuid uuid, result_code text, short_count integer, success boolean)
returns void language sql security invoker set search_path='' as $$
 update public.atlas_sinopac_sync set last_error_code=result_code,skipped_shorts=case when success then short_count else skipped_shorts end,
 last_success_at=case when success then clock_timestamp() else last_success_at end,lease_until=null
 where line_user_id=owner_id and account_id=account_uuid;
$$;
-- The same transaction as the existing CAS write holds the policy lock. A committed
-- revoke therefore cannot be followed by an earlier in-flight broker snapshot write.
create or replace function public.atlas_sinopac_replace(owner_id text,payload jsonb,expected_revision bigint default null)
returns jsonb language plpgsql security invoker set search_path='' as $$
begin
 if not public.atlas_sinopac_policy_ok(owner_id) then return jsonb_build_object('status','forbidden'); end if;
 return public.atlas_holdings_action('replaceAccount',owner_id,payload,expected_revision);
end $$;
revoke all on function public.atlas_sinopac_policy_ok(text),public.atlas_sinopac_replace(text,jsonb,bigint) from public,anon,authenticated;
grant execute on function public.atlas_sinopac_policy_ok(text),public.atlas_sinopac_replace(text,jsonb,bigint) to service_role;
revoke all on function public.atlas_sinopac_claim(text,uuid),public.atlas_sinopac_finish(text,uuid,text,integer,boolean) from public,anon,authenticated;
grant execute on function public.atlas_sinopac_claim(text,uuid),public.atlas_sinopac_finish(text,uuid,text,integer,boolean) to service_role;
commit;
