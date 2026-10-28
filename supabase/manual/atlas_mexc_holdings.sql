-- PR B, manual only. Apply AFTER atlas_holdings.sql; configure HOLDINGS_ENCRYPTION_KEY
-- server-side, then deploy user-settings, then publish the UI. Never execute from a browser.
begin;
create schema if not exists atlas_private;
revoke all on schema atlas_private from public,anon,authenticated;
grant usage on schema atlas_private to service_role;
create table if not exists atlas_private.atlas_mexc_connections (
  line_user_id text not null check (line_user_id ~ '^U[0-9a-f]{32}$'),
  account_id uuid not null,
  credential jsonb not null check (jsonb_typeof(credential)='object' and octet_length(credential::text) <= 4096),
  key_last4 text not null check (key_last4 ~ '^[A-Za-z0-9_-]{4}$'),
  connected_at timestamptz not null default clock_timestamp(),
  synced_at timestamptz not null default clock_timestamp(),
  snapshot_id uuid not null,
  holding_revision bigint not null check (holding_revision >= 0),
  needs_review boolean not null,
  balances jsonb not null check (jsonb_typeof(balances)='array' and jsonb_array_length(balances)<=1000),
  quotes jsonb not null check (jsonb_typeof(quotes)='object' and octet_length(quotes::text)<=524288),
  primary key(line_user_id,account_id),
  foreign key(line_user_id,account_id) references public.atlas_holding_accounts(line_user_id,id) on delete cascade
);
create table if not exists atlas_private.atlas_mexc_rate_limits (
  line_user_id text primary key check (line_user_id ~ '^U[0-9a-f]{32}$'),
  next_allowed_at timestamptz not null default clock_timestamp(),
  request_id uuid,
  account_id uuid,
  purpose text check (purpose in ('connect','sync')),
  expires_at timestamptz
);
alter table atlas_private.atlas_mexc_connections enable row level security;
alter table atlas_private.atlas_mexc_rate_limits enable row level security;
revoke all on atlas_private.atlas_mexc_connections,atlas_private.atlas_mexc_rate_limits from public,anon,authenticated;
grant select,insert,update,delete on atlas_private.atlas_mexc_connections,atlas_private.atlas_mexc_rate_limits to service_role;

-- Service-role-only. 'claim' can return ciphertext ONLY to the internal handler,
-- never to the browser. All public responses below explicitly whitelist fields.
create or replace function public.atlas_mexc_action(operation text, owner_id text, payload jsonb default '{}'::jsonb)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare
  account_uuid uuid;
  definition_value jsonb;
  account_row public.atlas_holding_accounts%rowtype;
  connection_row atlas_private.atlas_mexc_connections%rowtype;
  rate_row atlas_private.atlas_mexc_rate_limits%rowtype;
  account_exists boolean;
  expected bigint;
  request_uuid uuid;
  balance_value jsonb;
  quote_value jsonb;
  item jsonb;
  cost_value text;
  candidate jsonb;
  review_required boolean;
  result_value jsonb;
  document_value jsonb;
  connections_value jsonb;
  retry_at timestamptz;
