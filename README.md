YourOwnVision — WHAT HAPPENS?

A deterministic multiplayer story game for Telegram.

Players join a game, receive characters and roles, make decisions during timed rounds, and experience a branching story.

The architecture is designed for a Render Web Service with Supabase as the persistent source of truth.

---

Architecture

                         TELEGRAM
                            │
                            │ HTTPS webhook
                            ▼
                    ┌─────────────────┐
                    │     FastAPI     │
                    │                 │
                    │ GET  /health   │
                    │ POST /telegram │
                    │      /webhook  │
                    └────────┬────────┘
                             │
                             ▼
                         bot.py
                             │
                             ▼
                     game_service.py
                             │
                             ▼
                         engine.py
                             │
                    ┌────────┴────────┐
                    │                 │
                    ▼                 ▼
              Supabase DB       validated story
                    ▲                 ▲
                    │                 │
                    │          story_generator.py
                    │                 │
                    │          story_validator.py
                    │                 │
                    │                 ▼
                    │              Gemini
                    │
                    └────────────────────────

Responsibilities

"main.py"

HTTP application entry point.

Responsible for:

- FastAPI application
- "/health"
- "/telegram/webhook"
- Telegram webhook registration
- Telegram webhook authentication
- startup/shutdown lifecycle
- persistent game recovery
- Render "PORT" handling

It does not contain gameplay rules.

---

"bot.py"

Telegram integration layer.

Responsible for:

- Telegram commands
- callback queries
- private-message handling
- formatting Telegram responses
- passing gameplay actions to "game_service.py"

It should not contain the actual game rules.

---

"game_service.py"

Application/service layer.

Responsible for:

- coordinating Telegram actions with the game engine
- loading persistent game state
- submitting player decisions
- starting games
- resolving rounds
- handling deadlines
- recording game events
- coordinating database operations

---

"engine.py"

Deterministic gameplay engine.

Responsible for:

- scene transitions
- choice validation
- world-state changes
- relationship changes
- ending detection
- NPC/elimination rules
- calculating the result of a round

The engine must not depend on Telegram.

The same game state plus the same decisions should produce the same gameplay result.

---

"story_generator.py"

Gemini integration.

Gemini is used to generate/prepare story content.

Gemini is not called for every player click.

The intended pipeline is:

Gemini
   ↓
structured story output
   ↓
story_validator.py
   ↓
validated story
   ↓
engine.py

Gameplay then runs deterministically from the validated story.

---

"story_validator.py"

Validates generated story content before the engine can use it.

Validation includes:

- required IDs
- duplicate IDs
- scene references
- role references
- choice references
- first scene
- endings
- relationships
- timers
- playable characters
- player-count compatibility
- cross-references

Invalid Gemini output must never reach the gameplay engine.

---

"database.py"

Persistent Supabase access layer.

Supabase is the authoritative source of truth for:

- games
- players
- roles assigned to players
- decisions
- deadlines
- rounds
- scenes
- world state
- story history
- game events
- missed decisions
- resolution state

Python memory is not authoritative.

---

"models.py"

Shared data models and validation structures.

---

"config.py"

Environment configuration.

Secrets and deployment configuration are loaded from environment variables.

---

"supabase.sql"

PostgreSQL schema and atomic RPC functions.

The database layer expects functions including:

start_game_atomic
submit_decision_atomic
claim_round_resolution
complete_round_atomic
increment_missed_decisions
cancel_game_atomic

These functions provide database-level protection against concurrent workers and race conditions.

---

Render Architecture

This project is designed as a Render Web Service.

It is intentionally not a Telegram polling worker.

Build command

pip install -r requirements.txt

Start command

python main.py

The application must listen on:

0.0.0.0:$PORT

"main.py" obtains the port from the environment.

---

Telegram Webhook

The old architecture used Telegram polling:

getUpdates
    ↓
long polling

That architecture is not used anymore.

The new architecture is:

Telegram
   ↓
HTTPS POST
   ↓
/telegram/webhook
   ↓
FastAPI
   ↓
python-telegram-bot

Telegram webhook requests are authenticated using:

TELEGRAM_WEBHOOK_SECRET

The webhook URL is:

https://YOUR-RENDER-SERVICE.onrender.com/telegram/webhook

The actual public URL is configured through:

PUBLIC_BASE_URL

---

Health Check

Render should use:

/health

A successful health response looks like:

{
  "status": "ok",
  "service": "YourOwnVision",
  "environment": "production"
}

The health endpoint does not expose secrets.

---

Environment Variables

Never commit real secrets.

The repository should contain:

.env.example

but not the real:

.env

Real production secrets belong in Render Environment Variables.

Required production configuration includes:

TELEGRAM_BOT_TOKEN
TELEGRAM_WEBHOOK_SECRET
PUBLIC_BASE_URL
TELEGRAM_WEBHOOK_PATH

GEMINI_API_KEY
GEMINI_MODEL

SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY

ADMIN_USER_IDS

ENVIRONMENT
LOG_LEVEL

