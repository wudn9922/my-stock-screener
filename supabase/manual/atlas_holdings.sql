-- PR A. Maintenance-only: apply manually BEFORE deploying user-settings, then release the UI.
-- No prices, watchlists, grants, drawings, or existing account tables are changed.
begin;
create table if not exists public.atlas_holding_accounts (
  line_user_id text not null check (line_user_id ~ '^U[0-9a-f]{32}$'),
  id uuid not null,
  definition jsonb not null check (jsonb_typeof(definition) = 'object' and octet_length(definition::text) <= 4096),
  revision bigint not null default 0 check (revision >= 0),
  updated_at timestamptz not null default clock_timestamp(),
  primary key (line_user_id, id)
);
create table if not exists public.atlas_holdings (
  line_user_id text not null,
  account_id uuid not null,
  id uuid not null,
  symbol text not null check (length(symbol) between 1 and 32),
  market text not null check (market in ('TW','US','CRYPTO')),
  quantity numeric(36,18) not null check (quantity > 0 and quantity < 1000000000000000),
  average_cost numeric(36,18) not null check (average_cost >= 0 and average_cost < 1000000000000000),
  cost_currency text not null check (cost_currency in ('TWD','USD','USDT')),
  source text not null check (source in ('import','paste','manual','api')),
  updated_at timestamptz not null default clock_timestamp(),
  primary key (line_user_id, account_id, id),
  unique (line_user_id, account_id, symbol),
  foreign key (line_user_id, account_id) references public.atlas_holding_accounts(line_user_id,id) on delete cascade
);
alter table public.atlas_holding_accounts enable row level security;
alter table public.atlas_holdings enable row level security;
revoke all on public.atlas_holding_accounts, public.atlas_holdings from public, anon, authenticated;
grant select, insert, update, delete on public.atlas_holding_accounts, public.atlas_holdings to service_role;
-- No browser policies: LINE / remembered-device verification and the current cloud grant
-- are checked on EVERY user-settings request before this service-role-only RPC.
create or replace function public.atlas_holdings_action(
  operation text, owner_id text, payload jsonb default '{}'::jsonb, expected_revision bigint default null
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  account_uuid uuid;
  definition_value jsonb;
  existing public.atlas_holding_accounts%rowtype;
  item jsonb;
  target jsonb;
  old_count integer;
  next_count integer;
  total_count integer;
  document_value jsonb;
begin
  if owner_id is null or owner_id !~ '^U[0-9a-f]{32}$' or jsonb_typeof(payload) is distinct from 'object'
    or operation is null
    or octet_length(payload::text) > 1048576 then
    return jsonb_build_object('status','invalid');
  end if;
  if operation <> 'list' then
    -- Serialize ALL of this owner's writes: concurrent tabs cannot bypass row limits.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('atlas-holdings:' || owner_id, 0));
    if operation = 'replaceAccount' then
      definition_value := payload->'account';
      account_uuid := (definition_value->>'id')::uuid;
      if jsonb_typeof(payload->'holdings') is distinct from 'array' or jsonb_typeof(definition_value) is distinct from 'object'
        or not (definition_value ?& array['id','name','kind','currency','broker'])
        or jsonb_typeof(definition_value->'name') is distinct from 'string'
        or jsonb_typeof(definition_value->'kind') is distinct from 'string'
        or jsonb_typeof(definition_value->'currency') is distinct from 'string'
        or jsonb_typeof(definition_value->'broker') is distinct from 'string'
        or length(btrim(definition_value->>'name')) not between 1 and 40
        or definition_value->>'kind' not in ('tw-broker','us-broker','crypto-exchange')
        -- CASE is parenthesized: inside IF, PL/pgSQL would otherwise end the condition at the CASE's first THEN.
        or definition_value->>'currency' <> (case definition_value->>'kind' when 'tw-broker' then 'TWD' when 'us-broker' then 'USD' else 'USDT' end) then
        return jsonb_build_object('status','invalid');
      end if;
    else
      account_uuid := (payload->>'accountId')::uuid;
    end if;
    if account_uuid is null then return jsonb_build_object('status','invalid'); end if;
    select * into existing from public.atlas_holding_accounts a
      where a.line_user_id = owner_id and a.id = account_uuid for update;
    if found then
      if expected_revision is null or existing.revision <> expected_revision then return jsonb_build_object('status','conflict'); end if;
    else
      if operation <> 'replaceAccount' or expected_revision is not null then return jsonb_build_object('status','conflict'); end if;
      if (select count(*) from public.atlas_holding_accounts a where a.line_user_id = owner_id) >= 30 then return jsonb_build_object('status','limit'); end if;
    end if;
    select count(*) into old_count from public.atlas_holdings h where h.line_user_id = owner_id and h.account_id = account_uuid;
    select count(*) into total_count from public.atlas_holdings h where h.line_user_id = owner_id;
    if operation = 'replaceAccount' then
      next_count := jsonb_array_length(payload->'holdings');
      if next_count > 1000 or total_count - old_count + next_count > 2000 then return jsonb_build_object('status','limit'); end if;
      target := payload->'holdings';
    elsif operation = 'upsert' then
      definition_value := existing.definition;
      item := payload->'holding';
      if item->>'accountId' is distinct from account_uuid::text then return jsonb_build_object('status','invalid'); end if;
      if not exists(select 1 from public.atlas_holdings h where h.line_user_id=owner_id and h.account_id=account_uuid and h.id=(item->>'id')::uuid)
        and (old_count >= 1000 or total_count >= 2000) then return jsonb_build_object('status','limit'); end if;
      target := jsonb_build_array(item);
    elsif operation = 'delete' then
      delete from public.atlas_holdings h where h.line_user_id=owner_id and h.account_id=account_uuid and h.id=(payload->>'holdingId')::uuid;
    elsif operation = 'deleteAccount' then
      delete from public.atlas_holding_accounts a where a.line_user_id=owner_id and a.id=account_uuid;
    else
      return jsonb_build_object('status','invalid');
    end if;
    if operation in ('replaceAccount','upsert') then
      -- Validate the entire incoming set BEFORE replacing the previous rows.
      for item in select value from jsonb_array_elements(target) loop
        if jsonb_typeof(item) is distinct from 'object' or not (item ?& array['id','accountId','symbol','market','quantity','averageCost','costCurrency','source'])
          or exists(select 1 from unnest(array['id','accountId','symbol','market','quantity','averageCost','costCurrency','source']) as fields(field)
            where jsonb_typeof(item->field) is distinct from 'string')
          or item->>'accountId' is distinct from account_uuid::text
          or item->>'market' <> (case definition_value->>'kind' when 'tw-broker' then 'TW' when 'us-broker' then 'US' else 'CRYPTO' end)
          or item->>'costCurrency' <> definition_value->>'currency'
          or item->>'quantity' !~ '^(0|[1-9][0-9]{0,14})(\.[0-9]{1,18})?$'
          or item->>'averageCost' !~ '^(0|[1-9][0-9]{0,14})(\.[0-9]{1,18})?$'
          or (item->>'quantity')::numeric <= 0
          or (item->>'averageCost')::numeric < 0
          or item->>'source' not in ('import','paste','manual','api') then return jsonb_build_object('status','invalid'); end if;
        if (item->>'id')::uuid is null or item->>'symbol' is null then return jsonb_build_object('status','invalid'); end if;
      end loop;
      if (select count(*) from jsonb_array_elements(target)) <> (select count(distinct value->>'symbol') from jsonb_array_elements(target))
        or (select count(*) from jsonb_array_elements(target)) <> (select count(distinct value->>'id') from jsonb_array_elements(target)) then return jsonb_build_object('status','invalid'); end if;
      if operation = 'replaceAccount' then
        insert into public.atlas_holding_accounts(line_user_id,id,definition)
          values(owner_id,account_uuid,definition_value)
          on conflict(line_user_id,id) do update set definition=excluded.definition;
        delete from public.atlas_holdings h where h.line_user_id=owner_id and h.account_id=account_uuid;
      end if;
      for item in select value from jsonb_array_elements(target) loop
        insert into public.atlas_holdings(line_user_id,account_id,id,symbol,market,quantity,average_cost,cost_currency,source)
          values(owner_id,account_uuid,(item->>'id')::uuid,item->>'symbol',item->>'market',(item->>'quantity')::numeric,(item->>'averageCost')::numeric,item->>'costCurrency',item->>'source')
          on conflict(line_user_id,account_id,id) do update set symbol=excluded.symbol,market=excluded.market,quantity=excluded.quantity,
            average_cost=excluded.average_cost,cost_currency=excluded.cost_currency,source=excluded.source,updated_at=clock_timestamp();
      end loop;
    end if;
    if operation <> 'deleteAccount' then
      update public.atlas_holding_accounts a set revision=a.revision+1, updated_at=clock_timestamp()
        where a.line_user_id=owner_id and a.id=account_uuid;
    end if;
  end if;
  select jsonb_build_object('version',1,'accounts',coalesce(jsonb_agg(jsonb_build_object(
    'account',a.definition,'revision',a.revision,'holdings',coalesce((
      select jsonb_agg(jsonb_build_object('id',h.id,'accountId',h.account_id,'symbol',h.symbol,'market',h.market,
        'quantity',h.quantity::text,'averageCost',h.average_cost::text,'costCurrency',h.cost_currency,'source',h.source,'updatedAt',h.updated_at) order by h.symbol)
      from public.atlas_holdings h where h.line_user_id=owner_id and h.account_id=a.id
    ),'[]'::jsonb)) order by a.updated_at,a.id),'[]'::jsonb)) into document_value
    from public.atlas_holding_accounts a where a.line_user_id=owner_id;
  return jsonb_build_object('status','ok','document',document_value);
end;
$$;
revoke all on function public.atlas_holdings_action(text,text,jsonb,bigint) from public,anon,authenticated;
grant execute on function public.atlas_holdings_action(text,text,jsonb,bigint) to service_role;
notify pgrst, 'reload schema';
commit;
