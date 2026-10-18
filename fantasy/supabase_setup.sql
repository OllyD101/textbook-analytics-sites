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
  players    text[] not null,             -- the starting 11
  bench      text[] not null default '{}'::text[],   -- 3 subs, in order
  captain    text not null,
  vice       text not null,
  wildcard   boolean not null default false,
  transfers  int not null default 0,     -- worked out by the database
  hit        int not null default 0,     -- points deducted for extra transfers (database)
  updated_at timestamptz not null default now(),
  primary key (user_id, gw)
);
-- upgrades for projects set up earlier (safe to run again)
alter table public.fantasy_entries add column if not exists bench text[] not null default '{}'::text[];
alter table public.fantasy_entries add column if not exists chip text check (chip in ('triple', 'bench'));
alter table public.fantasy_players add column if not exists start_price numeric(4,1);

-- Price after each gameweek (written by the automation; prices change on form after each Saturday)
create table if not exists public.fantasy_price_history (
  player  text not null,
  gw      date not null references public.fantasy_gameweeks(gw) on delete cascade,
  price   numeric(4,1) not null,
  primary key (player, gw)
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
  v_squad      text[];
  v_limit      numeric := 100.0;
  v_chip_used  int;
  v_changes    int;
  v_wild_used  int;
begin
  -- the automation (service role) and club admins working in the SQL Editor may write anything;
  -- everything coming from the website (anon / authenticated) is checked below
  if coalesce(auth.role(), '') = 'service_role'
     or (auth.role() is null and current_user not in ('anon', 'authenticated')) then
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

  -- 11 starters + 3 subs, all different, known and active
  if array_length(new.players, 1) is distinct from 11 then
    raise exception 'Pick 11 starters.';
  end if;
  if coalesce(array_length(new.bench, 1), 0) <> 3 then
    raise exception 'Pick 3 subs.';
  end if;
  v_squad := new.players || new.bench;
  if (select count(distinct p) from unnest(v_squad) p) <> 14 then
    raise exception 'A player can only be picked once.';
  end if;
  select count(*) into v_unknown from unnest(v_squad) p
    where not exists (select 1 from fantasy_players fp where fp.name = p and fp.active);
  if v_unknown > 0 then
    raise exception 'Your team includes a player who isn''t available.';
  end if;

  -- positions: 4 batters, 4 bowlers, 2 all-rounders, 1 wicketkeeper
  select count(*) filter (where position = 'BAT'), count(*) filter (where position = 'BOWL'),
         count(*) filter (where position = 'AR'),  count(*) filter (where position = 'WK')
    into v_bat, v_bowl, v_ar, v_wk
    from fantasy_players where name = any(new.players);
  if v_bat <> 4 or v_bowl <> 4 or v_ar <> 2 or v_wk <> 1 then
    raise exception 'Your starting 11 needs 4 batters, 4 bowlers, 2 all-rounders and 1 wicketkeeper (you have %, %, %, %).', v_bat, v_bowl, v_ar, v_wk;
  end if;

  -- budget: all 14 players at today's prices. If your current squad has risen in value, you can spend
  -- that: the limit is £100m or your squad's current value, whichever is higher.
  select players || bench into v_prev from fantasy_entries
    where user_id = new.user_id and gw < new.gw order by gw desc limit 1;
  if v_prev is not null then
    select greatest(100.0, coalesce(sum(price), 0)) into v_limit from fantasy_players where name = any(v_prev);
  end if;
  select sum(price) into v_cost from fantasy_players where name = any(v_squad);
  if v_cost > v_limit then
    raise exception 'Over budget: £%m of £%m.', v_cost, v_limit;
  end if;

  -- captain and vice-captain come from the starting 11
  if not (new.captain = any(new.players)) or not (new.vice = any(new.players)) or new.captain = new.vice then
    raise exception 'Choose a captain and a different vice-captain from your starting 11.';
  end if;

  -- transfers: new names across all 14 (moving players between your 11 and the bench is free)
  if v_prev is null then
    new.transfers := 0;                      -- your first squad is free to pick
    new.wildcard  := false;
  else
    select count(*) into v_changes from unnest(v_squad) p where not (p = any(v_prev));
    new.transfers := v_changes;
  end if;

  -- chips: Triple Captain and Bench Boost, once a season each, and one chip per gameweek (wildcard included)
  if new.chip is not null then
    if new.wildcard then
      raise exception 'Only one chip a gameweek: you can''t play % and the wildcard together.', case new.chip when 'triple' then 'Triple Captain' else 'Bench Boost' end;
    end if;
    select count(*) into v_chip_used from fantasy_entries e join fantasy_gameweeks g using (gw)
      where e.user_id = new.user_id and e.chip = new.chip and g.season = v_season and e.gw <> new.gw;
    if v_chip_used > 0 then
      raise exception 'You''ve already used % this season.', case new.chip when 'triple' then 'Triple Captain' else 'Bench Boost' end;
    end if;
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
alter table public.fantasy_price_history enable row level security;
drop policy if exists "read price history" on public.fantasy_price_history;
create policy "read price history" on public.fantasy_price_history for select using (true);

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

grant select on public.fantasy_players, public.fantasy_gameweeks, public.fantasy_points, public.fantasy_managers, public.fantasy_price_history to anon, authenticated;
grant select, insert, update on public.fantasy_managers, public.fantasy_entries to authenticated;
grant select on public.fantasy_entries to anon;

-- ---------------------------------------------------------------------
-- 4. Scores and the league table
-- ---------------------------------------------------------------------
-- Auto-subs: each starter who didn't play is replaced by the first sub (bench order) of the SAME
-- position who did play. Returns the 11 that score.
create or replace function public.fantasy_effective_eleven(p_players text[], p_bench text[], p_gw date)
returns text[]
language plpgsql
stable
set search_path = public
as $$
declare
  v_out  text[] := p_players;
  v_used text[] := '{}';
  v_pos  text;
  v_sub  text;
begin
  for i in 1 .. coalesce(array_length(p_players, 1), 0) loop
    if not exists (select 1 from fantasy_points where gw = p_gw and player = p_players[i]) then
      select position into v_pos from fantasy_players where name = p_players[i];
      select b.name into v_sub
        from unnest(coalesce(p_bench, '{}')) with ordinality as b(name, ord)
        join fantasy_players fp on fp.name = b.name and fp.position = v_pos
        where not (b.name = any(v_used))
          and exists (select 1 from fantasy_points where gw = p_gw and player = b.name)
        order by b.ord limit 1;
      if v_sub is not null then
        v_out[i] := v_sub; v_used := v_used || v_sub; v_sub := null;
      end if;
    end if;
  end loop;
  return v_out;
end;
$$;

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
  select m.user_id, s.gw, s.season, e.gw as entry_gw,
         case when e.gw = s.gw and e.chip = 'bench' then e.players || e.bench          -- Bench Boost: all 14 count
              else public.fantasy_effective_eleven(e.players, e.bench, s.gw) end as players,
         e.captain, e.vice,
         case when e.gw = s.gw then e.chip end as chip,
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
  select t.user_id, t.gw, t.season, t.hit, t.chip, t.captain, t.vice, pl.player,
         coalesce(fp.points, 0) as points, (fp.player is not null) as played
  from team_for_gw t
  cross join lateral unnest(t.players) as pl(player)
  left join public.fantasy_points fp on fp.gw = t.gw and fp.player = pl.player
)
select user_id, gw, season,
       sum(points)
         + coalesce(max(points) filter (where player = captain and played),
                    max(points) filter (where player = vice and played), 0)
           * case when max(chip) = 'triple' then 2 else 1 end          -- Triple Captain: x3 instead of x2
         - max(hit) as points,
       case when bool_or(player = captain and played) then max(captain)
            when bool_or(player = vice and played) then max(vice) end as doubled,
       max(hit) as hit,
       max(chip) as chip
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

