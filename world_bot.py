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


def app_url(bot_username: str, start_param: str = "") -> str:
    """Return a real Telegram Mini App deep link, not a bot-DM link."""
    short_name = str(CONFIG.mini_app_short_name or "").strip()
    if bot_username and short_name:
        suffix = f"?startapp={start_param}" if start_param else ""
        return f"https://t.me/{bot_username}/{short_name}{suffix}"
    if bot_username:
        suffix = f"?startapp={start_param}" if start_param else "?startapp"
        return f"https://t.me/{bot_username}{suffix}"
    base = CONFIG.public_base_url.rstrip("/") + "/app"
    return f"{base}?startapp={start_param}" if start_param else base


def game_markup(bot_username: str, game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🎮 OPEN / JOIN WORLD", url=app_url(bot_username, f"w_{game_id}"))]]
    )


def world_card_text(game: dict[str, Any], roster: list[dict[str, Any]]) -> str:
    status = str(game.get("status") or "waiting")
    status_line = {
        "waiting": "🟡 <b>JOINING</b> — 45 seconds",
        "paused": "⏸ <b>PAUSED</b>",
        "active": "🟢 <b>LIVE WORLD</b>",
    }.get(status, "⚫ <b>ENDED</b>")
    names = "\n".join(
        f"• {escape(str(p.get('display_name') or 'Player'))}"
        for p in roster[:20]
    ) or "• No players yet"
    extra = {
        "waiting": "The 45-second timer is only the join lobby. The story itself has no round timer.",
        "paused": "The world is paused. Progress is preserved.",
        "active": "The story is live. Personal paths can diverge, then converge at shared turning points.",
    }.get(status, "This world is archived. Its saved state remains in Supabase.")
    return (
        "🎭 <b>WHAT HAPPENS?</b>\n\n"
        f"<b>{escape(str(game.get('title') or 'WHAT HAPPENS?'))}</b>\n"
        f"{status_line}\n\n"
        f"{extra}\n"
        "Death changes a player's run; it does not delete the shared world.\n\n"
        f"👥 <b>Players ({len(roster)})</b>\n{names}"
    )


async def publish_world_card(bot, game: dict[str, Any], roster: list[dict[str, Any]], db) -> None:
    chat_id = game.get("chat_id")
    if not chat_id:
        return
    me = await bot.get_me()
    markup = game_markup(me.username or "", str(game["id"]))
    message_id = game.get("telegram_message_id")
    if message_id:
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=world_card_text(game, roster),
                parse_mode="HTML",
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            return
        except Exception:
            logger.info("World card %s is no longer editable; creating replacement", game.get("id"))
    sent = await bot.send_message(
        chat_id=int(chat_id),
        text=world_card_text(game, roster),
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
        logger.exception("Could not persist Telegram world-card message id for %s", game.get("id"))


def maintenance_text(row: dict[str, Any] | None) -> str:
    if not row or not row.get("maintenance_until"):
        return ""
    try:
        until = datetime.fromisoformat(str(row["maintenance_until"]).replace("Z", "+00:00"))
        minutes = (max(0, int((until - datetime.now(timezone.utc)).total_seconds())) + 59) // 60
        return f"🛠 Maintenance is active. Estimated remaining time: <b>{minutes} min</b>. Your worlds and progress are safe."
    except Exception:
        return "🛠 Maintenance is active. Your worlds and progress are safe."


async def is_global_maintenance(svc: WorldService) -> tuple[bool, str]:
    row = await svc.maintenance()
    if not row or not row.get("maintenance_until"):
        return False, ""
    try:
        until = datetime.fromisoformat(str(row["maintenance_until"]).replace("Z", "+00:00"))
        active = datetime.now(timezone.utc) < until
        return active, maintenance_text(row) if active else ""
    except Exception:
        return False, ""


async def actor_can_moderate(update: Update, svc: WorldService, game: dict[str, Any], user_id: int, bot) -> bool:
    if CONFIG.bot_creator_id and user_id == CONFIG.bot_creator_id:
        return True
    settings = game.get("settings") or {}
    if user_id in [int(x) for x in settings.get("operator_ids", [])]:
        return True
    if int(game.get("creator_id") or 0) == user_id:
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
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user:
        return
    svc: WorldService = context.application.bot_data["world_service"]
    maintenance, maintenance_message = await is_global_maintenance(svc)
    if maintenance:
        await message.reply_html(maintenance_message)
        raise ApplicationHandlerStop
    bot = context.application.bot
    me = await bot.get_me()
    if chat.type == ChatType.PRIVATE:
        await message.reply_html(
            "🎭 <b>WHAT HAPPENS?</b>\n\nOpen a world from its group or use a friend's invite link.\nThe game opens directly in the Mini App.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎮 OPEN GAME", url=app_url(me.username or ""))]]),
        )
        raise ApplicationHandlerStop

    game = await svc.ensure_world_for_group(chat.id, user.id)
    await svc.join(str(game["id"]), user.id, user.username or "", user.first_name or "Player")
    game = await svc.ensure_active(await svc.get_world(str(game["id"])))
    await publish_world_card(bot, game, await svc.players(str(game["id"])), svc.db)
    raise ApplicationHandlerStop


