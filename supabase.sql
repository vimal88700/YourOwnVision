-- ============================================================
-- YourOwnVision / WHAT HAPPENS?
-- Production Supabase schema
--
-- Source of truth:
--     PostgreSQL / Supabase
--
-- IMPORTANT:
--     Do NOT run this blindly against a production database
--     containing data you need to preserve.
--
-- This migration is designed for the rebuilt architecture:
--
-- Telegram webhook
--      ↓
-- FastAPI
--      ↓
-- GameService
--      ↓
-- GameEngine
--      ↓
-- Supabase/PostgreSQL
--
-- PostgreSQL owns:
--     game state
--     player membership
--     decisions
--     deadlines
--     resolution claims
--     missed-decision counters
--     story history
--     event history
--
-- ============================================================


-- ============================================================
-- EXTENSIONS
-- ============================================================

create extension if not exists pgcrypto;


-- ============================================================
-- GAMES
-- ============================================================

create table if not exists public.games (
    id uuid primary key default gen_random_uuid(),

    chat_id bigint not null,

    creator_id bigint not null,

    status text not null default 'lobby',

    current_scene_id text,

    current_round integer not null default 0,

    -- Fully validated story JSON used by the deterministic engine.
    story jsonb not null default '{}'::jsonb,

    story_id text,

    story_fingerprint text,

    story_player_count integer,

    -- Persistent world state.
    world_state jsonb not null default '{}'::jsonb,

    -- Lobby deadline.
    join_deadline timestamptz,

    -- Current round deadline.
    decision_deadline timestamptz,

    -- Atomic round-resolution state.
    resolution_status text not null default 'pending',

    resolution_key text,

    -- Optimistic concurrency version.
    version integer not null default 1,

    created_at timestamptz not null default now(),

    updated_at timestamptz not null default now(),

    started_at timestamptz,

    completed_at timestamptz,

    cancelled_at timestamptz,

    ended_at timestamptz,

    metadata jsonb not null default '{}'::jsonb,

    constraint games_status_check
        check (
            status in (
                'lobby',
                'starting',
                'playing',
                'resolving',
                'completed',
                'cancelled'
            )
        ),

    constraint games_resolution_status_check
        check (
            resolution_status in (
                'pending',
                'resolving',
                'resolved'
            )
        ),

    constraint games_current_round_check
        check (current_round >= 0),

    constraint games_version_check
        check (version >= 1),

    constraint games_story_player_count_check
        check (
            story_player_count is null
            or story_player_count >= 1
        )
);


-- ============================================================
-- ONE ACTIVE GAME PER TELEGRAM CHAT
-- ============================================================

create unique index if not exists uq_games_one_active_per_chat
on public.games(chat_id)
where status not in (
    'completed',
    'cancelled'
);


-- ============================================================
-- GAME INDEXES
-- ============================================================

create index if not exists idx_games_status
on public.games(status);

create index if not exists idx_games_chat_created
on public.games(chat_id, created_at desc);

create index if not exists idx_games_deadline
on public.games(decision_deadline);

create index if not exists idx_games_join_deadline
on public.games(join_deadline);

create index if not exists idx_games_recovery
on public.games(status, decision_deadline);


-- ============================================================
-- PLAYERS
-- ============================================================

create table if not exists public.players (
    id uuid primary key default gen_random_uuid(),

    game_id uuid not null
        references public.games(id)
        on delete cascade,

    user_id bigint not null,

    username text not null default '',

    display_name text not null,

    role_id text,

    status text not null default 'pending',

    missed_decisions integer not null default 0,

    -- Used to make timeout/missed-decision accounting
    -- idempotent when multiple workers race.
    last_missed_round integer,

    joined_round integer not null default 0,

    became_npc_at timestamptz,

    eliminated_at timestamptz,

    left_at timestamptz,

    created_at timestamptz not null default now(),

    updated_at timestamptz not null default now(),

    constraint players_status_check
        check (
            status in (
                'pending',
                'active',
                'eliminated',
                'npc',
                'left'
            )
        ),

    constraint players_missed_decisions_check
        check (missed_decisions >= 0),

    constraint players_joined_round_check
        check (joined_round >= 0),

    constraint players_user_game_unique
        unique(game_id, user_id)
);


-- ============================================================
-- PLAYER INDEXES
-- ============================================================

create index if not exists idx_players_game
on public.players(game_id);

create index if not exists idx_players_game_status
on public.players(game_id, status);

create index if not exists idx_players_user
on public.players(user_id);

create index if not exists idx_players_role
on public.players(game_id, role_id);


-- ============================================================
-- ROLE UNIQUENESS
--
-- A role can never accidentally be assigned to two players
-- in the same game.
--
-- We intentionally do NOT exclude 'left' players.
--
-- Once a role has been used in a game, it cannot silently be
-- reused for another Telegram user.
-- ============================================================

