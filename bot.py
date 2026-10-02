from __future__ import annotations

import logging
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import CONFIG
from database import Database
from game_service import GameService
from story_generator import StoryGenerator


logger = logging.getLogger("YourOwnVision.bot")


# ============================================================
# SERVICES
# ============================================================

db = Database(
    CONFIG.supabase_url,
    CONFIG.supabase_service_key,
)

story_generator = StoryGenerator(
    CONFIG.gemini_api_key,
    CONFIG.gemini_model,
)

game_service = GameService(
    db,
    story_generator,
    join_seconds=CONFIG.game_join_seconds,
    default_decision_seconds=CONFIG.default_decision_seconds,
    minimum_decision_seconds=CONFIG.minimum_decision_seconds,
    maximum_decision_seconds=CONFIG.maximum_decision_seconds,
)


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def display_name(user: Any) -> str:
    if not user:
        return "Player"

    return (
        getattr(user, "full_name", None)
        or getattr(user, "username", None)
        or "Player"
    )


async def safe_answer(
    query: Any,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> None:
    try:
        await query.answer(
            text=text,
            show_alert=show_alert,
        )
    except TelegramError:
        pass


async def send_private(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """
    Attempt a private Telegram message.

    Telegram does not allow a bot to initiate a private
    conversation with a user who has never started the bot.

    Failure here MUST NOT undo the persistent game join.
    """

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
        )
        return True

    except Forbidden:
        logger.info(
            "Private Telegram chat unavailable for user %s.",
            user_id,
        )
        return False

    except TelegramError:
        logger.exception(
            "Could not send private message to user %s.",
            user_id,
        )
        return False


# ============================================================
# TELEGRAM TEXT
# ============================================================

def story_title(
    game: dict[str, Any],
) -> str:
    story = game.get("story")

    if isinstance(story, dict):
        value = story.get("title")

        if isinstance(value, str) and value.strip():
            return value.strip()

    return "WHAT HAPPENS?"


def scene_public_text(
    scene: dict[str, Any],
) -> str:
    value = scene.get("public_text")

    if isinstance(value, str) and value.strip():
        return value.strip()

    value = scene.get("description")

    if isinstance(value, str) and value.strip():
        return value.strip()

    return "Something is happening..."


def format_seconds(
    seconds: int,
) -> str:
    seconds = max(0, int(seconds))

    if seconds < 60:
        return f"{seconds} seconds"

    minutes, remainder = divmod(
        seconds,
        60,
    )

    if remainder == 0:
        return (
            f"{minutes} minute"
            f"{'' if minutes == 1 else 's'}"
        )

    return f"{minutes}m {remainder}s"


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user:
        return

    # --------------------------------------------------------
    # PRIVATE ONBOARDING
    # --------------------------------------------------------

    if chat and chat.type == ChatType.PRIVATE:
        onboarded = False

        try:
            games = await db.get_active_games()

            for game in games:
                player = await db.get_player(
                    game["id"],
                    user.id,
                )

                if not player:
                    continue

                updated = await db.mark_player_onboarded(
                    game["id"],
                    user.id,
                )

                if updated:
                    onboarded = True

        except Exception:
            logger.exception(
                "Private onboarding failed for user %s.",
                user.id,
            )

        if onboarded:
            await message.reply_text(
                "🎭 You're ready.\n\n"
                "Your WHAT HAPPENS? private channel is now "
                "connected to the game.\n\n"
                "When a decision is required, I'll send it here."
            )
        else:
            await message.reply_text(
                "🎭 **WHAT HAPPENS?**\n\n"
                "You're connected to the bot.\n\n"
                "If you joined a game in a group, "
                "your private role and decisions will appear here.\n\n"
                "Otherwise, return to the group and use /play "
                "to start a new story.",
                parse_mode="Markdown",
            )

        return

    # --------------------------------------------------------
    # GROUP / OTHER CHAT
    # --------------------------------------------------------

    await message.reply_text(
        "🎭 **WHAT HAPPENS?**\n\n"
        "A deterministic story game where players shape "
        "what happens next.\n\n"
        "Use /play in a group to start a game.\n\n"
        "Your secret role and private decisions are sent "
        "through this bot.",
        parse_mode="Markdown",
    )


# ============================================================
# /PLAY
# ============================================================

async def play(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user:
        return

    if chat.type == ChatType.PRIVATE:
        await message.reply_text(
            "Add me to a group first.\n\n"
            "Then use /play inside the group."
        )
        return

    existing = await db.get_active_game(
        chat.id
    )

    if existing:
        await message.reply_text(
            "🎭 A WHAT HAPPENS? game is already active "
            "in this group.\n\n"
            "Use the existing game's JOIN button."
        )
        return

    await message.reply_text(
        "🎭 Preparing the story..."
    )

    try:
        game = await game_service.create_game(
            chat_id=chat.id,
            creator_id=user.id,
        )

        # Creator is automatically added as the first player.
        await game_service.join_game(
            game_id=game["id"],
            user_id=user.id,
            username=user.username or "",
            display_name=display_name(user),
        )

    except Exception as exc:
        logger.exception(
            "Could not create game."
        )

        await message.reply_text(
            "⚠️ I couldn't create the game.\n\n"
            f"{exc}"
        )
        return

    title = story_title(game)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎭 JOIN",
                    callback_data=f"join:{game['id']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "▶️ START",
                    callback_data=f"begin:{game['id']}",
                ),
            ],
        ]
    )

    await message.reply_text(
        f"🎭 **WHAT HAPPENS?**\n\n"
        f"**{title}**\n\n"
        "The story is prepared and validated.\n"
        "Players can now join.\n\n"
        "Your private role and decisions will be "
        "sent by DM.\n\n"
        "If Telegram has never received `/start` "
        "from a player, they must open the bot privately "
        "before private decisions can be delivered.",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


# ============================================================
# JOIN
# ============================================================

async def join_game(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
) -> None:
    user = query.from_user

    try:
        game = await game_service.get_game(
            game_id
        )

        player = await game_service.join_game(
            game_id=game_id,
            user_id=user.id,
            username=user.username or "",
            display_name=display_name(user),
        )

    except Exception as exc:
        logger.info(
            "Join rejected for user %s: %s",
            user.id,
            exc,
        )

        await safe_answer(
            query,
            str(exc),
            show_alert=True,
        )
        return

    await safe_answer(
        query,
        "🎭 You're in!",
        show_alert=True,
    )

    # --------------------------------------------------------
    # PRIVATE ONBOARDING
    # --------------------------------------------------------

    dm_ok = await send_private(
        context,
        user.id,
        "🎭 **You're in WHAT HAPPENS?**\n\n"
        "Your private role and decisions will be sent here.\n\n"
        "If this is your first time using the bot, "
        "send `/start` in this private chat.",
        parse_mode=None,
    )

    if not dm_ok:
        # Do NOT remove the player.
        #
        # The persistent membership is valid.
        # The user simply has not opened the private bot chat.
        try:
            await context.bot.send_message(
                chat_id=game["chat_id"],
                text=(
                    f"👋 {display_name(user)} joined.\n\n"
                    "⚠️ Before the game starts, "
                    "open the bot privately and send /start "
                    "so I can deliver your secret role and decisions."
                ),
            )
        except TelegramError:
            pass

    else:
        try:
            await context.bot.send_message(
                chat_id=game["chat_id"],
                text=(
                    f"👋 {display_name(user)} joined the story."
                ),
            )
        except TelegramError:
            pass

    # Silence unused-variable warnings while keeping the
    # returned persistent player available for future UI.
    _ = player


# ============================================================
# START GAME
# ============================================================

async def begin_game(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
) -> None:
    try:
        game = await game_service.get_game(
            game_id
        )

    except Exception:
        await safe_answer(
            query,
            "Game not found.",
            show_alert=True,
        )
        return

    if game.get("status") != "lobby":
        await safe_answer(
            query,
            "This game cannot be started now.",
            show_alert=True,
        )
        return

    await safe_answer(
        query,
        "Starting...",
    )

    try:
        await game_service.start_game(
            game_id=game_id
        )

    except Exception as exc:
        logger.exception(
            "Could not start game %s.",
            game_id,
        )

        await safe_answer(
            query,
            str(exc),
            show_alert=True,
        )
        return

    await publish_current_scene(
        context,
        game_id,
    )


# ============================================================
# PUBLISH SCENE
# ============================================================

async def publish_current_scene(
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
) -> None:
    game, players = await (
        game_service.get_game_with_players(
            game_id
        )
    )

    if game.get("status") != "playing":
        return

    scene_id = game.get(
        "current_scene_id"
    )

    if not scene_id:
        logger.error(
            "Game %s is playing without a current scene.",
            game_id,
        )
        return

    engine = game_service.engine_for_game(
        game
    )

    scene = engine.get_scene(
        scene_id
    )

    round_number = int(
        game.get(
            "current_round",
            0,
        )
        or 0
    )

    timer_seconds = engine.timer_seconds(
        scene
    )

    title = story_title(game)
    public_text = scene_public_text(scene)

    try:
        await context.bot.send_message(
            chat_id=int(game["chat_id"]),
            text=(
                f"🎬 **{title}**\n\n"
                f"**Round {round_number}**\n\n"
                f"{public_text}\n\n"
                f"⏳ Decision window: "
                f"{format_seconds(timer_seconds)}"
            ),
            parse_mode="Markdown",
        )
    except TelegramError:
        logger.exception(
            "Could not publish scene for game %s.",
            game_id,
        )

    # --------------------------------------------------------
    # PRIVATE PLAYER DECISIONS
    # --------------------------------------------------------

    for player in players:
        status = game_service.player_status(
            player
        )

        if status != game_service.PLAYER_ACTIVE:
            continue

        if not game_service.is_required_for_round(
            player,
            round_number,
        ):
            continue

        role_id = player.get(
            "role_id"
        )

        if not role_id:
            logger.error(
                "Player %s has no role.",
                player.get("user_id"),
            )
            continue

        try:
            choices = engine.choices_for_role(
                scene,
                str(role_id),
                game.get("world_state")
                or engine.initial_world_state(),
            )
        except Exception:
            logger.exception(
                "Could not create choices for player %s.",
                player.get("user_id"),
            )
            continue

        if not choices:
            continue

        try:
            role = engine.get_role(
                str(role_id)
            )
        except Exception:
            logger.exception(
                "Could not load role %s.",
                role_id,
            )
            continue

        keyboard_rows: list[
            list[InlineKeyboardButton]
        ] = []

        for choice in choices:
            choice_id = str(
                choice["id"]
            )

            callback = (
                f"choose:"
                f"{game_id}:"
                f"{round_number}:"
                f"{choice_id}"
            )

            if len(
                callback.encode("utf-8")
            ) > 64:
                logger.error(
                    "Telegram callback_data too long: %s",
                    callback,
                )
                continue

            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        str(
                            choice.get(
                                "label",
                                choice_id,
                            )
                        ),
                        callback_data=callback,
                    )
                ]
            )

        if not keyboard_rows:
            continue

        secret_description = role.get(
            "secret_description",
            "",
        )

        role_name = role.get(
            "name",
            "Unknown",
        )

        text = (
            "🤫 **YOUR SECRET ROLE**\n\n"
            f"**{role_name}**\n\n"
            f"{secret_description}\n\n"
            "━━━━━━━━━━━━━━\n\n"
            f"⏳ You have about "
            f"{format_seconds(timer_seconds)}.\n\n"
            "**WHAT DO YOU DO?**"
        )

        dm_ok = await send_private(
            context,
            int(player["user_id"]),
            text,
            InlineKeyboardMarkup(
                keyboard_rows
            ),
        )

        if not dm_ok:
            logger.info(
                "Player %s has no accessible private chat.",
                player["user_id"],
            )


