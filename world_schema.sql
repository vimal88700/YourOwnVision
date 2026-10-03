-- WHAT HAPPENS? — safe persistent world schema v7
-- Run this on Supabase SQL Editor. It is NON-DESTRUCTIVE.
-- It does not DROP tables, DELETE worlds, DELETE players, or erase progress.
-- It adds only the columns/functions required by the current continuous-world code.

create extension if not exists pgcrypto;

do $$
begin
  create type public.player_status as enum (
    'invited','joined','active','dead','finished','left','alive'
  );
exception when duplicate_object then null;
end $$;

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
  settings jsonb not null default '{"allow_external_invites":true,"operator_ids":[],"max_players":20}'::jsonb,
  world_state jsonb not null default '{}'::jsonb,
  last_event jsonb not null default '{}'::jsonb,
  telegram_message_id bigint,
  telegram_message_chat_id bigint
);

alter table public.world_games add column if not exists chat_id bigint;
alter table public.world_games add column if not exists creator_id bigint;
alter table public.world_games add column if not exists title text default 'WHAT HAPPENS?';
alter table public.world_games add column if not exists status text default 'waiting';
alter table public.world_games add column if not exists join_deadline timestamptz;
alter table public.world_games add column if not exists started_at timestamptz;
alter table public.world_games add column if not exists created_at timestamptz default now();
alter table public.world_games add column if not exists updated_at timestamptz default now();
alter table public.world_games add column if not exists seed bigint;
alter table public.world_games add column if not exists version integer default 1;
alter table public.world_games add column if not exists max_players integer default 20;
alter table public.world_games add column if not exists settings jsonb default '{"allow_external_invites":true,"operator_ids":[],"max_players":20}'::jsonb;
alter table public.world_games add column if not exists world_state jsonb default '{}'::jsonb;
alter table public.world_games add column if not exists last_event jsonb default '{}'::jsonb;
alter table public.world_games add column if not exists telegram_message_id bigint;
alter table public.world_games add column if not exists telegram_message_chat_id bigint;

update public.world_games
set title=coalesce(nullif(title,''),'WHAT HAPPENS?'),
    status=coalesce(status,'waiting'),
    created_at=coalesce(created_at,now()),
    updated_at=coalesce(updated_at,now()),
    version=greatest(coalesce(version,1),1),
    max_players=greatest(1,least(coalesce(max_players,20),50)),
    settings=coalesce(settings,'{"allow_external_invites":true,"operator_ids":[],"max_players":20}'::jsonb),
    world_state=coalesce(world_state,'{}'::jsonb),
    last_event=coalesce(last_event,'{}'::jsonb),
    seed=coalesce(seed,extract(epoch from now())::bigint);

create index if not exists world_games_chat_status_idx
on public.world_games(chat_id,status,created_at desc);

drop index if exists public.uq_world_games_live_chat;
create unique index if not exists uq_world_games_live_chat_v7
on public.world_games(chat_id)
where chat_id is not null and status in ('waiting','active','paused');

create table if not exists public.world_players (
  id uuid primary key default gen_random_uuid(),
  game_id uuid not null references public.world_games(id) on delete cascade,
  telegram_user_id bigint not null,
  username text not null default '',
  display_name text not null default 'Player',
  status public.player_status not null default 'alive',
  joined_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  version integer not null default 1,
  state jsonb not null default '{}'::jsonb,
  health integer not null default 100,
  energy integer not null default 100,
  chances integer not null default 3,
  deaths integer not null default 0,
  chances_finished boolean not null default false,
  last_seen_at timestamptz
);

alter table public.world_players add column if not exists game_id uuid;
alter table public.world_players add column if not exists telegram_user_id bigint;
alter table public.world_players add column if not exists username text default '';
alter table public.world_players add column if not exists display_name text default 'Player';
alter table public.world_players add column if not exists status public.player_status default 'alive';
alter table public.world_players add column if not exists joined_at timestamptz default now();
alter table public.world_players add column if not exists updated_at timestamptz default now();
alter table public.world_players add column if not exists version integer default 1;
alter table public.world_players add column if not exists state jsonb default '{}'::jsonb;
alter table public.world_players add column if not exists health integer default 100;
alter table public.world_players add column if not exists energy integer default 100;
alter table public.world_players add column if not exists chances integer default 3;
alter table public.world_players add column if not exists deaths integer default 0;
alter table public.world_players add column if not exists chances_finished boolean default false;
alter table public.world_players add column if not exists last_seen_at timestamptz;

-- Compatibility aliases used by older versions. They are retained, not required by the new engine.
alter table public.world_players add column if not exists world_id uuid;
alter table public.world_players add column if not exists user_id bigint;

