from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import CONFIG
from world_service import WorldService

logger = logging.getLogger("YourOwnVision.world_bot")


def main_app_url(bot_username: str, start_param: str = "") -> str:
    """Main Mini App direct link. Works from a group when the Main Mini App is configured in BotFather."""
    base = f"https://t.me/{bot_username}"
    if start_param:
        return f"{base}?startapp={start_param}&mode=fullscreen"
    return f"{base}?startapp&mode=fullscreen"


def game_markup(bot_username: str, game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            "ENTER WORLD  •  PLAY NOW",
            url=main_app_url(bot_username, f"w_{game_id}"),
        )]]
    )


def world_card_text(game: dict[str, Any], roster: list[dict[str, Any]]) -> str:
    status = str(game.get("status") or "waiting")
    if status == "waiting":
        status_line = "🟠  <b>LOBBY OPEN</b>  •  45s entry window"
    elif status == "paused":
        status_line = "⏸  <b>WORLD PAUSED</b>  •  progress preserved"
    elif status == "active":
        status_line = "🟢  <b>WORLD LIVE</b>  •  choices are permanent"
    else:
        status_line = "⚫  <b>WORLD ARCHIVED</b>  •  history preserved"

    names = "\n".join(
        f"  {i}. {escape(str(p.get('display_name') or 'Player'))}"
        for i, p in enumerate(roster[:20], 1)
    ) or "  No players have joined yet."

    return (
        "🎭 <b>WHAT HAPPENS?</b>\n\n"
        f"<b>{escape(str(game.get('title') or 'WHAT HAPPENS?'))}</b>\n"
        f"{status_line}\n\n"
        "A persistent multiplayer world. Each player gets a personal route; "
        "the world keeps moving and periodically brings paths together.\n\n"
        f"👥 <b>PLAYERS  •  {len(roster)}</b>\n"
        f"{names}\n\n"
        "☠️ Death changes your run, not the shared world.\n"
        "🌐 Different groups always have completely separate worlds."
    )


async def publish_world_card(
    bot,
    game: dict[str, Any],
    roster: list[dict[str, Any]],
    db,
) -> None:
    chat_id = game.get("chat_id")
    if not chat_id:
        return

    me = await bot.get_me()
    markup = game_markup(me.username or "", str(game["id"]))
    message_id = game.get("telegram_message_id")
    text = world_card_text(game, roster)

    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=text,
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            return
        except Exception as exc:
            logger.warning("Could not edit world card %s: %s", game.get("id"), exc)

    sent = await bot.send_message(
        chat_id=int(chat_id),
        text=text,
        parse_mode="HTML",
        reply_markup=markup,
        disable_web_page_preview=True,
    )
    try:
        await db.request(
            "PATCH",
            "world_games",
            params={"id": f"eq.{game['id']}"},
            json={
                "telegram_message_id": int(sent.message_id),
                "telegram_message_chat_id": int(chat_id),
            },
            prefer="return=minimal",
        )
    except Exception:
        logger.exception("Could not save Telegram world-card message id")


def _parse_until(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def maintenance_text(row: dict[str, Any] | None) -> str:
    until = _parse_until(row.get("maintenance_until") if row else None)
    if not until:
        return ""
    left = max(0, int((until - datetime.now(timezone.utc)).total_seconds()))
    minutes = (left + 59) // 60
    return (
        "🛠 <b>MAINTENANCE MODE</b>\n\n"
        f"Estimated remaining time: <b>{minutes} min</b>.\n"
        "New world actions are temporarily paused.\n"
        "Existing worlds, players and progress are safe."
    )


async def is_global_maintenance(svc: WorldService) -> tuple[bool, str]:
    row = await svc.maintenance()
    until = _parse_until(row.get("maintenance_until") if row else None)
    if not until or datetime.now(timezone.utc) >= until:
        return False, ""
    return True, maintenance_text(row)


async def actor_can_moderate(
    game: dict[str, Any],
    user_id: int,
    bot,
) -> bool:
    if CONFIG.bot_creator_id and user_id == CONFIG.bot_creator_id:
        return True

    settings = game.get("settings") or {}
    try:
        operators = {int(x) for x in settings.get("operator_ids", [])}
    except Exception:
        operators = set()

    if user_id in operators or int(game.get("creator_id") or 0) == user_id:
        return True

    chat_id = game.get("chat_id")
    if chat_id:
        try:
            member = await bot.get_chat_member(int(chat_id), user_id)
            return member.status in {"creator", "administrator"}
        except Exception:
            return False
    return False


async def world_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return

    svc: WorldService = context.application.bot_data["world_service"]
    maintenance, text = await is_global_maintenance(svc)
    if maintenance:
        await message.reply_html(text)
        raise ApplicationHandlerStop

    bot = context.application.bot
    me = await bot.get_me()

    if chat.type == ChatType.PRIVATE:
        await message.reply_html(
            "🎭 <b>WHAT HAPPENS?</b>\n\n"
            "Open the game from its group. The group creates exactly one persistent world; "
            "repeating /play only joins/checks that same world.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "OPEN MAIN GAME",
                    url=main_app_url(me.username or ""),
                )
            ]]),
        )
        raise ApplicationHandlerStop

    # One persistent world per Telegram group. Never create another world for /play.
    game = await svc.ensure_world_for_group(chat.id, user.id)

    try:
        await svc.join(
            str(game["id"]),
            user.id,
            user.username or "",
            user.first_name or "Player",
        )
    except ValueError as exc:
        # A paused/archived world should be visible instead of producing a silent failure.
        await message.reply_text(str(exc))
        raise ApplicationHandlerStop

    # A player can send /play repeatedly. It is idempotent.
    game = await svc.ensure_active(await svc.get_world(str(game["id"])))
    roster = await svc.players(str(game["id"]))
    await publish_world_card(bot, game, roster, svc.db)

    logger.info(
        "play handled chat=%s user=%s game=%s status=%s players=%s",
        chat.id,
        user.id,
        game.get("id"),
        game.get("status"),
        len(roster),
    )
    raise ApplicationHandlerStop