# ============================================================
# CHOICE
# ============================================================

async def choose(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
    callback_round: int,
    choice_id: str,
) -> None:
    user = query.from_user

    try:
        game = await game_service.get_game(
            game_id
        )
    except Exception:
        await safe_answer(
            query,
            "Game not found.",
            show_alert=True,
        )
        return

    current_round = int(
        game.get(
            "current_round",
            0,
        )
        or 0
    )

    if callback_round != current_round:
        await safe_answer(
            query,
            "⏰ That decision belongs to an old round.",
            show_alert=True,
        )
        return

    if game.get("status") != "playing":
        await safe_answer(
            query,
            "This game is no longer accepting decisions.",
            show_alert=True,
        )
        return

    try:
        result = await game_service.submit_decision(
            game_id=game_id,
            user_id=user.id,
            choice_id=choice_id,
        )

    except Exception as exc:
        logger.info(
            "Decision rejected: game=%s user=%s: %s",
            game_id,
            user.id,
            exc,
        )

        await safe_answer(
            query,
            str(exc),
            show_alert=True,
        )
        return

    if result.get("duplicate"):
        await safe_answer(
            query,
            "You already made your decision.",
            show_alert=True,
        )
        return

    await safe_answer(
        query,
        "✅ Decision recorded.",
    )

    # Disable the decision buttons in the player's message.
    try:
        await query.edit_message_reply_markup(
            reply_markup=None
        )
    except TelegramError:
        pass

    resolution = result.get(
        "resolution"
    )

    if resolution and resolution.get(
        "resolved"
    ):
        await publish_resolution(
            context,
            game_id,
            resolution,
        )


