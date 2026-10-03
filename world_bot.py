from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import CONFIG
from database import Database
from world_service import WorldService


def app_url(start_param: str = "") -> str:
    base = CONFIG.public_base_url.rstrip("/") + "/app"
    return f"{base}?startapp={start_param}" if start_param else base


def group_game_markup(bot_username: str, game_id: str, *, show_settings: bool = False) -> InlineKeyboardMarkup:
    if bot_username:
        open_url = f"https://t.me/{bot_username}?startapp=game_{game_id}"
    else:
        open_url = app_url(f"game_{game_id}")
    rows = [[InlineKeyboardButton("🎮 OPEN WORLD", url=open_url)]]
    if show_settings:
        rows.append([InlineKeyboardButton("⚙️ WORLD SETTINGS", web_app=WebAppInfo(url=app_url(f"settings_{game_id}")))])
    return InlineKeyboardMarkup(rows)


def private_game_markup(game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🎮 OPEN WORLD", web_app=WebAppInfo(url=app_url(f"game_{game_id}")))]] )


def format_lobby(players: list[dict]) -> str:
    if not players:
        return "No players have joined yet."
    lines = [f"• {p.get('display_name') or 'Player'}" for p in players[:20]]
    return "\n".join(lines)


async def world_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return

    db: Database = context.application.bot_data["db"]
    svc: WorldService = context.application.bot_data["world_service"]

    if chat.type == ChatType.PRIVATE:
        await message.reply_text(
            "🎭 WHAT HAPPENS?\n\nOpen the Mini App to continue a world or use an invite link.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎮 OPEN WHAT HAPPENS?", web_app=WebAppInfo(url=app_url("home")))]]),
        )
        raise ApplicationHandlerStop

    game = await svc.get_group_world(chat.id)
    if not game:
        game = await svc.create_world(creator_id=user.id, chat_id=chat.id)

    players = await svc.players(str(game["id"]))
    status = "🟢 WORLD ACTIVE" if game.get("status") == "active" else "🟡 JOINING — 45 seconds"
    text = (
        "🎭 <b>WHAT HAPPENS?</b>\n\n"
        f"<b>{game.get('title', 'WHAT HAPPENS?')}</b>\n"
        f"{status}\n\n"
        "The 45-second timer is only for the lobby. The story has no round timer.\n"
        "Every player gets a different path, but the world creates shared convergence points.\n"
        "Death changes your path; it never deletes the shared world.\n\n"
        f"👥 <b>Players ({len(players)})</b>\n{format_lobby(players)}"
    )
    me = await context.bot.get_me()
    await message.reply_html(text, reply_markup=group_game_markup(me.username or "", str(game["id"])))
    raise ApplicationHandlerStop


async def world_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    payload = context.args[0] if context.args else ""
    if not (payload.startswith("world_") or payload.startswith("invite_")):
        return
    await message.reply_text(
        "🎭 WHAT HAPPENS?\n\n"
        "Your invite is ready. Open the Mini App and join instantly.\n"
        "Your choices happen inside the game — no bouncing between chats.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎮 OPEN GAME", web_app=WebAppInfo(url=app_url(payload)))]]),
    )
    raise ApplicationHandlerStop
