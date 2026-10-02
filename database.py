from __future__ import annotations

from typing import Any

import httpx


class Database:
    """
    Persistent data layer for WHAT HAPPENS?

    Responsibilities:
    - Games
    - Players
    - Decisions
    - Persistent sandbox/world state
    - Story history
    - Restart recovery
    - Safe one-decision-per-player handling

    Gameplay rules themselves belong in engine.py.
    """

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

    # ============================================================
    # LOW LEVEL HTTP
    # ============================================================

    async def request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        prefer: str | None = None,
    ) -> Any:

        url = f"{self.base_url}/rest/v1/{table}"

        headers = dict(self.headers)

        if prefer:
            headers["Prefer"] = prefer

        async with httpx.AsyncClient(
            timeout=20.0
        ) as client:

            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"Supabase error "
                f"{response.status_code}: "
                f"{response.text}"
            )

        if not response.content:
            return None

        return response.json()

    # ============================================================
    # GAMES
    # ============================================================

    async def create_game(
        self,
        *,
        chat_id: int,
        creator_id: int,
        story: dict[str, Any],
        fingerprint: str,
        join_deadline: str,
        world_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:

        payload = {
            "chat_id": chat_id,
            "creator_id": creator_id,
            "status": "lobby",
            "story": story,
            "story_fingerprint": fingerprint,
            "join_deadline": join_deadline,
            "current_scene_id": None,
            "current_round": 0,
            "decision_deadline": None,
            "world_state": world_state or {},
        }

        rows = await self.request(
            "POST",
            "games",
            params={"select": "*"},
            json=payload,
            prefer="return=representation",
        )

        if not rows:
            raise RuntimeError(
                "Supabase did not return created game."
            )

        return rows[0]

    async def get_active_game(
        self,
        chat_id: int,
    ) -> dict[str, Any] | None:

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

    async def get_active_games(
        self,
    ) -> list[dict[str, Any]]:

        """
        Used after bot restart.

        Finds every game that was not cleanly ended.
        """

        return await self.request(
            "GET",
            "games",
            params={
                "status": "neq.ended",
                "order": "created_at.asc",
            },
        )

    async def get_game(
        self,
        game_id: str,
    ) -> dict[str, Any] | None:

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
        values: dict[str, Any],
    ) -> None:

        if not values:
            return

        await self.request(
            "PATCH",
            "games",
            params={
                "id": f"eq.{game_id}"
            },
            json=values,
            prefer="return=minimal",
        )

    async def set_game_state(
        self,
        game_id: str,
        *,
        status: str | None = None,
        scene_id: str | None = None,
        round_number: int | None = None,
        world_state: dict[str, Any] | None = None,
        join_deadline: str | None = None,
        decision_deadline: str | None = None,
        ended_at: str | None = None,
    ) -> None:

        values: dict[str, Any] = {}

        if status is not None:
            values["status"] = status

        if scene_id is not None:
            values["current_scene_id"] = scene_id

        if round_number is not None:
            values["current_round"] = round_number

        if world_state is not None:
            values["world_state"] = world_state

        if join_deadline is not None:
            values["join_deadline"] = join_deadline

        if decision_deadline is not None:
            values["decision_deadline"] = decision_deadline

        if ended_at is not None:
            values["ended_at"] = ended_at

        if not values:
            return

        await self.update_game(
            game_id,
            values,
        )

    # ============================================================
    # PLAYERS
    # ============================================================

    async def add_player(
        self,
        *,
        game_id: str,
        user_id: int,
        username: str,
        display_name: str,
        role_id: str | None = None,
        joined_round: int = 0,
    ) -> dict[str, Any]:

        payload = {
            "game_id": game_id,
            "user_id": user_id,
            "username": username,
            "display_name": display_name,
            "role_id": role_id,
            "joined_round": joined_round,
            "missed_decisions": 0,
            "active": True,
        }

        rows = await self.request(
            "POST",
            "players",
            params={
                "select": "*",
                "on_conflict": "game_id,user_id",
            },
            json=payload,
            prefer=(
                "resolution=merge-duplicates,"
                "return=representation"
            ),
        )

        if not rows:
            existing = await self.get_player(
                game_id,
                user_id,
            )

            if existing:
                return existing

            raise RuntimeError(
                "Unable to create player."
            )

        return rows[0]

    async def get_players(
        self,
        game_id: str,
        *,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:

        params = {
            "game_id": f"eq.{game_id}",
            "order": "created_at.asc",
        }

        if active_only:
            params["active"] = "eq.true"

        return await self.request(
            "GET",
            "players",
            params=params,
        )

    async def get_active_players(
        self,
        game_id: str,
    ) -> list[dict[str, Any]]:

        return await self.get_players(
            game_id,
            active_only=True,
        )

    async def get_player(
        self,
        game_id: str,
        user_id: int,
    ) -> dict[str, Any] | None:

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
        values: dict[str, Any],
    ) -> None:

        if not values:
            return

        await self.request(
            "PATCH",
            "players",
            params={
                "game_id": f"eq.{game_id}",
                "user_id": f"eq.{user_id}",
            },
            json=values,
            prefer="return=minimal",
        )

    async def assign_role(
        self,
        game_id: str,
        user_id: int,
        role_id: str,
    ) -> None:

        await self.update_player(
            game_id,
            user_id,
            {
                "role_id": role_id
            },
        )

    async def increment_missed_decisions(
        self,
        game_id: str,
        user_id: int,
    ) -> int:

        """
        Reads current value and writes incremented value.

        The bot's game lock prevents concurrent increments
        for the same game during normal operation.
        """

        player = await self.get_player(
            game_id,
            user_id,
        )

        if not player:
            raise ValueError(
                "Player does not exist."
            )

        missed = int(
            player.get(
                "missed_decisions",
                0,
            )
        )

        missed += 1

        await self.update_player(
            game_id,
            user_id,
            {
                "missed_decisions": missed
            },
        )

        return missed

    async def convert_player_to_npc(
        self,
        game_id: str,
        user_id: int,
    ) -> None:

        """
        The human leaves active gameplay.

        Their character remains in the sandbox world.
        """

        await self.update_player(
            game_id,
            user_id,
            {
                "active": False
            },
        )

    # ============================================================
    # DECISIONS
    # ============================================================

    async def save_decision(
        self,
        *,
        game_id: str,
        round_number: int,
        scene_id: str,
        user_id: int,
        choice_id: str,
    ) -> bool:

        """
        Save exactly one decision.

        Returns:
            True  -> newly saved
            False -> player already decided

        The UNIQUE constraint in Supabase is the final
        protection against double-click/race conditions.
        """

        rows = await self.request(
            "POST",
            "decisions",
            params={
                "on_conflict": (
                    "game_id,round_number,user_id"
                )
            },
            json={
                "game_id": game_id,
                "round_number": round_number,
                "scene_id": scene_id,
                "user_id": user_id,
                "choice_id": choice_id,
                "resolved": False,
            },
            prefer=(
                "resolution=ignore-duplicates,"
                "return=representation"
            ),
        )

        return bool(rows)

    async def get_decision(
        self,
        game_id: str,
        round_number: int,
        user_id: int,
    ) -> dict[str, Any] | None:

        rows = await self.request(
            "GET",
            "decisions",
            params={
                "game_id": f"eq.{game_id}",
                "round_number": (
                    f"eq.{round_number}"
                ),
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
        )

        return rows[0] if rows else None

    async def get_round_decisions(
        self,
        game_id: str,
        round_number: int,
    ) -> list[dict[str, Any]]:

        return await self.request(
            "GET",
            "decisions",
            params={
                "game_id": f"eq.{game_id}",
                "round_number": (
                    f"eq.{round_number}"
                ),
                "order": "created_at.asc",
            },
        )

    async def get_unresolved_round_decisions(
        self,
        game_id: str,
        round_number: int,
    ) -> list[dict[str, Any]]:

        return await self.request(
            "GET",
            "decisions",
            params={
                "game_id": f"eq.{game_id}",
                "round_number": (
                    f"eq.{round_number}"
                ),
                "resolved": "eq.false",
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
                "round_number": (
                    f"eq.{round_number}"
                ),
            },
            json={
                "resolved": True
            },
            prefer="return=minimal",
        )

    # ============================================================
    # STORY HISTORY
    # ============================================================

    async def fingerprint_exists(
        self,
        fingerprint: str,
    ) -> bool:

        rows = await self.request(
            "GET",
            "story_history",
            params={
                "fingerprint": (
                    f"eq.{fingerprint}"
                ),
                "limit": "1",
            },
        )

        return bool(rows)

    async def save_story_history(
        self,
        fingerprint: str,
        story: dict[str, Any],
    ) -> None:

        await self.request(
            "POST",
            "story_history",
            params={
                "on_conflict": "fingerprint"
            },
            json={
                "fingerprint": fingerprint,
                "story": story,
            },
            prefer=(
                "resolution=ignore-duplicates,"
                "return=minimal"
            ),
        )

    # ============================================================
    # STORY HISTORY / GENERATION HELPERS
    # ============================================================

    async def get_recent_fingerprints(
        self,
        limit: int = 50,
    ) -> list[str]:

        rows = await self.request(
            "GET",
            "story_history",
            params={
                "select": "fingerprint",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        )

        return [
            row["fingerprint"]
            for row in rows
            if row.get("fingerprint")
        ]

    # ============================================================
    # WORLD STATE
    # ============================================================

    async def get_world_state(
        self,
        game_id: str,
    ) -> dict[str, Any]:

        game = await self.get_game(game_id)

        if not game:
            raise ValueError(
                "Game does not exist."
            )

        return dict(
            game.get(
                "world_state"
            )
            or {}
        )

    async def save_world_state(
        self,
        game_id: str,
        world_state: dict[str, Any],
    ) -> None:

        await self.update_game(
            game_id,
            {
                "world_state": world_state
            },
        )

    # ============================================================
    # CONVENIENCE
    # ============================================================

    async def end_game(
        self,
        game_id: str,
        ended_at: str,
    ) -> None:

        await self.update_game(
            game_id,
            {
                "status": "ended",
                "ended_at": ended_at,
                "decision_deadline": None,
                "join_deadline": None,
            },
        )