# ============================================================
# PUBLISH ROUND RESULT
# ============================================================

async def publish_resolution(
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
    resolution: dict[str, Any],
) -> None:
    if resolution.get(
        "already_claimed"
    ):
        return

    result = resolution.get(
        "result"
    )

    if not isinstance(
        result,
        dict,
    ):
        return

    game = await db.get_game(
        game_id
    )

    if not game:
        return

    ending = result.get(
        "ending"
    )

    if ending:
        ending_title = ending.get(
            "title",
            "The End",
        )

        ending_text = ending.get(
            "text",
            "The story has ended.",
        )

        try:
            await context.bot.send_message(
                chat_id=int(game["chat_id"]),
                text=(
                    "🎭 **WHAT HAPPENS? — THE END**\n\n"
                    f"**{ending_title}**\n\n"
                    f"{ending_text}"
                ),
                parse_mode="Markdown",
            )
        except TelegramError:
            logger.exception(
                "Could not publish ending."
            )

        return

    events = result.get(
        "events",
        []
    )

    public_events: list[str] = []

    if isinstance(events, list):
        for event in events:
            if not isinstance(
                event,
                dict,
            ):
                continue

            text = event.get(
                "public_text"
            ) or event.get(
                "text"
            )

            if text:
                public_events.append(
                    str(text)
                )

    if public_events:
        try:
            await context.bot.send_message(
                chat_id=int(game["chat_id"]),
                text=(
                    "💥 **WHAT HAPPENS?**\n\n"
                    + "\n\n".join(
                        f"• {event}"
                        for event in public_events
                    )
                ),
                parse_mode="Markdown",
            )
        except TelegramError:
            logger.exception(
                "Could not publish public events."
            )

    next_scene_id = result.get(
        "next_scene"
    )

    if not next_scene_id:
        return

    # The database has already committed the next scene.
    # Re-read it rather than trusting transient result state.
    refreshed = await db.get_game(
        game_id
    )

    if not refreshed:
        return

    if refreshed.get("status") != "playing":
        return

    await publish_current_scene(
        context,
        game_id,
    )


