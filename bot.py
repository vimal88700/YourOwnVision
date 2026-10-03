from __future__ import annotations

import logging
from datetime import datetime, timezone
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
    maximum_players=CONFIG.maximum_players,
)


def display_name(user: Any) -> str:
    if not user:
        return "Player"

    return (
        getattr(user, "full_name", None)
        or getattr(user, "username", None)
        or "Player"
    )


def story_title(game: dict[str, Any]) -> str:
    story = game.get("story")

    if isinstance(story, dict):
        title = story.get("title")

        if isinstance(title, str) and title.strip():
            return title.strip()

    return "WHAT HAPPENS?"


def scene_public_text(scene: dict[str, Any]) -> str:
    for key in ("public_text", "description"):
        value = scene.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    return "Something is happening..."


def format_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))

    if seconds < 60:
        return f"{seconds} seconds"

    minutes, remainder = divmod(seconds, 60)

    if remainder == 0:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"

    return f"{minutes}m {remainder}s"


def parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)

    return result.astimezone(timezone.utc)


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


async def bot_private_url(
    context: ContextTypes.DEFAULT_TYPE,
    payload: str | None = None,
) -> str | None:
    try:
        me = await context.bot.get_me()

        if not me.username:
            return None

        if payload:
            return (
                f"https://t.me/{me.username}"
                f"?start={payload}"
            )

        return f"https://t.me/{me.username}?start=game"

    except TelegramError:
        logger.exception(
            "Could not resolve bot username."
        )
        return None


async def private_markup(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    game_id: str | None = None,
    label: str = "🔐 OPEN BOT / START",
) -> InlineKeyboardMarkup | None:
    payload = (
        f"decision_{game_id}"
        if game_id
        else "game"
    )

    url = await bot_private_url(
        context,
        payload,
    )

    if not url:
        return None

    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                label,
                url=url,
            )
        ]]
    )


async def send_private(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    parse_mode: str | None = None,
) -> bool:
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return True
    except Forbidden:
        return False
    except TelegramError:
        logger.exception(
            "Could not send private message to user %s.",
            user_id,
        )
        return False


async def send_current_player_state(
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
    user_id: int,
) -> bool:
    try:
        game = await game_service.get_game(game_id)
        player = await db.get_player(
            game_id,
            user_id,
        )

        if not player:
            return False

        if game.get("status") != game_service.STATUS_PLAYING:
            return False

        if game_service.player_status(player) != game_service.PLAYER_ACTIVE:
            return False

        round_number = int(
            game.get("current_round", 0) or 0
        )

        joined_round = int(
            player.get("joined_round", 0) or 0
        )

        if joined_round > round_number:
            return False

        deadline = parse_timestamp(
            game.get("decision_deadline")
        )

        if (
            deadline is not None
            and datetime.now(timezone.utc) >= deadline
        ):
            return False

        role_id = player.get("role_id")
        scene_id = game.get("current_scene_id")

        if not role_id or not scene_id:
            return False

        engine = game_service.engine_for_game(game)
        scene = engine.get_scene(scene_id)

        world_state = (
            game.get("world_state")
            or engine.initial_world_state()
        )

        choices = engine.choices_for_role(
            scene,
            str(role_id),
            world_state,
        )

        if not choices:
            return False

        rows: list[list[InlineKeyboardButton]] = []

        for choice in choices:
            callback = (
                f"choose:{game_id}:"
                f"{round_number}:"
                f"{choice['id']}"
            )

            if len(callback.encode("utf-8")) > 64:
                logger.error(
                    "Callback is too long: %s",
                    callback,
                )
                continue

            rows.append(
                [InlineKeyboardButton(
                    str(choice.get(
                        "label",
                        choice["id"],
                    )),
                    callback_data=callback,
                )]
            )

        if not rows:
            return False

        role = engine.get_role(
            str(role_id)
        )

        text = (
            "🤫 **YOUR SECRET ROLE**\n\n"
            f"**{role.get('name', 'Unknown')}**\n\n"
            f"{role.get('secret_description', '')}\n\n"
            "━━━━━━━━━━━━━━\n\n"
            f"⏳ You have about "
            f"{format_seconds(engine.timer_seconds(scene))}.\n\n"
            "**WHAT DO YOU DO?**"
        )

        await send_private(
            context,
            user_id,
            text,
            InlineKeyboardMarkup(rows),
            parse_mode="Markdown",
        )

        return True

    except Exception:
        logger.exception(
            "Could not send private player state. "
            "game=%s user=%s",
            game_id,
            user_id,
        )
        return False


