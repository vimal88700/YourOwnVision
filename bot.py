from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import CONFIG
from database import Database
from engine import GameEngine
from story_generator import StoryGenerator


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("what-happens")


db = Database(
    CONFIG.supabase_url,
    CONFIG.supabase_service_key,
)

generator = StoryGenerator(
    CONFIG.gemini_api_key,
    CONFIG.gemini_model,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def user_name(update: Update) -> str:
    user = update.effective_user

    if not user:
        return "Player"

    return user.full_name or user.username or "Player"


def is_admin(user_id: int) -> bool:
    return user_id in CONFIG.admin_user_ids


def game_can_be_stopped(
    game: dict,
    user_id: int,
) -> bool:

    return (
        int(game["creator_id"]) == user_id
        or is_admin(user_id)
    )


async def send_private(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    text: str,
    reply_markup=None,
) -> bool:

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
        )
        return True

    except Exception as exc:
        logger.warning(
            "Could not DM user %s: %s",
            user_id,
            exc,
        )
        return False


# ---------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_message:
        return

    await update.effective_message.reply_text(
        "🎭 WHAT HAPPENS?\n\n"
        "A ridiculous group game.\n\n"
        "Nobody knows exactly what will happen.\n"
        "You just press buttons and find out.\n\n"
        "Add me to a group and use /play."
    )


# ---------------------------------------------------------------------
# /play
# ---------------------------------------------------------------------