# ============================================================
# /STOP
# ============================================================

async def stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if not message or not chat or not user:
        return

    if chat.type == ChatType.PRIVATE:
        await message.reply_text(
            "Use /stop inside the group."
        )
        return

    game = await db.get_active_game(
        chat.id
    )

    if not game:
        await message.reply_text(
            "There is no active WHAT HAPPENS? game."
        )
        return

    allowed = (
        int(game["creator_id"]) == user.id
        or user.id in CONFIG.admin_user_ids
    )

    if not allowed:
        try:
            member = await context.bot.get_chat_member(
                chat_id=chat.id,
                user_id=user.id,
            )

            allowed = member.status in {
                "administrator",
                "creator",
            }

        except TelegramError:
            allowed = False

    if not allowed:
        await message.reply_text(
            "Only the game creator or a group administrator "
            "can stop the game."
        )
        return

    try:
        await db.cancel_game_atomic(
            game["id"]
        )
    except Exception:
        logger.exception(
            "Could not cancel game %s.",
            game["id"],
        )

        await message.reply_text(
            "⚠️ I couldn't stop the game safely."
        )
        return

    await message.reply_text(
        "🛑 The WHAT HAPPENS? game has been stopped."
    )


# ============================================================
# CALLBACK ROUTER
# ============================================================

