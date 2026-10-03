from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def optional(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


@dataclass(frozen=True)
class Config:
    telegram_token: str
    telegram_webhook_secret: str
    public_base_url: str
    telegram_webhook_path: str
    supabase_url: str
    supabase_service_key: str
    port: int
    host: str
    environment: str
    log_level: str
    mini_app_short_name: str
    bot_creator_id: int


CONFIG = Config(
    telegram_token=required("TELEGRAM_BOT_TOKEN"),
    telegram_webhook_secret=required("TELEGRAM_WEBHOOK_SECRET"),
    public_base_url=required("PUBLIC_BASE_URL").rstrip("/"),
    telegram_webhook_path=optional("TELEGRAM_WEBHOOK_PATH", "/telegram/webhook"),
    supabase_url=required("SUPABASE_URL"),
    supabase_service_key=required("SUPABASE_SERVICE_ROLE_KEY"),
    port=int(os.getenv("PORT", "10000")),
    host=optional("HOST", "0.0.0.0"),
    environment=optional("ENVIRONMENT", "production"),
    log_level=optional("LOG_LEVEL", "INFO"),
    mini_app_short_name=optional("MINI_APP_SHORT_NAME", "what_happens"),
    bot_creator_id=int(os.getenv("BOT_CREATOR_ID", "0")),
)

if not CONFIG.public_base_url.startswith(("http://", "https://")):
    raise RuntimeError("PUBLIC_BASE_URL must start with http:// or https://")
if not CONFIG.telegram_webhook_path.startswith("/"):
    raise RuntimeError("TELEGRAM_WEBHOOK_PATH must start with '/'")
