from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut
from telegram.ext import ContextTypes

from config import CONFIG
from world_service import WorldService

logger=logging.getLogger("YourOwnVision.world_bot")
CARD_CACHE: dict[str,str]={}

def main_app_url(bot_username:str,start_param:str="")->str:
    if not bot_username:return f"{CONFIG.public_base_url.rstrip('/')}/app?startapp={start_param}"
    return f"https://t.me/{bot_username}?startapp={start_param}&mode=fullscreen"

def _svc(context):return context.application.bot_data["world_service"]
def _bot_username(context)->str:return str(context.application.bot_data.get("bot_username") or getattr(context.application.bot,"username","") or "")
def _hash(s:str)->str:return hashlib.sha256(s.encode()).hexdigest()

def enter_button(context,game_id:str,label:str="🎮 ENTER WORLD"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(label,url=main_app_url(_bot_username(context),f"w_{game_id}"))]])

def card_text(game:dict[str,Any],players:list[dict[str,Any]])->str:
    status=str(game.get("status") or "waiting"); world=game.get("world_state") or {}; settings=game.get("settings") or {}
    if status=="waiting":
        left=45
        try:left=max(0,int((datetime.fromisoformat(str(game.get("join_deadline")).replace("Z","+00:00"))-datetime.now(timezone.utc)).total_seconds()))
        except Exception:pass
        phase=f"🟡 LOBBY · {left}s"; intro="The 45-second timer is only for joining. The story has no round timer."
    elif status=="paused":phase="⏸ PAUSED";intro="The world is frozen safely. Saved progress remains intact."
    elif status in {"archived","terminated","completed"}:phase="◆ ENDED";intro="This world is preserved. Repeating /play will not create a replacement."
    else:phase="🟢 LIVE WORLD";intro="Explore, build relationships, take risks and meet other players at shared turning points."
    roster="\n".join(f"• {p.get('display_name') or 'Player'}" for p in players[:20]) or "• No players yet — enter first."
    return f"🎭 <b>{game.get('title') or 'WHAT HAPPENS?'}</b>\n\n<b>{phase}</b>\n{intro}\n\n📍 <b>{world.get('location_name') or 'Unknown'}</b> · {world.get('dimension_name') or 'The Unknown'}\n🌐 World turn {int(world.get('global_turn') or 0)}\n\n👥 <b>Players ({len(players)})</b>\n{roster}\n\n🔗 External invites: {'ON' if settings.get('allow_external_invites',True) else 'OFF'}"

async def publish_world_card(bot,game,players,db=None):
    game_id=str(game["id"]); text=card_text(game,players); fingerprint=_hash(text); chat_id=game.get("telegram_message_chat_id") or game.get("chat_id"); message_id=game.get("telegram_message_id")
    if not chat_id:return
    if CARD_CACHE.get(game_id)==fingerprint:return
    markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎮 ENTER WORLD",url=main_app_url(str(getattr(bot,"username","") or ""),f"w_{game_id}"))]])
    try:
        if message_id:
            await bot.edit_message_text(chat_id=int(chat_id),message_id=int(message_id),text=text,reply_markup=markup,parse_mode="HTML",disable_web_page_preview=True)
        else:
            msg=await bot.send_message(chat_id=int(chat_id),text=text,reply_markup=markup,parse_mode="HTML",disable_web_page_preview=True)
            if db:await db.request("PATCH","world_games",params={"id":f"eq.{game_id}"},json={"telegram_message_id":msg.message_id,"telegram_message_chat_id":int(chat_id)},prefer="return=minimal")
        CARD_CACHE[game_id]=fingerprint
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():CARD_CACHE[game_id]=fingerprint
        else:logger.warning("World card update failed for %s: %s",game_id,exc)
    except RetryAfter as exc:logger.warning("Telegram flood protection for %s; skipped card update for %.1fs",game_id,float(exc.retry_after))
    except (TimedOut,Forbidden) as exc:logger.warning("Telegram card transport problem for %s: %s",game_id,exc)
    except Exception:logger.exception("Unexpected world card failure for %s",game_id)

async def world_start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    message=update.effective_message; user=update.effective_user
    if not message or not user:return
    payload=(context.args[0] if context.args else "").strip(); svc=_svc(context)
    try:
        if payload.startswith("invite_"):
            token=payload[7:]; rows=await svc.db.request("GET","world_invites",params={"token":f"eq.{token}","active":"eq.true","limit":"1"})
            if not rows:await message.reply_text("That invite is invalid or expired.");return
            game=await svc.get_world(str(rows[0]["game_id"]))
            if not (game.get("settings") or {}).get("allow_external_invites",True):await message.reply_text("External invites are disabled for this world.");return
            if not await svc.get_player(str(game["id"]),int(user.id)):await svc.consume_invite(token,int(user.id),user.username or "",user.first_name or "Player")
            await message.reply_text("🎮 <b>You joined.</b> Open the persistent world below.",parse_mode="HTML",reply_markup=enter_button(context,str(game["id"])));return
        if payload.startswith("w_"):
            game=await svc.get_world(payload[2:]);await message.reply_text("🎮 <b>Return to your world.</b> Nothing was reset.",parse_mode="HTML",reply_markup=enter_button(context,str(game["id"])));return
        await message.reply_text("Use /play in the group where you want to play.")
    except Exception:logger.exception("/start failed");await message.reply_text("I couldn't open that world right now. The saved world is safe; please try again.")