The game configuration variables are also configurable through environment variables.

---

Telegram Token Security

If a Telegram bot token has ever been exposed publicly or committed to a repository:

1. Revoke it through BotFather.
2. Generate a new token.
3. Put the new token only in Render Environment Variables.
4. Do not put it in:
   - source code
   - README
   - ".env.example"
   - Git history
   - screenshots
   - public logs

Do not send the token in chat.

---

Gemini

The project is designed around the requested Gemini model:

gemini-3.8-flash

The configured variable is:

GEMINI_MODEL=gemini-3.8-flash

The application must use the current Google Gemini SDK/API conventions rather than old examples copied from earlier Gemini releases.

Gemini's role is limited to story generation/preparation.

It must not become the gameplay engine.

---

Gemini Story Pipeline

The intended flow is:

Request new story
       │
       ▼
Gemini
       │
       ▼
Structured output
       │
       ▼
Pydantic / schema validation
       │
       ▼
Story validator
       │
       ├── invalid → regenerate
       │
       └── valid
              │
              ▼
       story fingerprint
              │
              ▼
       Supabase story history
              │
              ▼
          Game Engine

A player clicking a choice must not trigger a Gemini request.

---

Deterministic Gameplay

Once a story has been validated and accepted:

validated story
+
persistent game state
+
persistent player decisions
+
current round

are sufficient for the engine to determine the next state.

The engine should not depend on:

- Telegram message timing
- Python process memory
- random hidden state
- Gemini responses
- Render worker lifetime

---

Supabase Is the Source of Truth

The game must survive a Render restart.

For example:

Round 4
deadline = 12:30:00

is stored in Supabase.

If Render crashes at:

12:29:30

and restarts at:

12:31:00

the application can inspect the database and determine that the deadline has expired.

No in-memory Python timer is required to know that.

---

Timers

The authoritative timer is:

games.decision_deadline

Python background tasks may be used as a convenience for checking expired games.

They are not the source of truth.

Therefore:

Render restart
      ↓
application starts
      ↓
Supabase recovery
      ↓
expired deadlines detected
      ↓
round resolution continues

---

Atomic Round Resolution

A round can have competing events.

For example:

Player submits final decision
            │
            ├──────────────┐
            │              │
            ▼              ▼
     Telegram request   timeout worker

Both may attempt to resolve the same round.

The database therefore uses a resolution claim.

The intended sequence is:

claim_round_resolution
          ↓
one worker wins
          ↓
round resolved
          ↓
complete_round_atomic

Other workers observe that the round has already been claimed/resolved.

This prevents duplicate round transitions.

---

Duplicate Telegram Updates

Telegram/webhook delivery can result in repeated requests.

The application therefore treats Telegram callbacks and gameplay actions as potentially duplicated.

Player decisions are protected by the database constraint:

(game_id, round_number, user_id)

Only one decision can exist for a player in a given round.

Repeated submissions must not create duplicate decisions.

---

Player Lifecycle

Players explicitly support these states:

pending
active
eliminated
npc
left

These states are persistent in Supabase.

They must not exist only in Python memory.

---

Missed Decisions

Missed decisions are stored in:

players.missed_decisions

The current timeout round is tracked using:

players.last_missed_round

This allows timeout handling to be idempotent.

For example, if two workers discover the same missed decision:

worker A → increment
worker B → sees same round already counted

The same missed decision is not counted twice.

Three missed decisions can therefore be enforced from persistent database state even after a Render restart.

---

Late Joining

Each player has:

joined_round

This records the first round in which the player participates.

The engine/service layer must consistently use this value when determining whether a player can participate in a round.

A player joining during a later round must not accidentally receive decisions from earlier rounds.

---

Role Assignment

Playable roles are assigned only after the generated story has been validated.

The database also protects against accidental role reuse within the same game.

A role cannot silently be assigned to multiple players in one game.

Before starting a game:

player count

must not exceed:

available playable roles

---

Game Events

Gameplay diagnostics are stored in:

game_events

Events can contain:

- game creation
- player joined
- player left
- game started
- role assignment
- decision submitted
- decision missed
- round resolution
- scene transition
- elimination
- NPC conversion
- ending
- cancellation
- errors/recovery events

This provides a persistent history for debugging and replay analysis.

---

Story History

Validated generated stories are stored in:

story_history

A structural fingerprint prevents accidental duplicate story storage.

The story stored there should be the validated story, not raw untrusted Gemini output.

---

Supabase Setup

Before running the new Python application against Supabase:

1. Open the Supabase project.
2. Open the SQL Editor.
3. Review "supabase.sql".
4. Apply the migration.
5. Confirm that the tables exist.
6. Confirm that the RPC functions exist.

Expected tables include:

games
players
decisions
story_history
game_events

Expected atomic functions include:

start_game_atomic
submit_decision_atomic
claim_round_resolution
complete_round_atomic
increment_missed_decisions
cancel_game_atomic

Do not deploy the new Python application while the database schema is still the old schema.

---

