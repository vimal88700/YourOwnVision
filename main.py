from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import MenuButtonWebApp, Update, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import CONFIG
from database import Database
from miniapp_api_v2 import configure_service, mount_static, router as miniapp_router
from world_bot_v2 import maintenance_command, pause_world, resume_world, terminate_world, world_play, world_start
from world_service_v2 import WorldService

LOG_LEVEL=str(CONFIG.log_level or "INFO").upper()
logging.basicConfig(level=getattr(logging,LOG_LEVEL,logging.INFO),format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger=logging.getLogger("YourOwnVision.main")

db=Database(CONFIG.supabase_url,CONFIG.supabase_service_key)
world_service=WorldService(db)
configure_service(db)
telegram_application=Application.builder().token(CONFIG.telegram_token).build()

def webhook_url()->str:return f"{CONFIG.public_base_url.rstrip('/')}{CONFIG.telegram_webhook_path if CONFIG.telegram_webhook_path.startswith('/') else '/'+CONFIG.telegram_webhook_path}"
def mini_app_url()->str:return f"{CONFIG.public_base_url.rstrip('/')}/app"

async def configure_telegram()->None:
    await telegram_application.bot.set_webhook(url=webhook_url(),secret_token=CONFIG.telegram_webhook_secret,allowed_updates=["message","callback_query"],drop_pending_updates=False)
    await telegram_application.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="🎮 Play",web_app=WebAppInfo(url=mini_app_url())))

async def telegram_error(update:object,context)->None:
    logger.exception("Unhandled Telegram update error",exc_info=context.error)
    try:
        message=getattr(update,"effective_message",None)
        if message: await message.reply_text("⚠️ Something failed inside the world engine. Your saved progress is safe; please try again.")
    except Exception: logger.exception("Could not send Telegram error message")

@asynccontextmanager
async def lifespan(app:FastAPI):
    telegram_application.bot_data["db"]=db
    telegram_application.bot_data["world_service"]=world_service
    telegram_application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.Regex(r"^/start(?:@\w+)?\s+(?:invite_|w_).+"),world_start),group=-20)
    telegram_application.add_handler(CommandHandler("play",world_play),group=-10)
    telegram_application.add_handler(CommandHandler("pause",pause_world),group=-10)
    telegram_application.add_handler(CommandHandler("resume",resume_world),group=-10)
    telegram_application.add_handler(CommandHandler(["endworld","terminateworld"],terminate_world),group=-10)
    telegram_application.add_handler(CommandHandler(["maintenance","maintainance"],maintenance_command),group=-10)
    telegram_application.add_error_handler(telegram_error)
    await telegram_application.initialize(); await telegram_application.start()
    me=await telegram_application.bot.get_me(); app.state.bot_username=me.username or ""; app.state.telegram_bot=telegram_application.bot; telegram_application.bot_data["bot_username"]=me.username or ""
    await configure_telegram()
    logger.info("YourOwnVision v6 started as @%s; webhook=%s",me.username,webhook_url())
    try:yield
    finally:
        await telegram_application.stop(); await telegram_application.shutdown(); await db.close()

app=FastAPI(title="YourOwnVision",description="WHAT HAPPENS? — persistent multiplayer world",version="6.0.0",lifespan=lifespan)
app.include_router(miniapp_router); mount_static(app)

@app.get("/health",response_class=JSONResponse)
async def health()->dict[str,Any]:return {"status":"ok","service":"YourOwnVision","version":"6.0.0"}

@app.post(CONFIG.telegram_webhook_path,response_class=JSONResponse)
async def telegram_webhook(request:Request,x_telegram_bot_api_secret_token:str|None=Header(default=None,alias="X-Telegram-Bot-Api-Secret-Token"))->dict[str,Any]:
    if x_telegram_bot_api_secret_token!=CONFIG.telegram_webhook_secret:raise HTTPException(status_code=403,detail="Forbidden")
    try:update=Update.de_json(await request.json(),telegram_application.bot)
    except Exception as exc:raise HTTPException(status_code=400,detail="Invalid Telegram update.") from exc
    if update is None:raise HTTPException(status_code=400,detail="Invalid Telegram update.")
    try:await telegram_application.process_update(update)
    except Exception:logger.exception("Telegram update processing failed")
    return {"ok":True}

@app.get("/telegram/status",response_class=JSONResponse)
async def telegram_status()->dict[str,Any]:
    info=await telegram_application.bot.get_webhook_info(); return {"url":info.url,"pending_update_count":info.pending_update_count,"last_error_date":info.last_error_date,"last_error_message":info.last_error_message,"allowed_updates":info.allowed_updates}

@app.get("/")
async def root()->dict[str,Any]:return {"service":"YourOwnVision","status":"running","miniapp":"/app","health":"/health","version":"6.0.0"}

def run()->None:
    import uvicorn
    uvicorn.run(app,host=CONFIG.host,port=CONFIG.port,log_level=LOG_LEVEL.lower())
if __name__=="__main__":run()
