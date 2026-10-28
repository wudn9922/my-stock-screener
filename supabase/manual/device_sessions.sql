-- Remembered-device sign-in: a 90-day sliding session per installed Atlas. Apply before deploying the
-- updated user-settings and chart-drawings functions. Only SHA-256 hashes of random device tokens are
-- stored; storage grants are still checked on every request. No browser has table/RPC privileges.
begin;
create schema if not exists atlas_private;
revoke all on schema atlas_private from public, anon, authenticated;
grant usage on schema atlas_private to service_role;
create table if not exists atlas_private.device_sessions (
  token_hash text primary key check (token_hash ~ '^[0-9a-f]{64}$'),
  user_id text not null check (user_id ~ '^U[0-9a-f]{32}$'),
  user_agent text check (length(user_agent) <= 200),
  created_at timestamptz not null default clock_timestamp(),
  last_used_at timestamptz not null default clock_timestamp(),
  expires_at timestamptz not null default clock_timestamp() + interval '90 days'
);
alter table atlas_private.device_sessions enable row level security;
revoke all on atlas_private.device_sessions from public, anon, authenticated;
grant select, insert, update, delete on atlas_private.device_sessions to service_role;
create index if not exists device_sessions_user on atlas_private.device_sessions(user_id, last_used_at);
create index if not exists device_sessions_expiry on atlas_private.device_sessions(expires_at);

create or replace function public.atlas_device_session(
  operation text, session_hash text, line_user_id text default null, agent text default null
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  session atlas_private.device_sessions%rowtype;
begin
  if session_hash is null or session_hash !~ '^[0-9a-f]{64}$' then
    raise exception 'Invalid device session' using errcode = '22023';
  end if;
  if operation = 'create' then
    if line_user_id is null or line_user_id !~ '^U[0-9a-f]{32}$' then
      raise exception 'Invalid device session owner' using errcode = '22023';
    end if;
    delete from atlas_private.device_sessions s where s.expires_at <= clock_timestamp();
    -- At most 10 remembered devices per account; the least recently used ones are signed out.
    delete from atlas_private.device_sessions s where s.token_hash in (
      select o.token_hash from atlas_private.device_sessions o
      where o.user_id = line_user_id order by o.last_used_at desc offset 9);
    insert into atlas_private.device_sessions(token_hash, user_id, user_agent)
      values(session_hash, line_user_id, left(agent, 200)) returning * into session;
    return jsonb_build_object('status', 'created', 'expiresAt', session.expires_at);
  end if;
  if operation = 'revoke' then
    delete from atlas_private.device_sessions s where s.token_hash = session_hash;
    return jsonb_build_object('status', 'revoked');
  end if;
  if operation <> 'use' then raise exception 'Invalid device session operation' using errcode = '22023'; end if;
  select * into session from atlas_private.device_sessions s where s.token_hash = session_hash for update;
  if not found then return jsonb_build_object('status', 'missing'); end if;
  if session.expires_at <= clock_timestamp() then
    delete from atlas_private.device_sessions s where s.token_hash = session_hash;
    return jsonb_build_object('status', 'missing');
  end if;
  -- Sliding expiry, written at most hourly (access is rechecked every minute while open).
  if session.last_used_at <= clock_timestamp() - interval '1 hour' then
    update atlas_private.device_sessions s set last_used_at = clock_timestamp(),
      expires_at = clock_timestamp() + interval '90 days' where s.token_hash = session_hash;
  end if;
  return jsonb_build_object('status', 'active', 'userId', session.user_id);
end;
$$;
revoke all on function public.atlas_device_session(text, text, text, text) from public, anon, authenticated;
grant execute on function public.atlas_device_session(text, text, text, text) to service_role;
notify pgrst, 'reload schema';
commit;