create unique index if not exists uq_players_game_role
on public.players(game_id, role_id)
where role_id is not null;


-- ============================================================
-- DECISIONS
-- ============================================================

create table if not exists public.decisions (
    id uuid primary key default gen_random_uuid(),

    game_id uuid not null
        references public.games(id)
        on delete cascade,

    round_number integer not null,

    scene_id text not null,

    user_id bigint not null,

    choice_id text not null,

    status text not null default 'open',

    submitted_at timestamptz not null default now(),

    resolved_at timestamptz,

    created_at timestamptz not null default now(),

    metadata jsonb not null default '{}'::jsonb,

    constraint decisions_status_check
        check (
            status in (
                'open',
                'resolved'
            )
        ),

    constraint decisions_round_check
        check (round_number >= 1),

    constraint decisions_unique_player_round
        unique(game_id, round_number, user_id)
);


-- ============================================================
-- DECISION INDEXES
-- ============================================================

create index if not exists idx_decisions_game_round
on public.decisions(game_id, round_number);

create index if not exists idx_decisions_game_round_status
on public.decisions(game_id, round_number, status);

create index if not exists idx_decisions_user
on public.decisions(user_id);


-- ============================================================
-- STORY HISTORY
-- ============================================================

create table if not exists public.story_history (
    id uuid primary key default gen_random_uuid(),

    fingerprint text not null unique,

    story jsonb not null,

    player_count integer,

    created_at timestamptz not null default now(),

    constraint story_history_player_count_check
        check (
            player_count is null
            or player_count >= 1
        )
);


create index if not exists idx_story_history_created
on public.story_history(created_at desc);


-- ============================================================
-- GAME EVENTS
-- ============================================================

create table if not exists public.game_events (
    id uuid primary key default gen_random_uuid(),

    game_id uuid not null
        references public.games(id)
        on delete cascade,

    event_type text not null,

    round_number integer,

    user_id bigint,

    payload jsonb not null default '{}'::jsonb,

    created_at timestamptz not null default now(),

    constraint game_events_round_check
        check (
            round_number is null
            or round_number >= 0
        )
);


create index if not exists idx_game_events_game_created
on public.game_events(game_id, created_at asc);

create index if not exists idx_game_events_game_round
on public.game_events(game_id, round_number);

create index if not exists idx_game_events_user
on public.game_events(user_id);


-- ============================================================
-- UPDATED_AT TRIGGER
-- ============================================================

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;


drop trigger if exists trg_games_updated_at
on public.games;

create trigger trg_games_updated_at
before update on public.games
for each row
execute function public.set_updated_at();


drop trigger if exists trg_players_updated_at
on public.players;

create trigger trg_players_updated_at
before update on public.players
for each row
execute function public.set_updated_at();


-- ============================================================
-- RPC: START GAME ATOMIC
--
-- Expected Python call:
--
-- start_game_atomic(
--     p_game_id,
--     p_scene_id,
--     p_round_number,
--     p_decision_deadline,
--     p_world_state
-- )
--
-- Locks the game row so two START callbacks cannot start the
-- same game simultaneously.
-- ============================================================

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

    select *
    into v_game
    from public.games
    where id = p_game_id
    for update;

    if not found then
        raise exception 'Game % does not exist', p_game_id;
    end if;

    if v_game.status not in ('lobby', 'starting') then
        raise exception
            'Game % cannot be started from status %',
            p_game_id,
            v_game.status;
    end if;

    select count(*)
    into v_player_count
    from public.players
    where game_id = p_game_id
      and status in ('pending', 'active');

    if v_player_count < 1 then
        raise exception
            'Game % has no playable players',
            p_game_id;
    end if;

    -- The application validates the story before reaching here.
    -- This additional database check protects against an invalid
    -- player count if the RPC is called directly.
    select count(*)
    into v_role_count
    from jsonb_array_elements(
        coalesce(v_game.story -> 'roles', '[]'::jsonb)
    ) role
    where coalesce(
        (role ->> 'playable')::boolean,
        true
    ) = true;

    if v_player_count > v_role_count then
        raise exception
            'Game % has % players but only % playable roles',
            p_game_id,
            v_player_count,
            v_role_count;
    end if;

    -- Verify that assigned roles are unique.
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
        world_state = coalesce(
            p_world_state,
            '{}'::jsonb
        ),
        resolution_status = 'pending',
        resolution_key = null,
        version = version + 1,
        started_at = coalesce(
            started_at,
            now()
        )
    where id = p_game_id
    returning *
    into v_game;

    update public.players
    set
        status = case
            when status = 'pending'
                then 'active'
            else status
        end
    where game_id = p_game_id
      and status = 'pending'
      and role_id is not null;

    return v_game;
