-- YourOwnVision v2 persistent-world hardening.
-- Safe for the existing database: this does NOT delete worlds, players or events.
create extension if not exists pgcrypto;
create table if not exists public.world_control (id integer primary key default 1 check(id=1),maintenance_until timestamptz,maintenance_by bigint,updated_at timestamptz not null default now());
insert into public.world_control(id) values(1) on conflict(id) do nothing;
create index if not exists world_games_chat_id_idx on public.world_games(chat_id);
create index if not exists world_games_status_idx on public.world_games(status);
create index if not exists world_players_game_idx on public.world_players(game_id);
create index if not exists world_players_user_idx on public.world_players(telegram_user_id);
create index if not exists world_events_game_seq_idx on public.world_events(game_id,sequence_no);
create unique index if not exists world_games_one_per_chat_idx on public.world_games(chat_id) where chat_id is not null;
create unique index if not exists world_players_one_user_per_world_idx on public.world_players(game_id,telegram_user_id);

-- Existing applications use the service-role key server-side; public API roles should not have direct table access.
alter table public.games enable row level security;
alter table public.players enable row level security;
alter table public.decisions enable row level security;
alter table public.story_history enable row level security;
alter table public.game_events enable row level security;
alter table public.world_managers enable row level security;
alter table public.bot_maintenance enable row level security;

-- Keep world counters accurate whenever a player joins.
create or replace function public.join_world_atomic(p_game_id uuid,p_user_id bigint,p_username text,p_display_name text)
returns public.world_players language plpgsql security definer set search_path=''
as $$
declare v_game public.world_games; v_player public.world_players; v_count integer;
begin
 select * into v_game from public.world_games where id=p_game_id for update;
 if not found then raise exception 'World not found'; end if;
 if v_game.status='paused' then raise exception 'This world is paused'; end if;
 if v_game.status in ('archived','completed','terminated') then raise exception 'This world has ended'; end if;
 select * into v_player from public.world_players where game_id=p_game_id and telegram_user_id=p_user_id limit 1;
 if found then
   if v_player.status in ('dead','finished','left') then
     update public.world_players set status='alive',health=100,energy=100,chances=3,chances_finished=false,
       state=jsonb_build_object('status','alive','lives',3,'health',100,'energy',100,'turns',0,'deaths',coalesce((state->>'deaths')::integer,0),'cycle',coalesce((state->>'cycle')::integer,0),'stats',jsonb_build_object('courage',0,'insight',0,'luck',0,'empathy',0,'honesty',0,'cunning',0),'inventory','[]'::jsonb,'relationships','{}'::jsonb,'flags',jsonb_build_object('returning',true),'checkpoint',jsonb_build_object('location_id','market','dimension','ordinary'),'last_choice',null,'path',jsonb_build_object('chapter',0,'node','intro','branch',0),'user_id',p_user_id),version=version+1,last_seen_at=now(),updated_at=now(),world_id=p_game_id,user_id=p_user_id where id=v_player.id returning * into v_player;
   else
     update public.world_players set last_seen_at=now(),updated_at=now(),username=coalesce(p_username,username),display_name=coalesce(nullif(p_display_name,''),display_name),world_id=coalesce(world_id,p_game_id),user_id=coalesce(user_id,p_user_id) where id=v_player.id returning * into v_player;
   end if;
 else
   select count(*) into v_count from public.world_players where game_id=p_game_id and status not in ('dead','finished','left');
   if v_count >= coalesce(v_game.max_players,20) then raise exception 'This world is full'; end if;
   insert into public.world_players(game_id,world_id,telegram_user_id,user_id,username,display_name,status,state,joined_at,updated_at,version,last_seen_at,health,energy,chances,deaths,chances_finished)
   values(p_game_id,p_game_id,p_user_id,p_user_id,coalesce(p_username,''),coalesce(nullif(p_display_name,''),'Player'),'alive',jsonb_build_object('status','alive','lives',3,'health',100,'energy',100,'turns',0,'deaths',0,'cycle',0,'stats',jsonb_build_object('courage',0,'insight',0,'luck',0,'empathy',0,'honesty',0,'cunning',0),'inventory','[]'::jsonb,'relationships','{}'::jsonb,'flags','{}'::jsonb,'checkpoint',jsonb_build_object('location_id','market','dimension','ordinary'),'last_choice',null,'path',jsonb_build_object('chapter',0,'node','intro','branch',0),'user_id',p_user_id),now(),now(),1,now(),100,100,3,0,false) returning * into v_player;
 end if;
 update public.world_games set player_count=(select count(*) from public.world_players where game_id=p_game_id and status<>'left'),living_player_count=(select count(*) from public.world_players where game_id=p_game_id and status='alive'),last_activity_at=now(),updated_at=now() where id=p_game_id;
 return v_player;
