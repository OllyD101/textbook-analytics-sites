-- =====================================================================
-- Hessle CC Fantasy Cricket — Supabase setup
-- Paste this whole file into Supabase: SQL Editor -> New query -> Run.
-- Safe to run once on a new project. (To start again, see the bottom.)
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Tables
-- ---------------------------------------------------------------------

-- Players, positions and prices (written by the automation from positions.csv)
create table if not exists public.fantasy_players (
  name        text primary key,
  position    text not null check (position in ('BAT', 'BOWL', 'AR', 'WK')),
  price       numeric(4,1) not null check (price between 3.0 and 15.0),
  xi          text,
  active      boolean not null default true,
  updated_at  timestamptz not null default now()
);

-- Gameweeks: one per Saturday; teams lock at the deadline (12:00 UK time)
create table if not exists public.fantasy_gameweeks (
  gw        date primary key,              -- the Saturday
  season    int  not null,
  deadline  timestamptz not null
);

-- Points per player per gameweek (written by the automation after each Saturday)
-- A row exists for every player who appeared, even with 0 points.
create table if not exists public.fantasy_points (
  player   text not null,
  gw       date not null references public.fantasy_gameweeks(gw) on delete cascade,
  points   int  not null,
  parts    jsonb not null default '{}'::jsonb,    -- e.g. {"runs": 45, "wickets": 24}
  matches  jsonb not null default '[]'::jsonb,
  primary key (player, gw)
);

-- Managers (one per signed-in person)
create table if not exists public.fantasy_managers (
  user_id       uuid primary key references auth.users(id) on delete cascade,
  team_name     text not null check (char_length(btrim(team_name)) between 2 and 40),
  manager_name  text not null check (char_length(btrim(manager_name)) between 1 and 40),
  created_at    timestamptz not null default now()
);

-- A manager's team for a gameweek. If they don't save one, last week's carries over.
create table if not exists public.fantasy_entries (
  user_id    uuid not null references public.fantasy_managers(user_id) on delete cascade,
  gw         date not null references public.fantasy_gameweeks(gw) on delete cascade,
  players    text[] not null,
  captain    text not null,
  vice       text not null,
  wildcard   boolean not null default false,
  transfers  int not null default 0,     -- worked out by the database
  hit        int not null default 0,     -- points deducted for extra transfers (database)
  updated_at timestamptz not null default now(),
  primary key (user_id, gw)
);

-- ---------------------------------------------------------------------
-- 2. The rules, checked by the database on every save
-- ---------------------------------------------------------------------
create or replace function public.fantasy_check_entry()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_deadline   timestamptz;
  v_season     int;
  v_next_gw    date;
  v_cost       numeric;
  v_unknown    int;
  v_bat int; v_bowl int; v_ar int; v_wk int;
  v_prev       text[];
  v_changes    int;
  v_wild_used  int;
