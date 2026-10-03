-- WHAT HAPPENS? continuous-world Mini App schema
-- Run once in Supabase SQL editor.
-- This is separate from the legacy round-game schema.

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
    settings jsonb not null default '{"allow_external_invites":true}'::jsonb,
    world_state jsonb not null default '{}'::jsonb,
    last_event jsonb not null default '{}'::jsonb,
    constraint world_games_status_check check (status in ('waiting','active','archived')),
    constraint world_games_version_check check (version >= 1),
    constraint world_games_max_players_check check (max_players between 1 and 50)
);

create index if not exists idx_world_games_chat_status
on public.world_games(chat_id, status, created_at desc);

create index if not exists idx_world_games_join_deadline
on public.world_games(join_deadline);

-- One live world per Telegram group. Private worlds (chat_id null) are allowed.
create unique index if not exists uq_world_games_live_chat
on public.world_games(chat_id)
where chat_id is not null and status in ('waiting','active');

create table if not exists public.world_players (
    id uuid primary key default gen_random_uuid(),
    game_id uuid not null references public.world_games(id) on delete cascade,
    telegram_user_id bigint not null,
    username text not null default '',
    display_name text not null default 'Player',
    status text not null default 'alive',
    joined_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    state jsonb not null default '{}'::jsonb,
    unique(game_id, telegram_user_id),
    constraint world_players_status_check check (status in ('alive','dead','finished'))
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

create table if not exists public.world_events (
    id bigint generated always as identity primary key,
    game_id uuid not null references public.world_games(id) on delete cascade,
    actor_user_id bigint,
    event_type text not null,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_world_events_game_time
on public.world_events(game_id, created_at asc);

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

-- Atomic player join. Existing players are returned without creating duplicates.
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
begin
  select * into v_game from public.world_games where id = p_game_id for update;
  if not found then raise exception 'World not found'; end if;
  if v_game.status not in ('waiting','active') then raise exception 'This world is no longer accepting players'; end if;

  select * into v_player
  from public.world_players
  where game_id = p_game_id and telegram_user_id = p_user_id
  limit 1;

  if found then
    return v_player;
  end if;

  if (select count(*) from public.world_players where game_id = p_game_id and status = 'alive') >= v_game.max_players then
    raise exception 'This world is full';
  end if;

  insert into public.world_players(game_id, telegram_user_id, username, display_name, state)
  values (
    p_game_id,
    p_user_id,
    coalesce(p_username,''),
    coalesce(nullif(p_display_name,''),'Player'),
    jsonb_build_object(
      'status','alive',
      'lives',3,
      'health',100,
      'energy',100,
      'turns',0,
      'cycle',0,
      'deaths',0,
      'stats',jsonb_build_object('courage',0,'insight',0,'luck',0,'empathy',0,'honesty',0,'cunning',0),
      'inventory','[]'::jsonb,
      'relationships','{}'::jsonb,
      'flags','{}'::jsonb,
      'checkpoint',jsonb_build_object('location_id','market','dimension','ordinary'),
      'last_choice',null,
      'path',jsonb_build_object('chapter',0,'node','intro')
    )
  ) returning * into v_player;

  return v_player;
end;
$$;

-- Atomic world + player update. The game version prevents duplicate/stale clicks.
create or replace function public.commit_world_turn(
    p_game_id uuid,
    p_player_id uuid,
    p_expected_game_version integer,
    p_expected_player_updated_at timestamptz,
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
begin
  select * into v_game from public.world_games where id = p_game_id for update;
  if not found then raise exception 'World not found'; end if;
  if v_game.status <> 'active' then raise exception 'World is not active'; end if;
  if v_game.version <> p_expected_game_version then raise exception 'World changed'; end if;

  select * into v_player from public.world_players where id = p_player_id for update;
  if not found or v_player.game_id <> p_game_id then raise exception 'Player is not in this world'; end if;
  if v_player.updated_at <> p_expected_player_updated_at then raise exception 'Player changed'; end if;

  update public.world_games
  set world_state = coalesce(p_world_state,'{}'::jsonb),
      last_event = v_event,
      version = version + 1
  where id = p_game_id;

  update public.world_players
  set state = coalesce(p_player_state,'{}'::jsonb),
      status = case when coalesce(p_player_state->>'status','alive') = 'alive' then 'alive' else 'dead' end
  where id = p_player_id;

  insert into public.world_events(game_id, actor_user_id, event_type, payload)
  values (p_game_id, nullif(v_event->>'user_id','')::bigint, coalesce(v_event->>'type','choice'), v_event);

  return jsonb_build_object('ok',true,'version',p_expected_game_version + 1);
end;
$$;

-- Expire only the 45-second lobby. It never ends an active world.
create or replace function public.activate_due_worlds()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer;
begin
  with due as (
    update public.world_games
    set status='active', started_at=coalesce(started_at,now()), version=version+1
    where status='waiting'
      and join_deadline is not null
      and join_deadline <= now()
      and exists(select 1 from public.world_players p where p.game_id=world_games.id and p.status='alive')
    returning 1
  ) select count(*) into v_count from due;
  return coalesce(v_count,0);
end;
$$;