async def world_play(update:Update,context:ContextTypes.DEFAULT_TYPE):
    message=update.effective_message; chat=update.effective_chat; user=update.effective_user
    if not message or not chat or not user:return
    if chat.type not in {"group","supergroup"}:
        await message.reply_text("Use /play inside a group.");return
    svc=_svc(context)
    try:
        if await svc.maintenance_active():await message.reply_text("🛠 Maintenance is active. Saved worlds are safe and no new world action is being processed.");return
        game=await svc.get_group_world(int(chat.id))
        if game and game.get("status") in {"archived","terminated","completed"}:
            await message.reply_text("◆ This group already has a finished world. I will not create another one accidentally.");return
        if not game:game=await svc.create_world(int(user.id),int(chat.id))
        before=await svc.get_player(str(game["id"]),int(user.id));game=await svc.ensure_active(game)
        await svc.join(str(game["id"]),int(user.id),user.username or "",user.first_name or "Player")
        await publish_world_card(context.bot,game,await svc.players(str(game["id"])),svc.db)
        await message.reply_text("✓ You're in this group's world. Tap ENTER WORLD to open the game." if before else "🎮 World joined. Tap ENTER WORLD to start playing.",reply_markup=enter_button(context,str(game["id"])))
    except Exception as exc:logger.exception("/play failed");await message.reply_text(f"⚠️ <b>World engine error</b>\n\n{str(exc)[:500]}\n\nYour saved data was not deleted.",parse_mode="HTML")

async def _moderate(update,context,action:str):
    chat=update.effective_chat;user=update.effective_user;message=update.effective_message
    if not chat or not user or not message:return
    svc=_svc(context);game=await svc.get_group_world(int(chat.id))
    if not game:await message.reply_text("No world exists here yet. Use /play.");return
    allowed=bool(CONFIG.bot_creator_id and user.id==CONFIG.bot_creator_id) or int(game.get("creator_id") or 0)==user.id
    if not allowed:
        try:allowed=(await context.bot.get_chat_member(chat.id,user.id)).status in {"creator","administrator"}
        except Exception:allowed=False
    try:allowed=allowed or user.id in {int(x) for x in (game.get("settings") or {}).get("operator_ids",[])}
    except Exception:pass
    if not allowed:await message.reply_text("You do not have permission to control this world.");return
    try:
        updated=await (svc.pause(str(game["id"]),user.id) if action=="pause" else svc.resume(str(game["id"]),user.id));await publish_world_card(context.bot,updated,await svc.players(str(game["id"])),svc.db);await message.reply_text("⏸ World paused safely." if action=="pause" else "▶️ World resumed safely.")
    except Exception:logger.exception("moderation failed");await message.reply_text("Control failed, but no world data was deleted.")
async def pause_world(update,context):await _moderate(update,context,"pause")
async def resume_world(update,context):await _moderate(update,context,"resume")

async def terminate_world(update,context):
    message=update.effective_message;chat=update.effective_chat;user=update.effective_user
    if not message or not chat or not user:return
    game=await _svc(context).get_group_world(int(chat.id))
    if not game:await message.reply_text("No world exists here.");return
    if not (CONFIG.bot_creator_id and user.id==CONFIG.bot_creator_id) and int(game.get("creator_id") or 0)!=user.id:
        await message.reply_text("Only the world creator can terminate it. Group admins cannot terminate a world.");return
    await message.reply_text("Termination is creator-only and is intentionally kept out of the ordinary group flow. Use the Control screen in the Mini App.")

async def maintenance_command(update,context):
    message=update.effective_message;user=update.effective_user
    if not message or not user:return
    if not (CONFIG.bot_creator_id and user.id==CONFIG.bot_creator_id):await message.reply_text("Only the bot creator can start maintenance.");return
    minutes=10
    if context.args:
        try:minutes=max(1,min(60,int(context.args[0])))
        except ValueError:pass
    until=datetime.now(timezone.utc)+timedelta(minutes=minutes)
    try:
        await _svc(context).db.rpc("set_maintenance",{"p_until":until.isoformat(),"p_actor":user.id});await message.reply_text(f"🛠 Maintenance started for {minutes} minutes. Worlds and player data remain stored.")
    except Exception:logger.exception("maintenance failed");await message.reply_text("Maintenance could not be activated. Nothing was changed.")
