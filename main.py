from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import MenuButtonWebApp, Update, WebAppInfo
from telegram.ext import CommandHandler, MessageHandler, filters

from bot import (
    create_application,
    db,
    game_service,
    process_update,
    publish_current_scene,
)
from config import CONFIG
from miniapp_api import mount_static, router as miniapp_router
from world_bot import world_play, world_start


logging.basicConfig(
    level=getattr(logging, CONFIG.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("YourOwnVision.main")

telegram_application = create_application()
recovery_task: asyncio.Task[None] | None = None
shutdown_event = asyncio.Event()
RECOVERY_INTERVAL_SECONDS = 10


def webhook_url() -> str:
    base = CONFIG.public_base_url.rstrip("/")
    path = CONFIG.telegram_webhook_path
    return f"{base}{path if path.startswith('/') else '/' + path}"


async def configure_telegram() -> None:
    url = webhook_url()
    await telegram_application.bot.set_webhook(
        url=url,
        secret_token=CONFIG.telegram_webhook_secret,
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=False,
    )
    try:
        await telegram_application.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="🎮 Play",
                web_app=WebAppInfo(url=CONFIG.public_base_url.rstrip("/") + "/app"),
            )
        )
    except Exception:
        logger.exception("Could not configure Telegram Mini App menu button.")
    info = await telegram_application.bot.get_webhook_info()
    logger.info(
        "Telegram webhook registered: url=%s pending=%s last_error=%s",
        info.url or "<empty>",
        info.pending_update_count,
        info.last_error_message or "<none>",
    )


class RecoveryContext:
    def __init__(self, bot: Any) -> None:
        self.bot = bot


async def recover_games_and_publish() -> None:
    results = await game_service.recover_all_due_games()
    for item in results:
        if not isinstance(item, dict):
            continue
        candidate = item.get("game") if isinstance(item.get("game"), dict) else item
        if candidate.get("status") != game_service.STATUS_PLAYING:
            continue
        game_id = candidate.get("id")
        if not game_id:
            continue
        try:
            await publish_current_scene(RecoveryContext(telegram_application.bot), str(game_id))
        except Exception:
            logger.exception("Could not publish recovered game %s", game_id)


async def recovery_loop() -> None:
    try:
        await recover_games_and_publish()
    except Exception:
        logger.exception("Initial legacy-game recovery failed.")
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=RECOVERY_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
        if shutdown_event.is_set():
            break
        try:
            await recover_games_and_publish()
        except Exception:
            logger.exception("Periodic legacy-game recovery failed.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global recovery_task
    shutdown_event.clear()
    await telegram_application.initialize()
    await telegram_application.start()
    me = await telegram_application.bot.get_me()
    app.state.bot_username = me.username or ""
    await configure_telegram()
    try:
        await recover_games_and_publish()
    except Exception:
        logger.exception("Startup legacy-game recovery failed.")
    recovery_task = asyncio.create_task(recovery_loop(), name="legacy-game-recovery")
    try:
        yield
    finally:
        shutdown_event.set()
        if recovery_task:
            try:
                await recovery_task
            except Exception:
                logger.exception("Recovery task shutdown failed.")
        try:
            await telegram_application.stop()
        finally:
            try:
                await telegram_application.shutdown()
            finally:
                await db.close()


app = FastAPI(
    title="YourOwnVision",
    description="WHAT HAPPENS? continuous Telegram Mini App world.",
    version="2.0.0",
    lifespan=lifespan,
)
app.include_router(miniapp_router)
mount_static(app)

# The Mini App is now the primary /play path. Negative handler groups have
# higher priority than the legacy bot handlers without requiring a risky rewrite.
telegram_application.add_handler(
    MessageHandler(
        filters.ChatType.PRIVATE & filters.Regex(r"^/start(?:@\w+)?\s+(?:world_|invite_).+"),
        world_start,
    ),
    group=-2,
)
telegram_application.add_handler(
    CommandHandler("play", world_play),
    group=-2,
)


@app.get("/health", response_class=JSONResponse)
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "YourOwnVision", "environment": CONFIG.environment}


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
    try:
        await process_update(telegram_application, update)
    except Exception as exc:
        logger.exception("Telegram update processing failed: %s", update.update_id)
        raise HTTPException(status_code=500, detail="Update processing failed.") from exc
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


@app.get("/", response_class=JSONResponse)
async def root() -> dict[str, Any]:
    return {"service": "YourOwnVision", "status": "running", "miniapp": "/app", "health": "/health"}


def run() -> None:
    import uvicorn
    uvicorn.run(app, host=CONFIG.host, port=CONFIG.port, log_level=CONFIG.log_level.lower())


if __name__ == "__main__":
    run()