begin
  -- the automation (service role) may write anything
  if coalesce(auth.role(), '') = 'service_role' then
    return new;
  end if;

  if new.user_id is distinct from auth.uid() then
    raise exception 'You can only save your own team.';
  end if;
  if tg_op = 'UPDATE' and (new.user_id <> old.user_id or new.gw <> old.gw) then
    raise exception 'A saved team can''t be moved to another gameweek.';
  end if;

  select deadline, season into v_deadline, v_season from fantasy_gameweeks where gw = new.gw;
  if v_deadline is null then
    raise exception 'That gameweek doesn''t exist.';
  end if;
  if now() >= v_deadline then
    raise exception 'Teams for this gameweek locked at %.', to_char(v_deadline at time zone 'Europe/London', 'HH24:MI on Dy DD Mon');
  end if;
  select min(gw) into v_next_gw from fantasy_gameweeks where deadline > now();
  if new.gw <> v_next_gw then
    raise exception 'You can only pick a team for the next gameweek (%).', v_next_gw;
  end if;

  -- 11 different, known, active players
  if array_length(new.players, 1) is distinct from 11
     or (select count(distinct p) from unnest(new.players) p) <> 11 then
    raise exception 'Pick 11 different players.';
  end if;
  select count(*) into v_unknown from unnest(new.players) p
    where not exists (select 1 from fantasy_players fp where fp.name = p and fp.active);
  if v_unknown > 0 then
    raise exception 'Your team includes a player who isn''t available.';
  end if;

  -- positions: 4 batters, 4 bowlers, 2 all-rounders, 1 wicketkeeper
  select count(*) filter (where position = 'BAT'), count(*) filter (where position = 'BOWL'),
         count(*) filter (where position = 'AR'),  count(*) filter (where position = 'WK'),
         sum(price)
    into v_bat, v_bowl, v_ar, v_wk, v_cost
    from fantasy_players where name = any(new.players);
  if v_bat <> 4 or v_bowl <> 4 or v_ar <> 2 or v_wk <> 1 then
    raise exception 'You need 4 batters, 4 bowlers, 2 all-rounders and 1 wicketkeeper (you have %, %, %, %).', v_bat, v_bowl, v_ar, v_wk;
  end if;

  -- budget
  if v_cost > 100.0 then
    raise exception 'Over budget: £%m of £100m.', v_cost;
  end if;

  -- captain and vice-captain
  if not (new.captain = any(new.players)) or not (new.vice = any(new.players)) or new.captain = new.vice then
    raise exception 'Choose a captain and a different vice-captain from your 11.';
  end if;

  -- transfers: compared with the team you had going into this gameweek
  select players into v_prev from fantasy_entries
    where user_id = new.user_id and gw < new.gw order by gw desc limit 1;
  if v_prev is null then
    new.transfers := 0;                      -- your first team is free to pick
    new.wildcard  := false;
  else
    select count(*) into v_changes from unnest(new.players) p where not (p = any(v_prev));
    new.transfers := v_changes;
  end if;

  if new.wildcard then
    select count(*) into v_wild_used from fantasy_entries e join fantasy_gameweeks g using (gw)
      where e.user_id = new.user_id and e.wildcard and g.season = v_season and e.gw <> new.gw;
    if v_wild_used > 0 then
      raise exception 'You''ve already used your wildcard this season.';
    end if;
    new.hit := 0;
  else
    new.hit := greatest(0, new.transfers - 2) * 4;
  end if;

  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists fantasy_check_entry on public.fantasy_entries;
create trigger fantasy_check_entry
  before insert or update on public.fantasy_entries
  for each row execute function public.fantasy_check_entry();

-- ---------------------------------------------------------------------
-- 3. Security: who can see and change what
-- ---------------------------------------------------------------------
alter table public.fantasy_players   enable row level security;
alter table public.fantasy_gameweeks enable row level security;
alter table public.fantasy_points    enable row level security;
alter table public.fantasy_managers  enable row level security;
alter table public.fantasy_entries   enable row level security;

-- Everyone can read players, gameweeks and points. Only the automation can write them.
drop policy if exists "read players"   on public.fantasy_players;
drop policy if exists "read gameweeks" on public.fantasy_gameweeks;
drop policy if exists "read points"    on public.fantasy_points;
create policy "read players"   on public.fantasy_players   for select using (true);
create policy "read gameweeks" on public.fantasy_gameweeks for select using (true);
create policy "read points"    on public.fantasy_points    for select using (true);

-- Team names are public (for the league table); you can create and edit only your own.
drop policy if exists "read managers"     on public.fantasy_managers;
drop policy if exists "create own manager" on public.fantasy_managers;
drop policy if exists "edit own manager"   on public.fantasy_managers;
create policy "read managers"      on public.fantasy_managers for select using (true);
create policy "create own manager" on public.fantasy_managers for insert to authenticated with check (user_id = auth.uid());
create policy "edit own manager"   on public.fantasy_managers for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());

-- Teams: you always see your own; other people's only once that gameweek has locked.
drop policy if exists "read own or locked entries" on public.fantasy_entries;
drop policy if exists "save own entry"             on public.fantasy_entries;
drop policy if exists "update own entry"           on public.fantasy_entries;
create policy "read own or locked entries" on public.fantasy_entries for select
  using (user_id = auth.uid() or exists (select 1 from public.fantasy_gameweeks g where g.gw = fantasy_entries.gw and g.deadline <= now()));
