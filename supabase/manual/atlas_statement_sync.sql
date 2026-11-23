-- Maintainer only; apply manually in the Supabase SQL editor BEFORE deploying user-settings and
-- the statement-sync service. Requires atlas_holdings.sql and account_storage_permissions.sql.
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
alter table public.atlas_statement_links enable row level security;
alter table public.atlas_statement_trades enable row level security;
alter table public.atlas_statement_messages enable row level security;
revoke all on public.atlas_statement_links, public.atlas_statement_trades, public.atlas_statement_messages
  from public, anon, authenticated;
grant select, insert, update, delete on public.atlas_statement_links, public.atlas_statement_trades,
  public.atlas_statement_messages to service_role;

-- Same rule as the SinoPac sync: holdings are written only while the owner's cloud grant holds.
create or replace function public.atlas_statement_policy_ok(owner_id text)
returns boolean language plpgsql security invoker set search_path = '' as $$
declare mode text;
begin
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('atlas-storage-user:' || owner_id, 0));
  select storage_mode into mode from public.atlas_storage_access where line_user_id = owner_id;
  return coalesce(mode, case when owner_id = public.atlas_storage_owner_id() then 'cloud' else 'none' end) = 'cloud';
end $$;

-- Applies every not-yet-applied trade of one broker/market to its linked account, in date order,
-- through the existing replaceAccount CAS. The holdings write and the "applied" marks commit in
-- ONE transaction, so a trade can never be counted twice. Manual corrections made on the site
-- stay: trades are applied on top of whatever the account holds now.
create or replace function public.atlas_statement_apply(owner_id text, broker_code text, market_code text)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  link public.atlas_statement_links%rowtype;
  entry jsonb;
  account jsonb;
  revision_value bigint;
  book jsonb := '{}'::jsonb;
  item jsonb;
  trade public.atlas_statement_trades%rowtype;
  q numeric;
  c numeric;
  applied text[] := '{}';
  holdings jsonb := '[]'::jsonb;
  result jsonb;
  symbol_key text;
begin
  if owner_id is null or owner_id !~ '^U[0-9a-f]{32}$' then return jsonb_build_object('status','invalid'); end if;
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('atlas-statement:' || owner_id, 0));
  if not public.atlas_statement_policy_ok(owner_id) then return jsonb_build_object('status','forbidden'); end if;
  select * into link from public.atlas_statement_links l
    where l.line_user_id = owner_id and l.broker = broker_code and l.market = market_code;
  if not found then return jsonb_build_object('status','no-account'); end if;
  if not exists (select 1 from public.atlas_statement_trades t where t.line_user_id = owner_id
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
  for trade in select * from public.atlas_statement_trades t where t.line_user_id = owner_id
      and t.broker = broker_code and t.market = market_code and t.applied_at is null
      order by t.trade_date, t.trade_key loop
    q := coalesce((book->trade.symbol->>'q')::numeric, 0);
    c := coalesce((book->trade.symbol->>'c')::numeric, 0);
    if trade.side = 'buy' then
      q := q + trade.quantity;
      c := c + trade.amount;
    else
      if trade.quantity > q then
        return jsonb_build_object('status','oversell','symbol',trade.symbol,'tradeDate',trade.trade_date);
      end if;
      c := case when trade.quantity = q then 0 else c * (q - trade.quantity) / q end;
      q := q - trade.quantity;
    end if;
    book := book || jsonb_build_object(trade.symbol, jsonb_build_object(
      'id', coalesce(book->trade.symbol->>'id', pg_catalog.gen_random_uuid()::text),
      'source', coalesce(book->trade.symbol->>'source', 'api'), 'q', q, 'c', c));
    applied := applied || trade.trade_key;
  end loop;
  for symbol_key, item in select key, value from jsonb_each(book) loop
    if (item->>'q')::numeric > 0 then
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
  return jsonb_build_object('status','ok','applied',coalesce(array_length(applied, 1), 0));
end $$;
revoke all on function public.atlas_statement_policy_ok(text), public.atlas_statement_apply(text,text,text)
  from public, anon, authenticated;
grant execute on function public.atlas_statement_policy_ok(text), public.atlas_statement_apply(text,text,text)
  to service_role;
notify pgrst, 'reload schema';
commit;
