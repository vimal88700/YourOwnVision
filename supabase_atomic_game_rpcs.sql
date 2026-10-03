-- YourOwnVision: restore the PostgreSQL RPCs required by database.py.
-- Safe for the existing schema because these are CREATE OR REPLACE functions.

create or replace function public.start_game_atomic(
    p_game_id uuid,
    p_scene_id text,
    p_round_number integer,
    p_decision_deadline timestamptz,
    p_world_state jsonb
)
returns public.games
language plpgsql
security definer
set search_path = public
as $$
declare
    v_game public.games;
    v_player_count integer;
    v_role_count integer;
begin
    if p_round_number < 1 then
        raise exception 'Round number must be at least 1';
    end if;

    select * into v_game
    from public.games
    where id = p_game_id
    for update;

    if not found then
        raise exception 'Game % does not exist', p_game_id;
    end if;

    if v_game.status not in ('lobby', 'starting') then
        raise exception 'Game % cannot be started from status %',
            p_game_id, v_game.status;
    end if;

    select count(*) into v_player_count
    from public.players
    where game_id = p_game_id
      and status in ('pending', 'active');

    if v_player_count < 1 then
        raise exception 'Game % has no playable players', p_game_id;
    end if;

    select count(*) into v_role_count
    from jsonb_array_elements(
        coalesce(v_game.story -> 'roles', '[]'::jsonb)
    ) role
    where coalesce((role ->> 'playable')::boolean, true) = true;

    if v_player_count > v_role_count then
        raise exception
            'Game % has % players but only % playable roles',
            p_game_id, v_player_count, v_role_count;
    end if;

    if exists (
        select 1
        from public.players
        where game_id = p_game_id
          and role_id is not null
        group by role_id
        having count(*) > 1
    ) then
        raise exception
            'Game % contains duplicate role assignments',
            p_game_id;
    end if;

    update public.games
    set
        status = 'playing',
        current_scene_id = p_scene_id,
        current_round = p_round_number,
        decision_deadline = p_decision_deadline,
        join_deadline = null,
        world_state = coalesce(p_world_state, '{}'::jsonb),
        resolution_status = 'pending',
        resolution_key = null,
        version = version + 1,
        started_at = coalesce(started_at, now())
    where id = p_game_id
    returning * into v_game;

    update public.players
    set status = case
        when status = 'pending' then 'active'
        else status
    end
    where game_id = p_game_id
      and status = 'pending'
      and role_id is not null;

    return v_game;
end;
$$;

