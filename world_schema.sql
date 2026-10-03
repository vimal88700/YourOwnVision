-- WHAT HAPPENS? — v4 continuous multiplayer world
-- Run this once in Supabase SQL Editor.
-- This schema intentionally stores current state, not a transcript.

create extension if not exists pgcrypto;

create table if not exists public.world_games (
    id uuid primary key default gen_random_uuid(),
    chat_id bigint,
    creator_id bigint not null,
    title text not null default 'WHAT HAPPENS?',
    status text not null default 'waiting',
    join_deadline timestamptz,
    started_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    seed bigint not null,
    version integer not null default 1,
    max_players integer not null default 20,
    settings jsonb not null default '{"allow_external_invites":true,"operator_ids":[]}'::jsonb,
    world_state jsonb not null default '{}'::jsonb,
    last_event jsonb not null default '{}'::jsonb,
    constraint world_games_status_check check (status in ('waiting','active','paused','archived')),
    constraint world_games_version_check check (version >= 1),
    constraint world_games_max_players_check check (max_players between 1 and 50)
);

create index if not exists idx_world_games_chat_status
on public.world_games(chat_id, status, created_at desc);

create index if not exists idx_world_games_join_deadline
on public.world_games(join_deadline);

-- Exactly one non-archived world per group. Different groups can never merge.
drop index if exists public.uq_world_games_live_chat;
create unique index if not exists uq_world_games_live_chat_v4
on public.world_games(chat_id)
where chat_id is not null and status in ('waiting','active','paused');

create table if not exists public.world_players (
    id uuid primary key default gen_random_uuid(),
    game_id uuid not null references public.world_games(id) on delete cascade,
    telegram_user_id bigint not null,
    username text not null default '',
    display_name text not null default 'Player',
    status text not null default 'alive',
    joined_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    version integer not null default 1,
    state jsonb not null default '{}'::jsonb,
    unique(game_id, telegram_user_id),
    constraint world_players_status_check check (status in ('alive','dead','finished')),
    constraint world_players_version_check check (version >= 1)
);

create index if not exists idx_world_players_game_joined
on public.world_players(game_id, joined_at asc);

create index if not exists idx_world_players_user
on public.world_players(telegram_user_id);

create table if not exists public.world_invites (
    id uuid primary key default gen_random_uuid(),
    token text not null unique,
    game_id uuid not null references public.world_games(id) on delete cascade,
    created_by bigint not null,
    created_at timestamptz not null default now(),
    uses integer not null default 0,
    max_uses integer not null default 100,
    active boolean not null default true
);

create index if not exists idx_world_invites_game
on public.world_invites(game_id);

-- Singleton global switch used by the bot creator for maintenance.
create table if not exists public.world_control (
    id integer primary key default 1 check (id = 1),
    maintenance_until timestamptz,
    maintenance_by bigint,
    updated_at timestamptz not null default now()
);

insert into public.world_control(id)
values (1)
on conflict (id) do nothing;

create or replace function public.world_set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_world_games_updated_at on public.world_games;
create trigger trg_world_games_updated_at
before update on public.world_games
for each row execute function public.world_set_updated_at();

drop trigger if exists trg_world_players_updated_at on public.world_players;
create trigger trg_world_players_updated_at
before update on public.world_players
for each row execute function public.world_set_updated_at();

drop trigger if exists trg_world_control_updated_at on public.world_control;
create trigger trg_world_control_updated_at
before update on public.world_control
for each row execute function public.world_set_updated_at();

-- Race-safe join. It also makes /play idempotent.
create or replace function public.join_world_atomic(
    p_game_id uuid,
    p_user_id bigint,
    p_username text,
    p_display_name text
)
returns public.world_players
language plpgsql
security definer
set search_path = public
as $$
declare
  v_game public.world_games;
  v_player public.world_players;
  v_max integer;
begin
  select * into v_game from public.world_games where id = p_game_id for update;
  if not found then raise exception 'World not found'; end if;
  if v_game.status = 'paused' then raise exception 'This world is paused'; end if;
  if v_game.status = 'archived' then raise exception 'This world has ended'; end if;

  select * into v_player
  from public.world_players
  where game_id = p_game_id and telegram_user_id = p_user_id
  limit 1;
  if found then return v_player; end if;

  v_max := coalesce(v_game.max_players, 20);
  if (select count(*) from public.world_players where game_id = p_game_id and status <> 'finished') >= v_max then
    raise exception 'This world is full';
  end if;

  insert into public.world_players(game_id, telegram_user_id, username, display_name, status, version, state)
  values (
    p_game_id,
    p_user_id,
    coalesce(p_username,''),
    coalesce(nullif(p_display_name,''),'Player'),
    'alive',
    1,
    jsonb_build_object(
      'status','alive',
      'lives',3,
      'health',100,
      'energy',100,
      'turns',0,
      'deaths',0,
      'cycle',0,
      'stats',jsonb_build_object('courage',0,'insight',0,'luck',0,'empathy',0,'honesty',0,'cunning',0),
      'inventory','[]'::jsonb,
      'relationships','{}'::jsonb,
      'flags','{}'::jsonb,
      'checkpoint',jsonb_build_object('location_id','market','dimension','ordinary'),
      'last_choice',null,
      'path',jsonb_build_object('chapter',0,'node','intro','branch',0),
      'user_id',p_user_id
    )
  ) returning * into v_player;

  return v_player;
end;
$$;

-- One serialized transition. The application also serializes choices per world
-- because the free Render service runs a single process/instance.
create or replace function public.commit_world_turn(
    p_game_id uuid,
    p_player_id uuid,
    p_expected_game_version integer,
    p_expected_player_version integer,
    p_world_state jsonb,
    p_player_state jsonb,
    p_event jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_game public.world_games;
  v_player public.world_players;
  v_event jsonb := coalesce(p_event,'{}'::jsonb);
  v_status text := coalesce(p_player_state->>'status','alive');
begin
  select * into v_game from public.world_games where id = p_game_id for update;
  if not found then raise exception 'World not found'; end if;
  if v_game.status <> 'active' then raise exception 'World is not active'; end if;
  if v_game.version <> p_expected_game_version then raise exception 'World changed'; end if;

  select * into v_player from public.world_players where id = p_player_id for update;
  if not found or v_player.game_id <> p_game_id then raise exception 'Player is not in this world'; end if;
  if v_player.version <> p_expected_player_version then raise exception 'Player changed'; end if;

  update public.world_games
  set world_state = coalesce(p_world_state,'{}'::jsonb),
      last_event = v_event,
      version = version + 1
  where id = p_game_id;

  update public.world_players
  set state = coalesce(p_player_state,'{}'::jsonb),
      status = case when v_status = 'alive' then 'alive' else 'dead' end,
      version = version + 1
  where id = p_player_id;

  return jsonb_build_object('ok',true,'version',p_expected_game_version + 1);
end;
$$;

-- Maintenance helpers.
create or replace function public.set_maintenance(
    p_until timestamptz,
    p_actor bigint
)
returns public.world_control
language plpgsql
security definer
set search_path = public
as $$
declare v_row public.world_control;
begin
  update public.world_control
  set maintenance_until = p_until, maintenance_by = p_actor
  where id = 1
  returning * into v_row;
  return v_row;
end;
$$;

-- Optional one-time cleanup for the previous architecture. This table is not
-- created by v4 and is never written by the new app.
-- If it exists from an older deployment, you may drop it after confirming you
-- do not need its old transcript data:
-- drop table if exists public.world_events;
