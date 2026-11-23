-- Applied automatically (idempotently) by the "Deploy Supabase Edge Functions" workflow before it
-- deploys user-settings; can also be run by hand in the Supabase SQL editor. Requires atlas_holdings.sql and account_storage_permissions.sql.
-- Holds parsed broker trades (no PDFs, no passwords, no e-mail bodies).
begin;
create table if not exists public.atlas_statement_links (
  line_user_id text not null check (line_user_id ~ '^U[0-9a-f]{32}$'),
  account_id uuid not null,
  broker text not null check (broker in ('cathay','sinopac','kgi')),
  market text not null check (market in ('TW','US')),
  created_at timestamptz not null default clock_timestamp(),
  primary key (line_user_id, account_id),
  unique (line_user_id, broker, market)
);
create table if not exists public.atlas_statement_trades (
  line_user_id text not null check (line_user_id ~ '^U[0-9a-f]{32}$'),
  broker text not null check (broker in ('cathay','sinopac','kgi')),
  market text not null check (market in ('TW','US')),
  trade_key text not null check (length(trade_key) between 1 and 80),
  trade_date date not null,
  symbol text not null check (symbol ~ '^[A-Z0-9][A-Z0-9.-]{0,15}$'),
  side text not null check (side in ('buy','sell')),
  quantity numeric(36,18) not null check (quantity > 0 and quantity < 1000000000000000),
  price numeric(36,18) not null check (price >= 0 and price < 1000000000000000),
  -- Cash of the trade: buy = cost including fees and tax; sell = proceeds after fees and tax.
  amount numeric(36,18) not null check (amount >= 0 and amount < 1000000000000000),
  currency text not null check (currency in ('TWD','USD')),
  message_id text not null check (length(message_id) between 1 and 200),
  account_id uuid,
  applied_at timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  primary key (line_user_id, broker, market, trade_key)
);
create table if not exists public.atlas_statement_messages (
  line_user_id text not null check (line_user_id ~ '^U[0-9a-f]{32}$'),
  message_id text not null check (length(message_id) between 1 and 200),
  broker text check (broker in ('cathay','sinopac','kgi')),
  received_at timestamptz,
  report_date date,
  status text not null check (length(status) between 1 and 40),
  detail text check (length(detail) <= 200),
  trades integer not null default 0 check (trades >= 0),
  processed_at timestamptz not null default clock_timestamp(),
  primary key (line_user_id, message_id)
);
-- Full long-position list a broker prints after the day's trades (永豐「庫存明細」). Applying it sets the
-- account to exactly these quantities, which also absorbs stock dividends, splits and missed e-mails.
create table if not exists public.atlas_statement_snapshots (
  line_user_id text not null check (line_user_id ~ '^U[0-9a-f]{32}$'),
  broker text not null check (broker in ('cathay','sinopac','kgi')),
  market text not null check (market in ('TW','US')),
  report_date date not null,
  positions jsonb not null check (jsonb_typeof(positions) = 'object'),
  message_id text not null check (length(message_id) between 1 and 200),
  account_id uuid,
  applied_at timestamptz,
  created_at timestamptz not null default clock_timestamp(),
  primary key (line_user_id, broker, market, report_date)
);
alter table public.atlas_statement_links enable row level security;
alter table public.atlas_statement_snapshots enable row level security;
alter table public.atlas_statement_trades enable row level security;
alter table public.atlas_statement_messages enable row level security;
revoke all on public.atlas_statement_links, public.atlas_statement_trades, public.atlas_statement_messages,
  public.atlas_statement_snapshots from public, anon, authenticated;
grant select, insert, update, delete on public.atlas_statement_links, public.atlas_statement_trades,
  public.atlas_statement_messages, public.atlas_statement_snapshots to service_role;

