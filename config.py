from __future__ import annotations

import os
from dataclasses import dataclass
from typing import FrozenSet

from dotenv import load_dotenv

load_dotenv()


def required(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def optional(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def parse_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = default if not raw or not raw.strip() else int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if minimum is not None and value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}.")
    if maximum is not None and value > maximum:
        raise RuntimeError(f"{name} must be <= {maximum}.")
    return value


def parse_ids(value: str | None) -> FrozenSet[int]:
    if not value:
        return frozenset()
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item:
            try:
                result.add(int(item))
            except ValueError as exc:
                raise RuntimeError(f"ADMIN_USER_IDS contains invalid ID: {item!r}") from exc
    return frozenset(result)


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise RuntimeError("PUBLIC_BASE_URL must start with http:// or https://")
    return value


@dataclass(frozen=True)
class Config:
    telegram_token: str
    telegram_webhook_secret: str
    public_base_url: str
    telegram_webhook_path: str
    gemini_api_key: str
    gemini_model: str
    supabase_url: str
    supabase_service_key: str
    admin_user_ids: FrozenSet[int]
    port: int
    host: str
    game_join_seconds: int
    default_decision_seconds: int
    minimum_decision_seconds: int
    maximum_decision_seconds: int
    maximum_players: int
    minimum_players: int
    maximum_story_generation_attempts: int
    environment: str
    log_level: str
    mini_app_short_name: str


CONFIG = Config(
    telegram_token=required("TELEGRAM_BOT_TOKEN"),
    telegram_webhook_secret=required("TELEGRAM_WEBHOOK_SECRET"),
    public_base_url=normalize_base_url(required("PUBLIC_BASE_URL")),
    telegram_webhook_path=optional("TELEGRAM_WEBHOOK_PATH", "/telegram/webhook"),
    gemini_api_key=required("GEMINI_API_KEY"),
    gemini_model=optional("GEMINI_MODEL", "gemini-3.8-flash"),
    supabase_url=normalize_base_url(required("SUPABASE_URL")),
    supabase_service_key=required("SUPABASE_SERVICE_ROLE_KEY"),
    admin_user_ids=parse_ids(os.getenv("ADMIN_USER_IDS")),
    port=parse_int("PORT", 10000, minimum=1, maximum=65535),
    host=optional("HOST", "0.0.0.0"),
    game_join_seconds=parse_int("GAME_JOIN_SECONDS", 45, minimum=10, maximum=3600),
    default_decision_seconds=parse_int("DEFAULT_DECISION_SECONDS", 35, minimum=15, maximum=900),
    minimum_decision_seconds=parse_int("MINIMUM_DECISION_SECONDS", 15, minimum=5, maximum=900),
    maximum_decision_seconds=parse_int("MAXIMUM_DECISION_SECONDS", 900, minimum=15, maximum=3600),
    maximum_players=parse_int("MAXIMUM_PLAYERS", 4, minimum=1, maximum=50),
    minimum_players=parse_int("MINIMUM_PLAYERS", 1, minimum=1, maximum=50),
    maximum_story_generation_attempts=parse_int("MAX_STORY_GENERATION_ATTEMPTS", 5, minimum=1, maximum=20),
    environment=optional("ENVIRONMENT", "production"),
    log_level=optional("LOG_LEVEL", "INFO"),
    mini_app_short_name=optional("MINI_APP_SHORT_NAME", "what_happens"),
)


def validate_config() -> None:
    if CONFIG.minimum_players > CONFIG.maximum_players:
        raise RuntimeError("MINIMUM_PLAYERS cannot be greater than MAXIMUM_PLAYERS.")
    if CONFIG.minimum_decision_seconds > CONFIG.maximum_decision_seconds:
        raise RuntimeError("MINIMUM_DECISION_SECONDS cannot be greater than MAXIMUM_DECISION_SECONDS.")
    if not CONFIG.telegram_webhook_path.startswith("/"):
        raise RuntimeError("TELEGRAM_WEBHOOK_PATH must start with '/'.")


validate_config()