begin
  if owner_id is null or owner_id !~ '^U[0-9a-f]{32}$' or operation is null
    or jsonb_typeof(payload) is distinct from 'object' or octet_length(payload::text)>1048576
    then return jsonb_build_object('status','invalid'); end if;
  if operation <> 'status' then
    -- Same per-owner lock as PR A: imports, API syncs and deletes share limits/CAS.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('atlas-holdings:' || owner_id,0));
  end if;
  if operation = 'backoff' then
    update atlas_private.atlas_mexc_rate_limits r set next_allowed_at=greatest(r.next_allowed_at,
      clock_timestamp()+make_interval(secs=>least(3600,greatest(30,(payload->>'seconds')::integer))))
      where r.line_user_id=owner_id and r.request_id=(payload->>'requestId')::uuid;
    return jsonb_build_object('status','ok');
  end if;
  if operation <> 'status' then
    account_uuid := (payload->>'accountId')::uuid;
    if account_uuid is null then return jsonb_build_object('status','invalid'); end if;
    select * into account_row from public.atlas_holding_accounts a where a.line_user_id=owner_id and a.id=account_uuid for update;
    account_exists := found;
    if account_exists and (account_row.definition->>'broker' is distinct from 'mexc'
      or account_row.definition->>'kind' is distinct from 'crypto-exchange'
      or account_row.definition->>'currency' is distinct from 'USDT') then return jsonb_build_object('status','invalid'); end if;
    select * into connection_row from atlas_private.atlas_mexc_connections c where c.line_user_id=owner_id and c.account_id=account_uuid for update;
    if operation = 'claim' then
      request_uuid := (payload->>'requestId')::uuid;
      if request_uuid is null or payload->>'purpose' not in ('connect','sync') then return jsonb_build_object('status','invalid'); end if;
      if payload->>'purpose' = 'connect' then
        definition_value := payload->'account'; expected := (payload->>'expectedRevision')::bigint;
        if jsonb_typeof(definition_value) is distinct from 'object'
          or definition_value->>'id' is distinct from account_uuid::text
          or definition_value->>'broker' is distinct from 'mexc'
          or definition_value->>'kind' is distinct from 'crypto-exchange'
          or definition_value->>'currency' is distinct from 'USDT'
          then return jsonb_build_object('status','invalid'); end if;
        if (account_exists and (expected is null or account_row.revision<>expected))
          or (not account_exists and expected is not null) then return jsonb_build_object('status','conflict'); end if;
        if not account_exists and (select count(*) from public.atlas_holding_accounts a where a.line_user_id=owner_id)>=30 then return jsonb_build_object('status','limit'); end if;
      elsif not account_exists or connection_row.account_id is null then return jsonb_build_object('status','missing');
      end if;
      select * into rate_row from atlas_private.atlas_mexc_rate_limits r where r.line_user_id=owner_id for update;
      if found and rate_row.next_allowed_at>clock_timestamp() then
        return jsonb_build_object('status','rate_limited','retryAfter',ceil(extract(epoch from rate_row.next_allowed_at-clock_timestamp())));
      end if;
      insert into atlas_private.atlas_mexc_rate_limits(line_user_id,next_allowed_at,request_id,account_id,purpose,expires_at)
        values(owner_id,clock_timestamp()+interval '30 seconds',request_uuid,account_uuid,payload->>'purpose',clock_timestamp()+interval '30 seconds')
        on conflict(line_user_id) do update set next_allowed_at=excluded.next_allowed_at,request_id=excluded.request_id,
          account_id=excluded.account_id,purpose=excluded.purpose,expires_at=excluded.expires_at;
      return jsonb_build_object('status','ok','expectedRevision',(case when account_exists then account_row.revision else null end),
        'credential',(case when payload->>'purpose'='sync' then connection_row.credential else null end));
    elsif operation = 'disconnect' then
      if not account_exists then return jsonb_build_object('status','missing'); end if;
      update atlas_private.atlas_mexc_rate_limits r set request_id=null where r.line_user_id=owner_id and r.account_id=account_uuid;
      delete from atlas_private.atlas_mexc_connections c where c.line_user_id=owner_id and c.account_id=account_uuid;
    elsif operation in ('finish','apply') then
      expected := (payload->>'expectedRevision')::bigint;
      if (account_exists and (expected is null or account_row.revision<>expected))
        or (not account_exists and expected is not null) then return jsonb_build_object('status','conflict'); end if;
      if operation = 'finish' then
        select * into rate_row from atlas_private.atlas_mexc_rate_limits r where r.line_user_id=owner_id for update;
        if rate_row.request_id is null or rate_row.request_id is distinct from (payload->>'requestId')::uuid
          or rate_row.account_id is distinct from account_uuid or rate_row.purpose is distinct from payload->>'purpose'
          or rate_row.expires_at<=clock_timestamp() then return jsonb_build_object('status','conflict'); end if;
        if payload->>'purpose' = 'connect' then
          definition_value := payload->'account';
          if definition_value->>'id' is distinct from account_uuid::text or definition_value->>'broker' is distinct from 'mexc'
            or definition_value->>'kind' is distinct from 'crypto-exchange' or definition_value->>'currency' is distinct from 'USDT'
            or jsonb_typeof(payload->'credential') is distinct from 'object'
            or payload->'credential'->>'v' is distinct from '1'
            or (payload->'credential'->>'iv') !~ '^[A-Za-z0-9+/]{16}$'
            or length(payload->'credential'->>'data') not between 24 and 2048
            or payload->>'keyLast4' !~ '^[A-Za-z0-9_-]{4}$' then return jsonb_build_object('status','invalid'); end if;
        elsif connection_row.account_id is null or not account_exists then return jsonb_build_object('status','missing');
        else definition_value:=account_row.definition; end if;
        balance_value:=payload->'balances'; quote_value:=payload->'quotes';
      else
        if connection_row.account_id is null or not connection_row.needs_review
          or connection_row.snapshot_id is distinct from (payload->>'snapshotId')::uuid
          or connection_row.holding_revision is distinct from account_row.revision
          or jsonb_typeof(payload->'costs') is distinct from 'object' then return jsonb_build_object('status','conflict'); end if;
        definition_value:=account_row.definition; balance_value:=connection_row.balances; quote_value:=connection_row.quotes;
      end if;
      if jsonb_typeof(balance_value) is distinct from 'array' or jsonb_array_length(balance_value)>1000
        or jsonb_typeof(quote_value) is distinct from 'object' or octet_length(quote_value::text)>524288
        then return jsonb_build_object('status','invalid'); end if;
      for item in select value from jsonb_array_elements(balance_value) loop
        if not (item ?& array['asset','symbol','quantity','free','locked'])
          or jsonb_typeof(item->'asset') is distinct from 'string' or item->>'asset' !~ '^[A-Z0-9]{1,20}$'
          or item->>'symbol' is distinct from ((item->>'asset') || '/USDT')
          or jsonb_typeof(item->'quantity') is distinct from 'string' or item->>'quantity' !~ '^(0|[1-9][0-9]{0,14})(\.[0-9]{1,18})?$'
          or jsonb_typeof(item->'free') is distinct from 'string' or item->>'free' !~ '^(0|[1-9][0-9]{0,14})(\.[0-9]{1,18})?$'
          or jsonb_typeof(item->'locked') is distinct from 'string' or item->>'locked' !~ '^(0|[1-9][0-9]{0,14})(\.[0-9]{1,18})?$'
          or (item->>'quantity')::numeric<=0
          or (item->>'quantity')::numeric<>(item->>'free')::numeric+(item->>'locked')::numeric
          then return jsonb_build_object('status','invalid'); end if;
      end loop;
      if jsonb_array_length(balance_value)<>(select count(distinct value->>'asset') from jsonb_array_elements(balance_value)) then return jsonb_build_object('status','invalid'); end if;
      review_required:=exists(select 1 from jsonb_array_elements(balance_value) b
        left join public.atlas_holdings h on h.line_user_id=owner_id and h.account_id=account_uuid and h.symbol=b->>'symbol'
        where h.id is null or (b->>'quantity')::numeric>h.quantity);
      if operation='apply' then
        for item in select value from jsonb_array_elements(balance_value) loop
          cost_value:=payload->'costs'->>(item->>'asset');
          if cost_value is null or cost_value !~ '^(0|[1-9][0-9]{0,14})(\.[0-9]{1,18})?$' then return jsonb_build_object('status','invalid'); end if;
        end loop;
        review_required:=false;
      end if;
      if not review_required then
        select coalesce(jsonb_agg(jsonb_build_object('id',coalesce(h.id,pg_catalog.gen_random_uuid()),'accountId',account_uuid,
          'symbol',b->>'symbol','market','CRYPTO','quantity',b->>'quantity',
          'averageCost',(case when operation='apply' then payload->'costs'->>(b->>'asset') else h.average_cost::text end),
          'costCurrency','USDT','source','api','updatedAt',clock_timestamp())),'[]'::jsonb) into candidate
          from jsonb_array_elements(balance_value) b left join public.atlas_holdings h
          on h.line_user_id=owner_id and h.account_id=account_uuid and h.symbol=b->>'symbol';
        result_value:=public.atlas_holdings_action('replaceAccount',owner_id,jsonb_build_object('account',definition_value,'holdings',candidate),expected);
        if result_value->>'status'<>'ok' then return result_value; end if;
      elsif not account_exists then
        result_value:=public.atlas_holdings_action('replaceAccount',owner_id,jsonb_build_object('account',definition_value,'holdings','[]'::jsonb),null);
        if result_value->>'status'<>'ok' then return result_value; end if;
      end if;
      select * into account_row from public.atlas_holding_accounts a where a.line_user_id=owner_id and a.id=account_uuid;
      if operation='finish' then
        insert into atlas_private.atlas_mexc_connections(line_user_id,account_id,credential,key_last4,snapshot_id,holding_revision,needs_review,balances,quotes)
          values(owner_id,account_uuid,coalesce(payload->'credential',connection_row.credential),coalesce(payload->>'keyLast4',connection_row.key_last4),
            rate_row.request_id,account_row.revision,review_required,balance_value,quote_value)
          on conflict(line_user_id,account_id) do update set credential=excluded.credential,key_last4=excluded.key_last4,
            connected_at=(case when rate_row.purpose='connect' then clock_timestamp() else atlas_mexc_connections.connected_at end),
            synced_at=clock_timestamp(),snapshot_id=excluded.snapshot_id,holding_revision=excluded.holding_revision,
            needs_review=excluded.needs_review,balances=excluded.balances,quotes=excluded.quotes;
        update atlas_private.atlas_mexc_rate_limits r set request_id=null where r.line_user_id=owner_id;
      else
        update atlas_private.atlas_mexc_connections c set holding_revision=account_row.revision,needs_review=false
          where c.line_user_id=owner_id and c.account_id=account_uuid;
      end if;
    else return jsonb_build_object('status','invalid'); end if;
  end if;
  select coalesce(jsonb_agg(jsonb_build_object('accountId',c.account_id,'keyLast4',c.key_last4,'connectedAt',c.connected_at,
    'syncedAt',c.synced_at,'snapshotId',c.snapshot_id,'holdingRevision',c.holding_revision,
    'needsReview',c.needs_review or c.holding_revision<>a.revision,'balances',c.balances,'quotes',c.quotes) order by c.connected_at,c.account_id),'[]'::jsonb)
    into connections_value from atlas_private.atlas_mexc_connections c join public.atlas_holding_accounts a
    on a.line_user_id=c.line_user_id and a.id=c.account_id where c.line_user_id=owner_id;
  select r.next_allowed_at into retry_at from atlas_private.atlas_mexc_rate_limits r where r.line_user_id=owner_id;
  document_value:=public.atlas_holdings_action('list',owner_id)->'document';
  return jsonb_build_object('status','ok','connections',connections_value,'retryAt',retry_at,'document',document_value);
end;
$$;
revoke all on function public.atlas_mexc_action(text,text,jsonb) from public,anon,authenticated;
grant execute on function public.atlas_mexc_action(text,text,jsonb) to service_role;
notify pgrst,'reload schema';
commit;
