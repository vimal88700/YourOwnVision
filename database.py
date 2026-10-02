from __future__ import annotations

import json
import logging
from typing import Any

import httpx


logger = logging.getLogger("YourOwnVision.database")


class DatabaseError(RuntimeError):
    """Base database-layer exception."""


class DatabaseNotFound(DatabaseError):
    """Requested database object does not exist."""


class DatabaseConflict(DatabaseError):
    """Operation conflicted with current persistent state."""


class Database:
    """
    Supabase persistence layer for WHAT HAPPENS?

    IMPORTANT ARCHITECTURE RULE

    This class is the persistence boundary.

    Telegram handlers and the game engine should NOT directly
    manipulate Supabase.

    The database is the source of truth for:

        games
        players
        decisions
        game_events
        story_history
        persistent world state
        deadlines
        resolution state

    Python memory is only a runtime cache/lock.

    Anything that must survive a Render restart belongs here.

    Some operations use PostgreSQL RPC functions. Those functions
    will be installed by the final supabase.sql migration.

    This is intentional.

    A sequence like:

        SELECT
        modify in Python
        UPDATE

    is NOT atomic.

    For important state transitions we instead use:

        PostgreSQL function
            ↓
        transaction
            ↓
        locked/validated state transition
            ↓
        result

    That is what protects the game when:

        Telegram callback
              +
        timeout worker
              +
        Render restart
              +
        duplicate callback

    happen close together.
    """

    def __init__(
        self,
        base_url: str,
        service_key: str,
        *,
        timeout: float = 20.0,
    ):
        if not base_url:
            raise ValueError(
                "Supabase URL is required."
            )

        if not service_key:
            raise ValueError(
                "Supabase service key is required."
            )

        self.base_url = base_url.rstrip("/")

        self.headers = {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }

        self.timeout = timeout

        # Reuse one HTTP client rather than creating a new
        # AsyncClient for every database query.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout,
                connect=10.0,
            ),
            headers=self.headers,
        )

        self._closed = False

    # ============================================================
    # LIFECYCLE
    # ============================================================

    async def close(self) -> None:
        """
        Close the shared HTTP client.

        Called during application shutdown.
        """

        if self._closed:
            return

        self._closed = True

        await self._client.aclose()

    # ============================================================
    # LOW-LEVEL REQUEST
    # ============================================================

    async def _request_url(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json_body: Any = None,
        prefer: str | None = None,
    ) -> Any:
        """
        Execute an HTTP request against Supabase.

        All database HTTP operations eventually pass through here.
        """

        if self._closed:
            raise DatabaseError(
                "Database client is already closed."
            )

        headers = {}

        if prefer:
            headers["Prefer"] = prefer

        try:
            response = await self._client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
            )

        except httpx.TimeoutException as exc:
            raise DatabaseError(
                "Supabase request timed out."
            ) from exc

        except httpx.HTTPError as exc:
            raise DatabaseError(
                f"Supabase HTTP error: {exc}"
            ) from exc

        if response.status_code >= 400:
            body = response.text

            logger.error(
                "Supabase request failed: %s %s -> %s: %s",
                method,
                url,
                response.status_code,
                body,
            )

            if response.status_code == 404:
                raise DatabaseNotFound(
                    body or "Supabase resource not found."
                )

            if response.status_code in {
                409,
                412,
            }:
                raise DatabaseConflict(
                    body
                    or "Supabase state conflict."
                )

            raise DatabaseError(
                f"Supabase error "
                f"{response.status_code}: "
                f"{body}"
            )

        if not response.content:
            return None

        content_type = response.headers.get(
            "content-type",
            "",
        )

        if "application/json" not in content_type:
            return response.text

        try:
            return response.json()

        except json.JSONDecodeError as exc:
            raise DatabaseError(
                "Supabase returned invalid JSON."
            ) from exc

    async def request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        prefer: str | None = None,
    ) -> Any:
        """
        REST table request.

        This method remains available for ordinary CRUD operations.
        """

        return await self._request_url(
            method,
            f"{self.base_url}/rest/v1/{table}",
            params=params,
            json_body=json,
            prefer=prefer,
        )

    async def rpc(
        self,
        function_name: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """
        Call a PostgreSQL function through Supabase RPC.

        Atomic game transitions will use this path.
        """

        return await self._request_url(
            "POST",
            (
                f"{self.base_url}/rest/v1/rpc/"
                f"{function_name}"
            ),
            json_body=payload or {},
            prefer="return=representation",
        )

    # ============================================================
    # HEALTH
    # ============================================================

    async def health_check(self) -> bool:
        """
        Verify that Supabase is reachable.

        This is NOT used as the Render HTTP health endpoint.

        /health must remain independent enough to tell Render
        whether the application itself is alive.

        This method is for application diagnostics/startup logging.
        """

        try:
            await self.request(
                "GET",
                "games",
                params={
                    "select": "id",
                    "limit": "1",
                },
            )

            return True

        except DatabaseError:
            logger.exception(
                "Supabase health check failed."
            )

            return False

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
        story_id: str | None = None,
        story_player_count: int | None = None,
    ) -> dict[str, Any]:
        """
        Create a new lobby.

        Story generation happens before this call.

        The generated story is already validated before being
        persisted.
        """

        payload = {
            "chat_id": chat_id,
            "creator_id": creator_id,
            "status": "lobby",
            "story": story,
            "story_id": story_id,
            "story_fingerprint": fingerprint,
            "story_player_count": story_player_count,
            "join_deadline": join_deadline,
            "current_scene_id": None,
            "current_round": 0,
            "decision_deadline": None,
            "resolution_status": "pending",
            "resolution_key": None,
            "world_state": world_state or {},
            "version": 1,
        }

        rows = await self.request(
            "POST",
            "games",
            params={
                "select": "*",
            },
            json=payload,
            prefer="return=representation",
        )

        if not rows:
            raise DatabaseError(
                "Supabase did not return the created game."
            )

        return rows[0]

    async def get_game(
        self,
        game_id: str,
    ) -> dict[str, Any] | None:
        """
        Retrieve a single game.
        """

        rows = await self.request(
            "GET",
            "games",
            params={
                "id": f"eq.{game_id}",
                "limit": "1",
            },
        )

        return rows[0] if rows else None

    async def get_active_game(
        self,
        chat_id: int,
    ) -> dict[str, Any] | None:
        """
        Return the newest active game in a chat.

        Terminal states are:

            completed
            cancelled
        """

        rows = await self.request(
            "GET",
            "games",
            params={
                "chat_id": f"eq.{chat_id}",
                "status": "not.in.(completed,cancelled)",
                "order": "created_at.desc",
                "limit": "1",
            },
        )

        return rows[0] if rows else None

    async def get_active_games(
        self,
    ) -> list[dict[str, Any]]:
        """
        Retrieve all non-terminal games.

        Used by startup recovery.

        We deliberately query the database rather than depending
        on Python memory.
        """

        return await self.request(
            "GET",
            "games",
            params={
                "status": "not.in.(completed,cancelled)",
                "order": "created_at.asc",
            },
        )

    async def update_game(
        self,
        game_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        Generic game update.

        IMPORTANT:

        This method is for non-critical metadata updates.

        Critical state transitions should use the atomic RPC
        methods below.
        """

        if not values:
            return None

        rows = await self.request(
            "PATCH",
            "games",
            params={
                "id": f"eq.{game_id}",
            },
            json=values,
            prefer="return=representation",
        )

        return rows[0] if rows else None

    async def update_game_versioned(
        self,
        game_id: str,
        expected_version: int,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Optimistic-concurrency update.

        Only updates the game if its version still equals the
        version the caller read.

        This protects against stale workers overwriting newer state.
        """

        if not values:
            raise ValueError(
                "Versioned update requires values."
            )

        payload = dict(values)

        payload["version"] = (
            expected_version + 1
        )

        rows = await self.request(
            "PATCH",
            "games",
            params={
                "id": f"eq.{game_id}",
                "version": f"eq.{expected_version}",
            },
            json=payload,
            prefer="return=representation",
        )

        if not rows:
            raise DatabaseConflict(
                "Game changed before the update could be applied."
            )

        return rows[0]

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
        resolution_status: str | None = None,
        resolution_key: str | None = None,
        ended_at: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Convenience update for game state.

        Critical transitions should eventually call the explicit
        RPC functions below.
        """

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

        if resolution_status is not None:
            values["resolution_status"] = (
                resolution_status
            )

        if resolution_key is not None:
            values["resolution_key"] = (
                resolution_key
            )

        if ended_at is not None:
            values["ended_at"] = ended_at

        if not values:
            return None

        return await self.update_game(
            game_id,
            values,
        )

    # ============================================================
    # ATOMIC GAME OPERATIONS
    # ============================================================

    async def start_game_atomic(
        self,
        game_id: str,
        *,
        scene_id: str,
        round_number: int,
        decision_deadline: str | None,
        world_state: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Atomically transition:

            lobby -> starting/playing

        and initialize the first round.

        The corresponding PostgreSQL function will validate the
        current state and lock the game row.
        """

        result = await self.rpc(
            "start_game_atomic",
            {
                "p_game_id": game_id,
                "p_scene_id": scene_id,
                "p_round_number": round_number,
                "p_decision_deadline": (
                    decision_deadline
                ),
                "p_world_state": world_state,
            },
        )

        if not result:
            raise DatabaseConflict(
                "Game could not be started."
            )

        if isinstance(result, list):
            return result[0]

        return result

    async def claim_round_resolution(
        self,
        game_id: str,
        round_number: int,
        resolution_key: str,
    ) -> bool:
        """
        Attempt to claim responsibility for resolving a round.

        Exactly one worker should receive True.

        Example race:

            Telegram callback
                    +
            timeout worker

        Both call this method.

        PostgreSQL decides the winner.
        """

        result = await self.rpc(
            "claim_round_resolution",
            {
                "p_game_id": game_id,
                "p_round_number": round_number,
                "p_resolution_key": resolution_key,
            },
        )

        if isinstance(result, bool):
            return result

        if isinstance(result, list) and result:
            value = result[0]

            if isinstance(value, bool):
                return value

            if isinstance(value, dict):
                return bool(
                    value.get(
                        "claimed",
                        False,
                    )
                )

        if isinstance(result, dict):
            return bool(
                result.get(
                    "claimed",
                    False,
                )
            )

        return False

    async def complete_round_atomic(
        self,
        *,
        game_id: str,
        round_number: int,
        next_scene_id: str | None,
        next_round_number: int | None,
        decision_deadline: str | None,
        world_state: dict[str, Any],
        game_status: str,
        resolution_key: str,
    ) -> dict[str, Any]:
        """
        Atomically complete a claimed round.

        This is the critical state transition:

            current round
                    ↓
            apply world state
                    ↓
            mark decisions resolved
                    ↓
            advance scene/round
                    ↓
            set deadline
                    ↓
            release resolution state

        All of that belongs in one PostgreSQL transaction.
        """

        result = await self.rpc(
            "complete_round_atomic",
            {
                "p_game_id": game_id,
                "p_round_number": round_number,
                "p_next_scene_id": next_scene_id,
                "p_next_round_number": (
                    next_round_number
                ),
                "p_decision_deadline": (
                    decision_deadline
                ),
                "p_world_state": world_state,
                "p_game_status": game_status,
                "p_resolution_key": resolution_key,
            },
        )

        if not result:
            raise DatabaseConflict(
                "Round completion failed or state changed."
            )

        if isinstance(result, list):
            return result[0]

        return result

    async def cancel_game_atomic(
        self,
        game_id: str,
    ) -> dict[str, Any] | None:
        """
        Atomically cancel an active game.
        """

        result = await self.rpc(
            "cancel_game_atomic",
            {
                "p_game_id": game_id,
            },
        )

        if isinstance(result, list):
            return result[0] if result else None

        return result

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
        status: str = "pending",
    ) -> dict[str, Any]:
        """
        Add a player.

        The database UNIQUE constraint:

            unique(game_id, user_id)

        prevents duplicate membership.
        """

        payload = {
            "game_id": game_id,
            "user_id": user_id,
            "username": username or "",
            "display_name": display_name or "Player",
            "role_id": role_id,
            "status": status,
            "missed_decisions": 0,
            "joined_round": joined_round,
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
                "resolution=ignore-duplicates,"
                "return=representation"
            ),
        )

        if rows:
            return rows[0]

        existing = await self.get_player(
            game_id,
            user_id,
        )

        if existing:
            return existing

        raise DatabaseError(
            "Unable to create player."
        )

    async def get_players(
        self,
        game_id: str,
        *,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Retrieve players for a game.
        """

        params = {
            "game_id": f"eq.{game_id}",
            "order": "created_at.asc",
        }

        if active_only:
            params["status"] = "eq.active"

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
        """
        Retrieve one player.
        """

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
    ) -> dict[str, Any] | None:
        """
        Generic player update.

        Important state transitions can use the RPC methods below.
        """

        if not values:
            return None

        rows = await self.request(
            "PATCH",
            "players",
            params={
                "game_id": f"eq.{game_id}",
                "user_id": f"eq.{user_id}",
            },
            json=values,
            prefer="return=representation",
        )

        return rows[0] if rows else None

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
                "role_id": role_id,
                "status": "active",
            },
        )

    async def mark_player_onboarded(
        self,
        game_id: str,
        user_id: int,
    ) -> dict[str, Any] | None:
        """
        Mark a player as having successfully completed private
        Telegram onboarding.
        """

        return await self.update_player(
            game_id,
            user_id,
            {
                "status": "active",
            },
        )

    async def increment_missed_decisions(
        self,
        game_id: str,
        user_id: int,
    ) -> int:
        """
        Atomically increment missed decisions.

        This must NOT use:

            SELECT missed
            UPDATE missed + 1

        because two workers could read the same value.

        PostgreSQL performs the increment atomically.
        """

        result = await self.rpc(
            "increment_missed_decisions",
            {
                "p_game_id": game_id,
                "p_user_id": user_id,
            },
        )

        if isinstance(result, int):
            return result

        if isinstance(result, list) and result:
            value = result[0]

            if isinstance(value, int):
                return value

            if isinstance(value, dict):
                return int(
                    value.get(
                        "missed_decisions",
                        0,
                    )
                )

        if isinstance(result, dict):
            return int(
                result.get(
                    "missed_decisions",
                    0,
                )
            )

        raise DatabaseError(
            "Could not read updated missed decision count."
        )

    async def reset_missed_decisions(
        self,
        game_id: str,
        user_id: int,
    ) -> None:
        await self.update_player(
            game_id,
            user_id,
            {
                "missed_decisions": 0,
            },
        )

    async def convert_player_to_npc(
        self,
        game_id: str,
        user_id: int,
    ) -> None:
        """
        Convert a player to NPC.

        The character remains part of the world but no longer
        receives human decisions.
        """

        await self.update_player(
            game_id,
            user_id,
            {
                "status": "npc",
                "became_npc_at": (
                    "now()"
                ),
            },
        )

    async def eliminate_player(
        self,
        game_id: str,
        user_id: int,
    ) -> None:
        """
        Mark a player as eliminated.
        """

        await self.update_player(
            game_id,
            user_id,
            {
                "status": "eliminated",
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
        Save exactly one decision for a player/round.

        PostgreSQL UNIQUE:

            game_id + round_number + user_id

        is the final duplicate protection.

        Returns:

            True  = this request created the decision
            False = decision already existed
        """

        result = await self.rpc(
            "submit_decision_atomic",
            {
                "p_game_id": game_id,
                "p_round_number": round_number,
                "p_scene_id": scene_id,
                "p_user_id": user_id,
                "p_choice_id": choice_id,
            },
        )

        if isinstance(result, bool):
            return result

        if isinstance(result, list) and result:
            value = result[0]

            if isinstance(value, bool):
                return value

            if isinstance(value, dict):
                return bool(
                    value.get(
                        "created",
                        False,
                    )
                )

        if isinstance(result, dict):
            return bool(
                result.get(
                    "created",
                    False,
                )
            )

        return False

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
                "status": "neq.resolved",
                "order": "created_at.asc",
            },
        )

    async def resolve_round_decisions(
        self,
        game_id: str,
        round_number: int,
    ) -> None:
        """
        Mark decisions resolved.

        Normally this is performed inside complete_round_atomic().
        This method exists for administrative/recovery use.
        """

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
                "status": "resolved",
            },
            prefer="return=minimal",
        )

    # Backward-compatible name used by the old bot.
    async def resolve_round(
        self,
        game_id: str,
        round_number: int,
    ) -> None:
        await self.resolve_round_decisions(
            game_id,
            round_number,
        )

    # ============================================================
    # STORY HISTORY
    # ============================================================

    async def fingerprint_exists(
        self,
        fingerprint: str,
    ) -> bool:
        """
        Check whether a structural story fingerprint already exists.
        """

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
        *,
        player_count: int | None = None,
    ) -> None:
        """
        Store story history.

        The fingerprint UNIQUE constraint prevents duplicates.
        """

        payload = {
            "fingerprint": fingerprint,
            "story": story,
        }

        if player_count is not None:
            payload["player_count"] = (
                player_count
            )

        await self.request(
            "POST",
            "story_history",
            params={
                "on_conflict": "fingerprint",
            },
            json=payload,
            prefer=(
                "resolution=ignore-duplicates,"
                "return=minimal"
            ),
        )

    async def get_recent_fingerprints(
        self,
        limit: int = 50,
    ) -> list[str]:
        """
        Return recent story fingerprints.

        Used to reduce structural repetition in generated stories.
        """

        limit = max(
            1,
            min(limit, 500),
        )

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

    async def get_story_by_fingerprint(
        self,
        fingerprint: str,
    ) -> dict[str, Any] | None:
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

        return rows[0] if rows else None

    # ============================================================
    # WORLD STATE
    # ============================================================

    async def get_world_state(
        self,
        game_id: str,
    ) -> dict[str, Any]:
        game = await self.get_game(
            game_id
        )

        if not game:
            raise DatabaseNotFound(
                f"Game {game_id} does not exist."
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
                "world_state": world_state,
            },
        )

    # ============================================================
    # EVENTS
    # ============================================================

    async def append_event(
        self,
        *,
        game_id: str,
        event_type: str,
        round_number: int | None = None,
        user_id: int | None = None,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Append an immutable game event.

        Event history is deliberately separate from current game
        state.

        Current state answers:

            "What is happening now?"

        Events answer:

            "How did we get here?"
        """

        values: dict[str, Any] = {
            "game_id": game_id,
            "event_type": event_type,
            "round_number": round_number,
            "user_id": user_id,
            "payload": payload or {},
        }

        if event_id:
            values["id"] = event_id

        rows = await self.request(
            "POST",
            "game_events",
            params={
                "select": "*",
            },
            json=values,
            prefer="return=representation",
        )

        if not rows:
            raise DatabaseError(
                "Supabase did not return the game event."
            )

        return rows[0]

    async def get_game_events(
        self,
        game_id: str,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """
        Retrieve chronological game history.
        """

        limit = max(
            1,
            min(limit, 5000),
        )

        rows = await self.request(
            "GET",
            "game_events",
            params={
                "game_id": f"eq.{game_id}",
                "order": "created_at.asc",
                "limit": str(limit),
            },
        )

        return rows

    # ============================================================
    # RECOVERY
    # ============================================================

    async def get_recoverable_games(
        self,
    ) -> list[dict[str, Any]]:
        """
        Find games that may need runtime recovery.

        This includes:

            lobby
            starting
            playing
            resolving

        """

        return await self.request(
            "GET",
            "games",
            params={
                "status": (
                    "in.(lobby,starting,playing,resolving)"
                ),
                "order": "created_at.asc",
            },
        )

    async def get_expired_decision_games(
        self,
    ) -> list[dict[str, Any]]:
        """
        Find playing/resolving games whose deadline has passed.

        The database deadline is authoritative.

        Python JobQueue is only a convenience mechanism.
        """

        return await self.request(
            "GET",
            "games",
            params={
                "status": (
                    "in.(playing,resolving)"
                ),
                "decision_deadline": "lt.now()",
                "order": "decision_deadline.asc",
            },
        )

    async def get_pending_lobbies(
        self,
    ) -> list[dict[str, Any]]:
        """
        Find lobbies whose join deadline has passed.
        """

        return await self.request(
            "GET",
            "games",
            params={
                "status": "eq.lobby",
                "join_deadline": "lt.now()",
                "order": "join_deadline.asc",
            },
        )

    # ============================================================
    # END / CANCEL
    # ============================================================

    async def end_game(
        self,
        game_id: str,
        ended_at: str,
    ) -> None:
        """
        Compatibility method.

        New gameplay code should use cancel_game_atomic()
        or complete_round_atomic() for important transitions.
        """

        await self.update_game(
            game_id,
            {
                "status": "completed",
                "ended_at": ended_at,
                "decision_deadline": None,
                "join_deadline": None,
                "resolution_status": "resolved",
            },
        )

    async def cancel_game(
        self,
        game_id: str,
    ) -> dict[str, Any] | None:
        return await self.cancel_game_atomic(
            game_id
        )
