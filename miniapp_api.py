from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from config import CONFIG
from database import DatabaseConflict, DatabaseError
from world_service import WorldService

router = APIRouter(prefix="/api/miniapp", tags=["miniapp"])
world_service: WorldService | None = None
STATIC_DIR = Path(__file__).resolve().parent / "miniapp"


class ChoiceRequest(BaseModel):
    game_id: str = Field(min_length=1, max_length=64)
    choice_id: str = Field(min_length=1, max_length=32)


class GameRequest(BaseModel):
    game_id: str = Field(min_length=1, max_length=64)


class OperatorRequest(GameRequest):
    user_id: int
    action: str = Field(pattern="^(add|remove)$")


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
    pairs_list = parse_qsl(init_data, keep_blank_values=True)
    pairs = dict(pairs_list)
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
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Telegram session.") from exc
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


async def auth_user(x_telegram_init_data: str | None, x_miniapp_start_param: str | None = None):
    data = validate_init_data(x_telegram_init_data or "")
    start = str(x_miniapp_start_param or data.get("start_param") or "")
    return data["user"], start, data.get("chat")


def app_deep_link(request: Request, start_param: str = "") -> str:
    bot_username = getattr(request.app.state, "bot_username", "")
    if bot_username and CONFIG.mini_app_short_name:
        suffix = f"?startapp={start_param}" if start_param else ""
        return f"https://t.me/{bot_username}/{CONFIG.mini_app_short_name}{suffix}"
    base = str(request.base_url).rstrip("/") + "/app"
    return f"{base}?startapp={start_param}" if start_param else base


async def maintenance_response() -> dict[str, Any] | None:
    row = await service().maintenance()
    if not row or not row.get("maintenance_until"):
        return None
    try:
        until = datetime.fromisoformat(str(row["maintenance_until"]).replace("Z", "+00:00"))
        if datetime.now(timezone.utc) >= until:
            return None
        return {"active": True, "until": until.isoformat()}
    except Exception:
        return None


async def resolve_game(start_param: str, chat: dict[str, Any] | None):
    svc = service()
    if start_param.startswith("w_"):
        try:
            return await svc.get_world(start_param[2:])
        except Exception:
            return None
    if start_param.startswith("invite_"):
        token = start_param[7:]
        rows = await svc.db.request("GET", "world_invites", params={"token": f"eq.{token}", "active": "eq.true", "limit": "1"})
        if rows:
            try:
                return await svc.get_world(str(rows[0]["game_id"]))
            except Exception:
                return None
    if chat and chat.get("id"):
        return await svc.get_group_world(int(chat["id"]))
    return None


async def can_group_admin(request: Request, game: dict[str, Any], user_id: int) -> bool:
    if int(game.get("creator_id") or 0) == user_id:
        return True
    bot = getattr(request.app.state, "telegram_bot", None)
    chat_id = game.get("chat_id")
    if not bot or not chat_id:
        return False
    try:
        member = await bot.get_chat_member(int(chat_id), user_id)
        return member.status in {"creator", "administrator"}
    except Exception:
        return False


async def can_moderate(request: Request, game: dict[str, Any], user_id: int) -> bool:
    if CONFIG.bot_creator_id and user_id == CONFIG.bot_creator_id:
        return True
    settings = game.get("settings") or {}
    if user_id in [int(x) for x in settings.get("operator_ids", [])]:
        return True
    return await can_group_admin(request, game, user_id)


@router.get("/bootstrap")
async def bootstrap(
    request: Request,
    x_telegram_init_data: str | None = Header(default=None),
    x_miniapp_start_param: str | None = Header(default=None),
) -> dict[str, Any]:
    user, start_param, chat = await auth_user(x_telegram_init_data, x_miniapp_start_param)
    maintenance = await maintenance_response()
    result: dict[str, Any] = {
        "user": user,
        "start_param": start_param,
        "game": None,
        "invite": None,
        "is_admin": False,
        "can_moderate": False,
        "can_terminate": False,
        "can_manage_operators": False,
        "maintenance": maintenance,
    }
    if maintenance:
        return result

    svc = service()
    game = await resolve_game(start_param, chat)
    user_id = int(user["id"])

    if start_param.startswith("invite_"):
        try:
            joined = await svc.consume_invite(start_param[7:], user_id, str(user.get("username") or ""), str(user.get("first_name") or "Player"))
            game = joined["game"]
            result["invite"] = "joined"
        except Exception as exc:
            result["invite"] = str(exc)

    if game:
        game = await svc.ensure_active(game)
        # Opening a group world is itself the join action. No second screen.
        try:
            await svc.join(str(game["id"]), user_id, str(user.get("username") or ""), str(user.get("first_name") or "Player"))
        except ValueError:
            # Paused worlds are still viewable; the UI will show the pause state.
            pass
        game = await svc.get_world(str(game["id"]))
        result["game"] = await svc.snapshot(str(game["id"]), user_id, include_unjoined=True)
        result["is_admin"] = await can_group_admin(request, game, user_id)
        result["can_moderate"] = await can_moderate(request, game, user_id)
        result["can_terminate"] = bool(CONFIG.bot_creator_id and user_id == CONFIG.bot_creator_id) or int(game.get("creator_id") or 0) == user_id
        result["can_manage_operators"] = result["can_terminate"]
    return result