end; $$;

-- Atomic turn commit: save player + world + durable event in one transaction.
create or replace function public.commit_world_turn(p_game_id uuid,p_player_id uuid,p_expected_game_version integer,p_expected_player_version integer,p_world_state jsonb,p_player_state jsonb,p_event jsonb)
returns jsonb language plpgsql security definer set search_path=''
as $$
declare v_game public.world_games; v_player public.world_players; v_status text:=coalesce(p_player_state->>'status','alive'); v_lives integer:=greatest(0,coalesce((p_player_state->>'lives')::integer,3)); v_deaths integer:=greatest(0,coalesce((p_player_state->>'deaths')::integer,0)); v_seq bigint;
begin
 select * into v_game from public.world_games where id=p_game_id for update;
 if not found then raise exception 'World not found'; end if;
 if v_game.status<>'active' then raise exception 'World is not active'; end if;
 if v_game.version<>p_expected_game_version then raise exception 'World changed'; end if;
 select * into v_player from public.world_players where id=p_player_id for update;
 if not found or v_player.game_id<>p_game_id then raise exception 'Player is not in this world'; end if;
 if v_player.version<>p_expected_player_version then raise exception 'Player changed'; end if;
 update public.world_games set world_state=coalesce(p_world_state,'{}'::jsonb),last_event=coalesce(p_event,'{}'::jsonb),version=version+1,state_version=state_version+1,event_count=event_count+1,last_activity_at=now(),updated_at=now() where id=p_game_id;
 update public.world_players set state=coalesce(p_player_state,'{}'::jsonb),status=case when v_status='alive' then 'alive'::public.player_status when v_status='dead' then 'dead'::public.player_status else 'finished'::public.player_status end,version=version+1,state_version=state_version+1,updated_at=now(),last_seen_at=now(),health=greatest(0,least(100,coalesce((p_player_state->>'health')::integer,health))),energy=greatest(0,least(100,coalesce((p_player_state->>'energy')::integer,energy))),chances=v_lives,deaths=v_deaths,chances_finished=(v_lives<=0),world_id=p_game_id,user_id=coalesce(user_id,(p_player_state->>'user_id')::bigint) where id=p_player_id;
 select coalesce(max(sequence_no),0)+1 into v_seq from public.world_events where game_id=p_game_id;
 insert into public.world_events(game_id,actor_user_id,event_type,payload,world_id,player_id,user_id,sequence_no,from_node,to_node,is_shared) values(p_game_id,(p_event->>'user_id')::bigint,coalesce(p_event->>'type','turn'),coalesce(p_event,'{}'::jsonb),p_game_id,p_player_id,(p_event->>'user_id')::bigint,v_seq,p_event->>'from_node',p_event->>'to_node',coalesce((p_event->>'shared')::boolean,false));
 return jsonb_build_object('ok',true,'version',p_expected_game_version+1,'player_version',p_expected_player_version+1,'sequence_no',v_seq);
end; $$;

create or replace function public.set_maintenance(p_until timestamptz,p_actor bigint) returns public.world_control language plpgsql security definer set search_path='' as $$declare v_row public.world_control;begin update public.world_control set maintenance_until=p_until,maintenance_by=p_actor,updated_at=now() where id=1 returning * into v_row;return v_row;end;$$;