def lobby_players_text(
    players: list[dict[str, Any]],
) -> str:
    visible = [
        player
        for player in players
        if game_service.player_status(player)
        in {
            game_service.PLAYER_PENDING,
            game_service.PLAYER_ACTIVE,
        }
    ]

    if not visible:
        return "👥 **Players joined:** nobody yet."

    lines = []

    for index, player in enumerate(
        visible,
        start=1,
    ):
        name = (
            player.get("display_name")
            or player.get("username")
            or "Player"
        )
        lines.append(
            f"{index}. {name}"
        )

    return (
        "👥 **Players joined:**\n"
        + "\n".join(lines)
    )


async def lobby_markup(
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(
            "🎭 JOIN",
            callback_data=f"join:{game_id}",
        )
    ]]

    url = await bot_private_url(
        context,
        f"onboard_{game_id}",
    )

    if url:
        rows.append([
            InlineKeyboardButton(
                "🔐 OPEN BOT / START",
                url=url,
            )
        ])

    return InlineKeyboardMarkup(rows)


async def render_lobby(
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
) -> tuple[str, InlineKeyboardMarkup]:
    game, players = await game_service.get_game_with_players(
        game_id
    )

    deadline = parse_timestamp(
        game.get("join_deadline")
    )

    remaining = CONFIG.game_join_seconds

    if deadline:
        remaining = max(
            0,
            int(
                (
                    deadline
                    - datetime.now(timezone.utc)
                ).total_seconds()
            ),
        )

    text = (
        "🎭 **WHAT HAPPENS?**\n\n"
        f"**{story_title(game)}**\n\n"
        "✅ Story created and validated.\n\n"
        f"⏳ **JOINING IS OPEN FOR "
        f"{format_seconds(remaining)}.**\n\n"
        f"{lobby_players_text(players)}\n\n"
        "There is **no game START button**. "
        "The game starts automatically when the "
        "45-second lobby ends.\n\n"
        "Anyone can join while the lobby is open. "
        "A player already in this game cannot join twice."
    )

    return (
        text,
        await lobby_markup(
            context,
            game_id,
        ),
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not user:
        return

    if chat and chat.type == ChatType.PRIVATE:
        payload = (
            context.args[0]
            if context.args
            else ""
        )

        target_game_id: str | None = None

        if payload.startswith("decision_"):
            target_game_id = payload[len("decision_"):]

        elif payload.startswith("onboard_"):
            target_game_id = payload[len("onboard_"):]

        if target_game_id:
            try:
                player = await db.get_player(
                    target_game_id,
                    user.id,
                )

                if player:
                    await db.mark_player_onboarded(
                        target_game_id,
                        user.id,
                    )

                    game = await game_service.get_game(
                        target_game_id
                    )

                    if game.get("status") == game_service.STATUS_PLAYING:
                        sent = await send_current_player_state(
                            context,
                            target_game_id,
                            user.id,
                        )

                        if sent:
                            await message.reply_text(
                                "✅ Your current private decision is above."
                            )
                            return

            except Exception:
                logger.exception(
                    "Targeted private onboarding failed."
                )

        # Without a game-specific deep-link there is nothing safe to
        # guess in a private chat. The group JOIN/OPEN BOT button always
        # carries the exact game id.
        sent = 0

        if sent:
            await message.reply_text(
                "✅ Your current private decision is above."
            )
        else:
            await message.reply_text(
                "🎭 **WHAT HAPPENS?**\n\n"
                "You're connected to the bot.\n\n"
                "Join a game from the group. "
                "Your secret role and private choices will appear here.",
                parse_mode="Markdown",
            )

        return

    await message.reply_text(
        "🎭 **WHAT HAPPENS?**\n\n"
        "Use /play in a group to open a 45-second lobby.\n\n"
        "The game starts automatically after the lobby.",
        parse_mode="Markdown",
    )


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
            "Add me to a group first, then use /play there."
        )
        return

    existing = await db.get_active_game(
        chat.id
    )

    if existing:
        text, markup = await render_lobby(
            context,
            str(existing["id"]),
        )

        if existing.get("status") == game_service.STATUS_PLAYING:
            markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔐 OPEN PRIVATE DECISION",
                        url=(
                            await bot_private_url(
                                context,
                                f"decision_{existing['id']}",
                            )
                        ),
                    )
                ]
            ])

            await message.reply_text(
                "🎭 A WHAT HAPPENS? game is already running.\n\n"
                "Use the button below for your private decision.",
                reply_markup=markup,
            )
        else:
            await message.reply_text(
                text,
                reply_markup=markup,
                parse_mode="Markdown",
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
    except Exception as exc:
        logger.exception(
            "Could not create game."
        )
        await message.reply_text(
            "⚠️ I couldn't create the game.\n\n"
            f"{exc}"
        )
        return

    text, markup = await render_lobby(
        context,
        str(game["id"]),
    )

    await message.reply_text(
        text,
        reply_markup=markup,
        parse_mode="Markdown",
    )


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

    if game.get("status") == game_service.STATUS_LOBBY:
        try:
            text, markup = await render_lobby(
                context,
                game_id,
            )

            if query.message:
                await query.message.edit_text(
                    text,
                    reply_markup=markup,
                    parse_mode="Markdown",
                )
        except TelegramError:
            pass

    dm_ok = await send_private(
        context,
        user.id,
        "🎭 **You're in WHAT HAPPENS?**\n\n"
        "Your private role/decisions will appear here.\n\n"
        "If the bot cannot message you yet, use the "
        "**OPEN BOT / START** button in the group.",
        reply_markup=await private_markup(
            context,
            game_id=game_id,
        ),
        parse_mode="Markdown",
    )

    if not dm_ok:
        try:
            await context.bot.send_message(
                chat_id=game["chat_id"],
                text=(
                    f"👋 {display_name(user)} joined.\n\n"
                    "🔐 Open the bot privately and press START "
                    "so I can send private decisions."
                ),
                reply_markup=await private_markup(
                    context,
                    game_id=game_id,
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

    # If this was a late join, deliver the secret role immediately,
    # but DO NOT deliver the current round's decision. The player is
    # eligible starting from joined_round = current_round + 1.
    if game.get("status") == game_service.STATUS_PLAYING:
        current_round = int(game.get("current_round", 0) or 0)
        joined_round = int(player.get("joined_round", 0) or 0)

        if joined_round > current_round:
            try:
                role = await game_service.get_player_role(
                    game_id=game_id,
                    user_id=user.id,
                )

                if role:
                    await send_private(
                        context,
                        user.id,
                        "🤫 **YOUR SECRET ROLE**\n\n"
                        f"**{role.get('name', 'Unknown')}**\n\n"
                        f"{role.get('secret_description', '')}\n\n"
                        "⏳ The current round is already running. "
                        f"You will become eligible from **Round {joined_round}**.",
                        parse_mode="Markdown",
                    )
            except Exception:
                logger.exception(
                    "Could not send late-join role."
                )

    _ = player


async def publish_current_scene(
    context: Any,
    game_id: str,
) -> None:
    game, players = await game_service.get_game_with_players(
        game_id
    )

    if game.get("status") != game_service.STATUS_PLAYING:
        return

    scene_id = game.get("current_scene_id")

    if not scene_id:
        logger.error(
            "Playing game has no current scene: %s",
            game_id,
        )
        return

    engine = game_service.engine_for_game(game)
    scene = engine.get_scene(scene_id)
    round_number = int(
        game.get("current_round", 0) or 0
    )

    timer_seconds = engine.timer_seconds(
        scene
    )

    private_url = await bot_private_url(
        context,
        f"decision_{game_id}",
    )

    rows = []

    if private_url:
        rows.append([
            InlineKeyboardButton(
                "🔐 OPEN PRIVATE DECISION",
                url=private_url,
            )
        ])

    # Keep a JOIN button on every public scene so someone who arrives
    # after the lobby can join for the NEXT round. Existing players are
    # safely rejected as duplicates by GameService.
    rows.append([
        InlineKeyboardButton(
            "➕ JOIN / JOIN NEXT ROUND",
            callback_data=f"join:{game_id}",
        )
    ])

    try:
        await context.bot.send_message(
            chat_id=int(game["chat_id"]),
            text=(
                f"🎬 **{story_title(game)}**\n\n"
                f"**Round {round_number}**\n\n"
                f"{scene_public_text(scene)}\n\n"
                f"⏳ Decision window: "
                f"{format_seconds(timer_seconds)}\n\n"
                "🔐 Tap **OPEN PRIVATE DECISION** "
                "to choose privately."
            ),
            reply_markup=(
                InlineKeyboardMarkup(rows)
                if rows
                else None
            ),
            parse_mode="Markdown",
        )
    except TelegramError:
        logger.exception(
            "Could not publish scene for game %s.",
            game_id,
        )

    for player in players:
        if game_service.player_status(player) != game_service.PLAYER_ACTIVE:
            continue

        if not game_service.is_required_for_round(
            player,
            round_number,
        ):
            continue

        await send_current_player_state(
            context,
            game_id,
            int(player["user_id"]),
        )


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
        game.get("current_round", 0) or 0
    )

    if callback_round != current_round:
        await safe_answer(
            query,
            "⏰ That decision belongs to an old round.",
            show_alert=True,
        )
        return

    if game.get("status") != game_service.STATUS_PLAYING:
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

    try:
        await query.edit_message_reply_markup(
            reply_markup=None
        )
    except TelegramError:
        pass

    resolution = result.get(
        "resolution"
    )

    if resolution and resolution.get("resolved"):
        await publish_resolution(
            context,
            game_id,
            resolution,
        )


async def publish_resolution(
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
    resolution: dict[str, Any],
) -> None:
    if resolution.get("already_claimed"):
        return

    result = resolution.get("result")

    if not isinstance(result, dict):
        return

    game = await db.get_game(game_id)

    if not game:
        return

    ending = result.get("ending")

    if ending:
        try:
            await context.bot.send_message(
                chat_id=int(game["chat_id"]),
                text=(
                    "🎭 **WHAT HAPPENS? — THE END**\n\n"
                    f"**{ending.get('title', 'The End')}**\n\n"
                    f"{ending.get('text', 'The story has ended.')}"
                ),
                parse_mode="Markdown",
            )
        except TelegramError:
            logger.exception(
                "Could not publish ending."
            )

        return

    public_events: list[str] = []

    for event in result.get(
        "events",
        [],
    ):
        payload = None

        if isinstance(event, dict):
            payload = event.get("payload", event)
        else:
            payload = getattr(event, "payload", None)

        if isinstance(payload, dict):
            text = (
                payload.get("public_event")
                or payload.get("public_text")
                or payload.get("text")
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

    next_scene = result.get(
        "next_scene"
    )

    if not next_scene:
        return

    refreshed = await db.get_game(
        game_id
    )

    if (
        refreshed
        and refreshed.get("status")
        == game_service.STATUS_PLAYING
    ):
        await publish_current_scene(
            context,
            game_id,
        )


async def begin_game(
    query: Any,
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
) -> None:
    await safe_answer(
        query,
        "The game starts automatically after the 45-second lobby.",
        show_alert=True,
    )


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
        int(game["creator_id"])
        == user.id
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
        "🛑 The WHAT HAPPENS? game has been stopped. "
        "You can use /play again."
    )


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

        if action == "begin":
            if len(parts) != 2:
                await safe_answer(
                    query,
                    "Invalid button.",
                    show_alert=True,
                )
                return

            await begin_game(
                query,
                context,
                parts[1],
            )
            return

        if action == "choose":
            if len(parts) < 4:
                await safe_answer(
                    query,
                    "Invalid decision button.",
                    show_alert=True,
                )
                return

            try:
                round_number = int(parts[2])
            except ValueError:
                await safe_answer(
                    query,
                    "Invalid round.",
                    show_alert=True,
                )
                return

            await choose(
                query,
                context,
                parts[1],
                round_number,
                ":".join(parts[3:]),
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


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.error(
        "Unhandled Telegram error: %s",
        context.error,
        exc_info=context.error,
    )


async def recover_active_games() -> None:
    logger.info(
        "Recovering active WHAT HAPPENS? games from Supabase..."
    )

    # Kept for compatibility with the existing main.py.
    results = await game_service.recover_all_due_games()

    logger.info(
        "Recovery completed. Processed %s game state(s).",
        len(results),
    )


def create_application() -> Application:
    application = (
        Application.builder()
        .token(CONFIG.telegram_token)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("play", play)
    )

    application.add_handler(
        CommandHandler("stop", stop)
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_router
        )
    )

    application.add_error_handler(
        error_handler
    )

    return application


async def process_update(
    application: Application,
    update: Update,
) -> None:
    await application.process_update(
        update
    )


__all__ = [
    "db",
    "game_service",
    "create_application",
    "process_update",
    "recover_active_games",
    "publish_current_scene",
]