async def world_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    payload = context.args[0].strip() if context.args else ""
    if not payload:
        return

    maintenance, text = await is_global_maintenance(
        context.application.bot_data["world_service"]
    )
    if maintenance:
        await message.reply_html(text)
        raise ApplicationHandlerStop

    me = await context.bot.get_me()
    if payload.startswith(("w_", "invite_")):
        await message.reply_html(
            "🎭 <b>WORLD READY</b>\n\n"
            "Tap once to enter the Mini App. Your progress is saved in the world.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "ENTER WORLD",
                    url=main_app_url(me.username or "", payload),
                )
            ]]),
        )
        raise ApplicationHandlerStop


async def pause_world(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or chat.type == ChatType.PRIVATE:
        return

    svc: WorldService = context.application.bot_data["world_service"]
    game = await svc.get_group_world(chat.id)
    if not game:
        await message.reply_text("No WHAT HAPPENS? world exists in this group yet. Use /play.")
        return

    if not await actor_can_moderate(game, user.id, context.bot):
        await message.reply_text(
            "Only the group owner/admin, assigned operator, world creator or bot creator can pause."
        )
        return

    updated = await svc.pause(str(game["id"]), user.id)
    await publish_world_card(context.bot, updated, await svc.players(str(game["id"])), svc.db)


async def resume_world(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or chat.type == ChatType.PRIVATE:
        return

    svc: WorldService = context.application.bot_data["world_service"]
    game = await svc.get_group_world(chat.id)
    if not game:
        await message.reply_text("No world is available in this group.")
        return

    if not await actor_can_moderate(game, user.id, context.bot):
        await message.reply_text("You do not have permission to resume this world.")
        return

    updated = await svc.resume(str(game["id"]), user.id)
    await publish_world_card(context.bot, updated, await svc.players(str(game["id"])), svc.db)


async def terminate_world(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or chat.type == ChatType.PRIVATE:
        return

    svc: WorldService = context.application.bot_data["world_service"]
    game = await svc.get_group_world(chat.id)
    if not game:
        await message.reply_text("No world is running in this group.")
        return

    if int(game.get("creator_id") or 0) != user.id and not (
        CONFIG.bot_creator_id and CONFIG.bot_creator_id == user.id
    ):
        await message.reply_text(
            "Only the world creator or bot creator can terminate this world. "
            "Group admins cannot terminate it."
        )
        return

    updated = await svc.terminate(str(game["id"]), user.id)
    await publish_world_card(context.bot, updated, await svc.players(str(game["id"])), svc.db)


async def maintenance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    if not CONFIG.bot_creator_id or user.id != CONFIG.bot_creator_id:
        await message.reply_text("Only the bot creator can use /maintenance.")
        return

    svc: WorldService = context.application.bot_data["world_service"]
    arg = (context.args[0] if context.args else "5").strip().lower()

    if arg in {"off", "0", "stop"}:
        await svc.db.rpc(
            "set_maintenance",
            {"p_until": None, "p_actor": user.id},
        )
        await message.reply_text("✅ Maintenance ended. No world data was deleted.")
        return

    try:
        minutes = max(1, min(60, int(arg)))
    except ValueError:
        await message.reply_text("Use /maintenance 5 or /maintenance off.")
        return

    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    await svc.db.rpc(
        "set_maintenance",
        {"p_until": until.isoformat(), "p_actor": user.id},
    )
    await message.reply_html(
        f"🛠 <b>Maintenance enabled.</b>\n\n"
        f"Estimated duration: <b>{minutes} minutes</b>.\n"
        "No world/player data is deleted.\n"
        "Use /maintenance off when finished."
    )
