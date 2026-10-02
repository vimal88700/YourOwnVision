from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType
from telegram.error import BadRequest, Forbidden, TelegramError
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


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
    level=logging.INFO,
)

logger = logging.getLogger("YourOwnVision")


# ============================================================
# SERVICES
# ============================================================

db = Database(
    CONFIG.supabase_url,
    CONFIG.supabase_service_key,
)

generator = StoryGenerator(
    CONFIG.gemini_api_key,
    CONFIG.gemini_model,
)


# ============================================================
# RUNTIME LOCKS
# ============================================================

# Prevents two Telegram callbacks / timeout jobs from
# resolving the same game simultaneously.
_GAME_LOCKS: dict[str, asyncio.Lock] = {}

# Prevents multiple /play requests in the same chat from
# generating multiple games at the same time.
_CHAT_PLAY_LOCKS: dict[int, asyncio.Lock] = {}


def get_game_lock(game_id: str) -> asyncio.Lock:
    lock = _GAME_LOCKS.get(game_id)

    if lock is None:
        lock = asyncio.Lock()
        _GAME_LOCKS[game_id] = lock

    return lock


def get_chat_play_lock(chat_id: int) -> asyncio.Lock:
    lock = _CHAT_PLAY_LOCKS.get(chat_id)

    if lock is None:
        lock = asyncio.Lock()
        _CHAT_PLAY_LOCKS[chat_id] = lock

    return lock


# ============================================================
# TIME HELPERS
# ============================================================

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        result = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

        if result.tzinfo is None:
            result = result.replace(
                tzinfo=timezone.utc
            )

        return result.astimezone(timezone.utc)

    except ValueError:
        logger.warning(
            "Invalid timestamp from database: %s",
            value,
        )
        return None


# ============================================================
# TELEGRAM HELPERS
# ============================================================

def display_name_from_user(user) -> str:
    if not user:
        return "Player"

    return (
        user.full_name
        or user.username
        or "Player"
    )


