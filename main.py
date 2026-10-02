from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import Update

from bot import (
    create_application,
    process_update,
    recover_active_games,
)
from config import CONFIG


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=getattr(
        logging,
        CONFIG.log_level.upper(),
        logging.INFO,
    ),
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(name)s "
        "%(message)s"
    ),
)

logger = logging.getLogger("YourOwnVision.main")


# ============================================================
# TELEGRAM APPLICATION
# ============================================================

telegram_application = create_application()

recovery_task: asyncio.Task[None] | None = None
shutdown_event = asyncio.Event()

RECOVERY_INTERVAL_SECONDS = 10


# ============================================================
# WEBHOOK URL
# ============================================================

def webhook_url() -> str:
    base = CONFIG.public_base_url.rstrip("/")
    path = CONFIG.telegram_webhook_path

    if not path.startswith("/"):
        path = f"/{path}"

    return f"{base}{path}"


# ============================================================
# TELEGRAM WEBHOOK REGISTRATION
# ============================================================

async def configure_telegram_webhook() -> None:
    url = webhook_url()

    logger.info(
        "Registering Telegram webhook: %s",
        url,
    )

    await telegram_application.bot.set_webhook(
        url=url,
        secret_token=CONFIG.telegram_webhook_secret,
        allowed_updates=[
            "message",
            "callback_query",
        ],
        drop_pending_updates=False,
    )

    info = await telegram_application.bot.get_webhook_info()

    logger.info(
        "Telegram webhook registered: url=%s pending=%s "
        "last_error=%s",
        info.url or "<empty>",
        info.pending_update_count,
        info.last_error_message or "<none>",
    )


# ============================================================
# GAME RECOVERY
# ============================================================

async def recovery_loop() -> None:
    logger.info(
        "Persistent game recovery loop started."
    )

    try:
        await recover_active_games()

    except Exception:
        logger.exception(
            "Initial game recovery failed."
        )

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=RECOVERY_INTERVAL_SECONDS,
            )
        except asyncio.TimeoutError:
            pass

        if shutdown_event.is_set():
            break

        try:
            await recover_active_games()

        except Exception:
            logger.exception(
                "Periodic game recovery failed."
            )

    logger.info(
        "Persistent game recovery loop stopped."
    )


# ============================================================
# FASTAPI LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global recovery_task

    logger.info(
        "Starting YourOwnVision application..."
    )

    shutdown_event.clear()

    # python-telegram-bot is used only as an update processor.
    # Telegram transport itself is handled by FastAPI.
    await telegram_application.initialize()
    await telegram_application.start()

    logger.info(
        "Telegram application initialized."
    )

    # Register the webhook on every startup.
    await configure_telegram_webhook()

    # Recover any persistent game state.
    try:
        await recover_active_games()

    except Exception:
        logger.exception(
            "Startup game recovery failed."
        )

    recovery_task = asyncio.create_task(
        recovery_loop(),
        name="persistent-game-recovery",
    )

    logger.info(
        "YourOwnVision application is ready."
    )

    try:
        yield

    finally:
        logger.info(
            "Shutting down YourOwnVision..."
        )

        shutdown_event.set()

        if recovery_task is not None:
            try:
                await recovery_task

            except asyncio.CancelledError:
                pass

            except Exception:
                logger.exception(
                    "Recovery task shutdown failed."
                )

            recovery_task = None

        # IMPORTANT:
        #
        # DO NOT call delete_webhook() here.
        #
        # During a Render deploy, the old instance can receive
        # SIGTERM after the new instance has already registered
        # the webhook. Deleting it here can remove the webhook
        # belonging to the new instance.
        #
        # The webhook is persistent Telegram configuration and
        # should be replaced on startup, not deleted on shutdown.

        logger.info(
            "Leaving Telegram webhook registered."
        )

        try:
            await telegram_application.stop()

        except Exception:
            logger.exception(
                "Telegram application stop failed."
            )

        try:
            await telegram_application.shutdown()

        except Exception:
            logger.exception(
                "Telegram application shutdown failed."
            )

        logger.info(
            "YourOwnVision shutdown complete."
        )


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="YourOwnVision",
    description="WHAT HAPPENS? deterministic Telegram story game.",
    version="1.0.0",
    lifespan=lifespan,
)


# ============================================================
# HEALTH
# ============================================================

@app.get(
    "/health",
    response_class=JSONResponse,
)
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "YourOwnVision",
        "environment": CONFIG.environment,
    }


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.post(
    CONFIG.telegram_webhook_path,
    response_class=JSONResponse,
)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
) -> dict[str, Any]:

    logger.info(
        "Telegram webhook request received: path=%s cf_ray=%s",
        request.url.path,
        request.headers.get("cf-ray", "<none>"),
    )

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    if (
        x_telegram_bot_api_secret_token
        != CONFIG.telegram_webhook_secret
    ):
        logger.warning(
            "Rejected Telegram webhook: invalid secret."
        )

        raise HTTPException(
            status_code=403,
            detail="Forbidden",
        )

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    try:
        payload = await request.json()

    except Exception as exc:
        logger.warning(
            "Rejected Telegram webhook: invalid JSON: %s",
            exc,
        )

        raise HTTPException(
            status_code=400,
            detail="Invalid JSON.",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail="Telegram update must be a JSON object.",
        )

    # --------------------------------------------------------
    # TELEGRAM UPDATE
    # --------------------------------------------------------

    try:
        update = Update.de_json(
            payload,
            telegram_application.bot,
        )

    except Exception as exc:
        logger.exception(
            "Could not deserialize Telegram update."
        )

        raise HTTPException(
            status_code=400,
            detail="Invalid Telegram update.",
        ) from exc

    if update is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid Telegram update.",
        )

    logger.info(
        "Telegram update received: update_id=%s",
        update.update_id,
    )

    # --------------------------------------------------------
    # PROCESS
    # --------------------------------------------------------

    try:
        await process_update(
            telegram_application,
            update,
        )

    except Exception as exc:
        logger.exception(
            "Telegram update processing failed: "
            "update_id=%s",
            update.update_id,
        )

        raise HTTPException(
            status_code=500,
            detail="Update processing failed.",
        ) from exc

    return {
        "ok": True,
    }


# ============================================================
# TELEGRAM WEBHOOK DIAGNOSTICS
# ============================================================

@app.get(
    "/telegram/status",
    response_class=JSONResponse,
)
async def telegram_status() -> dict[str, Any]:

    info = await telegram_application.bot.get_webhook_info()

    return {
        "url": info.url,
        "pending_update_count": info.pending_update_count,
        "has_custom_certificate": info.has_custom_certificate,
        "last_error_date": info.last_error_date,
        "last_error_message": info.last_error_message,
        "allowed_updates": info.allowed_updates,
        "max_connections": info.max_connections,
    }


# ============================================================
# ROOT
# ============================================================

@app.get(
    "/",
    response_class=JSONResponse,
)
async def root() -> dict[str, Any]:
    return {
        "service": "YourOwnVision",
        "status": "running",
        "telegram": "webhook",
        "health": "/health",
        "telegram_status": "/telegram/status",
    }


# ============================================================
# ENTRY POINT
# ============================================================

def run() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=CONFIG.host,
        port=CONFIG.port,
        log_level=CONFIG.log_level.lower(),
    )


if __name__ == "__main__":
    run()
