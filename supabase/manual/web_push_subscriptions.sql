-- Atlas phone notifications (standard Web Push). Apply BEFORE deploying the updated user-settings
-- function and the breakout_alert Cloud Run service; without it the push actions answer 503 and
-- alerts keep going to Discord only.
--
-- Server-only storage, same pattern as atlas_private.device_sessions: RLS on, no policies, no
-- anon/authenticated privileges. Only service_role (the user-settings Edge Function) calls
-- public.atlas_web_push. Endpoints are bearer capabilities, so they are never returned to browsers.
-- Re-running this file is safe.
begin;
create schema if not exists atlas_private;
revoke all on schema atlas_private from public, anon, authenticated;
grant usage on schema atlas_private to service_role;

create table if not exists atlas_private.web_push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  line_user_id text not null check (line_user_id ~ '^U[0-9a-f]{32}$'),
  endpoint text not null unique check (endpoint ~ '^https://' and length(endpoint) <= 1024),
  p256dh text not null check (p256dh ~ '^[A-Za-z0-9_-]{86,88}$'),
  auth text not null check (auth ~ '^[A-Za-z0-9_-]{22,24}$'),
  user_agent text check (length(user_agent) <= 60),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  last_success_at timestamptz,
  failure_count integer not null default 0 check (failure_count >= 0)
);
alter table atlas_private.web_push_subscriptions enable row level security;
revoke all on atlas_private.web_push_subscriptions from public, anon, authenticated;
grant select, insert, update, delete on atlas_private.web_push_subscriptions to service_role;
create index if not exists web_push_subscriptions_user on atlas_private.web_push_subscriptions(line_user_id, updated_at desc);

create or replace function public.atlas_web_push(operation text, account text, payload jsonb default '{}'::jsonb)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  row_id uuid;
  total integer;
  cap integer;
begin
  if account is null or account !~ '^U[0-9a-f]{32}$' then
    raise exception 'Invalid push account' using errcode = '22023';
  end if;
  payload := coalesce(payload, '{}'::jsonb);
  -- Serialize one account's changes so the device cap holds under concurrent subscribes.
  perform pg_advisory_xact_lock(hashtextextended('atlas-web-push:' || account, 0));

  if operation = 'subscribe' then
    cap := least(greatest(coalesce((payload->>'limit')::integer, 10), 1), 20);
    -- One browser subscription belongs to whoever subscribed it last (e.g. after switching LINE account).
    insert into atlas_private.web_push_subscriptions as s(line_user_id, endpoint, p256dh, auth, user_agent)
      values(account, payload->>'endpoint', payload->>'p256dh', payload->>'auth', left(payload->>'label', 60))
      on conflict (endpoint) do update set line_user_id = excluded.line_user_id, p256dh = excluded.p256dh,
        auth = excluded.auth, user_agent = excluded.user_agent, updated_at = clock_timestamp(), failure_count = 0
      returning s.id into row_id;
    -- Keep the most recently (re)subscribed devices; older ones stop receiving notifications.
    delete from atlas_private.web_push_subscriptions s where s.id in (
      select o.id from atlas_private.web_push_subscriptions o where o.line_user_id = account
      order by o.updated_at desc, o.created_at desc offset cap);
    select count(*) into total from atlas_private.web_push_subscriptions s where s.line_user_id = account;
    return jsonb_build_object('status', 'subscribed', 'id', row_id, 'count', total);
  end if;

  if operation = 'unsubscribe' then
    with removed as (
      delete from atlas_private.web_push_subscriptions s where s.line_user_id = account
        and (s.id::text = payload->>'id' or s.endpoint = payload->>'endpoint') returning 1)
    select count(*) into total from removed;
    return jsonb_build_object('removed', total);
  end if;

  if operation = 'list' then
    return coalesce((select jsonb_agg(jsonb_build_object(
        'id', s.id, 'label', s.user_agent, 'host', split_part(s.endpoint, '/', 3),
        'created_at', s.created_at, 'last_success_at', s.last_success_at, 'failure_count', s.failure_count,
        'current', s.endpoint = payload->>'endpoint') order by s.updated_at desc)
      from atlas_private.web_push_subscriptions s where s.line_user_id = account), '[]'::jsonb);
  end if;

  if operation = 'targets' then
    return coalesce((select jsonb_agg(jsonb_build_object('id', t.id, 'endpoint', t.endpoint, 'p256dh', t.p256dh, 'auth', t.auth))
      from (select * from atlas_private.web_push_subscriptions s where s.line_user_id = account
        order by s.updated_at desc limit 20) t), '[]'::jsonb);
  end if;

  if operation = 'report' then
    update atlas_private.web_push_subscriptions s set last_success_at = clock_timestamp(), failure_count = 0
      where s.line_user_id = account and s.id::text in (select jsonb_array_elements_text(coalesce(payload->'ok', '[]'::jsonb)));
    delete from atlas_private.web_push_subscriptions s
      where s.line_user_id = account and s.id::text in (select jsonb_array_elements_text(coalesce(payload->'gone', '[]'::jsonb)));
    update atlas_private.web_push_subscriptions s set failure_count = s.failure_count + 1
      where s.line_user_id = account and s.id::text in (select jsonb_array_elements_text(coalesce(payload->'failed', '[]'::jsonb)));
    -- A device that keeps failing (expired keys, uninstalled app) is dropped after 20 attempts in a row.
    delete from atlas_private.web_push_subscriptions s where s.line_user_id = account and s.failure_count >= 20;
    return jsonb_build_object('status', 'reported');
  end if;

  raise exception 'Invalid push operation' using errcode = '22023';
end;
$$;
revoke all on function public.atlas_web_push(text, text, jsonb) from public, anon, authenticated;
grant execute on function public.atlas_web_push(text, text, jsonb) to service_role;
notify pgrst, 'reload schema';
commit;