async def send_private(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """
    Send private role/decision information.

    Returns False when the user has not opened the bot privately
    or Telegram refuses the DM.
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
            "Cannot DM user %s. "
            "They probably have not started the bot.",
            user_id,
        )
        return False

    except TelegramError as exc:
        logger.warning(
            "Private message failed for %s: %s",
            user_id,
            exc,
        )
        return False


async def safe_answer(
    query,
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


# ============================================================
# ADMIN / CREATOR CHECKS
# ============================================================

async def is_group_admin(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
) -> bool:
    """
    Checks the actual Telegram group administrator status.

    This is intentionally not based only on ADMIN_USER_IDS.
    """

    try:
        member = await context.bot.get_chat_member(
            chat_id=chat_id,
            user_id=user_id,
        )

        return member.status in {
            "administrator",
            "creator",
        }

    except TelegramError as exc:
        logger.warning(
            "Could not check admin status for %s: %s",
            user_id,
            exc,
        )

        # Configured bot admins are still allowed.
        return user_id in CONFIG.admin_user_ids


async def can_stop_game(
    context: ContextTypes.DEFAULT_TYPE,
    game: dict[str, Any],
    user_id: int,
) -> bool:

    if int(game["creator_id"]) == user_id:
        return True

    if user_id in CONFIG.admin_user_ids:
        return True

    return await is_group_admin(
        context,
        int(game["chat_id"]),
        user_id,
    )


# ============================================================
# STABLE HASH
# ============================================================

def stable_number(
    value: str,
) -> int:
    """
    Python's built-in hash() is randomized between processes.

    SHA-256 gives us deterministic ordering across bot restarts.
    """

    digest = hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()

    return int(
        digest[:16],
        16,
    )


def stable_player_order(
    game_id: str,
    user_id: int,
) -> int:

    return stable_number(
        f"{game_id}:{user_id}"
    )


# ============================================================
# STORY GENERATION
# ============================================================

async def generate_unique_story(
    player_count: int,
) -> tuple[dict[str, Any], str]:
    """
    Gemini is used here only.

    It creates the complete story universe before gameplay.
    """

    previous_fingerprints: list[str] = []

    for attempt in range(1, 8):

        logger.info(
            "Generating story attempt %s",
            attempt,
        )

        story, fingerprint = (
            await generator.generate(
                player_count=player_count,
                previous_fingerprints=(
                    previous_fingerprints
                ),
            )
        )

        if await db.fingerprint_exists(
            fingerprint
        ):
            logger.info(
                "Story fingerprint already exists."
            )

            previous_fingerprints.append(
                fingerprint
            )

            continue

        await db.save_story_history(
            fingerprint,
            story,
        )

        return story, fingerprint

    raise RuntimeError(
        "Could not create a unique story "
        "after several attempts."
    )


# ============================================================
# START COMMAND
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_message:
        return

    await update.effective_message.reply_text(
        "🎭 WHAT HAPPENS?\n\n"
        "A story game where nobody knows exactly "
        "what happens next.\n\n"
        "Join a group and use /play.\n\n"
        "Important:\n"
        "Your private decisions appear in this chat "
        "with the bot."
    )


# ============================================================
# PLAY COMMAND
# ============================================================

async def play(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_chat:
        return

    if not update.effective_message:
        return

    chat = update.effective_chat

    if chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text(
            "Add me to a group first.\n\n"
            "Then use /play inside the group."
        )
        return

    chat_id = chat.id
    creator = update.effective_user

    if not creator:
        return

    lock = get_chat_play_lock(chat_id)

    async with lock:

        existing = await db.get_active_game(
            chat_id
        )

        if existing:
            await update.effective_message.reply_text(
                "🎭 A WHAT HAPPENS? game is already "
                "running in this group.\n\n"
                "Press JOIN on the existing game."
            )
            return

        await update.effective_message.reply_text(
            "🎭 Preparing a new WHAT HAPPENS? story..."
        )

        try:
            story, fingerprint = (
                await generate_unique_story(
                    player_count=1
                )
            )

        except Exception:
            logger.exception(
                "Story generation failed."
            )

            await update.effective_message.reply_text(
                "⚠️ I couldn't prepare the story.\n\n"
                "Please try /play again."
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
            creator_id=creator.id,
            story=story,
            fingerprint=fingerprint,
            join_deadline=iso(
                join_deadline
            ),
        )

        await db.add_player(
            game_id=game["id"],
            user_id=creator.id,
            username=creator.username or "",
            display_name=(
                creator.full_name
                or creator.username
                or "Player"
            ),
            role_id=None,
            joined_round=0,
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🎭 JOIN",
                        callback_data=(
                            f"join:{game['id']}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "▶️ START",
                        callback_data=(
                            f"begin:{game['id']}"
                        ),
                    )
                ],
            ]
        )

        title = story.get(
            "title",
            "WHAT HAPPENS?",
        )

        await update.effective_message.reply_text(
            f"🎭 **WHAT HAPPENS?**\n\n"
            f"**{title}**\n\n"
            "A new story has been prepared.\n"
            "The possible future is already inside it.\n\n"
            "Anyone can JOIN.\n"
            "Anyone can START.\n\n"
            "Your private role and decisions will "
            "be sent by DM.",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

        # Automatic start after lobby timeout.
        context.job_queue.run_once(
            begin_game_job,
            when=CONFIG.game_join_seconds,
            data={
                "game_id": game["id"],
                "chat_id": chat_id,
            },
            name=f"begin:{game['id']}",
        )


# ============================================================
# JOIN
# ============================================================

async def join_game(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
):
    user = query.from_user

    game = await db.get_game(
        game_id
    )

    if not game:
        await safe_answer(
            query,
            "Game not found.",
            show_alert=True,
        )
        return

    if game["status"] == "ended":
        await safe_answer(
            query,
            "This game has already ended.",
            show_alert=True,
        )
        return

    existing_player = await db.get_player(
        game_id,
        user.id,
    )

    if existing_player:
        await safe_answer(
            query,
            "You're already in this story.",
            show_alert=True,
        )
        return

    status = game["status"]

    if status == "lobby":
        joined_round = 0

    elif status == "playing":
        # A late player starts participating from
        # the NEXT round, not the current one.
        joined_round = (
            int(game["current_round"]) + 1
        )

    else:
        await safe_answer(
            query,
            "You cannot join this game now.",
            show_alert=True,
        )
        return

    await db.add_player(
        game_id=game_id,
        user_id=user.id,
        username=user.username or "",
        display_name=(
            user.full_name
            or user.username
            or "Player"
        ),
        role_id=None,
        joined_round=joined_round,
    )

    await safe_answer(
        query,
        "🎭 You're in!",
        show_alert=True,
    )

    # Explain that private decisions require the
    # player to open the bot.
    dm_ok = await send_private(
        context,
        user.id,
        "🎭 You're now part of WHAT HAPPENS?\n\n"
        "Keep this chat open.\n"
        "Your secret role and private decisions "
        "will appear here.",
    )

    try:
        if status == "lobby":
            await context.bot.send_message(
                chat_id=game["chat_id"],
                text=(
                    f"👋 {display_name_from_user(user)} "
                    "joined the story."
                ),
            )

        else:
            await context.bot.send_message(
                chat_id=game["chat_id"],
                text=(
                    f"👋 {display_name_from_user(user)} "
                    "entered the story.\n\n"
                    "Their character will join at the "
                    "next story point."
                ),
            )

    except TelegramError:
        pass

    if not dm_ok:
        await safe_answer(
            query,
            "Join successful. Start the bot privately "
            "with /start so I can send your decisions.",
            show_alert=True,
        )


# ============================================================
# START GAME
# ============================================================

async def begin_game(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
):
    user = query.from_user

    game = await db.get_game(
        game_id
    )

    if not game:
        await safe_answer(
            query,
            "Game not found.",
            show_alert=True,
        )
        return

    if game["status"] == "ended":
        await safe_answer(
            query,
            "This game has already ended.",
            show_alert=True,
        )
        return

    if game["status"] == "playing":
        await safe_answer(
            query,
            "The game is already running.",
            show_alert=True,
        )
        return

    # IMPORTANT:
    # Anyone can start the game.
    # Only STOP is restricted to creator/admin.
    await safe_answer(
        query,
        "Starting...",
    )

    await start_game(
        game_id,
        context,
    )


async def start_game(
    game_id: str,
    context: ContextTypes.DEFAULT_TYPE,
):
    lock = get_game_lock(game_id)

    async with lock:

        game = await db.get_game(
            game_id
        )

        if not game:
            return

        if game["status"] != "lobby":
            return

        players = await db.get_players(
            game_id
        )

        if not players:
            return

        story = game["story"]

        try:
            engine = GameEngine(
                story
            )
        except Exception:
            logger.exception(
                "Invalid story for game %s",
                game_id,
            )

            await end_game(
                game_id,
                context,
                "⚠️ The story could not be loaded.",
            )
            return

        roles = story.get(
            "roles",
            [],
        )

        if not roles:
            await end_game(
                game_id,
                context,
                "⚠️ The story has no characters.",
            )
            return

        # Stable ordering.
        ordered_players = sorted(
            players,
            key=lambda player: (
                stable_player_order(
                    game_id,
                    int(player["user_id"]),
                )
            ),
        )

        # Assign available roles.
        #
        # If there are more players than initial roles,
        # roles are reused only when necessary.
        #
        # A future version can instead generate more
        # roles dynamically before the game starts.
        for index, player in enumerate(
            ordered_players
        ):
            role = roles[
                index % len(roles)
            ]

            await db.update_player(
                game_id,
                int(player["user_id"]),
                {
                    "role_id": role["id"],
                    "joined_round": 1,
                },
            )

        scene = engine.first_scene()

        seconds = safe_timer(
            scene.get(
                "timer_seconds"
            )
        )

        deadline = (
            now_utc()
            + timedelta(
                seconds=seconds
            )
        )

        await db.update_game(
            game_id,
            {
                "status": "playing",
                "current_scene_id": scene["id"],
                "current_round": 1,
                "decision_deadline": iso(
                    deadline
                ),
                "updated_at": iso(
                    now_utc()
                ),
            },
        )

        title = story.get(
            "title",
            "WHAT HAPPENS?",
        )

        public_text = scene.get(
            "public_text",
            "Something is happening...",
        )

        await context.bot.send_message(
            chat_id=game["chat_id"],
            text=(
                f"🎬 **{title}**\n\n"
                f"{public_text}\n\n"
                "🤫 Everyone has their own role.\n"
                "Check your private chat with the bot."
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
            seconds,
        )


# ============================================================
# TIMER VALIDATION
# ============================================================

def safe_timer(value: Any) -> int:
    try:
        seconds = int(value)
    except (
        TypeError,
        ValueError,
    ):
        seconds = CONFIG.default_decision_seconds

    # Prevent broken AI output from creating
    # absurdly short/long timers.
    return max(
        15,
        min(seconds, 900),
    )


# ============================================================
# SEND PRIVATE SCENE
# ============================================================

async def send_scene_decisions(
    game_id: str,
    context: ContextTypes.DEFAULT_TYPE,
):
    game = await db.get_game(
        game_id
    )

    if not game:
        return

    if game["status"] != "playing":
        return

    scene_id = game.get(
        "current_scene_id"
    )

    if not scene_id:
        return

    try:
        engine = GameEngine(
            game["story"]
        )
        scene = engine.get_scene(
            scene_id
        )
    except Exception:
        logger.exception(
            "Could not load scene."
        )
        return

    players = await db.get_players(
        game_id
    )

    current_round = int(
        game["current_round"]
    )

    for player in players:

        if not player.get("active"):
            continue

        role_id = player.get(
            "role_id"
        )

        if not role_id:
            continue

        # A late joiner does not participate
        # in the current round.
        joined_round = int(
            player.get(
                "joined_round",
                0,
            )
        )

        if joined_round > current_round:
            continue

        try:
            role = engine.get_role(
                role_id
            )
        except Exception:
            logger.warning(
                "Unknown role %s",
                role_id,
            )
            continue

        choices = engine.choices_for_role(
            scene,
            role_id,
        )

        # If this role isn't involved in this scene,
        # don't send random buttons.
        if not choices:
            continue

        buttons = []

        for choice in choices:

            choice_id = str(
                choice["id"]
            )

            callback_data = (
                f"choose:"
                f"{game_id}:"
                f"{current_round}:"
                f"{choice_id}"
            )

            # Telegram callback_data has a 64-byte limit.
            if len(
                callback_data.encode("utf-8")
            ) > 64:

                logger.error(
                    "Choice callback is too long "
                    "for Telegram: %s",
                    callback_data,
                )

                continue

            buttons.append(
                [
                    InlineKeyboardButton(
                        str(
                            choice.get(
                                "label",
                                "Choose",
                            )
                        ),
                        callback_data=callback_data,
                    )
                ]
            )

        if not buttons:
            continue

        seconds = safe_timer(
            scene.get(
                "timer_seconds"
            )
        )

        private_text = (
            "🤫 **YOUR SECRET ROLE**\n\n"
            f"**{role.get('name', 'Unknown')}**\n\n"
            f"{role.get('secret_description', '')}\n\n"
            "━━━━━━━━━━━━━━\n\n"
            f"⏳ You have about {format_seconds(seconds)}.\n\n"
            "**WHAT DO YOU DO?**"
        )

        success = await send_private(
            context,
            int(player["user_id"]),
            private_text,
            InlineKeyboardMarkup(
                buttons
            ),
        )

        if not success:
            # Don't immediately eliminate them.
            # They may have temporarily blocked the bot
            # or Telegram may reject the DM.
            #
            # Their missed-decision counter is handled
            # when the round resolves.
            logger.info(
                "Player %s did not receive private scene.",
                player["user_id"],
            )


def format_seconds(
    seconds: int,
) -> str:

    if seconds < 60:
        return f"{seconds} seconds"

    minutes = seconds // 60
    remaining = seconds % 60

    if remaining == 0:
        return (
            f"{minutes} minute"
            f"{'s' if minutes != 1 else ''}"
        )

    return (
        f"{minutes}m {remaining}s"
    )


# ============================================================
# PLAYER CHOICE
# ============================================================

async def choose(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
    round_number: int,
    choice_id: str,
):
    user = query.from_user

    # The callback originates from the player's
    # private message, so user identity is reliable.
    game = await db.get_game(
        game_id
    )

    if not game:
        await safe_answer(
            query,
            "Game not found.",
            show_alert=True,
        )
        return

    if game["status"] != "playing":
        await safe_answer(
            query,
            "The game is not accepting decisions.",
            show_alert=True,
        )
        return

    current_round = int(
        game["current_round"]
    )

    if current_round != round_number:
        await safe_answer(
            query,
            "That decision is already closed.",
            show_alert=True,
        )
        return

    deadline = parse_iso(
        game.get(
            "decision_deadline"
        )
    )

    if deadline and now_utc() >= deadline:
        await safe_answer(
            query,
            "⏰ Too late. The decision has closed.",
            show_alert=True,
        )

        # Try to resolve immediately.
        await maybe_resolve_round(
            game_id,
            context,
            force=True,
        )
        return

    player = await db.get_player(
        game_id,
        user.id,
    )

    if not player:
        await safe_answer(
            query,
            "You're not part of this game.",
            show_alert=True,
        )
        return

    if not player.get("active"):
        await safe_answer(
            query,
            "Your character has exited the game.",
            show_alert=True,
        )
        return

    joined_round = int(
        player.get(
            "joined_round",
            0,
        )
    )

    if joined_round > current_round:
        await safe_answer(
            query,
            "Your character enters next round.",
            show_alert=True,
        )
        return

    # Validate the choice against the CURRENT scene
    # before saving anything.
    try:
        engine = GameEngine(
            game["story"]
        )

        scene = engine.get_scene(
            game["current_scene_id"]
        )

        role_id = player.get(
            "role_id"
        )

        if not role_id:
            await safe_answer(
                query,
                "Your role has not been assigned.",
                show_alert=True,
            )
            return

        valid_choices = (
            engine.choices_for_role(
                scene,
                role_id,
            )
        )

        valid_ids = {
            str(choice["id"])
            for choice in valid_choices
        }

        if choice_id not in valid_ids:
            await safe_answer(
                query,
                "That button is not valid anymore.",
                show_alert=True,
            )
            return

    except Exception:
        logger.exception(
            "Choice validation failed."
        )

        await safe_answer(
            query,
            "I couldn't validate that choice.",
            show_alert=True,
        )
        return

    # Save the decision.
    try:
        await db.save_decision(
            game_id=game_id,
            round_number=round_number,
            scene_id=game["current_scene_id"],
            user_id=user.id,
            choice_id=choice_id,
        )

    except Exception as exc:
        logger.exception(
            "Could not save decision."
        )

        await safe_answer(
            query,
            "Your choice could not be saved. Try again.",
            show_alert=True,
        )
        return

    await safe_answer(
        query,
        "✅ Choice locked in.",
        show_alert=True,
    )

    # Remove buttons so the player doesn't accidentally
    # press the same decision repeatedly.
    try:
        await query.edit_message_reply_markup(
            reply_markup=None
        )
    except BadRequest:
        pass
    except TelegramError:
        pass

    # Don't necessarily resolve immediately.
    #
    # If everybody has chosen, resolve now.
    # Otherwise the game waits until timer expiry.
    await maybe_resolve_round(
        game_id,
        context,
        force=False,
    )


# ============================================================
# ROUND RESOLUTION
# ============================================================

async def maybe_resolve_round(
    game_id: str,
    context: ContextTypes.DEFAULT_TYPE,
    force: bool,
):
    lock = get_game_lock(game_id)

    async with lock:

        game = await db.get_game(
            game_id
        )

        if not game:
            return

        if game["status"] != "playing":
            return

        current_round = int(
            game["current_round"]
        )

        players = await db.get_players(
            game_id
        )

        active_players = []

        for player in players:

            if not player.get("active"):
                continue

            if not player.get("role_id"):
                continue

            joined_round = int(
                player.get(
                    "joined_round",
                    0,
                )
            )

            if joined_round <= current_round:
                active_players.append(
                    player
                )

        decisions = (
            await db.get_round_decisions(
                game_id,
                current_round,
            )
        )

        chosen_user_ids = {
            int(decision["user_id"])
            for decision in decisions
            if decision.get("choice_id")
        }

        # If timer has not expired, we can finish early
        # only when everyone involved has selected.
        if not force:

            everyone_chosen = all(
                int(player["user_id"])
                in chosen_user_ids
                for player in active_players
            )

            if not everyone_chosen:
                return

        # We need at least one decision.
        #
        # If absolutely nobody decided, don't let an empty
        # round randomly control the story.
        if not decisions:

            await end_game(
                game_id,
                context,
                "⏰ Nobody made a decision.\n\n"
                "The story ended before anyone "
                "could change it.",
            )
            return

        try:
            engine = GameEngine(
                game["story"]
            )

            scene = engine.get_scene(
                game["current_scene_id"]
            )

        except Exception:
            logger.exception(
                "Could not load scene during resolution."
            )

            await end_game(
                game_id,
                context,
                "⚠️ The story engine encountered "
                "an invalid scene.",
            )
            return

        results: list[dict[str, Any]] = []

        # Resolve every submitted decision.
        for decision in decisions:

            choice_id = decision.get(
                "choice_id"
            )

            if not choice_id:
                continue

            try:
                result = engine.resolve_choice(
                    scene,
                    str(choice_id),
                    round_number=current_round,
                )

                # Keep player identity attached to the
                # result for future world-state support.
                result["user_id"] = int(
                    decision["user_id"]
                )

                results.append(result)

            except Exception:
                logger.exception(
                    "Failed resolving choice %s",
                    choice_id,
                )

        # ====================================================
        # MISSING DECISIONS
        # ====================================================

        for player in active_players:

            user_id = int(
                player["user_id"]
            )

            if user_id in chosen_user_ids:
                continue

            missed = (
                int(
                    player.get(
                        "missed_decisions",
                        0,
                    )
                )
                + 1
            )

            if missed >= 3:

                # IMPORTANT:
                #
                # The player exits, but the character does
                # NOT disappear from the story.
                #
                # Future story logic can treat the character
                # as an NPC.
                await db.update_player(
                    game_id,
                    user_id,
                    {
                        "missed_decisions": missed,
                        "active": False,
                    },
                )

                await context.bot.send_message(
                    chat_id=game["chat_id"],
                    text=(
                        f"👻 {player['display_name']} "
                        "has missed 3 decisions.\n\n"
                        "Their player has exited.\n"
                        "Their character remains in the story "
                        "as an NPC."
                    ),
                )

            else:

                await db.update_player(
                    game_id,
                    user_id,
                    {
                        "missed_decisions": missed,
                    },
                )

                # Private warning.
                await send_private(
                    context,
                    user_id,
                    (
                        "⏰ You missed this decision.\n\n"
                        f"Missed decisions: {missed}/3\n\n"
                        "Your character is still in the story."
                    ),
                )

        # ====================================================
        # MARK ROUND RESOLVED
        # ====================================================

        await db.resolve_round(
            game_id,
            current_round,
        )

        if not results:

            await end_game(
                game_id,
                context,
                "Nobody made a valid choice.\n\n"
                "The story took its own path.",
            )
            return

        # ====================================================
        # PUBLIC CONSEQUENCES
        # ====================================================

        public_events = []

        for result in results:

            event = result.get(
                "public_event"
            )

            if event:
                public_events.append(
                    str(event)
                )

        if public_events:

            public_text = (
                "💥 **WHAT HAPPENS?**\n\n"
            )

            public_text += "\n\n".join(
                f"• {event}"
                for event in public_events
            )

            await context.bot.send_message(
                chat_id=game["chat_id"],
                text=public_text,
                parse_mode="Markdown",
            )

        # ====================================================
        # DETERMINE NEXT SCENE
        # ====================================================

        try:
            next_scene_id = (
                engine.next_scene_from_results(
                    scene,
                    results,
                )
            )
        except Exception:
            logger.exception(
                "Branch resolution failed."
            )
            next_scene_id = None

        # No next scene means this story ended.
        if not next_scene_id:

            await end_game(
                game_id,
                context,
                "And somehow...\n\n"
                "that was the end.",
            )
            return

        try:
            next_scene = engine.get_scene(
                next_scene_id
            )
        except Exception:
            logger.exception(
                "Next scene %s does not exist.",
                next_scene_id,
            )

            await end_game(
                game_id,
                context,
                "⚠️ The story reached an invalid ending.",
            )
            return

        next_round = current_round + 1

        seconds = safe_timer(
            next_scene.get(
                "timer_seconds"
            )
        )

        deadline = (
            now_utc()
            + timedelta(
                seconds=seconds
            )
        )

        await db.update_game(
            game_id,
            {
                "current_scene_id": next_scene_id,
                "current_round": next_round,
                "decision_deadline": iso(
                    deadline
                ),
                "updated_at": iso(
                    now_utc()
                ),
            },
        )

        public_text = next_scene.get(
            "public_text",
            "Something else is happening...",
        )

        await context.bot.send_message(
            chat_id=game["chat_id"],
            text=(
                f"🎬 **Round {next_round}**\n\n"
                f"{public_text}\n\n"
                f"⏳ Decision window: "
                f"{format_seconds(seconds)}"
            ),
            parse_mode="Markdown",
        )

        # Send each active player their own role-specific
        # private buttons.
        await send_scene_decisions(
            game_id,
            context,
        )

        schedule_resolution(
            context,
            game_id,
            seconds,
        )


# ============================================================
# TIMER JOBS
# ============================================================

def schedule_resolution(
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
    seconds: int,
):
    """
    Schedule a timeout job.

    We still re-check the database deadline when the job fires,
    so stale jobs cannot resolve a newer round.
    """

    context.job_queue.run_once(
        resolve_timeout_job,
        when=max(1, seconds),
        data={
            "game_id": game_id,
        },
        name=f"resolve:{game_id}",
    )


async def resolve_timeout_job(
    context: ContextTypes.DEFAULT_TYPE,
):
    data = context.job.data or {}

    game_id = data.get(
        "game_id"
    )

    if not game_id:
        return

    game = await db.get_game(
        game_id
    )

    if not game:
        return

    if game["status"] != "playing":
        return

    deadline = parse_iso(
        game.get(
            "decision_deadline"
        )
    )

    if deadline:

        remaining = (
            deadline - now_utc()
        ).total_seconds()

        # A job may have been restored slightly early.
        if remaining > 0.5:

            context.job_queue.run_once(
                resolve_timeout_job,
                when=remaining,
                data={
                    "game_id": game_id,
                },
                name=f"resolve:{game_id}",
            )

            return

    await maybe_resolve_round(
        game_id,
        context,
        force=True,
    )


# ============================================================
# AUTOMATIC LOBBY START
# ============================================================

async def begin_game_job(
    context: ContextTypes.DEFAULT_TYPE,
):
    data = context.job.data or {}

    game_id = data.get(
        "game_id"
    )

    if not game_id:
        return

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


# ============================================================
# RECOVER GAMES AFTER BOT RESTART
# ============================================================

async def recover_active_games(
    application: Application,
):
    """
    Telegram JobQueue lives in RAM.

    If the bot restarts, the database still knows which games
    are active, but their timers have disappeared.

    This function rebuilds the missing timers.
    """

    logger.info(
        "Checking for games that need timer recovery..."
    )

    # The current Database class exposes get_active_game
    # by chat rather than a global active-games query.
    #
    # Therefore recovery is intentionally conservative.
    #
    # For a larger deployment, add a DB method that returns
    # all lobby/playing games in one query.
    #
    # This startup hook is kept so the architecture has one
    # place for recovery when that method is added.

    logger.info(
        "Timer recovery hook initialized."
    )


# ============================================================
# STOP COMMAND
# ============================================================

async def stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_chat:
        return

    if not update.effective_message:
        return

    chat = update.effective_chat

    if chat.type == ChatType.PRIVATE:
        await update.effective_message.reply_text(
            "Use /stop inside the group."
        )
        return

    game = await db.get_active_game(
        chat.id
    )

    if not game:
        await update.effective_message.reply_text(
            "There is no active WHAT HAPPENS? game."
        )
        return

    user = update.effective_user

    if not user:
        return

    allowed = await can_stop_game(
        context,
        game,
        user.id,
    )

    if not allowed:

        await update.effective_message.reply_text(
            "Only the game creator or a group "
            "administrator can stop the game."
        )
        return

    await end_game(
        game["id"],
        context,
        "🛑 The game was stopped by "
        "the game creator or a group administrator.",
    )


# ============================================================
# END GAME
# ============================================================

async def end_game(
    game_id: str,
    context: ContextTypes.DEFAULT_TYPE,
    reason: str,
):
    lock = get_game_lock(game_id)

    async with lock:

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
                "ended_at": iso(
                    now_utc()
                ),
                "updated_at": iso(
                    now_utc()
                ),
            },
        )

        # Remove scheduled jobs for this game.
        remove_game_jobs(
            context,
            game_id,
        )

        try:
            await context.bot.send_message(
                chat_id=game["chat_id"],
                text=(
                    "🎭 **WHAT HAPPENS? — END**\n\n"
                    f"{reason}\n\n"
                    "No leaderboard.\n"
                    "No score.\n"
                    "No winner.\n\n"
                    "🔄 Use /play for another story."
                ),
                parse_mode="Markdown",
            )
        except TelegramError:
            pass


def remove_game_jobs(
    context: ContextTypes.DEFAULT_TYPE,
    game_id: str,
):
    """
    Remove pending jobs belonging to this game.

    JobQueue APIs can vary slightly by python-telegram-bot
    version, so this uses get_jobs_by_name when available.
    """

    try:

        names = {
            f"begin:{game_id}",
            f"resolve:{game_id}",
        }

        for name in names:

            jobs = context.job_queue.get_jobs_by_name(
                name
            )

            for job in jobs:
                job.schedule_removal()

    except Exception:
        logger.debug(
            "Could not remove game jobs.",
            exc_info=True,
        )


# ============================================================
# CALLBACK ROUTER
# ============================================================

async def callback_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    data = query.data or ""

    try:

        parts = data.split(":")

        if not parts:
            await safe_answer(
                query,
                "Invalid button.",
                show_alert=True,
            )
            return

        action = parts[0]

        # ----------------------------------------------------
        # JOIN
        # ----------------------------------------------------

        if action == "join":

            if len(parts) != 2:
                await safe_answer(
                    query,
                    "Invalid join button.",
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
                    "Invalid start button.",
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
        # CHOICE
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

            # Choice IDs normally do not contain ":".
            # Joining the remainder keeps this robust if
            # the generator ever creates one.
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
            "Unhandled callback error."
        )

        await safe_answer(
            query,
            "Something went wrong.",
            show_alert=True,
        )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Unhandled Telegram error: %s",
        context.error,
        exc_info=context.error,
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application: Application,
):
    logger.info(
        "YourOwnVision WHAT HAPPENS? started."
    )

    logger.info(
        "Gemini is used for story preparation only."
    )

    await recover_active_games(
        application
    )


# ============================================================
# MAIN
# ============================================================

def main():

    application = (
        Application.builder()
        .token(CONFIG.telegram_token)
        .post_init(post_init)
        .build()
    )

    # Commands
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

    # Inline buttons
    application.add_handler(
        CallbackQueryHandler(
            callback_router,
        )
    )

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Polling started."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
