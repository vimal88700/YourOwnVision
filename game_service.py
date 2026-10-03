from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from database import (
    Database,
    DatabaseConflict,
    DatabaseError,
)
from engine import GameEngine
from story_generator import StoryGenerator


logger = logging.getLogger("YourOwnVision.game_service")


class GameService:
    """
    Application/game orchestration layer.

    Telegram
        ↓
    GameService
        ↓
    Database  ← authoritative persistent state
        ↑
    GameEngine ← deterministic gameplay
        ↑
    StoryGenerator ← Gemini story preparation only

    This class never:

    - imports Telegram
    - sends Telegram messages
    - polls Telegram
    - stores authoritative game state in memory
    - uses an in-memory timer as the source of truth
    - calls Gemini for player decisions
    """

    STATUS_LOBBY = "lobby"
    STATUS_STARTING = "starting"
    STATUS_PLAYING = "playing"
    STATUS_RESOLVING = "resolving"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"

    PLAYER_PENDING = "pending"
    PLAYER_ACTIVE = "active"
    PLAYER_ELIMINATED = "eliminated"
    PLAYER_NPC = "npc"
    PLAYER_LEFT = "left"

    MAX_PLAYERS = 100
    MAX_MISSED_DECISIONS = 3

    def __init__(
        self,
        db: Database,
        story_generator: StoryGenerator,
        *,
        join_seconds: int = 60,
        default_decision_seconds: int = 35,
        minimum_decision_seconds: int = 15,
        maximum_decision_seconds: int = 900,
        maximum_players: int = 4,
    ) -> None:
        self.db = db
        self.story_generator = story_generator

        self.max_players = max(
            1,
            min(
                int(maximum_players),
                50,
            ),
        )

        self.join_seconds = max(
            10,
            int(join_seconds),
        )

        self.minimum_decision_seconds = max(
            5,
            int(minimum_decision_seconds),
        )

        self.maximum_decision_seconds = max(
            self.minimum_decision_seconds,
            int(maximum_decision_seconds),
        )

        self.default_decision_seconds = min(
            self.maximum_decision_seconds,
            max(
                self.minimum_decision_seconds,
                int(default_decision_seconds),
            ),
        )

    # ============================================================
    # TIME
    # ============================================================

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def to_iso(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(
                tzinfo=timezone.utc
            )

        return value.astimezone(
            timezone.utc
        ).isoformat()

    @staticmethod
    def parse_timestamp(
        value: Any,
    ) -> datetime | None:
        if value is None:
            return None

        if isinstance(value, datetime):
            result = value

        elif isinstance(value, str):
            try:
                result = datetime.fromisoformat(
                    value.replace(
                        "Z",
                        "+00:00",
                    )
                )
            except ValueError:
                logger.warning(
                    "Invalid database timestamp: %r",
                    value,
                )
                return None

        else:
            return None

        if result.tzinfo is None:
            result = result.replace(
                tzinfo=timezone.utc
            )

        return result.astimezone(
            timezone.utc
        )

    # ============================================================
    # GAME LOOKUP
    # ============================================================

    async def get_game(
        self,
        game_id: str,
    ) -> dict[str, Any]:
        game = await self.db.get_game(
            game_id
        )

        if not game:
            raise ValueError(
                "Game does not exist."
            )

        return game

    async def get_game_with_players(
        self,
        game_id: str,
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
    ]:
        game = await self.get_game(
            game_id
        )

        players = await self.db.get_players(
            game_id
        )

        return game, players

    def engine_for_game(
        self,
        game: dict[str, Any],
    ) -> GameEngine:
        story = game.get(
            "story"
        )

        if not isinstance(
            story,
            dict,
        ):
            raise ValueError(
                "Game does not contain a valid story."
            )

        return GameEngine(
            story
        )

    # ============================================================
    # PLAYER STATUS
    # ============================================================

    @classmethod
    def player_status(
        cls,
        player: dict[str, Any],
    ) -> str:
        status = player.get(
            "status"
        )

        if status in {
            cls.PLAYER_PENDING,
            cls.PLAYER_ACTIVE,
            cls.PLAYER_ELIMINATED,
            cls.PLAYER_NPC,
            cls.PLAYER_LEFT,
        }:
            return status

        # Compatibility with older database rows.
        if player.get(
            "active",
            False,
        ):
            return cls.PLAYER_ACTIVE

        return cls.PLAYER_LEFT

    @classmethod
    def is_counted_player(
        cls,
        player: dict[str, Any],
    ) -> bool:
        return cls.player_status(
            player
        ) in {
            cls.PLAYER_PENDING,
            cls.PLAYER_ACTIVE,
        }

    @classmethod
    def is_required_for_round(
        cls,
        player: dict[str, Any],
        round_number: int,
    ) -> bool:
        status = cls.player_status(
            player
        )

        if status not in {
            cls.PLAYER_ACTIVE,
            cls.PLAYER_NPC,
        }:
            return False

        joined_round = int(
            player.get(
                "joined_round",
                0,
            )
            or 0
        )

        return joined_round <= int(
            round_number
        )

    @classmethod
    def active_human_players(
        cls,
        players: list[dict[str, Any]],
        round_number: int,
    ) -> list[dict[str, Any]]:
        return [
            player
            for player in players
            if cls.player_status(player)
            == cls.PLAYER_ACTIVE
            and cls.is_required_for_round(
                player,
                round_number,
            )
        ]

    # ============================================================
    # STORY GENERATION
    # ============================================================

    async def generate_unique_story(
        self,
        *,
        player_count: int,
        max_attempts: int = 5,
    ) -> tuple[
        dict[str, Any],
        str,
    ]:
        if player_count < 1:
            raise ValueError(
                "player_count must be at least 1."
            )

        recent = await self.db.get_recent_fingerprints(
            limit=100
        )

        previous = list(
            recent
        )

        for _ in range(
            max_attempts
        ):
            story, fingerprint = (
                await self.story_generator.generate(
                    player_count=player_count,
                    previous_fingerprints=previous,
                )
            )

            engine = GameEngine(
                story
            )

            self.validate_player_count(
                player_count,
                engine,
            )

            if fingerprint in previous:
                continue

            if await self.db.fingerprint_exists(
                fingerprint
            ):
                previous.append(
                    fingerprint
                )
                continue

            return (
                story,
                fingerprint,
            )

        raise RuntimeError(
            "Unable to generate a unique validated story."
        )

    # ============================================================
    # GAME CREATION
    # ============================================================

    async def create_game(
        self,
        *,
        chat_id: int,
        creator_id: int,
    ) -> dict[str, Any]:
        existing = await self.db.get_active_game(
            chat_id
        )

        if existing:
            raise DatabaseConflict(
                "This chat already has an active game."
            )

        # A lobby does not yet know the final player count.
        #
        # The generator is therefore asked for the configured
        # minimum viable story. Later joins are still checked
        # against the actual playable roles.
        story_player_count = self.max_players

        story, fingerprint = (
            await self.generate_unique_story(
                player_count=story_player_count
            )
        )

        engine = GameEngine(
            story
        )

        world_state = (
            engine.initial_world_state()
        )

        join_deadline = (
            self.now_utc()
            + timedelta(
                seconds=self.join_seconds
            )
        )

        game = await self.db.create_game(
            chat_id=chat_id,
            creator_id=creator_id,
            story=story,
            fingerprint=fingerprint,
            join_deadline=self.to_iso(
                join_deadline
            ),
            world_state=world_state,
            story_player_count=story_player_count,
        )

        try:
            await self.db.save_story_history(
                fingerprint,
                story,
                player_count=story_player_count,
            )
        except DatabaseConflict:
            logger.warning(
                "Story fingerprint already existed: %s",
                fingerprint,
            )

        await self.db.append_event(
            game_id=game["id"],
            event_type="game_created",
            round_number=0,
            user_id=creator_id,
            payload={
                "chat_id": chat_id,
                "story_fingerprint": fingerprint,
            },
        )

        return game

    # ============================================================
    # JOIN / LEAVE
    # ============================================================

    async def join_game(
        self,
        *,
        game_id: str,
        user_id: int,
        username: str,
        display_name: str,
    ) -> dict[str, Any]:
        game = await self.get_game(
            game_id
        )

        status = game.get(
            "status"
        )

        if status not in {
            self.STATUS_LOBBY,
            self.STATUS_PLAYING,
        }:
            raise ValueError(
                "This game is not accepting new players."
            )

        existing = await self.db.get_player(
            game_id,
            user_id,
        )

        if existing:
            existing_status = self.player_status(
                existing
            )

            if existing_status == self.PLAYER_LEFT:
                raise ValueError(
                    "A player who left cannot rejoin."
                )

            raise ValueError(
                "You are already in this game."
            )

        players = await self.db.get_players(
            game_id
        )

        counted = sum(
            1
            for player in players
            if self.is_counted_player(
                player
            )
        )

        if counted >= self.max_players:
            raise ValueError(
                "The game has reached its player limit."
            )

        engine = self.engine_for_game(
            game
        )

        assigned_role_ids = {
            str(player["role_id"])
            for player in players
            if player.get("role_id")
            and self.player_status(player)
            not in {
                self.PLAYER_LEFT,
            }
        }

        available_roles = [
            role
            for role in engine.playable_roles()
            if role["id"] not in assigned_role_ids
        ]

        if not available_roles:
            raise ValueError(
                "The story has no unused playable role "
                "for another player."
            )

        current_round = int(
            game.get(
                "current_round",
                0,
            )
            or 0
        )

        if status == self.STATUS_LOBBY:
            joined_round = 0
        else:
            joined_round = current_round + 1

        player = await self.db.add_player(
            game_id=game_id,
            user_id=user_id,
            username=username or "",
            display_name=display_name or "Player",
            joined_round=joined_round,
            status=self.PLAYER_PENDING,
        )

        await self.db.append_event(
            game_id=game_id,
            event_type="player_joined",
            round_number=current_round,
            user_id=user_id,
            payload={
                "joined_round": joined_round,
                "display_name": display_name or "Player",
            },
        )

        return player

    async def leave_game(
        self,
        *,
        game_id: str,
        user_id: int,
    ) -> None:
        player = await self.db.get_player(
            game_id,
            user_id,
        )

        if not player:
            return

        status = self.player_status(
            player
        )

        if status in {
            self.PLAYER_LEFT,
            self.PLAYER_ELIMINATED,
        }:
            return

        await self.db.update_player(
            game_id,
            user_id,
            {
                "status": self.PLAYER_LEFT,
            },
        )

        await self.db.append_event(
            game_id=game_id,
            event_type="player_left",
            user_id=user_id,
            payload={},
        )

    async def eliminate_player(
        self,
        *,
        game_id: str,
        user_id: int,
    ) -> None:
        await self.db.eliminate_player(
            game_id,
            user_id,
        )

        await self.db.append_event(
            game_id=game_id,
            event_type="player_eliminated",
            user_id=user_id,
            payload={},
        )

    async def convert_player_to_npc(
        self,
        *,
        game_id: str,
        user_id: int,
    ) -> None:
        await self.db.convert_player_to_npc(
            game_id,
            user_id,
        )

        await self.db.append_event(
            game_id=game_id,
            event_type="player_converted_to_npc",
            user_id=user_id,
            payload={},
        )

    # ============================================================
    # PLAYER COUNT / ROLE ASSIGNMENT
    # ============================================================

    @staticmethod
    def validate_player_count(
        player_count: int,
        engine: GameEngine,
    ) -> None:
        if player_count < 1:
            raise ValueError(
                "At least one player is required."
            )

        playable_count = len(
            engine.playable_roles()
        )

        if player_count > playable_count:
            raise ValueError(
                f"Story has only {playable_count} playable roles "
                f"but requires {player_count}."
            )

    async def assign_roles(
        self,
        *,
        game_id: str,
    ) -> dict[int, str]:
        game, players = await self.get_game_with_players(
            game_id
        )

        engine = self.engine_for_game(
            game
        )

        eligible_players = [
            player
            for player in players
            if self.player_status(player)
            in {
                self.PLAYER_PENDING,
                self.PLAYER_ACTIVE,
            }
        ]

        if not eligible_players:
            raise ValueError(
                "There are no players to assign roles to."
            )

        self.validate_player_count(
            len(eligible_players),
            engine,
        )

        # Never reuse an already assigned role.
        existing_assignments: dict[str, int] = {}

        for player in players:
            role_id = player.get(
                "role_id"
            )

            if not role_id:
                continue

            if self.player_status(player) in {
                self.PLAYER_LEFT,
            }:
                continue

            if role_id in existing_assignments:
                raise DatabaseConflict(
                    f"Role {role_id} is already assigned to "
                    "more than one player."
                )

            existing_assignments[role_id] = int(
                player["user_id"]
            )

        available_roles = [
            role["id"]
            for role in engine.playable_roles()
            if role["id"]
            not in existing_assignments
        ]

        unassigned = [
            player
            for player in eligible_players
            if not player.get("role_id")
        ]

        if len(available_roles) < len(unassigned):
            raise ValueError(
                "Not enough unused playable roles remain."
            )

        # Deterministic assignment.
        unassigned.sort(
            key=lambda player: str(
                player["user_id"]
            )
        )

        available_roles.sort()

        assignments: dict[int, str] = {}

        for player, role_id in zip(
            unassigned,
            available_roles,
        ):
            user_id = int(
                player["user_id"]
            )

            await self.db.assign_role(
                game_id,
                user_id,
                role_id,
            )

            assignments[user_id] = role_id

        return assignments

    # ============================================================
    # START GAME
    # ============================================================

    async def start_game(
        self,
        *,
        game_id: str,
    ) -> dict[str, Any]:
        game, players = await self.get_game_with_players(
            game_id
        )

        if game.get("status") not in {
            self.STATUS_LOBBY,
            self.STATUS_STARTING,
        }:
            raise ValueError(
                "Game cannot be started from its current state."
            )

        active_players = [
            player
            for player in players
            if self.is_counted_player(
                player
            )
        ]

        if not active_players:
            raise ValueError(
                "At least one player is required."
            )

        engine = self.engine_for_game(
            game
        )

        self.validate_player_count(
            len(active_players),
            engine,
        )

        await self.assign_roles(
            game_id=game_id
        )

        # Reload after role assignment so the database is the
        # source of truth.
        players = await self.db.get_players(
            game_id
        )

        scene_id = engine.first_scene_id()

        scene = engine.get_scene(
            scene_id
        )

        world_state = engine.enter_scene(
            game.get(
                "world_state"
            )
            or engine.initial_world_state(),
            scene_id,
        )

        timer_seconds = min(
            self.maximum_decision_seconds,
            max(
                self.minimum_decision_seconds,
                engine.timer_seconds(scene),
            ),
        )

        deadline = (
            self.now_utc()
            + timedelta(
                seconds=timer_seconds
            )
        )

        started = await self.db.start_game_atomic(
            game_id,
            scene_id=scene_id,
            round_number=1,
            decision_deadline=self.to_iso(
                deadline
            ),
            world_state=world_state,
        )

        await self.db.append_event(
            game_id=game_id,
            event_type="game_started",
            round_number=1,
            payload={
                "scene_id": scene_id,
                "decision_deadline": self.to_iso(
                    deadline
                ),
            },
        )

        return started

    # ============================================================
    # DECISIONS
    # ============================================================

    async def submit_decision(
        self,
        *,
        game_id: str,
        user_id: int,
        choice_id: str,
    ) -> dict[str, Any]:
        game = await self.get_game(
            game_id
        )

        if game.get("status") != self.STATUS_PLAYING:
            raise ValueError(
                "The game is not currently accepting decisions."
            )

        round_number = int(
            game.get(
                "current_round",
                0,
            )
            or 0
        )

        scene_id = game.get(
            "current_scene_id"
        )

        if not scene_id:
            raise ValueError(
                "Game has no current scene."
            )

        player = await self.db.get_player(
            game_id,
            user_id,
        )

        if not player:
            raise ValueError(
                "You are not a player in this game."
            )

        if self.player_status(player) != self.PLAYER_ACTIVE:
            raise ValueError(
                "You are not currently an active player."
            )

        joined_round = int(
            player.get(
                "joined_round",
                0,
            )
            or 0
        )

        if joined_round > round_number:
            raise ValueError(
                "You joined after this round began."
            )

        deadline = self.parse_timestamp(
            game.get(
                "decision_deadline"
            )
        )

        if deadline is not None:
            if self.now_utc() >= deadline:
                # Timeout handling and this request can race.
                # The resolver below is the authoritative arbiter.
                await self.resolve_round_if_due(
                    game_id=game_id,
                    expected_round=round_number,
                )

                raise ValueError(
                    "The decision deadline has expired."
                )

        engine = self.engine_for_game(
            game
        )

        scene = engine.get_scene(
            scene_id
        )

        role_id = player.get(
            "role_id"
        )

        if not role_id:
            raise ValueError(
                "Player has no assigned role."
            )

        world_state = (
            game.get(
                "world_state"
            )
            or await self.db.get_world_state(
                game_id
            )
            or engine.initial_world_state()
        )

        engine.validate_choice_for_role(
            scene,
            str(role_id),
            choice_id,
            world_state,
        )

        created = await self.db.save_decision(
            game_id=game_id,
            round_number=round_number,
            scene_id=scene_id,
            user_id=user_id,
            choice_id=choice_id,
        )

        if not created:
            existing = await self.db.get_decision(
                game_id,
                round_number,
                user_id,
            )

            return {
                "accepted": False,
                "duplicate": True,
                "decision": existing,
            }

        await self.db.reset_missed_decisions(
            game_id,
            user_id,
        )

        await self.db.append_event(
            game_id=game_id,
            event_type="decision_submitted",
            round_number=round_number,
            user_id=user_id,
            payload={
                "scene_id": scene_id,
                "choice_id": choice_id,
            },
        )

        result = await self.resolve_if_ready(
            game_id=game_id
        )

        return {
            "accepted": True,
            "duplicate": False,
            "resolved": bool(result),
            "resolution": result,
        }

    # ============================================================
    # ROUND RESOLUTION
    # ============================================================

    def resolution_key(
        self,
        game_id: str,
        round_number: int,
    ) -> str:
        raw = (
            f"{game_id}:"
            f"{round_number}"
        )

        return hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()

    async def _resolve_round(
        self,
        *,
        game: dict[str, Any],
        players: list[dict[str, Any]],
        decisions: list[dict[str, Any]],
        round_number: int,
        force_timeout: bool,
    ) -> dict[str, Any]:
        engine = self.engine_for_game(
            game
        )

        scene_id = game.get(
            "current_scene_id"
        )

        if not scene_id:
            raise ValueError(
                "Game has no current scene."
            )

        world_state = (
            game.get(
                "world_state"
            )
            or await self.db.get_world_state(
                game["id"]
            )
            or engine.initial_world_state()
        )

        eligible_players = [
            player
            for player in players
            if self.is_required_for_round(
                player,
                round_number,
            )
        ]

        if not force_timeout:
            human_players = [
                player
                for player in eligible_players
                if self.player_status(player)
                == self.PLAYER_ACTIVE
            ]

            decision_users = {
                int(
                    decision["user_id"]
                )
                for decision in decisions
            }

            if any(
                int(player["user_id"])
                not in decision_users
                for player in human_players
            ):
                raise RuntimeError(
                    "Round is not ready for resolution."
                )

        result = engine.resolve_round(
            scene_id=scene_id,
            round_number=round_number,
            world_state=world_state,
            players=eligible_players,
            decisions=decisions,
        )

        # Persist missed-decision state.
        #
        # This is intentionally done before the atomic round
        # completion so that the database remains authoritative.
        for missed in result.get(
            "missed_results",
            [],
        ):
            user_id = int(
                missed["user_id"]
            )

            new_count = int(
                missed["missed_decisions"]
            )

            await self.db.increment_missed_decisions(
                game["id"],
                user_id,
            )

            if new_count >= self.MAX_MISSED_DECISIONS:
                await self.db.convert_player_to_npc(
                    game["id"],
                    user_id,
                )

                await self.db.append_event(
                    game_id=game["id"],
                    event_type="player_converted_to_npc",
                    round_number=round_number,
                    user_id=user_id,
                    payload={
                        "reason": "missed_decisions",
                        "missed_decisions": new_count,
                    },
                )

            else:
                await self.db.append_event(
                    game_id=game["id"],
                    event_type="decision_missed",
                    round_number=round_number,
                    user_id=user_id,
                    payload={
                        "missed_decisions": new_count,
                    },
                )

        ending = result.get(
            "ending"
        )

        next_scene_id = result.get(
            "next_scene"
        )

        if ending:
            next_round_number = None
            next_deadline = None
            game_status = self.STATUS_COMPLETED

        elif next_scene_id:
            next_round_number = (
                round_number + 1
            )

            next_scene = engine.get_scene(
                next_scene_id
            )

            timer_seconds = min(
                self.maximum_decision_seconds,
                max(
                    self.minimum_decision_seconds,
                    engine.timer_seconds(
                        next_scene
                    ),
                ),
            )

            next_deadline = (
                self.now_utc()
                + timedelta(
                    seconds=timer_seconds
                )
            )

            next_deadline_iso = self.to_iso(
                next_deadline
            )

            result["next_deadline"] = (
                next_deadline_iso
            )

            game_status = self.STATUS_PLAYING

        else:
            raise ValueError(
                "Round produced neither an ending nor "
                "a next scene."
            )

        resolution_key = self.resolution_key(
            game["id"],
            round_number,
        )

        claimed = await self.db.claim_round_resolution(
            game["id"],
            round_number,
            resolution_key,
        )

        if not claimed:
            # Another worker/callback owns the resolution.
            # This is expected during timeout/click races.
            return {
                "resolved": False,
                "already_claimed": True,
                "round_number": round_number,
            }

        if ending:
            deadline_iso = None
        else:
            deadline_iso = result["next_deadline"]

        completed = await self.db.complete_round_atomic(
            game_id=game["id"],
            round_number=round_number,
            next_scene_id=next_scene_id,
            next_round_number=next_round_number,
            decision_deadline=deadline_iso,
            world_state=result["world_state"],
            game_status=game_status,
            resolution_key=resolution_key,
        )

        await self.db.append_event(
            game_id=game["id"],
            event_type=(
                "game_completed"
                if ending
                else "round_resolved"
            ),
            round_number=round_number,
            payload={
                "scene_id": scene_id,
                "next_scene_id": next_scene_id,
                "ending": ending,
                "forced_timeout": force_timeout,
            },
        )

        return {
            "resolved": True,
            "already_claimed": False,
            "round_number": round_number,
            "result": result,
            "game": completed,
        }

    async def resolve_if_ready(
        self,
        *,
        game_id: str,
    ) -> dict[str, Any] | None:
        game, players = await self.get_game_with_players(
            game_id
        )

        if game.get("status") != self.STATUS_PLAYING:
            return None

        round_number = int(
            game.get(
                "current_round",
                0,
            )
            or 0
        )

        decisions = await self.db.get_round_decisions(
            game_id,
            round_number,
        )

        required_players = [
            player
            for player in players
            if self.is_required_for_round(
                player,
                round_number,
            )
        ]

        required_user_ids = {
            int(player["user_id"])
            for player in required_players
            if self.player_status(player)
            == self.PLAYER_ACTIVE
        }

        decision_user_ids = {
            int(decision["user_id"])
            for decision in decisions
        }

        if not required_user_ids.issubset(
            decision_user_ids
        ):
            return None

        return await self._resolve_round(
            game=game,
            players=players,
            decisions=decisions,
            round_number=round_number,
            force_timeout=False,
        )

    async def resolve_round_if_due(
        self,
        *,
        game_id: str,
        expected_round: int | None = None,
    ) -> dict[str, Any] | None:
        game, players = await self.get_game_with_players(
            game_id
        )

        if game.get("status") != self.STATUS_PLAYING:
            return None

        round_number = int(
            game.get(
                "current_round",
                0,
            )
            or 0
        )

        if (
            expected_round is not None
            and round_number != expected_round
        ):
            return None

        deadline = self.parse_timestamp(
            game.get(
                "decision_deadline"
            )
        )

        if deadline is None:
            raise DatabaseError(
                "Playing game has no decision deadline."
            )

        if self.now_utc() < deadline:
            return None

        decisions = await self.db.get_round_decisions(
            game_id,
            round_number,
        )

        return await self._resolve_round(
            game=game,
            players=players,
            decisions=decisions,
            round_number=round_number,
            force_timeout=True,
        )

    # Backwards-compatible public method.
    async def resolve_round(
        self,
        *,
        game_id: str,
    ) -> dict[str, Any] | None:
        game, players = await self.get_game_with_players(
            game_id
        )

        if game.get("status") != self.STATUS_PLAYING:
            return None

        round_number = int(
            game.get(
                "current_round",
                0,
            )
            or 0
        )

        decisions = await self.db.get_round_decisions(
            game_id,
            round_number,
        )

        return await self._resolve_round(
            game=game,
            players=players,
            decisions=decisions,
            round_number=round_number,
            force_timeout=True,
        )

    # ============================================================
    # START/LOBBY RECOVERY
    # ============================================================

    async def finish_lobby(
        self,
        game_id: str,
    ) -> dict[str, Any] | None:
        game = await self.get_game(
            game_id
        )

        if game.get("status") != self.STATUS_LOBBY:
            return None

        deadline = self.parse_timestamp(
            game.get(
                "join_deadline"
            )
        )

        if deadline is not None:
            if self.now_utc() < deadline:
                return None

        players = await self.db.get_players(
            game_id
        )

        eligible = [
            player
            for player in players
            if self.is_counted_player(
                player
            )
        ]

        if not eligible:
            await self.db.cancel_game_atomic(
                game_id
            )

            await self.db.append_event(
                game_id=game_id,
                event_type="game_cancelled",
                round_number=0,
                payload={
                    "reason": "empty_lobby",
                },
            )

            return None

        try:
            return await self.start_game(
                game_id=game_id
            )

        except DatabaseConflict:
            # Another worker started it.
            return await self.db.get_game(
                game_id
            )

    async def recover_game(
        self,
        game: dict[str, Any],
    ) -> dict[str, Any] | None:
        game_id = str(
            game["id"]
        )

        status = game.get(
            "status"
        )

        if status == self.STATUS_LOBBY:
            return await self.finish_lobby(
                game_id
            )

        if status == self.STATUS_PLAYING:
            return await self.resolve_round_if_due(
                game_id=game_id
            )

        if status == self.STATUS_RESOLVING:
            # A previous worker may have died after claiming
            # resolution. The database remains authoritative.
            #
            # Re-read the game and only attempt recovery when the
            # stored resolution state permits it.
            logger.info(
                "Recovering resolving game %s",
                game_id,
            )

            refreshed = await self.db.get_game(
                game_id
            )

            if not refreshed:
                return None

            return await self.resolve_round_if_due(
                game_id=game_id
            )

        return None

    async def recover_active_games(
        self,
    ) -> list[dict[str, Any]]:
        games = await self.db.get_recoverable_games()

        results: list[dict[str, Any]] = []

        for game in games:
            try:
                result = await self.recover_game(
                    game
                )

                if result is not None:
                    results.append(
                        result
                    )

            except Exception:
                logger.exception(
                    "Failed to recover game %s",
                    game.get("id"),
                )

        return results

    async def recover_all_due_games(
        self,
    ) -> list[dict[str, Any]]:
        """
        Recovery entry point for application startup.

        The database supplies recoverable games. No Python
        process-local timer is required.
        """

        results: list[dict[str, Any]] = []

        try:
            pending_lobbies = (
                await self.db.get_pending_lobbies()
            )

            for game in pending_lobbies:
                try:
                    result = await self.finish_lobby(
                        game["id"]
                    )

                    if result:
                        results.append(
                            result
                        )

                except Exception:
                    logger.exception(
                        "Failed to recover lobby %s",
                        game.get("id"),
                    )

        except Exception:
            logger.exception(
                "Failed to recover pending lobbies."
            )

        try:
            expired_games = (
                await self.db.get_expired_decision_games()
            )

            for game in expired_games:
                try:
                    result = await self.resolve_round_if_due(
                        game_id=game["id"]
                    )

                    if result:
                        results.append(
                            result
                        )

                except Exception:
                    logger.exception(
                        "Failed to recover expired game %s",
                        game.get("id"),
                    )

        except Exception:
            logger.exception(
                "Failed to recover expired games."
            )

        return results

    # ============================================================
    # ROLE / UI DATA
    # ============================================================

    async def get_player_role(
        self,
        *,
        game_id: str,
        user_id: int,
    ) -> dict[str, Any] | None:
        game = await self.get_game(
            game_id
        )

        player = await self.db.get_player(
            game_id,
            user_id,
        )

        if not player:
            return None

        role_id = player.get(
            "role_id"
        )

        if not role_id:
            return None

        engine = self.engine_for_game(
            game
        )

        return engine.get_role(
            str(role_id)
        )

    async def get_player_choices(
        self,
        *,
        game_id: str,
        user_id: int,
    ) -> list[dict[str, Any]]:
        game = await self.get_game(
            game_id
        )

        player = await self.db.get_player(
            game_id,
            user_id,
        )

        if not player:
            raise ValueError(
                "Player does not exist."
            )

        role_id = player.get(
            "role_id"
        )

        if not role_id:
            return []

        scene_id = game.get(
            "current_scene_id"
        )

        if not scene_id:
            return []

        engine = self.engine_for_game(
            game
        )

        scene = engine.get_scene(
            scene_id
        )

        world_state = (
            game.get(
                "world_state"
            )
            or await self.db.get_world_state(
                game_id
            )
            or engine.initial_world_state()
        )

        return engine.choices_for_role(
            scene,
            str(role_id),
            world_state,
        )

    # ============================================================
    # EVENTS
    # ============================================================

    async def record_event(
        self,
        *,
        game_id: str,
        event_type: str,
        round_number: int | None = None,
        user_id: int | None = None,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> None:
        await self.db.append_event(
            game_id=game_id,
            event_type=event_type,
            round_number=round_number,
            user_id=user_id,
            payload=payload or {},
            event_id=event_id,
        )
