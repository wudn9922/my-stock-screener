-- Manual only, after atlas_mexc_holdings.sql, before deploying user-settings.
-- Independent metadata-only quota. No positions, balances or plaintext keys stored.
begin;
create table if not exists atlas_private.atlas_mexc_futures_limits (
  line_user_id text not null,
  account_id uuid not null,
  next_allowed_at timestamptz not null,
  request_id uuid not null,
  expires_at timestamptz not null,
  primary key (line_user_id, account_id),
  foreign key (line_user_id, account_id)
    references atlas_private.atlas_mexc_connections(line_user_id, account_id) on delete cascade
);
alter table atlas_private.atlas_mexc_futures_limits enable row level security;
revoke all on atlas_private.atlas_mexc_futures_limits from public, anon, authenticated;
grant select, insert, update, delete on atlas_private.atlas_mexc_futures_limits to service_role;

create or replace function public.atlas_mexc_futures_read(operation text, owner_id text, payload jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare
  account_uuid uuid;
  request_uuid uuid;
  definition_value jsonb;
  credential_value jsonb;
  quota atlas_private.atlas_mexc_futures_limits%rowtype;
begin
  if owner_id is null or owner_id !~ '^U[0-9a-f]{32}$'
    or operation is null or operation not in ('claim','verify','backoff')
    or jsonb_typeof(payload) is distinct from 'object' or octet_length(payload::text)>8192
    then return jsonb_build_object('status','invalid'); end if;
  account_uuid := (payload->>'accountId')::uuid;
  request_uuid := (payload->>'requestId')::uuid;
  if account_uuid is null or request_uuid is null then return jsonb_build_object('status','invalid'); end if;
  -- Serialize against connect/disconnect and account deletion, without touching spot quotas.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('atlas-holdings:' || owner_id,0));
  select a.definition, c.credential into definition_value, credential_value
    from public.atlas_holding_accounts a
    join atlas_private.atlas_mexc_connections c on c.line_user_id=a.line_user_id and c.account_id=a.id
    where a.line_user_id=owner_id and a.id=account_uuid;
  if not found then return jsonb_build_object('status','missing'); end if;
  if definition_value->>'broker' is distinct from 'mexc'
    or definition_value->>'kind' is distinct from 'crypto-exchange'
    or definition_value->>'currency' is distinct from 'USDT'
    then return jsonb_build_object('status','invalid'); end if;
  select * into quota from atlas_private.atlas_mexc_futures_limits r
    where r.line_user_id=owner_id and r.account_id=account_uuid for update;
  if operation='claim' then
    if found and quota.next_allowed_at>clock_timestamp() then
      return jsonb_build_object('status','rate_limited',
        'retryAfter',ceil(extract(epoch from quota.next_allowed_at-clock_timestamp())));
    end if;
    insert into atlas_private.atlas_mexc_futures_limits
      (line_user_id,account_id,next_allowed_at,request_id,expires_at)
      values(owner_id,account_uuid,clock_timestamp()+interval '30 seconds',request_uuid,clock_timestamp()+interval '30 seconds')
      on conflict(line_user_id,account_id) do update set
        next_allowed_at=excluded.next_allowed_at,request_id=excluded.request_id,expires_at=excluded.expires_at;
    -- Ciphertext stays internal to the Edge handler, never part of its public response.
    return jsonb_build_object('status','ok','credential',credential_value);
  end if;
  if quota.request_id is distinct from request_uuid or quota.expires_at<=clock_timestamp()
    then return jsonb_build_object('status','conflict'); end if;
  if operation='backoff' then
    update atlas_private.atlas_mexc_futures_limits r
      set next_allowed_at=greatest(r.next_allowed_at,clock_timestamp()+make_interval(secs=>least(3600,greatest(30,(payload->>'seconds')::integer))))
      where r.line_user_id=owner_id and r.account_id=account_uuid;
  elsif credential_value is distinct from payload->'credential' then
    return jsonb_build_object('status','conflict');
  end if;
  return jsonb_build_object('status','ok');
exception when invalid_text_representation or numeric_value_out_of_range then
  return jsonb_build_object('status','invalid');
end;
$$;
revoke all on function public.atlas_mexc_futures_read(text,text,jsonb) from public,anon,authenticated;
grant execute on function public.atlas_mexc_futures_read(text,text,jsonb) to service_role;
commit;