-- Same rule as the SinoPac sync: holdings are written only while the owner's cloud grant holds.
create or replace function public.atlas_statement_policy_ok(owner_id text)
returns boolean language plpgsql security invoker set search_path = '' as $$
declare mode text;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('atlas-storage-user:' || owner_id, 0));
  select storage_mode into mode from public.atlas_storage_access where line_user_id = owner_id;
  return coalesce(mode, case when owner_id = public.atlas_storage_owner_id() then 'cloud' else 'none' end) = 'cloud';
end $$;

-- Applies every not-yet-applied trade and position snapshot of one broker/market to its linked
-- account, in date order (a day's trades before that day's snapshot), through the existing
-- replaceAccount CAS. The holdings write and the "applied" marks commit in ONE transaction, so
-- nothing is ever counted twice. Manual corrections made on the site stay until the next snapshot:
-- trades are applied on top of whatever the account holds now. Anything dated on or before the
-- newest applied snapshot is already reflected in it and is only marked applied.
create or replace function public.atlas_statement_apply(owner_id text, broker_code text, market_code text)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  link public.atlas_statement_links%rowtype;
  entry jsonb;
  account jsonb;
  revision_value bigint;
  book jsonb := '{}'::jsonb;
  item jsonb;
  event record;
  q numeric;
  c numeric;
  baseline date;
  last_pending date;
  applied text[] := '{}';
  snapshots date[] := '{}';
  holdings jsonb := '[]'::jsonb;
  result jsonb;
  symbol_key text;
  missing integer := 0;
