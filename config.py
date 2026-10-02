from __future__ import annotations

import os
from dataclasses import dataclass
from typing import FrozenSet

from dotenv import load_dotenv


# Load .env for local development.
#
# On Render, the values should come from Render Environment
# Variables. Never commit a real .env file.
load_dotenv()


def required(name: str) -> str:
    """
    Return a required environment variable.

    Fail immediately at startup instead of allowing the application
    to start with a broken configuration.
    """
    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(
            f"Missing required environment variable: {name}"
        )

    return value.strip()


def optional(
    name: str,
    default: str,
) -> str:
    """
    Return an optional environment variable.
    """
    value = os.getenv(name)

    if value is None or not value.strip():
        return default

    return value.strip()


def parse_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """
    Parse an integer environment variable safely.
    """
    raw = os.getenv(name)

    if raw is None or not raw.strip():
        value = default

    else:
        try:
            value = int(raw.strip())

        except ValueError as exc:
            raise RuntimeError(
                f"{name} must be an integer."
            ) from exc

    if minimum is not None and value < minimum:
        raise RuntimeError(
            f"{name} must be >= {minimum}."
        )

    if maximum is not None and value > maximum:
        raise RuntimeError(
            f"{name} must be <= {maximum}."
        )

    return value


def parse_ids(
    value: str | None,
) -> FrozenSet[int]:
    """
    Parse comma-separated Telegram user IDs.

    Example:

        ADMIN_USER_IDS=123456789,987654321
    """
    if not value:
        return frozenset()

    result: set[int] = set()

    for item in value.split(","):
        item = item.strip()

        if not item:
            continue

        try:
            result.add(int(item))

        except ValueError:
            raise RuntimeError(
                "ADMIN_USER_IDS contains an invalid Telegram user ID: "
                f"{item!r}"
            )

    return frozenset(result)


def normalize_base_url(
    value: str,
) -> str:
    """
    Normalize a public/base URL.

    The application expects a URL such as:

        https://yourownvision.onrender.com
    """
    value = value.strip().rstrip("/")

    if not value.startswith(
        ("http://", "https://")
    ):
        raise RuntimeError(
            "PUBLIC_BASE_URL must start with "
            "http:// or https://"
        )

    return value


@dataclass(frozen=True)
class Config:
    # ------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------

    telegram_token: str

    # Secret used by Telegram when sending webhook requests.
    #
    # This is NOT the bot token.
    telegram_webhook_secret: str

    # Public HTTPS address of the Render service.
    #
    # Example:
    # https://yourownvision.onrender.com
    public_base_url: str

    # Webhook path.
    #
    # Keep this obscure enough that random traffic is less likely
    # to hit it, while still treating the secret header as the
    # actual authentication mechanism.
    telegram_webhook_path: str

    # ------------------------------------------------------------
    # Gemini
    # ------------------------------------------------------------

    gemini_api_key: str

    gemini_model: str

    # ------------------------------------------------------------
    # Supabase
    # ------------------------------------------------------------

    supabase_url: str

    supabase_service_key: str

    # ------------------------------------------------------------
    # Administration
    # ------------------------------------------------------------

    admin_user_ids: FrozenSet[int]

    # ------------------------------------------------------------
    # HTTP / Render
    # ------------------------------------------------------------

    port: int

    host: str

    # ------------------------------------------------------------
    # Game configuration
    # ------------------------------------------------------------

    game_join_seconds: int

    default_decision_seconds: int

    minimum_decision_seconds: int

    maximum_decision_seconds: int

    maximum_players: int

    minimum_players: int

    maximum_story_generation_attempts: int

    # ------------------------------------------------------------
    # Runtime
    # ------------------------------------------------------------

    environment: str

    log_level: str