async def play(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_chat:
        return

    if update.effective_chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text(
            "Add me to a group first, then use /play there."
        )
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    existing = await db.get_active_game(
        chat_id
    )

    if existing:
        await update.effective_message.reply_text(
            "🎭 A WHAT HAPPENS? game is already running.\n\n"
            "Press JOIN to enter it."
        )
        return

    # The first player becomes creator.
    #
    # We initially generate the story for a
    # small group. The story itself is not tied
    # to the exact number of players.
    #
    # The runtime can accommodate late joiners.
    try:
        story, fingerprint = await generate_unique_story(
            player_count=4
        )

    except Exception:
        logger.exception(
            "Story generation failed"
        )

        await update.effective_message.reply_text(
            "Something broke while preparing the story.\n"
            "Try /play again in a moment."
        )
        return

    join_deadline = (
        now_utc()
        + timedelta(
            seconds=CONFIG.game_join_seconds
        )
    )

    game = await db.create_game(
        chat_id=chat_id,
        creator_id=user_id,
        story=story,
        fingerprint=fingerprint,
        join_deadline=iso(join_deadline),
    )

    await db.add_player(
        game_id=game["id"],
        user_id=user_id,
        username=update.effective_user.username or "",
        display_name=user_name(update),
        role_id=None,
        joined_round=0,
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎭 JOIN",
                    callback_data=f"join:{game['id']}",
                )
            ],
            [
                InlineKeyboardButton(
                    "▶️ START",
                    callback_data=f"begin:{game['id']}",
                )
            ],
        ]
    )

    await update.effective_message.reply_text(
        "🎭 WHAT HAPPENS?\n\n"
        f"**{story['title']}**\n\n"
        "Someone is about to make a terrible decision.\n"
        "The only question is who.\n\n"
        "Tap JOIN.\n"
        "The game starts soon.",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

    context.job_queue.run_once(
        begin_game_job,
        when=CONFIG.game_join_seconds,
        data={
            "game_id": game["id"],
            "chat_id": chat_id,
        },
        name=f"begin:{game['id']}",
    )


async def generate_unique_story(
    player_count: int,
) -> tuple[dict, str]:

    # We don't have a huge history query here.
    # The fingerprint itself is stored permanently
    # and we retry if a collision happens.
    previous: list[str] = []

    for _ in range(5):

        story, fingerprint = await generator.generate(
            player_count=player_count,
            previous_fingerprints=previous,
        )

        if not await db.fingerprint_exists(
            fingerprint
        ):
            await db.save_story_history(
                fingerprint,
                story,
            )

            return story, fingerprint

        previous.append(fingerprint)

    raise RuntimeError(
        "Could not generate a unique story."
    )


# ---------------------------------------------------------------------
# JOIN
# ---------------------------------------------------------------------

async def join_game(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
):

    user = query.from_user

    game = await db.get_game(game_id)

    if not game or game["status"] == "ended":
        await query.answer(
            "That game is already over.",
            show_alert=True,
        )
        return

    player = await db.get_player(
        game_id,
        user.id,
    )

    if player:
        await query.answer(
            "You're already in.",
            show_alert=True,
        )
        return

    current_round = int(
        game.get("current_round") or 0
    )

    await db.add_player(
        game_id=game_id,
        user_id=user.id,
        username=user.username or "",
        display_name=user.full_name,
        role_id=None,
        joined_round=current_round,
    )

    await query.answer(
        "You're in.",
        show_alert=True,
    )

    try:
        await context.bot.send_message(
            chat_id=game["chat_id"],
            text=f"👋 {user.full_name} joined WHAT HAPPENS?",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------
# BEGIN
# ---------------------------------------------------------------------

async def begin_game(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
):

    game = await db.get_game(game_id)

    if not game or game["status"] == "ended":
        await query.answer(
            "Game is over.",
            show_alert=True,
        )
        return

    if not game_can_be_stopped(
        game,
        query.from_user.id,
    ):
        # Anyone can initiate the game, but
        # only creator/admin can manually START.
        await query.answer(
            "Only the creator can start it manually.",
            show_alert=True,
        )
        return

    await query.answer()

    await start_game(
        game_id,
        context,
    )


async def start_game(
    game_id: str,
    context: ContextTypes.DEFAULT_TYPE,
):

    game = await db.get_game(game_id)

    if not game or game["status"] == "ended":
        return

    players = await db.get_players(game_id)

    if not players:
        return

    story = game["story"]

    engine = GameEngine(story)

    roles = story["roles"]

    # Deterministic role assignment based on
    # user IDs + game ID.
    ordered = sorted(
        players,
        key=lambda p: (
            hash(
                f"{game_id}:{p['user_id']}"
            )
        ),
    )

    for index, player in enumerate(ordered):

        role = roles[
            index % len(roles)
        ]

        await db.update_player(
            game_id,
            int(player["user_id"]),
            {
                "role_id": role["id"],
                "joined_round": 0,
            },
        )

    scene = engine.first_scene()

    deadline = (
        now_utc()
        + timedelta(
            seconds=int(
                scene.get(
                    "timer_seconds",
                    CONFIG.default_decision_seconds,
                )
            )
        )
    )

    await db.update_game(
        game_id,
        {
            "status": "playing",
            "current_scene_id": scene["id"],
            "current_round": 1,
            "decision_deadline": iso(deadline),
            "updated_at": iso(now_utc()),
        },
    )

    await context.bot.send_message(
        chat_id=game["chat_id"],
        text=(
            f"🎬 **{story['title']}**\n\n"
            f"{scene['public_text']}\n\n"
            "👀 Check your private messages.\n"
            "Your role has a decision."
        ),
        parse_mode="Markdown",
    )

    await send_scene_decisions(
        game_id,
        context,
    )

    schedule_resolution(
        context,
        game_id,
        int(
            scene.get(
                "timer_seconds",
                CONFIG.default_decision_seconds,
            )
        ),
    )


# ---------------------------------------------------------------------
# SEND PRIVATE DECISIONS
# ---------------------------------------------------------------------

async def send_scene_decisions(
    game_id: str,
    context: ContextTypes.DEFAULT_TYPE,
):

    game = await db.get_game(game_id)

    if not game:
        return

    scene_id = game["current_scene_id"]

    if not scene_id:
        return

    engine = GameEngine(
        game["story"]
    )

    scene = engine.get_scene(
        scene_id
    )

    players = await db.get_players(
        game_id
    )

    round_number = int(
        game["current_round"]
    )

    for player in players:

        if not player["active"]:
            continue

        role_id = player.get("role_id")

        if not role_id:
            continue

        choices = engine.choices_for_role(
            scene,
            role_id,
        )

        if not choices:
            continue

        keyboard = []

        for choice in choices:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        choice["label"],
                        callback_data=(
                            f"choose:"
                            f"{game_id}:"
                            f"{round_number}:"
                            f"{choice['id']}"
                        ),
                    )
                ]
            )

        role = engine.get_role(
            role_id
        )

        text = (
            "🤫 **YOUR SECRET ROLE**\n\n"
            f"**{role['name']}**\n"
            f"{role['secret_description']}\n\n"
            "WHAT DO YOU DO?"
        )

        await send_private(
            context,
            int(player["user_id"]),
            text,
            InlineKeyboardMarkup(
                keyboard
            ),
        )


