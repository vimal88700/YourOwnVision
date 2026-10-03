from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from database import DatabaseConflict
from config import CONFIG
from world_service import WorldService

router = APIRouter(prefix="/api/miniapp", tags=["miniapp"])
world_service: WorldService | None = None
STATIC_DIR = Path(__file__).resolve().parent / "miniapp"


class ChoiceRequest(BaseModel):
    game_id: str
    choice_id: str = Field(min_length=1, max_length=32)
    expected_version: int = Field(ge=1)


class CreateWorldRequest(BaseModel):
    title: str | None = Field(default=None, max_length=80)


class GameRequest(BaseModel):
    game_id: str = Field(min_length=1, max_length=64)


def configure_service(db: Database) -> None:
    global world_service
    world_service = WorldService(db)


def service() -> WorldService:
    if world_service is None:
        raise RuntimeError("World service is not configured")
    return world_service


def validate_init_data(init_data: str) -> dict[str, Any]:
    if not init_data:
        raise HTTPException(status_code=401, detail="Open this Mini App from Telegram.")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    supplied_hash = pairs.pop("hash", "")
    if not supplied_hash:
        raise HTTPException(status_code=401, detail="Invalid Telegram session.")
    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", CONFIG.telegram_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, supplied_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram session signature.")
    try:
        auth_date = int(pairs.get("auth_date", "0") or 0)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Telegram session.")
    if auth_date <= 0 or int(time.time()) - auth_date > 86400:
        raise HTTPException(status_code=401, detail="Telegram session expired. Reopen the Mini App.")
    try:
        user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=401, detail="Invalid Telegram user payload.") from exc
    if not user.get("id"):
        raise HTTPException(status_code=401, detail="Telegram user is missing.")
    chat = None
    if pairs.get("chat"):
        try:
            chat = json.loads(pairs["chat"])
        except json.JSONDecodeError:
            chat = None
    return {"user": user, "start_param": pairs.get("start_param", ""), "chat": chat}


async def auth_user(x_telegram_init_data: str | None, x_miniapp_start_param: str | None = None) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    data = validate_init_data(x_telegram_init_data or "")
    return data["user"], str(x_miniapp_start_param or data.get("start_param") or ""), data.get("chat")


def miniapp_url(request: Request, start_param: str = "") -> str:
    base = str(request.base_url).rstrip("/") + "/app"
    bot_username = getattr(request.app.state, "bot_username", "")
    if CONFIG.mini_app_short_name and bot_username:
        suffix = f"?startapp={start_param}" if start_param else ""
        return f"https://t.me/{bot_username}/{CONFIG.mini_app_short_name}{suffix}"
    return base + (f"?startapp={start_param}" if start_param else "")


async def resolve_game(start_param: str, chat: dict[str, Any] | None) -> dict[str, Any] | None:
    svc = service()
    if start_param.startswith("game_"):
        try:
            return await svc.get_world(start_param[5:])
        except Exception:
            return None
    if start_param.startswith("invite_"):
        try:
            token = start_param[7:]
            rows = await svc.db.request("GET", "world_invites", params={"token": f"eq.{token}", "active": "eq.true", "limit": "1"})
            return await svc.get_world(rows[0]["game_id"]) if rows else None
        except Exception:
            return None
    if chat and chat.get("id"):
        try:
            return await svc.get_group_world(int(chat["id"]))
        except Exception:
            return None
    return None


@router.get("/bootstrap")
async def bootstrap(
    request: Request,
    x_telegram_init_data: str | None = Header(default=None),
    x_miniapp_start_param: str | None = Header(default=None),
) -> dict[str, Any]:
    user, start_param, chat = await auth_user(x_telegram_init_data, x_miniapp_start_param)
    user_id = int(user["id"])
    result: dict[str, Any] = {"user": user, "start_param": start_param, "game": None, "invite": None, "is_admin": False}
    svc = service()

    game = await resolve_game(start_param, chat)
    if start_param.startswith("invite_"):
        token = start_param[7:]
        try:
            joined = await svc.consume_invite(token, user_id, str(user.get("username") or ""), str(user.get("first_name") or "Player"))
            game = joined["game"]
            result["invite"] = "joined"
        except Exception as exc:
            result["invite"] = str(exc)
    if game:
        if game.get("status") == "waiting" and game.get("join_deadline"):
            try:
                deadline = datetime.fromisoformat(str(game["join_deadline"]).replace("Z", "+00:00"))
                if svc.now() >= deadline and await svc.players(str(game["id"])):
                    game = await svc.start(str(game["id"]))
            except Exception:
                pass
        player = await svc.get_player(str(game["id"]), user_id)
        result["game"] = await svc.snapshot(str(game["id"]), user_id, include_unjoined=True)
        if game.get("chat_id"):
            bot = getattr(request.app.state, "telegram_bot", None)
            if bot:
                try:
                    member = await bot.get_chat_member(int(game["chat_id"]), user_id)
                    result["is_admin"] = member.status in {"creator", "administrator"}
                except Exception:
                    result["is_admin"] = int(game.get("creator_id") or 0) == user_id
        if player:
            result["game"]["player"] = result["game"].get("player")
    return result