-- Pick stats for the player cards: % of managers with each player in their 14, and % captaining him,
-- for the latest gameweek that has locked (so nobody sees picks before the deadline).
create or replace view public.fantasy_pick_stats as
with last_locked as (select max(gw) as gw from public.fantasy_gameweeks where deadline <= now()),
teams as (
  select distinct on (e.user_id) e.user_id, e.players || e.bench as squad, e.captain
  from public.fantasy_entries e, last_locked l
  where e.gw <= l.gw
  order by e.user_id, e.gw desc
),
n as (select count(*)::numeric as managers from teams)
select p.player,
       round(100 * count(*) / nullif((select managers from n), 0), 1) as picked_pct,
       round(100 * count(*) filter (where t.captain = p.player) / nullif((select managers from n), 0), 1) as captain_pct,
       (select managers from n)::int as managers,
       (select gw from last_locked) as gw
from teams t cross join lateral unnest(t.squad) as p(player)
group by p.player;

grant select on public.fantasy_gw_scores, public.fantasy_standings, public.fantasy_pick_stats to anon, authenticated;

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
--   drop view if exists public.fantasy_pick_stats, public.fantasy_standings, public.fantasy_gw_scores;
--   drop table if exists public.fantasy_price_history, public.fantasy_entries, public.fantasy_managers, public.fantasy_points,
--                        public.fantasy_gameweeks, public.fantasy_players cascade;
--   drop function if exists public.fantasy_check_entry(), public.fantasy_create_season(int, date, date), public.fantasy_effective_eleven(text[], text[], date);
-- =====================================================================