update public.world_players
set username=coalesce(username,''),
    display_name=coalesce(nullif(display_name,''),'Player'),
    joined_at=coalesce(joined_at,now()),
    updated_at=coalesce(updated_at,now()),
    version=greatest(coalesce(version,1),1),
    state=coalesce(state,'{}'::jsonb),
    health=greatest(0,least(100,coalesce(health,100))),
    energy=greatest(0,least(100,coalesce(energy,100))),
    chances=greatest(coalesce(chances,3),0),
    deaths=greatest(coalesce(deaths,0),0),
    chances_finished=coalesce(chances_finished,false),
    world_id=coalesce(world_id,game_id),
    user_id=coalesce(user_id,telegram_user_id);

create index if not exists world_players_game_idx
on public.world_players(game_id,joined_at);
create index if not exists world_players_user_idx
on public.world_players(telegram_user_id);
create unique index if not exists world_players_game_user_uidx
on public.world_players(game_id,telegram_user_id);

create table if not exists public.world_invites (
  token text primary key,
  game_id uuid not null references public.world_games(id) on delete cascade,
  created_by bigint not null,
  created_at timestamptz not null default now(),
  uses integer not null default 0,
  max_uses integer not null default 100,
  active boolean not null default true,
  revoked boolean not null default false
);

alter table public.world_invites add column if not exists game_id uuid;
alter table public.world_invites add column if not exists created_by bigint;
alter table public.world_invites add column if not exists uses integer default 0;
alter table public.world_invites add column if not exists max_uses integer default 100;
alter table public.world_invites add column if not exists active boolean default true;
alter table public.world_invites add column if not exists revoked boolean default false;
alter table public.world_invites add column if not exists created_at timestamptz default now();

update public.world_invites
set active=coalesce(active,not coalesce(revoked,false)),
    revoked=coalesce(revoked,false),
    uses=greatest(coalesce(uses,0),0),
    max_uses=greatest(coalesce(max_uses,100),1),
    created_at=coalesce(created_at,now());

create index if not exists world_invites_game_idx on public.world_invites(game_id);
create index if not exists world_invites_active_idx on public.world_invites(active);

create table if not exists public.world_control (
  id integer primary key default 1 check(id=1),
  maintenance_until timestamptz,
  maintenance_by bigint,
  updated_at timestamptz not null default now()
);

insert into public.world_control(id) values(1) on conflict(id) do nothing;

create or replace function public.world_set_updated_at()
returns trigger language plpgsql set search_path='' as $$
begin
  new.updated_at=now();
  return new;
end;
$$;

drop trigger if exists trg_world_games_updated_at on public.world_games;
create trigger trg_world_games_updated_at before update on public.world_games for each row execute function public.world_set_updated_at();
drop trigger if exists trg_world_players_updated_at on public.world_players;
create trigger trg_world_players_updated_at before update on public.world_players for each row execute function public.world_set_updated_at();
drop trigger if exists trg_world_control_updated_at on public.world_control;
create trigger trg_world_control_updated_at before update on public.world_control for each row execute function public.world_set_updated_at();

create or replace function public.join_world_atomic(
  p_game_id uuid,
  p_user_id bigint,
  p_username text,
  p_display_name text
)
returns public.world_players
language plpgsql
security definer
set search_path=''
as $$
declare
  v_game public.world_games;
  v_player public.world_players;
