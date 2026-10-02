import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def required(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value


def parse_ids(value: str | None) -> set[int]:
    if not value:
        return set()

    result = set()

    for item in value.split(","):
        item = item.strip()

        if item:
            try:
                result.add(int(item))
            except ValueError:
                pass

    return result


@dataclass(frozen=True)
class Config:
    telegram_token: str
    gemini_api_key: str
    gemini_model: str

    supabase_url: str
    supabase_service_key: str

    admin_user_ids: set[int]

    game_join_seconds: int
    default_decision_seconds: int


CONFIG = Config(
    telegram_token=required("TELEGRAM_BOT_TOKEN"),
    gemini_api_key=required("GEMINI_API_KEY"),
    gemini_model=os.getenv(
        "GEMINI_MODEL",
        "gemini-3.8-flash",
    ),
    supabase_url=required("SUPABASE_URL").rstrip("/"),
    supabase_service_key=required("SUPABASE_SERVICE_ROLE_KEY"),
    admin_user_ids=parse_ids(os.getenv("ADMIN_USER_IDS")),
    game_join_seconds=int(
        os.getenv("GAME_JOIN_SECONDS", "45")
    ),
    default_decision_seconds=int(
        os.getenv("DEFAULT_DECISION_SECONDS", "35")
    ),
)