@router.post("/worlds")
async def create_world(
    body: CreateWorldRequest,
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    user, _, chat = await auth_user(x_telegram_init_data)
    chat_id = int(chat["id"]) if chat and chat.get("id") else None
    svc = service()
    if chat_id:
        existing = await svc.get_group_world(chat_id)
        if existing:
            return {"game": await svc.snapshot(str(existing["id"]), int(user["id"]), include_unjoined=True)}
    game = await svc.create_world(creator_id=int(user["id"]), chat_id=chat_id, title=body.title)
    return {"game": await svc.snapshot(str(game["id"]), int(user["id"]), include_unjoined=True)}


@router.post("/join")
async def join_world(
    body: GameRequest,
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    svc = service()
    player = await svc.join(body.game_id, int(user["id"]), str(user.get("username") or ""), str(user.get("first_name") or "Player"))
    return {"player": player, "snapshot": await svc.snapshot(body.game_id, int(user["id"]))}


@router.get("/worlds/{game_id}")
async def get_world(
    game_id: str,
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    return await service().snapshot(game_id, int(user["id"]), include_unjoined=True)


@router.post("/choice")
async def choose(
    body: ChoiceRequest,
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    try:
        return await service().choose(body.game_id, int(user["id"]), body.choice_id, body.expected_version)
    except DatabaseConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/invite")
async def invite(
    request: Request,
    body: GameRequest,
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    svc = service()
    player = await svc.get_player(body.game_id, int(user["id"]))
    if not player:
        raise HTTPException(status_code=403, detail="Join the world before inviting friends.")
    game = await svc.get_world(body.game_id)
    if not (game.get("settings") or {}).get("allow_external_invites", True):
        raise HTTPException(status_code=403, detail="External invites are disabled for this world.")
    token = await svc.create_invite(body.game_id, int(user["id"]))
    bot_username = getattr(request.app.state, "bot_username", "")
    if bot_username:
        link = f"https://t.me/{bot_username}?startapp=invite_{token}"
    else:
        link = f"{request.base_url}app?startapp=invite_{token}"
    return {"token": token, "link": link}


@router.post("/settings/invites")
async def set_invites(
    request: Request,
    body: GameRequest,
    x_telegram_init_data: str | None = Header(default=None),
) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    svc = service()
    game = await svc.get_world(body.game_id)
    if int(game.get("creator_id") or 0) != int(user["id"]):
        bot = getattr(request.app.state, "telegram_bot", None)
        if not bot or not game.get("chat_id"):
            raise HTTPException(status_code=403, detail="Only the group owner/admin can change settings.")
        try:
            member = await bot.get_chat_member(int(game["chat_id"]), int(user["id"]))
            if member.status not in {"creator", "administrator"}:
                raise HTTPException(status_code=403, detail="Only the group owner/admin can change settings.")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=403, detail="Could not verify group admin status.") from exc
    settings = dict(game.get("settings") or {})
    settings["allow_external_invites"] = not bool(settings.get("allow_external_invites", True))
    rows = await svc.db.request("PATCH", "world_games", params={"id": f"eq.{body.game_id}"}, json={"settings": settings}, prefer="return=representation")
    return {"settings": (rows[0].get("settings") if rows else settings)}


def mount_static(app: Any) -> None:
    from fastapi.staticfiles import StaticFiles
    assets = STATIC_DIR / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    app.mount("/app/assets", StaticFiles(directory=str(assets)), name="miniapp-assets")

    @app.get("/app", include_in_schema=False)
    async def miniapp_index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))
