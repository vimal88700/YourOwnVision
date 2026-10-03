from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import CONFIG
from world_service import WorldService


def app_url(bot_username: str, start_param: str = "") -> str:
    if bot_username and CONFIG.mini_app_short_name:
        suffix = f"?startapp={start_param}" if start_param else ""
        return f"https://t.me/{bot_username}/{CONFIG.mini_app_short_name}{suffix}"
    base = CONFIG.public_base_url.rstrip("/") + "/app"
    return f"{base}?startapp={start_param}" if start_param else base


def game_markup(bot_username: str, game_id: str, *, joined: bool = False) -> InlineKeyboardMarkup:
    label = "🎮 OPEN STORY" if joined else "🎮 JOIN WORLD"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, url=app_url(bot_username, f"w_{game_id}"))]])


def maintenance_text(row: dict[str, Any] | None) -> str:
    if not row or not row.get("maintenance_until"):
        return ""
    try:
        until = datetime.fromisoformat(str(row["maintenance_until"]).replace("Z", "+00:00"))
        seconds = max(0, int((until - datetime.now(timezone.utc)).total_seconds()))
        minutes = (seconds + 59) // 60
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
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
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
            "🎭 <b>WHAT HAPPENS?</b>\n\n"
            "Open a world from its group or use a friend's invite link.\n"
            "The Mini App contains the whole game — no private-chat bouncing.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎮 OPEN GAME", url=app_url(me.username or ""))]]),
        )
        raise ApplicationHandlerStop

    game = await svc.ensure_world_for_group(chat.id, user.id)
    player = await svc.join(str(game["id"]), user.id, user.username or "", user.first_name or "Player")
    game = await svc.ensure_active(await svc.get_world(str(game["id"])))
    roster = await svc.players(str(game["id"]))
    status = "🟢 LIVE WORLD" if game.get("status") == "active" else "🟡 LOBBY OPEN — 45s to join"
    if game.get("status") == "paused":
        status = "⏸ WORLD PAUSED"

    names = "\n".join(f"• {p.get('display_name') or 'Player'}" for p in roster[:20]) or "• Nobody yet"
    text = (
        "🎭 <b>WHAT HAPPENS?</b>\n\n"
        f"<b>{game.get('title', 'WHAT HAPPENS?')}</b>\n"
        f"{status}\n\n"
        "The 45-second timer is only the join lobby. The story has no round timer.\n"
        "Every player gets a personal path. Shared convergence points bring everyone together.\n"
        "Death changes your run; the world continues.\n\n"
        f"👥 <b>Players ({len(roster)})</b>\n{names}"
    )
    await message.reply_html(text, reply_markup=game_markup(me.username or "", str(game["id"]), joined=True))
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
    if payload.startswith("invite_") or payload.startswith("w_"):
        await message.reply_html(
            "🎭 <b>WHAT HAPPENS?</b>\n\n"
            "Your game is ready. Open it directly — your friend can join without being in the original group.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎮 JOIN WORLD", url=app_url(me.username or "", payload))]]),
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
    await svc.pause(str(game["id"]), user.id)
    await message.reply_text("⏸ World paused. No choices are lost; resume whenever you are ready.")


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
    await svc.resume(str(game["id"]), user.id)
    await message.reply_text("▶️ World resumed. Open the Mini App to continue.", reply_markup=game_markup((await context.bot.get_me()).username or "", str(game["id"]), joined=True))


async def terminate_world(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not chat or not user or chat.type == ChatType.PRIVATE:
        return
    svc: WorldService = context.application.bot_data["world_service"]
    game = await svc.get_group_world(chat.id)
    if not game:
        await message.reply_text("No world is running in this group.")
        return
    # Group admins cannot terminate. Only the world creator or the bot creator can.
    if int(game.get("creator_id") or 0) != user.id and not (CONFIG.bot_creator_id and CONFIG.bot_creator_id == user.id):
        await message.reply_text("Only the creator of this world (or the bot creator) can terminate it. Group admins can pause/resume, not terminate.")
        return
    await svc.terminate(str(game["id"]), user.id)
    await message.reply_text("🛑 This world has been archived. Its current state remains stored; a new /play can create a new world later.")


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
        f"🛠 <b>Maintenance mode enabled.</b>\n\n"
        f"Estimated duration: <b>{minutes} minutes</b>.\n"
        "All groups are paused for new actions, but no world/player data is deleted.\n"
        "Use /maintenance off when the update is finished."
    )