end;
$$;


-- ============================================================
-- RPC: SUBMIT DECISION ATOMIC
--
-- This is the authoritative write for a player decision.
--
-- The game row is locked before the decision is inserted.
--
-- This protects against:
--
--     player click
--          VS
--     timeout resolver
--
-- and prevents stale-round writes.
-- ============================================================

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
    select *
    into v_game
    from public.games
    where id = p_game_id
    for update;

    if not found then
        raise exception 'Game % does not exist', p_game_id;
    end if;

    if v_game.status <> 'playing' then
        raise exception
            'Game is not accepting decisions';
    end if;

    if v_game.current_round <> p_round_number then
        raise exception
            'Decision belongs to an old or future round';
    end if;

    if v_game.current_scene_id <> p_scene_id then
        raise exception
            'Decision belongs to an old or future scene';
    end if;

    if v_game.decision_deadline is null then
        raise exception
            'Game has no decision deadline';
    end if;

    if now() >= v_game.decision_deadline then
        raise exception
            'Decision deadline has expired';
    end if;

    select *
    into v_player
    from public.players
    where game_id = p_game_id
      and user_id = p_user_id
    for update;

    if not found then
        raise exception
            'Player is not a member of this game';
    end if;

    if v_player.status <> 'active' then
        raise exception
            'Player is not active';
    end if;

    if v_player.joined_round > p_round_number then
        raise exception
            'Player joined after this round began';
    end if;

    select *
    into v_existing
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
    returning *
    into v_decision;

    return jsonb_build_object(
        'created', true,
        'duplicate', false,
        'decision_id', v_decision.id
    );
end;
$$;


-- ============================================================
-- RPC: CLAIM ROUND RESOLUTION
--
-- Exactly one worker can claim a round.
--
-- This protects against:
--
--     final Telegram decision
--              VS
--     timeout recovery worker
--              VS
--     Render restart recovery
-- ============================================================

