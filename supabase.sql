create extension if not exists pgcrypto;


create table if not exists games (
    id uuid primary key default gen_random_uuid(),

    chat_id bigint not null,
    creator_id bigint not null,

    status text not null default 'lobby',

    current_scene_id text,
    current_round integer not null default 0,

    story jsonb not null default '{}'::jsonb,

    story_fingerprint text,

    join_deadline timestamptz,
    decision_deadline timestamptz,

    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    ended_at timestamptz
);


create table if not exists players (
    id uuid primary key default gen_random_uuid(),

    game_id uuid not null references games(id) on delete cascade,

    user_id bigint not null,
    username text not null default '',
    display_name text not null,

    role_id text,

    missed_decisions integer not null default 0,

    active boolean not null default true,

    joined_round integer not null default 0,

    created_at timestamptz not null default now(),

    unique(game_id, user_id)
);


create table if not exists decisions (
    id uuid primary key default gen_random_uuid(),

    game_id uuid not null references games(id) on delete cascade,

    round_number integer not null,
    scene_id text not null,

    user_id bigint not null,

    choice_id text,

    resolved boolean not null default false,

    created_at timestamptz not null default now(),

    unique(game_id, round_number, user_id)
);


create table if not exists story_history (
    id uuid primary key default gen_random_uuid(),

    fingerprint text not null unique,

    story jsonb not null,

    created_at timestamptz not null default now()
);


create index if not exists idx_games_chat_status
on games(chat_id, status);


create index if not exists idx_players_game
on players(game_id);


create index if not exists idx_decisions_game_round
on decisions(game_id, round_number);
