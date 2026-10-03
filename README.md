# YourOwnVision — WHAT HAPPENS? v4

A lightweight Telegram HTML5 Mini App for a continuous multiplayer story.

## What changed

- `/play` in a group automatically creates/reuses that group's world and joins the caller.
- The Mini App opens directly from the group message using a Telegram Mini App deep link.
- No `CREATE WORLD` screen is required for normal play.
- The first 45 seconds are **only a lobby window**. The story has no round timer.
- A world with zero players is never thrown away; a later `/play` reopens its lobby.
- Players are auto-joined when they open a valid world/invite link.
- Friends outside the original group can join when the group setting allows it.
- Every group has a separate world ID. Worlds can never merge.
- Personal choices create different paths; procedural generation creates many combinations without a Gemini call on clicks.
- Shared convergence scenes periodically bring players together.
- Death resets the player's run/path/inventory/relationships while preserving only compact survival metadata (`deaths`, `cycle`, `lives`). The shared world continues.
- Player/world state lives in Supabase, not Render RAM.
- Per-world asyncio locks + Supabase version checks prevent duplicate/stale clicks on the single free Render instance.
- No story transcript/event table is written by v4, which keeps Supabase usage low.
- Sound is generated with Web Audio; there are no audio files.
- The UI uses CSS-generated visuals; there are no heavy images.
- `/pause` and `/resume` are available to permitted moderators.
- `/endworld` is restricted to the world creator or `BOT_CREATOR_ID`; group admins cannot terminate a world unless they are also its creator.
- `/maintenance N` is available only to `BOT_CREATOR_ID`. It blocks new actions without deleting data; `/maintenance off` resumes normal operation.

## Supabase

Run `world_schema.sql` in the Supabase SQL Editor.

The schema contains only:

- `world_games` — one persistent world per group
- `world_players` — compact current player state
- `world_invites` — small invite records
- `world_control` — one maintenance row

The new application does not write story transcripts.

## Environment

Required:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `PUBLIC_BASE_URL`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `BOT_CREATOR_ID`

Optional:

- `MINI_APP_SHORT_NAME=what_happens`
- `TELEGRAM_WEBHOOK_PATH=/telegram/webhook`
- `LOG_LEVEL=INFO`

## Telegram / BotFather

Create/configure the Mini App with short name `what_happens` (or change the environment value).

The group button uses the official direct Mini App link format:

`https://t.me/<bot_username>/<short_name>?startapp=w_<world_uuid>`

That is intentional: using a plain bot username link can fall back to the bot chat instead of opening the Mini App.

## Deploy

1. Run `world_schema.sql` in Supabase.
2. Put the contents of this repository at the root of the GitHub repository.
3. Set the Render environment variables.
4. Deploy with `python main.py`.
5. Confirm `/health` returns `status=ok`.
6. Put the bot in a group.
7. Send `/play` in the group.
8. The bot creates/reuses the group world, joins the sender, and posts the Mini App button.

Do not put the Telegram token or Supabase service key in Git.
