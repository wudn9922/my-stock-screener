-- Atlas market-chart usage quota (manual migration).
--
-- DEPLOY: run this whole file once in the Supabase SQL editor BEFORE the market-chart function that calls
-- public.atlas_market_chart_quota is deployed (merging to main auto-deploys it). Without it every signed-in
-- user who is not cloud-authorised or the admin gets HTTP 503 from market-chart. Safe to re-run.
--
-- ADJUST: the limits are not stored here. market-chart passes them on every call from the QUOTA_LIMITS
-- block in supabase/functions/market-chart/access.ts; edit that block and redeploy the function. The
-- 10-minute window and the Asia/Taipei day are defined in the function below (edit and re-run this file).
--
-- CLEANUP: the RPC deletes buckets older than 2 days on about 1 call in 200. Manual equivalent:
--   delete from atlas_private.atlas_market_chart_usage where bucket_start < now() - interval '2 days';
-- Today's site total:
--   select request_count from atlas_private.atlas_market_chart_usage where subject = 'site' and period = 'day'
--   order by bucket_start desc limit 1;
--
-- Browsers have no table or RPC privileges; only the service role (the Edge Function) can call it.
begin;
create schema if not exists atlas_private;
revoke all on schema atlas_private from public, anon, authenticated;
grant usage on schema atlas_private to service_role;

-- One row per (subject, period, bucket): subject is a LINE user ID or 'site'.
create table if not exists atlas_private.atlas_market_chart_usage (
  subject text not null check (subject = 'site' or subject ~ '^U[0-9a-f]{32}$'),
  period text not null check (period in ('window', 'day')),
  bucket_start timestamptz not null,
  request_count integer not null default 0 check (request_count >= 0),
  primary key (subject, period, bucket_start)
);
create index if not exists atlas_market_chart_usage_age on atlas_private.atlas_market_chart_usage(bucket_start);
alter table atlas_private.atlas_market_chart_usage enable row level security;
revoke all on atlas_private.atlas_market_chart_usage from public, anon, authenticated;
grant select, insert, update, delete on atlas_private.atlas_market_chart_usage to service_role;

-- One call per market-chart request. Atomically counts the user's 10-minute window, the user's Taipei day
-- and the site's Taipei day (insert ... on conflict do update ... returning; the row locks are held until
-- the call commits, always in that order, so concurrent requests cannot both pass a limit or deadlock).
-- A refused request is uncounted again, so hammering after a limit never inflates any total.
-- Returns {allowed:true, window, daily, site} or {allowed:false, code, retryAfterSeconds, window, daily, site}
-- with code USER_WINDOW | USER_DAILY | SITE_DAILY. site_exempt (cloud users) is counted but never refused
-- by the site cap.
create or replace function public.atlas_market_chart_quota(
  user_id text, window_limit integer, daily_limit integer, site_limit integer, site_exempt boolean default false
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  t timestamptz := clock_timestamp();
  window_start timestamptz := pg_catalog.date_bin(interval '10 minutes', t, timestamptz '2000-01-01 00:00:00+00');
  day_start timestamptz := pg_catalog.date_trunc('day', t at time zone 'Asia/Taipei') at time zone 'Asia/Taipei';
  window_count integer;
  day_count integer;
  site_count integer;
  refused text;
  retry_at timestamptz;
begin
  if user_id is null or user_id !~ '^U[0-9a-f]{32}$' or window_limit is null or window_limit < 1
    or daily_limit is null or daily_limit < 1 or site_limit is null or site_limit < 1 or site_exempt is null then
    raise exception 'Invalid market-chart quota request' using errcode = '22023';
  end if;
  if pg_catalog.random() < 0.005 then
    delete from atlas_private.atlas_market_chart_usage u where u.bucket_start < t - interval '2 days';
  end if;

  insert into atlas_private.atlas_market_chart_usage as u (subject, period, bucket_start, request_count)
    values (user_id, 'window', window_start, 1)
    on conflict (subject, period, bucket_start) do update set request_count = u.request_count + 1
    returning u.request_count into window_count;
  if window_count > window_limit then
    refused := 'USER_WINDOW'; retry_at := window_start + interval '10 minutes';
  else
    insert into atlas_private.atlas_market_chart_usage as u (subject, period, bucket_start, request_count)
      values (user_id, 'day', day_start, 1)
      on conflict (subject, period, bucket_start) do update set request_count = u.request_count + 1
      returning u.request_count into day_count;
    if day_count > daily_limit then
      refused := 'USER_DAILY'; retry_at := day_start + interval '1 day';
    else
      insert into atlas_private.atlas_market_chart_usage as u (subject, period, bucket_start, request_count)
        values ('site', 'day', day_start, 1)
        on conflict (subject, period, bucket_start) do update set request_count = u.request_count + 1
        returning u.request_count into site_count;
      if site_count > site_limit and not site_exempt then
        refused := 'SITE_DAILY'; retry_at := day_start + interval '1 day';
      end if;
    end if;
  end if;

  if refused is null then
    return jsonb_build_object('allowed', true, 'window', window_count, 'daily', day_count, 'site', site_count);
  end if;
  -- Uncount the refused request in every bucket it reached (rows are still locked by this call).
  update atlas_private.atlas_market_chart_usage u set request_count = u.request_count - 1
    where u.request_count > 0 and (
      (u.subject = user_id and u.period = 'window' and u.bucket_start = window_start)
      or (day_count is not null and u.subject = user_id and u.period = 'day' and u.bucket_start = day_start)
      or (site_count is not null and u.subject = 'site' and u.period = 'day' and u.bucket_start = day_start));
  return jsonb_build_object('allowed', false, 'code', refused,
    'retryAfterSeconds', greatest(1, ceil(extract(epoch from retry_at - t)))::integer,
    'window', window_count - 1, 'daily', day_count - 1, 'site', site_count - 1);
end;
$$;
revoke all on function public.atlas_market_chart_quota(text, integer, integer, integer, boolean) from public, anon, authenticated;
grant execute on function public.atlas_market_chart_quota(text, integer, integer, integer, boolean) to service_role;
notify pgrst, 'reload schema';
commit;