create policy "save own entry"   on public.fantasy_entries for insert to authenticated with check (user_id = auth.uid());
create policy "update own entry" on public.fantasy_entries for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());

grant select on public.fantasy_players, public.fantasy_gameweeks, public.fantasy_points, public.fantasy_managers to anon, authenticated;
grant select, insert, update on public.fantasy_managers, public.fantasy_entries to authenticated;
grant select on public.fantasy_entries to anon;

-- ---------------------------------------------------------------------
-- 4. Scores and the league table
-- ---------------------------------------------------------------------
-- Each manager's score for every locked gameweek that has points.
-- The team used is the latest one saved on or before that gameweek (so teams carry over).
-- Captain doubles; if the captain didn't play, the vice-captain doubles. Transfer hits
-- come off in the gameweek the transfers were made.
create or replace view public.fantasy_gw_scores as
with scored_gws as (
  select g.gw, g.season from public.fantasy_gameweeks g
  where g.deadline <= now() and exists (select 1 from public.fantasy_points p where p.gw = g.gw)
),
team_for_gw as (
  select m.user_id, s.gw, s.season, e.gw as entry_gw, e.players, e.captain, e.vice,
         case when e.gw = s.gw then e.hit else 0 end as hit
  from public.fantasy_managers m
  cross join scored_gws s
  join lateral (
    select * from public.fantasy_entries e
    where e.user_id = m.user_id and e.gw <= s.gw
    order by e.gw desc limit 1
  ) e on true
),
player_pts as (
  select t.user_id, t.gw, t.season, t.hit, t.captain, t.vice, pl.player,
         coalesce(fp.points, 0) as points, (fp.player is not null) as played
  from team_for_gw t
  cross join lateral unnest(t.players) as pl(player)
  left join public.fantasy_points fp on fp.gw = t.gw and fp.player = pl.player
)
select user_id, gw, season,
       sum(points)
         + coalesce(max(points) filter (where player = captain and played),
                    max(points) filter (where player = vice and played), 0)
         - max(hit) as points,
       case when bool_or(player = captain and played) then max(captain)
            when bool_or(player = vice and played) then max(vice) end as doubled,
       max(hit) as hit
from player_pts
group by user_id, gw, season;

create or replace view public.fantasy_standings as
with latest as (select max(gw) as gw from public.fantasy_gw_scores)
select m.user_id, m.team_name, m.manager_name,
       coalesce(sum(s.points), 0)::int as total,
       coalesce(sum(s.points) filter (where s.gw = (select gw from latest)), 0)::int as last_week,
       rank() over (order by coalesce(sum(s.points), 0) desc) as position
from public.fantasy_managers m
left join public.fantasy_gw_scores s on s.user_id = m.user_id
group by m.user_id, m.team_name, m.manager_name;

grant select on public.fantasy_gw_scores, public.fantasy_standings to anon, authenticated;

-- ---------------------------------------------------------------------
-- 5. Gameweeks for a season: every Saturday between two dates, locking at 12:00 UK time
-- ---------------------------------------------------------------------
create or replace function public.fantasy_create_season(p_season int, p_first date, p_last date)
returns int
language sql
security definer
set search_path = public
as $$
  insert into fantasy_gameweeks (gw, season, deadline)
  select d::date, p_season, ((d::date + time '12:00') at time zone 'Europe/London')
  from generate_series(p_first, p_last, interval '1 day') d
  where extract(isodow from d) = 6
  on conflict (gw) do nothing;
  select count(*)::int from fantasy_gameweeks where season = p_season;
$$;
revoke all on function public.fantasy_create_season(int, date, date) from public, anon, authenticated;

-- The 2027 season: every Saturday from 3 April to 25 September (change the dates if needed)
select public.fantasy_create_season(2027, '2027-04-03', '2027-09-25');

-- =====================================================================
-- To remove everything and start again (careful — deletes all teams):
--   drop view if exists public.fantasy_standings, public.fantasy_gw_scores;
--   drop table if exists public.fantasy_entries, public.fantasy_managers, public.fantasy_points,
--                        public.fantasy_gameweeks, public.fantasy_players cascade;
--   drop function if exists public.fantasy_check_entry(), public.fantasy_create_season(int, date, date);
-- =====================================================================