create or replace function public.claim_round_resolution(
    p_game_id uuid,
    p_round_number integer,
    p_resolution_key text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    v_game public.games;
begin
    select *
    into v_game
    from public.games
    where id = p_game_id
    for update;

    if not found then
        return jsonb_build_object(
            'claimed', false,
            'reason', 'game_not_found'
        );
    end if;

    if v_game.current_round <> p_round_number then
        return jsonb_build_object(
            'claimed', false,
            'reason', 'stale_round'
        );
    end if;

    if v_game.status <> 'playing' then
        return jsonb_build_object(
            'claimed', false,
            'reason', 'game_not_playing'
        );
    end if;

    if v_game.resolution_status <> 'pending' then
        return jsonb_build_object(
            'claimed', false,
            'reason', 'already_claimed',
            'resolution_key', v_game.resolution_key
        );
    end if;

    update public.games
    set
        status = 'resolving',
        resolution_status = 'resolving',
        resolution_key = p_resolution_key,
        version = version + 1
    where id = p_game_id;

    return jsonb_build_object(
        'claimed', true,
        'resolution_key', p_resolution_key
    );
end;
$$;


-- ============================================================
-- RPC: COMPLETE ROUND ATOMIC
--
-- Called only after a worker successfully claims resolution.
--
-- This is the authoritative round transition.
-- ============================================================

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
    select *
    into v_game
    from public.games
    where id = p_game_id
    for update;

    if not found then
        raise exception
            'Game % does not exist',
            p_game_id;
    end if;

    if v_game.current_round <> p_round_number then
        raise exception
            'Game round changed while resolving';
    end if;

    if v_game.resolution_status <> 'resolving' then
        raise exception
            'Round is not currently being resolved';
    end if;

    if v_game.resolution_key <> p_resolution_key then
        raise exception
            'Resolution ownership mismatch';
    end if;

    if p_game_status not in (
        'playing',
        'completed'
    ) then
        raise exception
            'Invalid next game status: %',
            p_game_status;
    end if;

    if p_game_status = 'playing' then

        if p_next_scene_id is null then
            raise exception
                'Playing game requires next scene';
        end if;

        if p_next_round_number is null then
            raise exception
                'Playing game requires next round';
        end if;

        if p_decision_deadline is null then
            raise exception
                'Playing game requires decision deadline';
        end if;

    else

        if p_next_round_number is not null then
            raise exception
                'Completed game cannot have next round';
        end if;

    end if;

    -- Resolve all decisions for this round.
    update public.decisions
    set
        status = 'resolved',
        resolved_at = now()
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
            world_state = coalesce(
                p_world_state,
                '{}'::jsonb
            ),
            resolution_status = 'resolved',
            resolution_key = null,
            completed_at = now(),
            ended_at = now(),
            version = version + 1
        where id = p_game_id
        returning *
        into v_game;

    else

        update public.games
        set
            status = 'playing',
            current_scene_id = p_next_scene_id,
            current_round = p_next_round_number,
            decision_deadline = p_decision_deadline,
            world_state = coalesce(
                p_world_state,
                '{}'::jsonb
            ),
            resolution_status = 'pending',
            resolution_key = null,
            version = version + 1
        where id = p_game_id
        returning *
        into v_game;

    end if;

    return v_game;
end;
$$;


-- ============================================================
-- RPC: INCREMENT MISSED DECISIONS
--
-- This operation is intentionally idempotent for the current
-- round.
--
-- If two recovery workers both discover the same missed player,
-- only the first call increments the persistent counter.
--
-- The current game.current_round identifies the timeout round.
-- ============================================================

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
    select *
    into v_game
    from public.games
    where id = p_game_id
    for update;

    if not found then
        raise exception
            'Game % does not exist',
            p_game_id;
    end if;

    select *
    into v_player
    from public.players
    where game_id = p_game_id
      and user_id = p_user_id
    for update;

    if not found then
        raise exception
            'Player % does not exist in game %',
            p_user_id,
            p_game_id;
    end if;

    -- If this player's timeout for this round has already been
    -- counted, return the existing persistent value.
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


-- ============================================================
-- RPC: CANCEL GAME ATOMIC
-- ============================================================

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
    select *
    into v_game
    from public.games
    where id = p_game_id
    for update;

    if not found then
        return null;
    end if;

    if v_game.status in (
        'completed',
        'cancelled'
    ) then
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
    returning *
    into v_game;

    return v_game;
end;
$$;


-- ============================================================
-- RPC PERMISSIONS
--
-- The Python application uses the Supabase service-role key.
--
-- These functions are SECURITY DEFINER so the API can invoke
-- the transaction logic without exposing direct database
-- mutation capabilities to untrusted clients.
-- ============================================================

revoke all on function public.start_game_atomic(
    uuid,
    text,
    integer,
    timestamptz,
    jsonb
) from public;

revoke all on function public.submit_decision_atomic(
    uuid,
    integer,
    text,
    bigint,
    text
) from public;

revoke all on function public.claim_round_resolution(
    uuid,
    integer,
    text
) from public;

revoke all on function public.complete_round_atomic(
    uuid,
    integer,
    text,
    integer,
    timestamptz,
    jsonb,
    text,
    text
) from public;

revoke all on function public.increment_missed_decisions(
    uuid,
    bigint
) from public;

revoke all on function public.cancel_game_atomic(
    uuid
) from public;


-- ============================================================
-- SERVICE ROLE ACCESS
--
-- Supabase's service_role is normally granted broad access by
-- the platform. These grants make the intended application
-- access explicit.
-- ============================================================

grant execute on function public.start_game_atomic(
    uuid,
    text,
    integer,
    timestamptz,
    jsonb
) to service_role;

grant execute on function public.submit_decision_atomic(
    uuid,
    integer,
    text,
    bigint,
    text
) to service_role;

grant execute on function public.claim_round_resolution(
    uuid,
    integer,
    text
) to service_role;

grant execute on function public.complete_round_atomic(
    uuid,
    integer,
    text,
    integer,
    timestamptz,
    jsonb,
    text,
    text
) to service_role;

grant execute on function public.increment_missed_decisions(
    uuid,
    bigint
) to service_role;

grant execute on function public.cancel_game_atomic(
    uuid
) to service_role;


-- ============================================================
-- TABLE ACCESS FOR SERVICE ROLE
-- ============================================================

grant select, insert, update, delete
on public.games
to service_role;

grant select, insert, update, delete
on public.players
to service_role;

grant select, insert, update, delete
on public.decisions
to service_role;

grant select, insert, update, delete
on public.story_history
to service_role;

grant select, insert, update, delete
on public.game_events
to service_role;


-- ============================================================
-- COMMENTS
-- ============================================================

comment on table public.games is
'Persistent WHAT HAPPENS? game state. PostgreSQL is authoritative.';

comment on table public.players is
'Persistent game membership, lifecycle, role and missed-decision state.';

comment on table public.decisions is
'Immutable player decision submissions for each game round.';

comment on table public.story_history is
'Validated Gemini-generated story history keyed by structural fingerprint.';

comment on table public.game_events is
'Append-only diagnostic and replay history for game execution.';

comment on column public.games.decision_deadline is
'Authoritative round deadline. Never rely solely on Python memory for timers.';

comment on column public.games.resolution_status is
'Database-level arbitration state preventing concurrent round resolution.';

comment on column public.players.joined_round is
'First round in which this player may participate.';

comment on column public.players.last_missed_round is
'Used to make timeout/missed-decision accounting idempotent.';


-- ============================================================
-- END
-- ============================================================
