from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ChatType
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot import db
from config import CONFIG
from world_service import WorldService

world_service = WorldService(db)


def app_url(start_param: str = "") -> str:
    url = CONFIG.public_base_url.rstrip("/") + "/app"
    if start_param:
        url += "?startapp=" + start_param
    return url


async def world_private_button(context: ContextTypes.DEFAULT_TYPE, start_param: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "🎮 OPEN WHAT HAPPENS?",
        web_app=WebAppInfo(url=app_url(start_param)),
    )]])


async def world_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat or chat.type != ChatType.PRIVATE:
        return
    payload = context.args[0] if context.args else ""
    if not (payload.startswith("world_") or payload.startswith("invite_")):
        return
    await message.reply_text(
        "🎭 **WHAT HAPPENS?**\n\n"
        "The new continuous world is ready.\n\n"
        "No rounds. No forced ending. Your choices shape the world, "
        "relationships, discoveries and consequences.\n\n"
        "Open the Mini App to join.",
        reply_markup=await world_private_button(context, payload),
        parse_mode="Markdown",
    )
    raise ApplicationHandlerStop


async def world_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not user:
        return
    if chat.type == ChatType.PRIVATE:
        await message.reply_text(
            "🎭 Open the Mini App below to create or continue your world.",
            reply_markup=await world_private_button(context, "home"),
        )
        return

    game = await world_service.get_group_world(chat.id)
    if not game:
        game = await world_service.create_world(creator_id=user.id, chat_id=chat.id)

    try:
        me = await context.bot.get_me()
        username = me.username or ""
    except Exception:
        username = ""
    if not username:
        await message.reply_text("⚠️ I could not resolve my Telegram username yet. Please try again.")
        return

    deep_link = f"https://t.me/{username}?start=world_{game['id']}"
    status_line = (
        "🟢 The world is already alive. You can join now."
        if game.get("status") == "active"
        else "⏳ Players have 45 seconds to join. The story itself does not use a round timer."
    )
    await message.reply_text(
        "🎭 **WHAT HAPPENS? — CONTINUOUS WORLD**\n\n"
        f"**{game.get('title', 'WHAT HAPPENS?')}**\n\n"
        f"{status_line}\n\n"
        "Tap **JOIN / OPEN GAME** to enter the Mini App.\n"
        "Friends can use the same invite flow even if they are not in this group.\n\n"
        "The world keeps going while players make choices. Death changes your path; it does not end the world.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎮 JOIN / OPEN GAME", url=deep_link)]]),
        parse_mode="Markdown",
    )
    raise ApplicationHandlerStop