Local Development

Install dependencies:

pip install -r requirements.txt

Create a local environment file:

.env

using:

.env.example

as the template.

Never commit ".env".

Run:

python main.py

The application will listen on the configured host and port.

For local Telegram webhook testing, the endpoint must be publicly reachable over HTTPS. A tunnel can be used during development if necessary.

---

Render Deployment

Push the rebuilt project to the repository.

Render should use:

Build Command:
pip install -r requirements.txt

and:

Start Command:
python main.py

The included "render.yaml" describes the intended Web Service configuration.

Add the real secret values in Render:

TELEGRAM_BOT_TOKEN
GEMINI_API_KEY
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY

Do not commit those values.

---

Telegram Webhook Deployment Flow

After deployment:

Render starts
      ↓
main.py starts
      ↓
FastAPI starts
      ↓
Telegram application initializes
      ↓
Telegram webhook is registered
      ↓
Supabase games are recovered
      ↓
service is ready

Telegram then sends updates to:

POST /telegram/webhook

There should be no repeated:

getUpdates

requests.

---

Startup Recovery

The application performs database-backed recovery when it starts.

The recovery process checks active games and persistent deadlines.

This is important because Render Web Services can restart.

The application must assume:

the Python process can disappear at any time

and therefore must never rely on process memory for critical game state.

---

Private Telegram Onboarding

Telegram users must first open the bot privately before the bot can reliably send them private messages.

The bot layer should therefore distinguish:

JOIN requested

from:

private onboarding completed

A group JOIN operation must not blindly assume that Telegram private messaging is already available.

The "/start" command should recover pending private onboarding.

---

Security

Never expose:

TELEGRAM_BOT_TOKEN
GEMINI_API_KEY
SUPABASE_SERVICE_ROLE_KEY

to players.

Never place the Supabase service-role key in client-side code.

Never trust Telegram callback data as authoritative game state.

Never trust Gemini output without validation.

Never use Python memory as the only source of truth.

---

Important Deployment Rule

Do not deploy individual Python files independently.

The architecture is intentionally interconnected.

Before the first production deployment, verify that these pieces match:

config.py
models.py
database.py
story_validator.py
story_generator.py
engine.py
game_service.py
bot.py
main.py
requirements.txt
render.yaml
supabase.sql
.env.example
README.md

The database migration must match the RPC calls made by "database.py".

The environment variables expected by "config.py" must match Render.

The Telegram webhook path must match:

TELEGRAM_WEBHOOK_PATH

and the FastAPI route.

The Gemini model configuration must match the current Gemini SDK implementation.

---

Production Checklist

Before deployment:

- [ ] Old Telegram polling code removed.
- [ ] No "getUpdates" loop remains.
- [ ] FastAPI "/health" exists.
- [ ] FastAPI "/telegram/webhook" exists.
- [ ] Application binds to "0.0.0.0:$PORT".
- [ ] Telegram token regenerated if previously exposed.
- [ ] Telegram token stored only in Render.
- [ ] Gemini key stored only in Render.
- [ ] Supabase service-role key stored only in Render.
- [ ] "supabase.sql" applied.
- [ ] Required RPC functions exist.
- [ ] "GEMINI_MODEL" is configured correctly.
- [ ] Generated stories are validated.
- [ ] Player decisions are persisted.
- [ ] Game deadlines are persisted.
- [ ] Game events are persisted.
- [ ] Missed decisions are persisted.
- [ ] Round resolution is database-protected.
- [ ] Duplicate decisions are prevented.
- [ ] Role duplication is prevented.
- [ ] Late joining uses "joined_round".
- [ ] Startup recovery works.
- [ ] Telegram webhook secret is configured.
- [ ] Render health check is "/health".
- [ ] No secrets are committed to Git.

---

Files

YourOwnVision/
│
├── main.py
├── bot.py
├── game_service.py
├── engine.py
├── story_generator.py
├── story_validator.py
├── database.py
├── models.py
├── config.py
│
├── requirements.txt
├── render.yaml
├── supabase.sql
├── .env.example
└── README.md

---

Design Principle

The central design rule of WHAT HAPPENS? is:

«Telegram is the interface. Gemini prepares the story. The engine determines the game. Supabase remembers the game.»

That separation is what allows the game to survive:

- Telegram retries
- duplicate callbacks
- concurrent requests
- worker races
- Render restarts
- delayed processing
- expired deadlines
- process crashes

without losing the authoritative game state.


### Exactly what to do

1. Create `README.md` in the **root** of `YourOwnVision`.
2. Paste the complete contents above.
3. Save it.
4. **Do not deploy yet.**
5. Do not add any real secrets to the README.

At this point, the agreed file sequence is complete.

However, **we are not finished with the architecture**. Before deployment, I want to reconcile the actual uploaded Python files against each other because the files we've replaced have interdependencies, and I don't want to pretend the project is deploy-ready merely because all filenames now exist.

**The next step should therefore be an architecture/code consistency check of the actual project files, not blindly deploying it.**