begin
  select * into v_game from public.world_games where id=p_game_id for update;
  if not found then raise exception 'World not found'; end if;
  if v_game.status='paused' then raise exception 'This world is paused'; end if;
  if v_game.status in ('archived','completed','terminated') then raise exception 'This world has ended'; end if;

  select * into v_player from public.world_players where game_id=p_game_id and telegram_user_id=p_user_id limit 1;
  if found then
    if v_player.status in ('dead','finished','left') then
      update public.world_players
      set status='alive', health=100, energy=100, chances=3, chances_finished=false,
          state=jsonb_build_object('status','alive','lives',3,'health',100,'energy',100,'turns',0,
            'deaths',coalesce((state->>'deaths')::integer,0),'cycle',coalesce((state->>'cycle')::integer,0),
            'stats',jsonb_build_object('courage',0,'insight',0,'luck',0,'empathy',0,'honesty',0,'cunning',0),
            'inventory','[]'::jsonb,'relationships','{}'::jsonb,'flags',jsonb_build_object('returning',true),
            'checkpoint',jsonb_build_object('location_id','market','dimension','ordinary'),
            'last_choice',null,'path',jsonb_build_object('chapter',0,'node','intro','branch',0),'user_id',p_user_id),
          version=version+1,last_seen_at=now(),updated_at=now(),world_id=p_game_id,user_id=p_user_id
      where id=v_player.id returning * into v_player;
    else
      update public.world_players set last_seen_at=now(),updated_at=now(),username=coalesce(p_username,username),display_name=coalesce(nullif(p_display_name,''),display_name) where id=v_player.id returning * into v_player;
    end if;
    return v_player;
  end if;

  if (select count(*) from public.world_players where game_id=p_game_id and status not in ('dead','finished','left')) >= coalesce(v_game.max_players,20) then
    raise exception 'This world is full';
  end if;

  insert into public.world_players(game_id,world_id,telegram_user_id,user_id,username,display_name,status,state,joined_at,updated_at,version,last_seen_at,health,energy,chances,deaths,chances_finished)
  values(p_game_id,p_game_id,p_user_id,p_user_id,coalesce(p_username,''),coalesce(nullif(p_display_name,''),'Player'),'alive',
    jsonb_build_object('status','alive','lives',3,'health',100,'energy',100,'turns',0,'deaths',0,'cycle',0,
      'stats',jsonb_build_object('courage',0,'insight',0,'luck',0,'empathy',0,'honesty',0,'cunning',0),
      'inventory','[]'::jsonb,'relationships','{}'::jsonb,'flags','{}'::jsonb,
      'checkpoint',jsonb_build_object('location_id','market','dimension','ordinary'),
      'last_choice',null,'path',jsonb_build_object('chapter',0,'node','intro','branch',0),'user_id',p_user_id),
    now(),now(),1,now(),100,100,3,0,false)
  returning * into v_player;
  return v_player;
end;
$$;

create or replace function public.commit_world_turn(
  p_game_id uuid, p_player_id uuid, p_expected_game_version integer, p_expected_player_version integer,
  p_world_state jsonb, p_player_state jsonb, p_event jsonb
)
returns jsonb language plpgsql security definer set search_path='' as $$
declare
  v_game public.world_games;
  v_player public.world_players;
  v_status text:=coalesce(p_player_state->>'status','alive');
  v_lives integer:=greatest(0,coalesce((p_player_state->>'lives')::integer,3));
  v_deaths integer:=greatest(0,coalesce((p_player_state->>'deaths')::integer,0));
begin
  select * into v_game from public.world_games where id=p_game_id for update;
  if not found then raise exception 'World not found'; end if;
  if v_game.status<>'active' then raise exception 'World is not active'; end if;
  if v_game.version<>p_expected_game_version then raise exception 'World changed'; end if;
  select * into v_player from public.world_players where id=p_player_id for update;
  if not found or v_player.game_id<>p_game_id then raise exception 'Player is not in this world'; end if;
  if v_player.version<>p_expected_player_version then raise exception 'Player changed'; end if;

  update public.world_games set world_state=coalesce(p_world_state,'{}'::jsonb),last_event=coalesce(p_event,'{}'::jsonb),version=version+1,updated_at=now() where id=p_game_id;
  update public.world_players set
    state=coalesce(p_player_state,'{}'::jsonb),
    status=case when v_status='alive' then 'alive'::public.player_status when v_status='dead' then 'dead'::public.player_status else 'finished'::public.player_status end,
    version=version+1,updated_at=now(),last_seen_at=now(),
    health=greatest(0,least(100,coalesce((p_player_state->>'health')::integer,health))),
    energy=greatest(0,least(100,coalesce((p_player_state->>'energy')::integer,energy))),
    chances=v_lives,deaths=v_deaths,chances_finished=(v_lives<=0),world_id=p_game_id,user_id=coalesce(user_id,(p_player_state->>'user_id')::bigint)
  where id=p_player_id;

  return jsonb_build_object('ok',true,'version',p_expected_game_version+1,'player_version',p_expected_player_version+1);
end;
$$;

create or replace function public.set_maintenance(p_until timestamptz,p_actor bigint)
returns public.world_control language plpgsql security definer set search_path='' as $$
declare v_row public.world_control;
begin
  update public.world_control set maintenance_until=p_until,maintenance_by=p_actor where id=1 returning * into v_row;
  return v_row;
end;
$$;

revoke all on function public.join_world_atomic(uuid,bigint,text,text) from public;
revoke all on function public.commit_world_turn(uuid,uuid,integer,integer,jsonb,jsonb,jsonb) from public;
revoke all on function public.set_maintenance(timestamptz,bigint) from public;
grant execute on function public.join_world_atomic(uuid,bigint,text,text) to service_role;
grant execute on function public.commit_world_turn(uuid,uuid,integer,integer,jsonb,jsonb,jsonb) to service_role;
grant execute on function public.set_maintenance(timestamptz,bigint) to service_role;