# ---------------------------------------------------------------------
# CHOICE
# ---------------------------------------------------------------------

async def choose(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
    round_number: int,
    choice_id: str,
):

    user = query.from_user

    game = await db.get_game(
        game_id
    )

    if not game or game["status"] != "playing":
        await query.answer(
            "This game isn't accepting choices.",
            show_alert=True,
        )
        return

    if int(game["current_round"]) != round_number:
        await query.answer(
            "That decision has already passed.",
            show_alert=True,
        )
        return

    player = await db.get_player(
        game_id,
        user.id,
    )

    if not player or not player["active"]:
        await query.answer(
            "You're not an active player.",
            show_alert=True,
        )
        return

    await db.save_decision(
        game_id=game_id,
        round_number=round_number,
        scene_id=game["current_scene_id"],
        user_id=user.id,
        choice_id=choice_id,
    )

    await query.answer(
        "Choice locked in.",
        show_alert=True,
    )

    await query.edit_message_reply_markup(
        reply_markup=None
    )

    # Check whether everyone has already chosen.
    await maybe_resolve_round(
        game_id,
        context,
        force=False,
    )


# ---------------------------------------------------------------------
# ROUND RESOLUTION
# ---------------------------------------------------------------------

async def maybe_resolve_round(
    game_id: str,
    context: ContextTypes.DEFAULT_TYPE,
    force: bool,
):

    game = await db.get_game(
        game_id
    )

    if not game or game["status"] != "playing":
        return

    players = await db.get_players(
        game_id
    )

    active_players = [
        p for p in players
        if p["active"]
        and p.get("role_id")
        and int(p.get("joined_round", 0))
        <= int(game["current_round"])
    ]

    decisions = await db.get_round_decisions(
        game_id,
        int(game["current_round"]),
    )

    chosen_ids = {
        int(d["user_id"])
        for d in decisions
        if d.get("choice_id")
    }

    if not force and any(
        int(p["user_id"]) not in chosen_ids
        for p in active_players
    ):
        return

    engine = GameEngine(
        game["story"]
    )

    scene = engine.get_scene(
        game["current_scene_id"]
    )

    results = []

    for decision in decisions:

        if not decision.get("choice_id"):
            continue

        try:
            result = engine.resolve_choice(
                scene,
                decision["choice_id"],
                round_number=int(
                    game["current_round"]
                ),
            )

            results.append(result)

        except Exception:
            logger.exception(
                "Could not resolve decision"
            )

    # Missing decisions.
    for player in active_players:

        uid = int(player["user_id"])

        if uid not in chosen_ids:

            missed = int(
                player["missed_decisions"]
            ) + 1

            if missed >= GameEngine.MAX_MISSED:

                await db.update_player(
                    game_id,
                    uid,
                    {
                        "missed_decisions": missed,
                        "active": False,
                    },
                )

                await context.bot.send_message(
                    chat_id=game["chat_id"],
                    text=(
                        f"👻 {player['display_name']} "
                        "missed too many decisions and "
                        "has become an NPC."
                    ),
                )

            else:

                await db.update_player(
                    game_id,
                    uid,
                    {
                        "missed_decisions": missed
                    },
                )

    if not results:

        await end_game(
            game_id,
            context,
            "Nobody made a decision. "
            "The universe took over.",
        )
        return

    await db.resolve_round(
        game_id,
        int(game["current_round"]),
    )

    # Public consequences.
    events = [
        result["public_event"]
        for result in results
        if result.get("public_event")
    ]

    if events:

        text = "💥 **WHAT HAPPENS?**\n\n"

        text += "\n\n".join(
            f"• {event}"
            for event in events
        )

        await context.bot.send_message(
            chat_id=game["chat_id"],
            text=text,
            parse_mode="Markdown",
        )

    next_scene_id = (
        engine.next_scene_from_results(
            scene,
            results,
        )
    )

    if not next_scene_id:

        await end_game(
            game_id,
            context,
            "And somehow... that was the end.",
        )
        return

    next_scene = engine.get_scene(
        next_scene_id
    )

    next_round = (
        int(game["current_round"]) + 1
    )

    deadline = (
        now_utc()
        + timedelta(
            seconds=int(
                next_scene.get(
                    "timer_seconds",
                    CONFIG.default_decision_seconds,
                )
            )
        )
    )

    await db.update_game(
        game_id,
        {
            "current_scene_id": next_scene_id,
            "current_round": next_round,
            "decision_deadline": iso(deadline),
            "updated_at": iso(now_utc()),
        },
    )

    await context.bot.send_message(
        chat_id=game["chat_id"],
        text=(
            f"🎬 **Round {next_round}**\n\n"
            f"{next_scene['public_text']}"
        ),
        parse_mode="Markdown",
    )

    await send_scene_decisions(
        game_id,
        context,
    )

    schedule_resolution(
        context,
        game_id,
        int(
            next_scene.get(
                "timer_seconds",
                CONFIG.default_decision_seconds,
            )
        ),
    )


