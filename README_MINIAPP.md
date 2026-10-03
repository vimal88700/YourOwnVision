# YourOwnVision — Continuous WHAT HAPPENS? Mini App

This package replaces the old round-based `/play` experience with a lightweight Telegram Mini App world.

## Included
- `main.py` — FastAPI + Telegram webhook + Mini App hosting.
- `config.py` — adds `MINI_APP_SHORT_NAME`.
- `world_bot.py` — `/play` creates/opens the continuous world and private Mini App bridge.
- `world_service.py` — persistent sandbox/RPG engine using the existing Supabase `world_*` tables and `commit_world_turn` RPC.
- `miniapp_api.py` — Telegram `initData` validation and Mini App API.
- `miniapp/` — mobile-first vanilla HTML/CSS/JS Mini App; no Node build and very low RAM use.

## Required Render environment
Keep the existing variables. Add:

`MINI_APP_SHORT_NAME=what_happens`

The value must match the Mini App short name configured for the bot in BotFather. The current backend can still run the app at `/app` even before a direct-link short name is configured.

## Supabase
The project already has:
- `world_games`
- `world_players`
- `world_invites`
- `world_events`
- `commit_world_turn(...)`

No extra migration is required for this package.

## BotFather
Configure the bot's Mini App URL as:

`https://yourownvision.onrender.com/app`

Use the same short name as `MINI_APP_SHORT_NAME`.

## Game behavior
- 45 seconds is only the initial join window.
- There is no fixed round count and no automatic story ending.
- New players can join a living world.
- Existing players cannot be duplicated.
- Choices happen inside the Mini App instead of forcing users into DMs.
- Players have 3 chances/lives. Death changes location/path rather than ending the shared world.
- When all 3 chances are lost, a new cycle starts elsewhere while persistent discoveries and the world continue.
- Locations, dimensions, NPC relationships, risk, stats and discoveries are persisted in Supabase.
- Invite links are designed for friends who are not members of the original group.

## Free-tier design
The Mini App is plain HTML/CSS/JS. Render only serves the app and API; no Node build server is required. Supabase stores compact JSON state and events. Turn generation is deterministic from the world seed instead of making an AI request on every click.
