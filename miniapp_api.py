from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from bot import db
from config import CONFIG
from world_service import WorldService


router = APIRouter(prefix="/api/miniapp", tags=["miniapp"])
world_service = WorldService(db)
STATIC_DIR = Path(__file__).resolve().parent / "miniapp"


class ChoiceRequest(BaseModel):
    game_id: str
    choice_id: str = Field(min_length=1, max_length=32)
    expected_version: int = Field(ge=1)


class CreateWorldRequest(BaseModel):
    title: str | None = Field(default=None, max_length=80)


class InviteRequest(BaseModel):
    game_id: str


def validate_init_data(init_data: str) -> dict[str, Any]:
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram session is required.")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    supplied_hash = pairs.pop("hash", "")
    if not supplied_hash:
        raise HTTPException(status_code=401, detail="Invalid Telegram session.")
    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", CONFIG.telegram_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, supplied_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram session signature.")
    auth_date = int(pairs.get("auth_date", "0") or 0)
    if auth_date <= 0 or abs(int(time.time()) - auth_date) > 86400:
        raise HTTPException(status_code=401, detail="Telegram session expired. Reopen the Mini App.")
    try:
        user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=401, detail="Invalid Telegram user payload.") from exc
    if not user.get("id"):
        raise HTTPException(status_code=401, detail="Telegram user is missing.")
    return {"user": user, "start_param": pairs.get("start_param", "")}


async def auth_user(x_telegram_init_data: str | None) -> tuple[dict[str, Any], str]:
    data = validate_init_data(x_telegram_init_data or "")
    return data["user"], str(data.get("start_param") or "")


def miniapp_url(request: Request, start_param: str = "") -> str:
    base = str(request.base_url).rstrip("/") + "/app"
    if CONFIG.mini_app_short_name:
        bot_username = request.app.state.bot_username
        if bot_username:
            suffix = f"?startapp={start_param}" if start_param else ""
            return f"https://t.me/{bot_username}/{CONFIG.mini_app_short_name}{suffix}"
    return base + (f"?startapp={start_param}" if start_param else "")


@router.get("/bootstrap")
async def bootstrap(
    request: Request,
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    user, start_param = await auth_user(x_telegram_init_data)
    user_id = int(user["id"])
    result: dict[str, Any] = {"user": user, "start_param": start_param, "game": None, "invite": None}
    if start_param.startswith("game_"):
        game_id = start_param[5:]
        try:
            game = await world_service.get_world(game_id)
            player = await world_service.get_player(game_id, user_id)
            if player:
                result["game"] = await world_service.snapshot(game_id, user_id)
            else:
                result["game"] = {
                    "game": {"id": game["id"], "title": game["title"], "status": game["status"], "join_deadline": game.get("join_deadline"), "version": game.get("version", 1)},
                    "players": await world_service.get_players(game_id),
                    "player": None,
                }
        except Exception:
            result["game"] = None
    elif start_param.startswith("invite_"):
        token = start_param[7:]
        try:
            joined = await world_service.consume_invite(
                token, user_id, str(user.get("username") or ""), str(user.get("first_name") or "Player")
            )
            result["game"] = await world_service.snapshot(str(joined["game"]["id"]), user_id)
            result["invite"] = "joined"
        except Exception as exc:
            result["invite"] = str(exc)
    return result


@router.post("/worlds")
async def create_world(
    request: Request,
    body: CreateWorldRequest,
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    user, _ = await auth_user(x_telegram_init_data)
    game = await world_service.create_world(creator_id=int(user["id"]), title=body.title)
    await world_service.join(str(game["id"]), int(user["id"]), str(user.get("username") or ""), str(user.get("first_name") or "Player"))
    return {"game": await world_service.snapshot(str(game["id"]), int(user["id"]))}


@router.post("/join")
async def join_world(
    body: dict[str, str],
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    user, _ = await auth_user(x_telegram_init_data)
    game_id = str(body.get("game_id") or "")
    if not game_id:
        raise HTTPException(status_code=400, detail="game_id is required")
    player = await world_service.join(game_id, int(user["id"]), str(user.get("username") or ""), str(user.get("first_name") or "Player"))
    return {"player": player, "snapshot": await world_service.snapshot(game_id, int(user["id"]))}


@router.get("/worlds/{game_id}")
async def get_world(
    game_id: str,
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    user, _ = await auth_user(x_telegram_init_data)
    return await world_service.snapshot(game_id, int(user["id"]))


@router.post("/choice")
async def choose(
    body: ChoiceRequest,
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    user, _ = await auth_user(x_telegram_init_data)
    try:
        return await world_service.choose(body.game_id, int(user["id"]), body.choice_id, body.expected_version)
    except Exception as exc:
        message = str(exc)
        status = 409 if "changed" in message.lower() or "conflict" in message.lower() else 400
        raise HTTPException(status_code=status, detail=message) from exc


@router.post("/invite")
async def invite(
    request: Request,
    body: InviteRequest,
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    user, _ = await auth_user(x_telegram_init_data)
    player = await world_service.get_player(body.game_id, int(user["id"]))
    if not player:
        raise HTTPException(status_code=403, detail="Join the world before inviting friends.")
    token = await world_service.create_invite(body.game_id, int(user["id"]))
    bot_username = getattr(request.app.state, "bot_username", None)
    return {"token": token, "start_param": f"invite_{token}", "bot_username": bot_username}


def mount_static(app: Any) -> None:
    from fastapi.staticfiles import StaticFiles
    app.mount("/app/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="miniapp-assets")

    @app.get("/app", include_in_schema=False)
    async def miniapp_index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))