create or replace function public.submit_decision_atomic(
    p_game_id uuid,
    p_round_number integer,
    p_scene_id text,
    p_user_id bigint,
    p_choice_id text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_game public.games;
    v_player public.players;
    v_existing public.decisions;
    v_decision public.decisions;
begin
    select * into v_game
    from public.games
    where id = p_game_id
    for update;

    if not found then
        raise exception 'Game % does not exist', p_game_id;
    end if;

    if v_game.status <> 'playing' then
        raise exception 'Game is not accepting decisions';
    end if;

    if v_game.current_round <> p_round_number then
        raise exception 'Decision belongs to an old or future round';
    end if;

    if v_game.current_scene_id <> p_scene_id then
        raise exception 'Decision belongs to an old or future scene';
    end if;

    if v_game.decision_deadline is null then
        raise exception 'Game has no decision deadline';
    end if;

    if now() >= v_game.decision_deadline then
        raise exception 'Decision deadline has expired';
    end if;

    select * into v_player
    from public.players
    where game_id = p_game_id
      and user_id = p_user_id
    for update;

    if not found then
        raise exception 'Player is not a member of this game';
    end if;

    if v_player.status <> 'active' then
        raise exception 'Player is not active';
    end if;

    if v_player.joined_round > p_round_number then
        raise exception 'Player joined after this round began';
    end if;

    select * into v_existing
    from public.decisions
    where game_id = p_game_id
      and round_number = p_round_number
      and user_id = p_user_id
    limit 1;

    if found then
        return jsonb_build_object(
            'created', false,
            'duplicate', true,
            'decision_id', v_existing.id
        );
    end if;

    insert into public.decisions (
        game_id,
        round_number,
        scene_id,
        user_id,
        choice_id,
        status,
        submitted_at
    )
    values (
        p_game_id,
        p_round_number,
        p_scene_id,
        p_user_id,
        p_choice_id,
        'open',
        now()
    )
    returning * into v_decision;

    return jsonb_build_object(
        'created', true,
        'duplicate', false,
        'decision_id', v_decision.id
    );
end;
$$;

create or replace function public.complete_round_atomic(
    p_game_id uuid,
    p_round_number integer,
    p_next_scene_id text,
    p_next_round_number integer,
    p_decision_deadline timestamptz,
    p_world_state jsonb,
    p_game_status text,
    p_resolution_key text
)
returns public.games
language plpgsql
security definer
set search_path = public
as $$
declare
    v_game public.games;
begin
    select * into v_game
    from public.games
    where id = p_game_id
    for update;

    if not found then
        raise exception 'Game % does not exist', p_game_id;
    end if;

    if v_game.current_round <> p_round_number then
        raise exception 'Game round changed while resolving';
    end if;

    if v_game.resolution_status <> 'resolving' then
        raise exception 'Round is not currently being resolved';
    end if;

    if v_game.resolution_key <> p_resolution_key then
        raise exception 'Resolution ownership mismatch';
    end if;

    if p_game_status not in ('playing', 'completed') then
        raise exception 'Invalid next game status: %', p_game_status;
    end if;

    if p_game_status = 'playing' then
        if p_next_scene_id is null then
            raise exception 'Playing game requires next scene';
        end if;
        if p_next_round_number is null then
            raise exception 'Playing game requires next round';
        end if;
        if p_decision_deadline is null then
            raise exception 'Playing game requires decision deadline';
        end if;
    else
        if p_next_round_number is not null then
            raise exception 'Completed game cannot have next round';
        end if;
    end if;

    update public.decisions
    set status = 'resolved', resolved_at = now()
    where game_id = p_game_id
      and round_number = p_round_number
      and status <> 'resolved';

    if p_game_status = 'completed' then
        update public.games
        set
            status = 'completed',
            current_scene_id = coalesce(
                p_next_scene_id,
                current_scene_id
            ),
            current_round = p_round_number,
            decision_deadline = null,
            world_state = coalesce(p_world_state, '{}'::jsonb),
            resolution_status = 'resolved',
            resolution_key = null,
            completed_at = now(),
            ended_at = now(),
            version = version + 1
        where id = p_game_id
        returning * into v_game;
    else
        update public.games
        set
            status = 'playing',
            current_scene_id = p_next_scene_id,
            current_round = p_next_round_number,
            decision_deadline = p_decision_deadline,
            world_state = coalesce(p_world_state, '{}'::jsonb),
            resolution_status = 'pending',
            resolution_key = null,
            version = version + 1
        where id = p_game_id
        returning * into v_game;
    end if;

    return v_game;
end;
$$;

create or replace function public.increment_missed_decisions(
    p_game_id uuid,
    p_user_id bigint
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_game public.games;
    v_player public.players;
    v_new_count integer;
begin
    select * into v_game
    from public.games
    where id = p_game_id
    for update;

    if not found then
        raise exception 'Game % does not exist', p_game_id;
    end if;

    select * into v_player
    from public.players
    where game_id = p_game_id
      and user_id = p_user_id
    for update;

    if not found then
        raise exception
            'Player % does not exist in game %',
            p_user_id, p_game_id;
    end if;

    if v_player.last_missed_round = v_game.current_round then
        return v_player.missed_decisions;
    end if;

    v_new_count := v_player.missed_decisions + 1;

    update public.players
    set
        missed_decisions = v_new_count,
        last_missed_round = v_game.current_round
    where id = v_player.id;

    return v_new_count;
end;
$$;

create or replace function public.cancel_game_atomic(
    p_game_id uuid
)
returns public.games
language plpgsql
security definer
set search_path = public
as $$
declare
    v_game public.games;
begin
    select * into v_game
    from public.games
    where id = p_game_id
    for update;

    if not found then
        return null;
    end if;

    if v_game.status in ('completed', 'cancelled') then
        return v_game;
    end if;

    update public.games
    set
        status = 'cancelled',
        join_deadline = null,
        decision_deadline = null,
        resolution_status = 'resolved',
        resolution_key = null,
        cancelled_at = now(),
        ended_at = now(),
        version = version + 1
    where id = p_game_id
    returning * into v_game;

    return v_game;
end;
$$;

revoke all on function public.start_game_atomic(
    uuid, text, integer, timestamptz, jsonb
) from public;

revoke all on function public.submit_decision_atomic(
    uuid, integer, text, bigint, text
) from public;

revoke all on function public.claim_round_resolution(
    uuid, integer, text
) from public;

revoke all on function public.complete_round_atomic(
    uuid, integer, text, integer, timestamptz, jsonb, text, text
) from public;

revoke all on function public.increment_missed_decisions(
    uuid, bigint
) from public;

revoke all on function public.cancel_game_atomic(
    uuid
) from public;

grant execute on function public.start_game_atomic(
    uuid, text, integer, timestamptz, jsonb
) to service_role;

grant execute on function public.submit_decision_atomic(
    uuid, integer, text, bigint, text
) to service_role;

grant execute on function public.claim_round_resolution(
    uuid, integer, text
) to service_role;

grant execute on function public.complete_round_atomic(
    uuid, integer, text, integer, timestamptz, jsonb, text, text
) to service_role;

grant execute on function public.increment_missed_decisions(
    uuid, bigint
) to service_role;

grant execute on function public.cancel_game_atomic(
    uuid
) to service_role;