# ---------------------------------------------------------------------
# TIMERS
# ---------------------------------------------------------------------

def schedule_resolution(
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
    seconds: int,
):

    context.job_queue.run_once(
        resolve_timeout_job,
        when=seconds,
        data=game_id,
        name=f"resolve:{game_id}",
    )


async def resolve_timeout_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    game_id = context.job.data

    await maybe_resolve_round(
        game_id,
        context,
        force=True,
    )


async def begin_game_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    data = context.job.data

    game_id = data["game_id"]

    game = await db.get_game(
        game_id
    )

    if not game:
        return

    if game["status"] != "lobby":
        return

    await start_game(
        game_id,
        context,
    )


# ---------------------------------------------------------------------
# STOP
# ---------------------------------------------------------------------

async def stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.effective_chat:
        return

    game = await db.get_active_game(
        update.effective_chat.id
    )

    if not game:
        await update.effective_message.reply_text(
            "There is no active game."
        )
        return

    user_id = update.effective_user.id

    if not game_can_be_stopped(
        game,
        user_id,
    ):
        await update.effective_message.reply_text(
            "Only the game creator or a configured admin can stop it."
        )
        return

    await end_game(
        game["id"],
        context,
        "🛑 The game was stopped.",
    )


# ---------------------------------------------------------------------
# END
# ---------------------------------------------------------------------

async def end_game(
    game_id: str,
    context: ContextTypes.DEFAULT_TYPE,
    reason: str,
):

    game = await db.get_game(
        game_id
    )

    if not game:
        return

    if game["status"] == "ended":
        return

    await db.update_game(
        game_id,
        {
            "status": "ended",
            "ended_at": iso(now_utc()),
            "updated_at": iso(now_utc()),
        },
    )

    await context.bot.send_message(
        chat_id=game["chat_id"],
        text=(
            "🎭 **WHAT HAPPENS? — END**\n\n"
            f"{reason}\n\n"
            "🔄 Use /play when you want another one."
        ),
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------
# CALLBACK ROUTER
# ---------------------------------------------------------------------

async def callback_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    data = query.data or ""

    parts = data.split(":")

    try:

        if parts[0] == "join":

            await join_game(
                query,
                context,
                parts[1],
            )
            return

        if parts[0] == "begin":

            await begin_game(
                query,
                context,
                parts[1],
            )
            return

        if parts[0] == "choose":

            await choose(
                query,
                context,
                parts[1],
                int(parts[2]),
                ":".join(parts[3:]),
            )
            return

        await query.answer()

    except Exception:
        logger.exception(
            "Callback processing failed"
        )

        try:
            await query.answer(
                "Something went wrong.",
                show_alert=True,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------
# ERROR HANDLER
# ---------------------------------------------------------------------

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.exception(
        "Unhandled exception",
        exc_info=context.error,
    )


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main():

    application = (
        Application.builder()
        .token(CONFIG.telegram_token)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "play",
            play,
        )
    )

    application.add_handler(
        CommandHandler(
            "stop",
            stop,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_router,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "WHAT HAPPENS? bot starting..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