begin
  if owner_id is null or owner_id !~ '^U[0-9a-f]{32}$' then return jsonb_build_object('status','invalid'); end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('atlas-statement:' || owner_id, 0));
  if not public.atlas_statement_policy_ok(owner_id) then return jsonb_build_object('status','forbidden'); end if;
  select * into link from public.atlas_statement_links l
    where l.line_user_id = owner_id and l.broker = broker_code and l.market = market_code;
  if not found then return jsonb_build_object('status','no-account'); end if;
  select max(s.report_date) filter (where s.applied_at is not null), max(s.report_date) filter (where s.applied_at is null)
    into baseline, last_pending from public.atlas_statement_snapshots s
    where s.line_user_id = owner_id and s.broker = broker_code and s.market = market_code;
  if last_pending is null and not exists (select 1 from public.atlas_statement_trades t where t.line_user_id = owner_id
      and t.broker = broker_code and t.market = market_code and t.applied_at is null) then
    return jsonb_build_object('status','ok','applied',0);
  end if;
  select value into entry from jsonb_array_elements(public.atlas_holdings_action('list', owner_id)->'document'->'accounts')
    where value->'account'->>'id' = link.account_id::text;
  account := entry->'account';
  if entry is null or account->>'kind' <> (case market_code when 'TW' then 'tw-broker' else 'us-broker' end) then
    return jsonb_build_object('status','no-account');
  end if;
  revision_value := (entry->>'revision')::bigint;
  for item in select value from jsonb_array_elements(entry->'holdings') loop
    book := book || jsonb_build_object(item->>'symbol', jsonb_build_object('id', item->>'id', 'source', item->>'source',
      'q', (item->>'quantity')::numeric, 'c', (item->>'quantity')::numeric * (item->>'averageCost')::numeric));
  end loop;
  for event in
    select 0 as kind, t.trade_date as day, t.trade_key as ref, t.symbol, t.side, t.quantity, t.amount, null::jsonb as positions
      from public.atlas_statement_trades t where t.line_user_id = owner_id
      and t.broker = broker_code and t.market = market_code and t.applied_at is null
    union all
    select 1, s.report_date, s.report_date::text, null, null, null, null, s.positions
      from public.atlas_statement_snapshots s where s.line_user_id = owner_id
      and s.broker = broker_code and s.market = market_code and s.applied_at is null
    order by 2, 1, 3
  loop
    if event.kind = 1 then
      snapshots := snapshots || event.day;
      continue when baseline is not null and event.day <= baseline;
      baseline := event.day;
      -- Quantities follow the broker; the total cost of a held symbol stays (so a stock dividend
      -- lowers the average). A symbol with no known cost gets 0 and is reported as missingCost.
      for symbol_key in select key from jsonb_each(book) where not event.positions ? key loop
        book := book || jsonb_build_object(symbol_key, (book->symbol_key) || '{"q":0,"c":0}'::jsonb);
      end loop;
      for symbol_key, item in select key, value from jsonb_each(event.positions) loop
        q := (item #>> '{}')::numeric;
        c := case when coalesce((book->symbol_key->>'q')::numeric, 0) > 0 then (book->symbol_key->>'c')::numeric else 0 end;
        book := book || jsonb_build_object(symbol_key, jsonb_build_object(
          'id', coalesce(book->symbol_key->>'id', pg_catalog.gen_random_uuid()::text),
          'source', coalesce(book->symbol_key->>'source', 'api'), 'q', q, 'c', c));
      end loop;
      continue;
    end if;
    applied := applied || event.ref;
    continue when baseline is not null and event.day <= baseline;
    q := coalesce((book->event.symbol->>'q')::numeric, 0);
    c := coalesce((book->event.symbol->>'c')::numeric, 0);
    if event.side = 'buy' then
      q := q + event.quantity;
      c := c + event.amount;
    elsif event.quantity >= q then
      -- Selling more than the book holds is fine only when a later snapshot will set the truth.
      if event.quantity > q and (last_pending is null or last_pending < event.day) then
        return jsonb_build_object('status','oversell','symbol',event.symbol,'tradeDate',event.day);
      end if;
      q := 0;
      c := 0;
    else
      c := c * (q - event.quantity) / q;
      q := q - event.quantity;
    end if;
    book := book || jsonb_build_object(event.symbol, jsonb_build_object(
      'id', coalesce(book->event.symbol->>'id', pg_catalog.gen_random_uuid()::text),
      'source', coalesce(book->event.symbol->>'source', 'api'), 'q', q, 'c', c));
  end loop;
  for symbol_key, item in select key, value from jsonb_each(book) loop
    if (item->>'q')::numeric > 0 then
      missing := missing + case when (item->>'c')::numeric = 0 then 1 else 0 end;
      holdings := holdings || jsonb_build_array(jsonb_build_object(
        'id', item->>'id', 'accountId', link.account_id::text, 'symbol', symbol_key, 'market', market_code,
        'quantity', round((item->>'q')::numeric, 18)::text,
        'averageCost', round((item->>'c')::numeric / (item->>'q')::numeric, 18)::text,
        'costCurrency', account->>'currency', 'source', item->>'source'));
    end if;
  end loop;
  result := public.atlas_holdings_action('replaceAccount', owner_id,
    jsonb_build_object('account', account, 'holdings', holdings), revision_value);
  if result->>'status' <> 'ok' then return jsonb_build_object('status', result->>'status'); end if;
  update public.atlas_statement_trades t set applied_at = clock_timestamp(), account_id = link.account_id
    where t.line_user_id = owner_id and t.broker = broker_code and t.market = market_code and t.trade_key = any(applied);
  update public.atlas_statement_snapshots s set applied_at = clock_timestamp(), account_id = link.account_id
    where s.line_user_id = owner_id and s.broker = broker_code and s.market = market_code and s.report_date = any(snapshots);
  return jsonb_build_object('status','ok','applied',coalesce(array_length(applied, 1), 0),
    'snapshots',coalesce(array_length(snapshots, 1), 0),'missingCost',missing);
end $$;
revoke all on function public.atlas_statement_policy_ok(text), public.atlas_statement_apply(text,text,text)
  from public, anon, authenticated;
grant execute on function public.atlas_statement_policy_ok(text), public.atlas_statement_apply(text,text,text)
  to service_role;
notify pgrst, 'reload schema';
commit;