@router.post("/join")
async def join_world(body: GameRequest, x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    maintenance = await maintenance_response()
    if maintenance:
        raise HTTPException(status_code=503, detail="Maintenance is active. Your world is safe; try again when it ends.")
    svc = service()
    player = await svc.join(body.game_id, int(user["id"]), str(user.get("username") or ""), str(user.get("first_name") or "Player"))
    return {"player": player, "snapshot": await svc.snapshot(body.game_id, int(user["id"]))}


@router.get("/worlds/{game_id}")
async def get_world(game_id: str, x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    return await service().snapshot(game_id, int(user["id"]), include_unjoined=True)


@router.post("/choice")
async def choose(body: ChoiceRequest, x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    maintenance = await maintenance_response()
    if maintenance:
        raise HTTPException(status_code=503, detail="Maintenance is active. Your progress is safe.")
    try:
        return await service().choose(body.game_id, int(user["id"]), body.choice_id)
    except DatabaseConflict as exc:
        raise HTTPException(status_code=409, detail="Someone else just changed the world. Refreshing your current scene…") from exc
    except (DatabaseError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/invite")
async def invite(request: Request, body: GameRequest, x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    svc = service()
    player = await svc.get_player(body.game_id, int(user["id"]))
    if not player:
        raise HTTPException(status_code=403, detail="Join the world before inviting friends.")
    game = await svc.get_world(body.game_id)
    if not (game.get("settings") or {}).get("allow_external_invites", True):
        raise HTTPException(status_code=403, detail="External invites are disabled for this world.")
    token = await svc.create_invite(body.game_id, int(user["id"]))
    return {"token": token, "link": app_deep_link(request, f"invite_{token}")}


@router.post("/settings/invites")
async def set_invites(request: Request, body: GameRequest, x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    game = await service().get_world(body.game_id)
    if not await can_group_admin(request, game, int(user["id"])):
        raise HTTPException(status_code=403, detail="Only the group owner/admin or world creator can change settings.")
    settings = dict(game.get("settings") or {})
    settings["allow_external_invites"] = not bool(settings.get("allow_external_invites", True))
    updated = await service().update_settings(body.game_id, {"settings": settings})
    return {"settings": updated.get("settings") or settings}


@router.post("/settings/operators")
async def set_operator(request: Request, body: OperatorRequest, x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    game = await service().get_world(body.game_id)
    if int(game.get("creator_id") or 0) != int(user["id"]) and not (CONFIG.bot_creator_id and CONFIG.bot_creator_id == int(user["id"])):
        raise HTTPException(status_code=403, detail="Only the world creator or bot creator can assign operators.")
    current = [int(x) for x in (game.get("settings") or {}).get("operator_ids", [])]
    if body.action == "add" and body.user_id not in current:
        current.append(body.user_id)
    if body.action == "remove":
        current = [x for x in current if x != body.user_id]
    updated = await service().set_operators(body.game_id, current)
    return {"settings": updated.get("settings") or {"operator_ids": current}}


@router.post("/moderation/pause")
async def pause(request: Request, body: GameRequest, x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    game = await service().get_world(body.game_id)
    if not await can_moderate(request, game, int(user["id"])):
        raise HTTPException(status_code=403, detail="You do not have permission to pause this world.")
    updated = await service().pause(body.game_id, int(user["id"]))
    return {"game": updated}


@router.post("/moderation/resume")
async def resume(request: Request, body: GameRequest, x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    game = await service().get_world(body.game_id)
    if not await can_moderate(request, game, int(user["id"])):
        raise HTTPException(status_code=403, detail="You do not have permission to resume this world.")
    updated = await service().resume(body.game_id, int(user["id"]))
    return {"game": updated}


@router.post("/moderation/terminate")
async def terminate(request: Request, body: GameRequest, x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    user, _, _ = await auth_user(x_telegram_init_data)
    game = await service().get_world(body.game_id)
    user_id = int(user["id"])
    if user_id != int(game.get("creator_id") or 0) and not (CONFIG.bot_creator_id and user_id == CONFIG.bot_creator_id):
        raise HTTPException(status_code=403, detail="Only the world creator or bot creator can terminate a world.")
    updated = await service().terminate(body.game_id, user_id)
    return {"game": updated}


def mount_static(app: Any) -> None:
    from fastapi.staticfiles import StaticFiles
    assets = STATIC_DIR / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    app.mount("/app/assets", StaticFiles(directory=str(assets)), name="miniapp-assets")

    @app.get("/app", include_in_schema=False)
    async def miniapp_index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))