async def world_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    payload = context.args[0] if context.args else ""
    if not payload:
        return
    svc: WorldService = context.application.bot_data["world_service"]
    maintenance, maintenance_message = await is_global_maintenance(svc)
    if maintenance:
        await message.reply_html(maintenance_message)
        raise ApplicationHandlerStop
    me = await context.bot.get_me()
    if payload.startswith(("invite_", "w_")):
        await message.reply_html(
            "🎭 <b>WHAT HAPPENS?</b>\n\nYour game is ready. Tap below to open the Mini App directly.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎮 JOIN / OPEN WORLD", url=app_url(me.username or "", payload))]]),
        )
        raise ApplicationHandlerStop


async def pause_world(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or chat.type == ChatType.PRIVATE:
        return
    svc: WorldService = context.application.bot_data["world_service"]
    game = await svc.get_group_world(chat.id)
    if not game:
        await message.reply_text("No active WHAT HAPPENS? world in this group. Use /play.")
        return
    if not await actor_can_moderate(update, svc, game, user.id, context.bot):
        await message.reply_text("Only the group owner/admin, assigned operator, world creator, or bot creator can pause the world.")
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
    if not await actor_can_moderate(update, svc, game, user.id, context.bot):
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
    if int(game.get("creator_id") or 0) != user.id and not (CONFIG.bot_creator_id and CONFIG.bot_creator_id == user.id):
        await message.reply_text("Only the creator of this world (or the bot creator) can terminate it. Group admins cannot terminate.")
        return
    updated = await svc.terminate(str(game["id"]), user.id)
    await publish_world_card(context.bot, updated, await svc.players(str(game["id"])), svc.db)


async def maintenance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user = update.effective_message, update.effective_user
    if not message or not user or user.id != CONFIG.bot_creator_id:
        return
    svc: WorldService = context.application.bot_data["world_service"]
    arg = (context.args[0] if context.args else "5").strip().lower()
    if arg in {"off", "0", "stop"}:
        await svc.db.rpc("set_maintenance", {"p_until": None, "p_actor": user.id})
        await message.reply_text("✅ Maintenance ended. No world data was deleted.")
        return
    try:
        minutes = max(1, min(60, int(arg)))
    except ValueError:
        await message.reply_text("Use /maintenance 5, or /maintenance off.")
        return
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    await svc.db.rpc("set_maintenance", {"p_until": until.isoformat(), "p_actor": user.id})
    await message.reply_html(
        f"🛠 <b>Maintenance mode enabled.</b>\n\nEstimated duration: <b>{minutes} minutes</b>.\nAll groups are paused for new actions, but no world/player data is deleted.\nUse /maintenance off when the update is finished."
    )
