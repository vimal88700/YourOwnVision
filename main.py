from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import CONFIG
from database import Database
from miniapp_api import configure_service, mount_static, router as miniapp_router
from world_bot import maintenance_command, pause_world, resume_world, terminate_world, world_play, world_start
from world_service import WorldService

logging.basicConfig(
    level=getattr(logging, CONFIG.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("YourOwnVision.main")

db = Database(CONFIG.supabase_url, CONFIG.supabase_service_key)
world_service = WorldService(db)
configure_service(db)
telegram_application = Application.builder().token(CONFIG.telegram_token).build()


def webhook_url() -> str:
    path = CONFIG.telegram_webhook_path
    return f"{CONFIG.public_base_url.rstrip('/')}{path if path.startswith('/') else '/' + path}"


async def configure_telegram() -> None:
    await telegram_application.bot.set_webhook(
        url=webhook_url(),
        secret_token=CONFIG.telegram_webhook_secret,
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=False,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    telegram_application.bot_data["db"] = db
    telegram_application.bot_data["world_service"] = world_service

    telegram_application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.Regex(r"^/start(?:@\w+)?\s+(?:invite_|w_).+"),
            world_start,
        ),
        group=-20,
    )
    telegram_application.add_handler(CommandHandler("play", world_play), group=-10)
    telegram_application.add_handler(CommandHandler("pause", pause_world), group=-10)
    telegram_application.add_handler(CommandHandler("resume", resume_world), group=-10)
    telegram_application.add_handler(CommandHandler(["endworld", "terminateworld"], terminate_world), group=-10)
    telegram_application.add_handler(CommandHandler("maintenance", maintenance_command), group=-10)

    await telegram_application.initialize()
    await telegram_application.start()
    me = await telegram_application.bot.get_me()
    app.state.bot_username = me.username or ""
    app.state.telegram_bot = telegram_application.bot
    await configure_telegram()
    logger.info("YourOwnVision started as @%s", me.username)
    try:
        yield
    finally:
        await telegram_application.stop()
        await telegram_application.shutdown()
        await db.close()


app = FastAPI(
    title="YourOwnVision",
    description="WHAT HAPPENS? — continuous multiplayer Telegram Mini App",
    version="4.0.0",
    lifespan=lifespan,
)
app.include_router(miniapp_router)
mount_static(app)


@app.get("/health", response_class=JSONResponse)
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "YourOwnVision", "version": "4.0.0"}


@app.post(CONFIG.telegram_webhook_path, response_class=JSONResponse)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict[str, Any]:
    if x_telegram_bot_api_secret_token != CONFIG.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        payload = await request.json()
        update = Update.de_json(payload, telegram_application.bot)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Telegram update.") from exc
    if update is None:
        raise HTTPException(status_code=400, detail="Invalid Telegram update.")
    await telegram_application.process_update(update)
    return {"ok": True}


@app.get("/telegram/status", response_class=JSONResponse)
async def telegram_status() -> dict[str, Any]:
    info = await telegram_application.bot.get_webhook_info()
    return {
        "url": info.url,
        "pending_update_count": info.pending_update_count,
        "last_error_date": info.last_error_date,
        "last_error_message": info.last_error_message,
        "allowed_updates": info.allowed_updates,
    }


@app.get("/")
async def root() -> dict[str, Any]:
    return {"service": "YourOwnVision", "status": "running", "miniapp": "/app", "health": "/health"}


def run() -> None:
    import uvicorn
    uvicorn.run(app, host=CONFIG.host, port=CONFIG.port, log_level=CONFIG.log_level.lower())


if __name__ == "__main__":
    run()