async def callback_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if not query:
        return

    data = query.data or ""

    try:
        parts = data.split(":")

        action = parts[0] if parts else ""

        # ----------------------------------------------------
        # JOIN
        # ----------------------------------------------------

        if action == "join":
            if len(parts) != 2:
                await safe_answer(
                    query,
                    "Invalid JOIN button.",
                    show_alert=True,
                )
                return

            await join_game(
                query,
                context,
                parts[1],
            )
            return

        # ----------------------------------------------------
        # BEGIN
        # ----------------------------------------------------

        if action == "begin":
            if len(parts) != 2:
                await safe_answer(
                    query,
                    "Invalid START button.",
                    show_alert=True,
                )
                return

            await begin_game(
                query,
                context,
                parts[1],
            )
            return

        # ----------------------------------------------------
        # CHOOSE
        # ----------------------------------------------------

        if action == "choose":
            if len(parts) < 4:
                await safe_answer(
                    query,
                    "Invalid decision button.",
                    show_alert=True,
                )
                return

            game_id = parts[1]

            try:
                round_number = int(
                    parts[2]
                )
            except ValueError:
                await safe_answer(
                    query,
                    "Invalid round.",
                    show_alert=True,
                )
                return

            choice_id = ":".join(
                parts[3:]
            )

            await choose(
                query,
                context,
                game_id,
                round_number,
                choice_id,
            )
            return

        await safe_answer(
            query,
            "Unknown button.",
            show_alert=True,
        )

    except Exception:
        logger.exception(
            "Unhandled Telegram callback."
        )

        await safe_answer(
            query,
            "Something went wrong.",
            show_alert=True,
        )


# ============================================================
# TELEGRAM ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.error(
        "Unhandled Telegram error: %s",
        context.error,
        exc_info=context.error,
    )


# ============================================================
# STARTUP / RECOVERY
# ============================================================

async def recover_active_games() -> None:
    """
    Recover persistent state after a Render restart.

    There is intentionally no JobQueue timer here.

    Game deadlines live in Supabase. The upcoming main.py
    runtime recovery loop will periodically call this method,
    which resolves games whose database deadline has expired.
    """

    logger.info(
        "Recovering active WHAT HAPPENS? games from Supabase..."
    )

    results = await game_service.recover_all_due_games()

    logger.info(
        "Recovery completed. Processed %s game state(s).",
        len(results),
    )


# ============================================================
# APPLICATION FACTORY
# ============================================================

def create_application() -> Application:
    """
    Create the Telegram application.

    IMPORTANT:

    This function does NOT call run_polling().

    main.py owns the HTTP server and feeds Telegram webhook
    updates into this Application instance.
    """

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

    return application


# ============================================================
# WEBHOOK UPDATE PROCESSOR
# ============================================================

async def process_update(
    application: Application,
    update: Update,
) -> None:
    """
    Feed one Telegram webhook update into python-telegram-bot.

    FastAPI/main.py owns the HTTP endpoint.
    """

    await application.process_update(
        update
    )


__all__ = [
    "db",
    "game_service",
    "create_application",
    "process_update",
    "recover_active_games",
]
