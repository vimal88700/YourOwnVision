# YourOwnVision v2

This package is the persistent-world rebuild prepared from the current GitHub repository, Render logs, and live Supabase schema.

## Important Telegram step

The group **ENTER WORLD** button uses a Main Mini App deep link. Telegram falls back to the bot chat if the bot has no Main Mini App configured. Configure the bot's Main Mini App in **@BotFather** to `https://yourownvision.onrender.com/app`.

## Architecture

- Supabase is the source of truth for worlds, players, events and maintenance state.
- Render stores no game state in RAM or local files.
- No Gemini call is needed for the core game loop.
- `/play` creates the first world for a group and then always returns that same persistent world.
- Group worlds are isolated by `chat_id` and protected by a unique database index.
- Death resets the player's run but preserves compact survival metadata (`deaths`, `cycle`, `lives`); the shared world is not deleted.
- The 45-second timer exists only in the lobby.
- The Mini App is an RPG-style HUD with world scene, character, relationships, map and control views rather than a questionnaire layout.
- Telegram group-card updates are cached to avoid repeated edit/flood requests.
