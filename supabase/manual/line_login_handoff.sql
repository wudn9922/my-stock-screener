-- One-time LINE login transport only. Apply before deploying the updated user-settings function.
-- No personal settings, grants or accounts are created. No browser has table/RPC privileges.
begin;
create schema if not exists atlas_private;
revoke all on schema atlas_private from public, anon, authenticated;
grant usage on schema atlas_private to service_role;
create table if not exists atlas_private.line_login_handoffs (
  id uuid primary key,
  read_hash text not null check (read_hash ~ '^[0-9a-f]{64}$'),
  write_hash text not null check (write_hash ~ '^[0-9a-f]{64}$'),
  bucket text not null check (bucket ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default clock_timestamp(),
  expires_at timestamptz not null default clock_timestamp() + interval '2 minutes',
  ciphertext text check (length(ciphertext) <= 16384)
);
alter table atlas_private.line_login_handoffs enable row level security;
revoke all on atlas_private.line_login_handoffs from public, anon, authenticated;
grant select, insert, update, delete on atlas_private.line_login_handoffs to service_role;
create index if not exists line_login_handoffs_expiry on atlas_private.line_login_handoffs(expires_at);
create index if not exists line_login_handoffs_bucket on atlas_private.line_login_handoffs(bucket);

create or replace function public.atlas_line_login_handoff(
  operation text, intent_id uuid, read_hash text default null, write_hash text default null,
  caller_bucket text default null, encrypted_token text default null
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  intent atlas_private.line_login_handoffs%rowtype;
begin
  if operation = 'begin' then
    if read_hash is null or read_hash !~ '^[0-9a-f]{64}$' or write_hash is null
       or write_hash !~ '^[0-9a-f]{64}$' or caller_bucket is null or caller_bucket !~ '^[0-9a-f]{64}$'
       or read_hash = write_hash then
      raise exception 'Invalid login intent' using errcode = '22023';
    end if;
    -- Bound both global storage and anonymous creation. The short lock covers only begin.
    perform pg_advisory_xact_lock(hashtextextended('atlas-line-login-capacity-v1', 0));
    delete from atlas_private.line_login_handoffs h where h.expires_at <= clock_timestamp();
    if (select count(*) from atlas_private.line_login_handoffs) >= 512
       or (select count(*) from atlas_private.line_login_handoffs h where h.bucket = caller_bucket) >= 5 then
      return jsonb_build_object('status', 'busy');
    end if;
    insert into atlas_private.line_login_handoffs(id, read_hash, write_hash, bucket)
      values(intent_id, read_hash, write_hash, caller_bucket) returning * into intent;
    return jsonb_build_object('status', 'pending', 'expiresAt', intent.expires_at);
  end if;
  select * into intent from atlas_private.line_login_handoffs h where h.id = intent_id for update;
  if not found then return jsonb_build_object('status', 'missing'); end if;
  if intent.expires_at <= clock_timestamp() then
    delete from atlas_private.line_login_handoffs h where h.id = intent_id;
    return jsonb_build_object('status', 'missing');
  end if;
  if operation = 'complete' then
    if write_hash is distinct from intent.write_hash then return jsonb_build_object('status', 'missing'); end if;
    if intent.ciphertext is not null then return jsonb_build_object('status', 'used'); end if;
    if encrypted_token is null or length(encrypted_token) > 16384 then
      raise exception 'Invalid login payload' using errcode = '22023';
    end if;
    update atlas_private.line_login_handoffs h set ciphertext = encrypted_token where h.id = intent_id;
    return jsonb_build_object('status', 'complete');
  end if;
  if operation not in ('poll', 'cancel') then raise exception 'Invalid login operation' using errcode = '22023'; end if;
  if read_hash is distinct from intent.read_hash then return jsonb_build_object('status', 'missing'); end if;
  if operation = 'cancel' or intent.ciphertext is not null then
    delete from atlas_private.line_login_handoffs h where h.id = intent_id;
    return jsonb_build_object('status', 'consumed', 'ciphertext', intent.ciphertext);
  end if;
  return jsonb_build_object('status', 'pending');
end;
$$;
revoke all on function public.atlas_line_login_handoff(text, uuid, text, text, text, text) from public, anon, authenticated;
grant execute on function public.atlas_line_login_handoff(text, uuid, text, text, text, text) to service_role;
notify pgrst, 'reload schema';
commit;