CONFIG = Config(
    # ============================================================
    # TELEGRAM
    # ============================================================

    telegram_token=required(
        "TELEGRAM_BOT_TOKEN"
    ),

    telegram_webhook_secret=required(
        "TELEGRAM_WEBHOOK_SECRET"
    ),

    public_base_url=normalize_base_url(
        required("PUBLIC_BASE_URL")
    ),

    telegram_webhook_path=optional(
        "TELEGRAM_WEBHOOK_PATH",
        "/telegram/webhook",
    ),

    # ============================================================
    # GEMINI
    # ============================================================

    gemini_api_key=required(
        "GEMINI_API_KEY"
    ),

    # Keep the model configurable.
    #
    # Do not hard-code a model name in game logic.
    #
    # If your Google AI account uses another current model,
    # change GEMINI_MODEL in Render instead of changing code.
    gemini_model=optional(
        "GEMINI_MODEL",
        "gemini-2.5-flash",
    ),

    # ============================================================
    # SUPABASE
    # ============================================================

    supabase_url=normalize_base_url(
        required("SUPABASE_URL")
    ),

    supabase_service_key=required(
        "SUPABASE_SERVICE_ROLE_KEY"
    ),

    # ============================================================
    # ADMIN
    # ============================================================

    admin_user_ids=parse_ids(
        os.getenv("ADMIN_USER_IDS")
    ),

    # ============================================================
    # RENDER / HTTP
    # ============================================================

    # Render supplies PORT automatically.
    #
    # Local development can override it.
    port=parse_int(
        "PORT",
        10000,
        minimum=1,
        maximum=65535,
    ),

    host=optional(
        "HOST",
        "0.0.0.0",
    ),

    # ============================================================
    # GAME
    # ============================================================

    game_join_seconds=parse_int(
        "GAME_JOIN_SECONDS",
        45,
        minimum=10,
        maximum=3600,
    ),

    default_decision_seconds=parse_int(
        "DEFAULT_DECISION_SECONDS",
        35,
        minimum=15,
        maximum=900,
    ),

    minimum_decision_seconds=parse_int(
        "MINIMUM_DECISION_SECONDS",
        15,
        minimum=5,
        maximum=900,
    ),

    maximum_decision_seconds=parse_int(
        "MAXIMUM_DECISION_SECONDS",
        900,
        minimum=15,
        maximum=3600,
    ),

    # The current story generator creates four roles.
    #
    # We will make this dynamic in the story generator later,
    # but keep a safe upper bound here.
    maximum_players=parse_int(
        "MAXIMUM_PLAYERS",
        4,
        minimum=1,
        maximum=50,
    ),

    minimum_players=parse_int(
        "MINIMUM_PLAYERS",
        1,
        minimum=1,
        maximum=50,
    ),

    maximum_story_generation_attempts=parse_int(
        "MAX_STORY_GENERATION_ATTEMPTS",
        5,
        minimum=1,
        maximum=20,
    ),

    # ============================================================
    # RUNTIME
    # ============================================================

    environment=optional(
        "ENVIRONMENT",
        "production",
    ),

    log_level=optional(
        "LOG_LEVEL",
        "INFO",
    ),
)


def validate_config() -> None:
    """
    Validate cross-field configuration relationships.

    This is intentionally separate from Config construction so
    the values can be loaded first and then checked together.
    """

    if (
        CONFIG.minimum_players
        > CONFIG.maximum_players
    ):
        raise RuntimeError(
            "MINIMUM_PLAYERS cannot be greater than "
            "MAXIMUM_PLAYERS."
        )

    if (
        CONFIG.minimum_decision_seconds
        > CONFIG.maximum_decision_seconds
    ):
        raise RuntimeError(
            "MINIMUM_DECISION_SECONDS cannot be greater than "
            "MAXIMUM_DECISION_SECONDS."
        )

    if not CONFIG.telegram_webhook_path.startswith(
        "/"
    ):
        raise RuntimeError(
            "TELEGRAM_WEBHOOK_PATH must start with '/'."
        )


validate_config()
