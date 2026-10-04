# YourOwnVision — root-cause report

## Confirmed from the live Render/Supabase state

1. **The Mini App was returning HTTP 500 because the database schema did not contain `world_control`.** Render logs showed `GET /api/miniapp/bootstrap -> 500` with `PGRST205: Could not find the table public.world_control`.
2. **The deployed application also repeatedly called a missing `activate_due_worlds` RPC**, producing `PGRST202` errors. The new hardening migration removes the need for that RPC in the current world flow.
3. **Telegram world-card updates were flooding the API.** Render logs showed repeated `Message is not modified`, then `Flood control exceeded`, then `Timed out`. The new `world_bot.py` caches the rendered card and ignores Telegram's harmless `message is not modified` response.
4. **The live group world existed but its player counters were stale.** Supabase showed the active group world with one real player while the old counters could remain at zero. The migration updates counters whenever a player joins and the existing live counters were reconciled.
5. **The database had no protection against multiple worlds for the same group.** The migration adds a unique `chat_id` index so a group can own one persistent world.
6. **The old `/play` flow could create a replacement after a world became terminal because group lookup only considered waiting/active/paused worlds.** The updated bot treats a terminal world as the group's existing world and refuses accidental replacement.
7. **The group Mini App button can only open the Mini App directly if Telegram knows the bot's Main Mini App.** The link format used by the new card is the official `?startapp=...` Main Mini App deep-link format. Configure the Main Mini App once in @BotFather.

## Free-tier strategy

- No Gemini request is required for the core game loop.
- Render RAM is used only for the HTTP client, Telegram bot process and short-lived card cache.
- Game state stays in Supabase.
- The Mini App uses CSS-generated scene art and Web Audio rather than large image/audio downloads.
- No local files are used for persistence.
- Supabase is the durable source of truth.
