-- Apply manually before enabling watchlist sync. No changes to existing stocks/groups/drawings.
begin;
create table if not exists public.atlas_watchlists (
  id uuid not null,
  line_user_id text not null check (line_user_id ~ '^U[0-9a-f]{32}$'),
  definition jsonb not null check (jsonb_typeof(definition) = 'object'),
  updated_at timestamptz not null default clock_timestamp(),
  primary key (line_user_id, id)
);
alter table public.atlas_watchlists enable row level security;
revoke all on public.atlas_watchlists from anon, authenticated;
grant select, insert, update, delete on public.atlas_watchlists to service_role;
-- LINE tokens are verified by user-settings; no browser role receives a table policy.
create or replace function public.atlas_watchlist_updated_at()
returns trigger language plpgsql set search_path = public as $$
begin
  new.updated_at = clock_timestamp();
  return new;
end;
$$;
drop trigger if exists atlas_watchlist_updated_at on public.atlas_watchlists;
create trigger atlas_watchlist_updated_at before update on public.atlas_watchlists
for each row execute function public.atlas_watchlist_updated_at();
commit;
