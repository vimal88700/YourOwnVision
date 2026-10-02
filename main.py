from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import Update

from bot import (
    create_application,
    game_service,
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

logger = logging.getLogger(
    "YourOwnVision.main"
)


# ============================================================
# GLOBAL RUNTIME STATE
# ============================================================

telegram_application = create_application()

recovery_task: asyncio.Task[None] | None = None

shutdown_event = asyncio.Event()


# ============================================================
# CONSTANTS
# ============================================================

RECOVERY_INTERVAL_SECONDS = 10

HEALTH_STATUS = "ok"


# ============================================================
# WEBHOOK URL
# ============================================================

def webhook_url() -> str:
    """
    Return the complete public Telegram webhook URL.

    Example:

        https://example.onrender.com/telegram/webhook
    """

    base = (
        CONFIG.public_base_url
        .rstrip("/")
    )

    path = (
        CONFIG.telegram_webhook_path
        if CONFIG.telegram_webhook_path.startswith("/")
        else f"/{CONFIG.telegram_webhook_path}"
    )

    return f"{base}{path}"


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

async def configure_telegram_webhook() -> None:
    """
    Register the Render HTTPS endpoint with Telegram.

    This replaces Telegram polling/getUpdates completely.

    Telegram will POST updates to:

        /telegram/webhook
    """

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

    logger.info(
        "Telegram webhook registered successfully."
    )


async def remove_telegram_webhook() -> None:
    """
    Remove the webhook during application shutdown.

    Pending updates are deliberately NOT discarded.
    """

    try:
        await telegram_application.bot.delete_webhook(
            drop_pending_updates=False,
        )

        logger.info(
            "Telegram webhook removed."
        )

    except Exception:
        logger.exception(
            "Failed to remove Telegram webhook."
        )


# ============================================================
# DATABASE RECOVERY
# ============================================================

async def recovery_loop() -> None:
    """
    Recover gameplay using persistent Supabase deadlines.

    IMPORTANT:

    This loop is only a convenience/recovery worker.

    It is NOT the source of truth for timers.

    The authoritative deadline is stored in Supabase.

    Therefore:

        Render restart
            ↓
        process starts again
            ↓
        database is inspected
            ↓
        expired rounds are resolved

    No game is lost simply because this process stopped.
    """

    logger.info(
        "Persistent game recovery loop started."
    )

    # Immediate recovery after startup.
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
async def lifespan(
    app: FastAPI,
):
    """
    Application lifecycle.

    Startup order:

        1. Initialize PTB
        2. Start PTB application
        3. Register Telegram webhook
        4. Recover persistent game state
        5. Start recovery loop

    Shutdown order:

        1. Stop recovery loop
        2. Remove Telegram webhook
        3. Stop PTB
        4. Shutdown PTB
    """

    global recovery_task

    logger.info(
        "Starting YourOwnVision application..."
    )

    # --------------------------------------------------------
    # TELEGRAM APPLICATION INITIALIZATION
    # --------------------------------------------------------

    await telegram_application.initialize()

    await telegram_application.start()

    logger.info(
        "Telegram application initialized."
    )

    # --------------------------------------------------------
    # WEBHOOK REGISTRATION
    # --------------------------------------------------------

    await configure_telegram_webhook()

    # --------------------------------------------------------
    # DATABASE RECOVERY
    # --------------------------------------------------------

    try:
        await recover_active_games()

    except Exception:
        logger.exception(
            "Startup game recovery failed."
        )

    # --------------------------------------------------------
    # BACKGROUND RECOVERY
    # --------------------------------------------------------

    shutdown_event.clear()

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

        # ----------------------------------------------------
        # STOP RECOVERY
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # TELEGRAM WEBHOOK
        # ----------------------------------------------------

        await remove_telegram_webhook()

        # ----------------------------------------------------
        # TELEGRAM APPLICATION
        # ----------------------------------------------------

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
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="YourOwnVision",
    description=(
        "WHAT HAPPENS? deterministic Telegram story game."
    ),
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
    """
    Render health endpoint.

    This intentionally does not expose:

    - Telegram token
    - Gemini key
    - Supabase key
    - internal database credentials
    """

    return {
        "status": HEALTH_STATUS,
        "service": "YourOwnVision",
        "environment": CONFIG.environment,
    }


# ============================================================
# TELEGRAM WEBHOOK ENDPOINT
# ============================================================

@app.post(
    CONFIG.telegram_webhook_path,
    response_class=JSONResponse,
)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None,
    ),
) -> dict[str, Any]:
    """
    Receive Telegram webhook updates.

    Telegram authenticates webhook requests using the secret
    token configured in set_webhook().
    """

    # --------------------------------------------------------
    # AUTHENTICATE TELEGRAM
    # --------------------------------------------------------

    if (
        x_telegram_bot_api_secret_token
        != CONFIG.telegram_webhook_secret
    ):
        logger.warning(
            "Rejected Telegram webhook request with invalid "
            "secret token."
        )

        raise HTTPException(
            status_code=403,
            detail="Forbidden",
        )

    # --------------------------------------------------------
    # READ JSON
    # --------------------------------------------------------

    try:
        payload = await request.json()

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON.",
        )

    if not isinstance(
        payload,
        dict,
    ):
        raise HTTPException(
            status_code=400,
            detail="Telegram update must be a JSON object.",
        )

    # --------------------------------------------------------
    # CONVERT TO TELEGRAM UPDATE
    # --------------------------------------------------------

    try:
        update = Update.de_json(
            payload,
            telegram_application.bot,
        )

    except Exception:
        logger.exception(
            "Could not deserialize Telegram update."
        )

        raise HTTPException(
            status_code=400,
            detail="Invalid Telegram update.",
        )

    if update is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid Telegram update.",
        )

    # --------------------------------------------------------
    # PROCESS UPDATE
    # --------------------------------------------------------
    #
    # Telegram expects a fast successful HTTP response.
    #
    # python-telegram-bot handles the actual update through
    # Application.process_update().
    #

    try:
        await process_update(
            telegram_application,
            update,
        )

    except Exception:
        logger.exception(
            "Telegram update processing failed."
        )

        # Return 200 only when the update was accepted into
        # the application processing path. Here process_update()
        # has already been awaited, so a failure is a real
        # processing failure.
        #
        # Returning 500 lets Telegram retry the webhook update.
        raise HTTPException(
            status_code=500,
            detail="Update processing failed.",
        )

    return {
        "ok": True,
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
        "health": "/health",
    }


# ============================================================
# LOCAL ENTRY POINT
# ============================================================

def run() -> None:
    """
    Start the FastAPI application with Uvicorn.

    Render supplies PORT automatically.

    Required binding:

        0.0.0.0:$PORT
    """

    import uvicorn

    uvicorn.run(
        app,
        host=CONFIG.host,
        port=CONFIG.port,
        log_level=CONFIG.log_level.lower(),
    )


if __name__ == "__main__":
    run()
