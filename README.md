# YourOwnVision — WHAT HAPPENS? Mini App v3

A lightweight continuous multiplayer story for Telegram.

## Architecture

Telegram group `/play`
→ one persistent world per group
→ Telegram Main Mini App deep link
→ FastAPI
→ Supabase authoritative state
→ deterministic low-RAM story engine

### Important rules
- 45 seconds is **only the lobby join window**.
- A waiting world with zero players is reused instead of discarded.
- An active world never ends because a story timer expires.
- A player can join only once per world; clicking JOIN again never duplicates them.
- Every player gets a different path and relationships, while periodic convergence points bring everyone together.
- Death removes the old run's path/inventory/relationships and respawns the player elsewhere. Only minimal death/chance/cycle state remains.
- Losing all 3 chances starts a new cycle in the same living world.
- Friends outside the original group can join through invite links when the group owner/admin allows it.
- Different groups always have different world IDs and never share player state.
- No per-click Gemini call. Story variation is deterministic and local so clicks are fast and free-tier friendly.
- Sound uses the browser Web Audio API; there are no audio files.
- The Mini App uses CSS-generated visuals instead of heavy image assets.

## Supabase setup
Run `world_schema.sql` in the Supabase SQL Editor once. It creates only the `world_*` tables and RPCs used by this Mini App.

## Render variables
Required:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `PUBLIC_BASE_URL` — your Render HTTPS URL, e.g. `https://yourownvision.onrender.com`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

Optional:
- `TELEGRAM_WEBHOOK_PATH=/telegram/webhook`
- `MINI_APP_SHORT_NAME=what_happens`
- `ENVIRONMENT=production`
- `LOG_LEVEL=INFO`

## BotFather
Configure the bot's **Main Mini App** URL to:

`https://YOUR-RENDER-DOMAIN/app`

The group `/play` button uses a Telegram Mini App deep link with `startapp=game_<id>`, so the Mini App opens directly from the group instead of sending players into a DM.

## Deploy
1. Run `world_schema.sql` in Supabase.
2. Replace the repository contents with this package.
3. Commit and push to GitHub.
4. Render auto-deploys with `python main.py`.
5. Open the Render `/health` endpoint and confirm `status=ok`.
6. Test `/play` in a group.

Do not paste the Telegram bot token into source files.
