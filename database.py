from __future__ import annotations

from typing import Any

import httpx


class Database:
    def __init__(
        self,
        base_url: str,
        service_key: str,
    ):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }

    async def request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
    ) -> Any:

        url = f"{self.base_url}/rest/v1/{table}"

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(
                method,
                url,
                headers=self.headers,
                params=params,
                json=json,
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"Supabase error {response.status_code}: "
                f"{response.text}"
            )

        if not response.content:
            return None

        return response.json()

    async def create_game(
        self,
        *,
        chat_id: int,
        creator_id: int,
        story: dict,
        fingerprint: str,
        join_deadline: str,
    ) -> dict:

        rows = await self.request(
            "POST",
            "games",
            params={"select": "*"},
            json={
                "chat_id": chat_id,
                "creator_id": creator_id,
                "status": "lobby",
                "story": story,
                "story_fingerprint": fingerprint,
                "join_deadline": join_deadline,
            },
        )

        return rows[0]

    async def get_active_game(
        self,
        chat_id: int,
    ) -> dict | None:

        rows = await self.request(
            "GET",
            "games",
            params={
                "chat_id": f"eq.{chat_id}",
                "status": "neq.ended",
                "order": "created_at.desc",
                "limit": "1",
            },
        )

        return rows[0] if rows else None

    async def get_game(
        self,
        game_id: str,
    ) -> dict | None:

        rows = await self.request(
            "GET",
            "games",
            params={
                "id": f"eq.{game_id}",
                "limit": "1",
            },
        )

        return rows[0] if rows else None

    async def update_game(
        self,
        game_id: str,
        values: dict,
    ) -> None:

        await self.request(
            "PATCH",
            "games",
            params={"id": f"eq.{game_id}"},
            json=values,
        )

    async def add_player(
        self,
        *,
        game_id: str,
        user_id: int,
        username: str,
        display_name: str,
        role_id: str | None,
        joined_round: int,
    ) -> dict:

        rows = await self.request(
            "POST",
            "players",
            params={
                "select": "*",
                "on_conflict": "game_id,user_id",
            },
            json={
                "game_id": game_id,
                "user_id": user_id,
                "username": username,
                "display_name": display_name,
                "role_id": role_id,
                "joined_round": joined_round,
            },
        )

        return rows[0]

    async def get_players(
        self,
        game_id: str,
    ) -> list[dict]:

        return await self.request(
            "GET",
            "players",
            params={
                "game_id": f"eq.{game_id}",
                "order": "created_at.asc",
            },
        )

    async def get_player(
        self,
        game_id: str,
        user_id: int,
    ) -> dict | None:

        rows = await self.request(
            "GET",
            "players",
            params={
                "game_id": f"eq.{game_id}",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )

        return rows[0] if rows else None

    async def update_player(
        self,
        game_id: str,
        user_id: int,
        values: dict,
    ) -> None:

        await self.request(
            "PATCH",
            "players",
            params={
                "game_id": f"eq.{game_id}",
                "user_id": f"eq.{user_id}",
            },
            json=values,
        )

    async def save_decision(
        self,
        *,
        game_id: str,
        round_number: int,
        scene_id: str,
        user_id: int,
        choice_id: str,
    ) -> None:

        await self.request(
            "POST",
            "decisions",
            params={"on_conflict": "game_id,round_number,user_id"},
            json={
                "game_id": game_id,
                "round_number": round_number,
                "scene_id": scene_id,
                "user_id": user_id,
                "choice_id": choice_id,
                "resolved": False,
            },
        )

    async def get_round_decisions(
        self,
        game_id: str,
        round_number: int,
    ) -> list[dict]:

        return await self.request(
            "GET",
            "decisions",
            params={
                "game_id": f"eq.{game_id}",
                "round_number": f"eq.{round_number}",
            },
        )

    async def resolve_round(
        self,
        game_id: str,
        round_number: int,
    ) -> None:

        await self.request(
            "PATCH",
            "decisions",
            params={
                "game_id": f"eq.{game_id}",
                "round_number": f"eq.{round_number}",
            },
            json={"resolved": True},
        )

    async def fingerprint_exists(
        self,
        fingerprint: str,
    ) -> bool:

        rows = await self.request(
            "GET",
            "story_history",
            params={
                "fingerprint": f"eq.{fingerprint}",
                "limit": "1",
            },
        )

        return bool(rows)

    async def save_story_history(
        self,
        fingerprint: str,
        story: dict,
    ) -> None:

        await self.request(
            "POST",
            "story_history",
            params={"on_conflict": "fingerprint"},
            json={
                "fingerprint": fingerprint,
                "story": story,
            },
      )
