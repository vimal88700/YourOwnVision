from future import annotations

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

Telegram must call this service rather than manipulating
gameplay state directly.

Architecture:

    Telegram
        ↓
    GameService
        ↓
    Database  ← persistent source of truth
        ↑
    GameEngine ← deterministic gameplay
        ↑
    StoryGenerator ← Gemini content preparation only

This class deliberately does NOT:

    - import Telegram
    - send Telegram messages
    - use Telegram polling
    - keep authoritative state in memory
    - call Gemini for player decisions
    - own authoritative timers

Persistent state belongs in Supabase.
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

def __init__(
    self,
    db: Database,
    story_generator: StoryGenerator,
    *,
    join_seconds: int = 60,
    default_decision_seconds: int = 35,
) -> None:

    self.db = db
    self.story_generator = story_generator

    self.join_seconds = max(
        1,
        int(join_seconds),
    )

    self.default_decision_seconds = max(
        10,
        int(default_decision_seconds),
    )

# ============================================================
# TIME HELPERS
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

    if isinstance(
        value,
        datetime,
    ):
        result = value

    elif isinstance(
        value,
        str,
    ):
        try:
            result = datetime.fromisoformat(
                value.replace(
                    "Z",
                    "+00:00",
                )
            )
        except ValueError:
            logger.warning(
                "Invalid timestamp from database: %r",
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

        # Defensive second validation at the engine boundary.
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
# CREATE GAME
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

    # We cannot know the final player count yet.
    #
    # Generate a story using the minimum player count.
    # The story validator/engine still ensures that the story
    # contains enough playable roles for later joins.
    story, fingerprint = (
        await self.generate_unique_story(
            player_count=1
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
        story_player_count=1,
    )

    # Story history is deliberately persisted separately from
    # the current game state.
    try:
        await self.db.save_story_history(
            fingerprint,
            story,
        )
    except DatabaseConflict:
        # The database uniqueness constraint won a race.
        # The game itself remains valid only if its fingerprint
        # was accepted by the games transaction.
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
# PLAYER LIFECYCLE
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

    # Compatibility with an older schema.
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

    if status != cls.PLAYER_ACTIVE:
        return False

    joined_round = int(
        player.get(
            "joined_round",
            0,
        )
        or 0
    )

    return joined_round <= round_number

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

        return existing

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

    if counted >= self.MAX_PLAYERS:
        raise ValueError(
            "The game has reached its player limit."
        )

    engine = self.engine_for_game(
        game
    )

    playable_count = len(
        engine.playable_roles()
    )

    if counted >= playable_count:
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
        # A player joining during gameplay becomes eligible
        # only from the following round.
        joined_round = (
            current_round + 1
        )

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
            "display_name": display_name,
        },
    )

    return player

# ============================================================
# PLAYER STATUS
# ============================================================

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
# PLAYER COUNT
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
            f"Story contains only {playable_count} "
            f"playable roles for {player_count} players."
        )

# ============================================================
# ROLE ASSIGNMENT
# ============================================================

async def assign_roles(
    self,
    game_id: str,
) -> dict[str, str]:

    game, players = (
        await self.get_game_with_players(
            game_id
        )
    )

    engine = self.engine_for_game(
        game
    )

    eligible = [
        player
        for player in players
        if self.player_status(
            player
        )
        in {
            self.PLAYER_PENDING,
            self.PLAYER_ACTIVE,
        }
    ]

    self.validate_player_count(
        len(eligible),
        engine,
    )

    # If every eligible player already has a role, never
    # reassign. This protects against accidental role reuse.
    existing_roles = [
        player.get(
            "role_id"
        )
        for player in eligible
        if player.get(
            "role_id"
        )
    ]

    if len(existing_roles) != len(
        set(existing_roles)
    ):
        raise DatabaseConflict(
            "Persisted player state already contains "
            "duplicate role assignments."
        )

    missing = [
        player
        for player in eligible
        if not player.get(
            "role_id"
        )
    ]

    if not missing:
        return {
            str(
                player["user_id"]
            ): player["role_id"]
            for player in eligible
        }

    # Existing roles are reserved permanently.
    reserved = set(
        existing_roles
    )

    available_roles = [
        role
        for role in engine.playable_roles()
        if role["id"] not in reserved
    ]

    if len(available_roles) < len(missing):
        raise ValueError(
            "There are not enough unused playable roles."
        )

    # Stable deterministic assignment.
    missing_ids = sorted(
        (
            int(
                player["user_id"]
            )
            for player in missing
        )
    )

    available_roles.sort(
        key=lambda role: role["id"]
    )

    assignments: dict[str, str] = {}

    for index, user_id in enumerate(
        missing_ids
    ):
        assignments[
            str(user_id)
        ] = available_roles[
            index
        ]["id"]

    # Persist each role. The database uniqueness constraints
    # are the final protection against duplicate membership/
    # role state.
    for user_id_string, role_id in assignments.items():

        user_id = int(
            user_id_string
        )

        player = await self.db.get_player(
            game_id,
            user_id,
        )

        if not player:
            raise DatabaseConflict(
                "Player disappeared while assigning roles."
            )

        existing_role = player.get(
            "role_id"
        )

        if existing_role:

            if existing_role != role_id:
                raise DatabaseConflict(
                    "A role assignment changed concurrently."
                )

            continue

        await self.db.assign_role(
            game_id,
            user_id,
            role_id,
        )

        await self.db.append_event(
            game_id=game_id,
            event_type="role_assigned",
            user_id=user_id,
            payload={
                "role_id": role_id,
            },
        )

    # Return the complete persisted assignment.
    refreshed = await self.db.get_players(
        game_id
    )

    return {
        str(
            player["user_id"]
        ): player["role_id"]
        for player in refreshed
        if player.get(
            "role_id"
        )
    }

# ============================================================
# START GAME
# ============================================================

async def start_game(
    self,
    game_id: str,
) -> dict[str, Any]:

    game, players = (
        await self.get_game_with_players(
            game_id
        )
    )

    if game.get(
        "status"
    ) != self.STATUS_LOBBY:
        raise ValueError(
            "Only a lobby can be started."
        )

    engine = self.engine_for_game(
        game
    )

    eligible = [
        player
        for player in players
        if self.player_status(
            player
        )
        in {
            self.PLAYER_PENDING,
            self.PLAYER_ACTIVE,
        }
    ]

    self.validate_player_count(
        len(eligible),
        engine,
    )

    assignments = await self.assign_roles(
        game_id
    )

    first_scene_id = (
        engine.first_scene_id()
    )

    world_state = (
        engine.initial_world_state()
    )

    world_state = engine.enter_scene(
        world_state,
        first_scene_id,
    )

    scene = engine.get_scene(
        first_scene_id
    )

    deadline = (
        self.now_utc()
        + timedelta(
            seconds=engine.timer_seconds(
                scene
            )
        )
    )

    # The PostgreSQL RPC is authoritative for the transition.
    # It must lock the game row and reject a concurrent start.
    started = await self.db.start_game_atomic(
        game_id,
        scene_id=first_scene_id,
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
            "scene_id": first_scene_id,
            "decision_deadline": self.to_iso(
                deadline
            ),
            "assignments": assignments,
        },
    )

    return {
        "game": started,
        "scene": scene,
        "assignments": assignments,
        "decision_deadline": self.to_iso(
            deadline
        ),
    }

# ============================================================
# DECISION SUBMISSION
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

    if game.get(
        "status"
    ) != self.STATUS_PLAYING:
        raise ValueError(
            "This game is not accepting decisions."
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

    if round_number < 1 or not scene_id:
        raise ValueError(
            "The current round is not valid."
        )

    deadline = self.parse_timestamp(
        game.get(
            "decision_deadline"
        )
    )

    if (
        deadline is not None
        and self.now_utc() >= deadline
    ):
        raise ValueError(
            "The decision deadline has passed."
        )

    player = await self.db.get_player(
        game_id,
        user_id,
    )

    if not player:
        raise ValueError(
            "You are not a member of this game."
        )

    if self.player_status(
        player
    ) != self.PLAYER_ACTIVE:
        raise ValueError(
            "You are not an active player."
        )

    if not self.is_required_for_round(
        player,
        round_number,
    ):
        raise ValueError(
            "You are not participating in this round."
        )

    role_id = player.get(
        "role_id"
    )

    if not role_id:
        raise ValueError(
            "Your role has not been assigned."
        )

    engine = self.engine_for_game(
        game
    )

    scene = engine.get_scene(
        scene_id
    )

    # Validate before persistence.
    engine.validate_choice_for_role(
        scene,
        role_id,
        choice_id,
        game.get(
            "world_state"
        )
        or {},
    )

    created = await self.db.save_decision(
        game_id=game_id,
        round_number=round_number,
        scene_id=scene_id,
        user_id=user_id,
        choice_id=choice_id,
    )

    if not created:
        raise DatabaseConflict(
            "A decision has already been submitted "
            "for this player and round."
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

    decisions = await self.db.get_round_decisions(
        game_id,
        round_number,
    )

    required = [
        player
        for player in await self.db.get_players(
            game_id
        )
        if self.is_required_for_round(
            player,
            round_number,
        )
    ]

    decided_users = {
        int(
            decision["user_id"]
        )
        for decision in decisions
    }

    all_decided = all(
        int(
            player["user_id"]
        )
        in decided_users
        for player in required
    )

    return {
        "accepted": True,
        "all_decided": all_decided,
        "game_id": game_id,
        "round_number": round_number,
        "scene_id": scene_id,
        "choice_id": choice_id,
    }

# ============================================================
# ROUND RESOLUTION
# ============================================================

async def resolve_round(
    self,
    game_id: str,
) -> dict[str, Any] | None:

    game = await self.get_game(
        game_id
    )

    status = game.get(
        "status"
    )

    if status not in {
        self.STATUS_PLAYING,
        self.STATUS_RESOLVING,
    }:
        return None

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

    if round_number < 1 or not scene_id:
        return None

    # One unique resolution key identifies this exact
    # game/round resolution attempt.
    resolution_key = (
        f"{game_id}:round:{round_number}"
    )

    claimed = await self.db.claim_round_resolution(
        game_id,
        round_number,
        resolution_key,
    )

    if not claimed:
        return None

    try:

        # Re-read AFTER claiming.
        game = await self.get_game(
            game_id
        )

        engine = self.engine_for_game(
            game
        )

        players = await self.db.get_players(
            game_id
        )

        decisions = await self.db.get_round_decisions(
            game_id,
            round_number,
        )

        result = engine.resolve_round(
            scene_id=scene_id,
            round_number=round_number,
            world_state=game.get(
                "world_state"
            )
            or {},
            players=players,
            decisions=decisions,
        )

        # Persist missed-decision state before the atomic round
        # completion. The database increment itself is atomic.
        #
        # The final SQL migration will tighten this into the
        # complete round transaction.
        for missed in result.get(
            "missed_results",
            [],
        ):

            user_id = int(
                missed["user_id"]
            )

            new_count = (
                await self.db.increment_missed_decisions(
                    game_id,
                    user_id,
                )
            )

            if new_count >= 3:

                await self.db.eliminate_player(
                    game_id,
                    user_id,
                )

                await self.db.append_event(
                    game_id=game_id,
                    event_type="player_eliminated_for_missed_decisions",
                    round_number=round_number,
                    user_id=user_id,
                    payload={
                        "missed_decisions": new_count,
                    },
                )

            else:

                await self.db.append_event(
                    game_id=game_id,
                    event_type="decision_missed",
                    round_number=round_number,
                    user_id=user_id,
                    payload={
                        "missed_decisions": new_count,
                    },
                )

        for decision in decisions:

            user_id = int(
                decision["user_id"]
            )

            await self.db.reset_missed_decisions(
                game_id,
                user_id,
            )

        ending = result.get(
            "ending"
        )

        next_scene_id = result.get(
            "next_scene"
        )

        if ending:

            game_status = (
                self.STATUS_COMPLETED
            )

            next_round_number = (
                round_number
            )

            next_deadline = None

        elif next_scene_id:

            next_scene = engine.get_scene(
                next_scene_id
            )

            next_round_number = (
                round_number + 1
            )

            next_deadline_dt = (
                self.now_utc()
                + timedelta(
                    seconds=engine.timer_seconds(
                        next_scene
                    )
                )
            )

            next_deadline = self.to_iso(
                next_deadline_dt
            )

            game_status = (
                self.STATUS_PLAYING
            )

        else:

            game_status = (
                self.STATUS_COMPLETED
            )

            next_round_number = (
                round_number
            )

            next_deadline = None

        completed = (
            await self.db.complete_round_atomic(
                game_id=game_id,
                round_number=round_number,
                next_scene_id=next_scene_id,
                next_round_number=next_round_number,
                decision_deadline=next_deadline,
                world_state=result[
                    "world_state"
                ],
                game_status=game_status,
                resolution_key=resolution_key,
            )
        )

        await self.db.append_event(
            game_id=game_id,
            event_type="round_resolved",
            round_number=round_number,
            payload={
                "scene_id": scene_id,
                "next_scene_id": next_scene_id,
                "next_round_number": next_round_number,
                "ending": ending,
            },
        )

        return {
            "game_id": game_id,
            "round_number": round_number,
            "scene_id": scene_id,
            "result": result,
            "completed": completed,
        }

    except Exception:

        logger.exception(
            "Round resolution failed for %s round %s.",
            game_id,
            round_number,
        )

        raise

# ============================================================
# RESOLVE WHEN EVERY PLAYER HAS DECIDED
# ============================================================

async def resolve_if_ready(
    self,
    game_id: str,
) -> dict[str, Any] | None:

    game = await self.get_game(
        game_id
    )

    if game.get(
        "status"
    ) != self.STATUS_PLAYING:
        return None

    round_number = int(
        game.get(
            "current_round",
            0,
        )
        or 0
    )

    players = await self.db.get_players(
        game_id
    )

    required = [
        player
        for player in players
        if self.is_required_for_round(
            player,
            round_number,
        )
    ]

    decisions = await self.db.get_round_decisions(
        game_id,
        round_number,
    )

    decided = {
        int(
            decision["user_id"]
        )
        for decision in decisions
    }

    required_ids = {
        int(
            player["user_id"]
        )
        for player in required
    }

    if not required_ids.issubset(
        decided
    ):
        return None

    return await self.resolve_round(
        game_id
    )

# ============================================================
# EXPIRED ROUND
# ============================================================

async def resolve_if_expired(
    self,
    game_id: str,
) -> dict[str, Any] | None:

    game = await self.get_game(
        game_id
    )

    if game.get(
        "status"
    ) != self.STATUS_PLAYING:
        return None

    deadline = self.parse_timestamp(
        game.get(
            "decision_deadline"
        )
    )

    if deadline is None:
        logger.error(
            "Playing game %s has no decision deadline.",
            game_id,
        )
        return None

    if self.now_utc() < deadline:
        return None

    return await self.resolve_round(
        game_id
    )

# ============================================================
# STARTUP RECOVERY
# ============================================================

async def recover_active_games(
    self,
) -> list[dict[str, Any]]:

    """
    Recover games entirely from persistent database state.

    This is called after Render starts/restarts.

    Nothing here depends on an in-memory timer surviving.
    """

    games = await self.db.get_recoverable_games()

    recovery: list[
        dict[str, Any]
    ] = []

    now = self.now_utc()

    for game in games:

        status = game.get(
            "status"
        )

        if status == self.STATUS_LOBBY:

            deadline = self.parse_timestamp(
                game.get(
                    "join_deadline"
                )
            )

            if (
                deadline is not None
                and now >= deadline
            ):
                recovery.append(
                    {
                        "game_id": game["id"],
                        "action": "finish_lobby",
                    }
                )

        elif status in {
            self.STATUS_PLAYING,
            self.STATUS_RESOLVING,
        }:

            deadline = self.parse_timestamp(
                game.get(
                    "decision_deadline"
                )
            )

            if (
                status == self.STATUS_RESOLVING
                or (
                    deadline is not None
                    and now >= deadline
                )
            ):
                recovery.append(
                    {
                        "game_id": game["id"],
                        "action": "resolve_round",
                    }
                )

    return recovery

async def recover_game(
    self,
    game_id: str,
) -> dict[str, Any] | None:

    game = await self.get_game(
        game_id
    )

    status = game.get(
        "status"
    )

    if status == self.STATUS_LOBBY:

        deadline = self.parse_timestamp(
            game.get(
                "join_deadline"
            )
        )

        if (
            deadline is not None
            and self.now_utc() >= deadline
        ):
            return await self.finish_lobby(
                game_id
            )

        return None

    if status in {
        self.STATUS_PLAYING,
        self.STATUS_RESOLVING,
    }:

        return await self.resolve_if_expired(
            game_id
        )

    return None

async def finish_lobby(
    self,
    game_id: str,
) -> dict[str, Any] | None:

    game, players = (
        await self.get_game_with_players(
            game_id
        )
    )

    if game.get(
        "status"
    ) != self.STATUS_LOBBY:
        return None

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
            payload={
                "reason": "no_players",
            },
        )

        return {
            "cancelled": True,
            "reason": "no_players",
        }

    return await self.start_game(
        game_id
    )

async def recover_all_due_games(
    self,
) -> list[dict[str, Any]]:

    items = await self.recover_active_games()

    results: list[
        dict[str, Any]
    ] = []

    for item in items:

        game_id = item[
            "game_id"
        ]

        try:

            result = await self.recover_game(
                game_id
            )

            if result is not None:
                results.append(
                    {
                        "game_id": game_id,
                        "result": result,
                    }
                )

        except Exception:

            logger.exception(
                "Failed recovering game %s.",
                game_id,
            )

    return results

# ============================================================
# PLAYER ROLE / CHOICE ACCESS
# ============================================================

async def get_player_role(
    self,
    game_id: str,
    user_id: int,
) -> dict[str, Any]:

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
        raise ValueError(
            "Player has no assigned role."
        )

    engine = self.engine_for_game(
        game
    )

    return engine.get_role(
        role_id
    )

async def get_player_choices(
    self,
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
        return []

    round_number = int(
        game.get(
            "current_round",
            0,
        )
        or 0
    )

    if not self.is_required_for_round(
        player,
        round_number,
    ):
        return []

    role_id = player.get(
        "role_id"
    )

    scene_id = game.get(
        "current_scene_id"
    )

    if not role_id or not scene_id:
        return []

    engine = self.engine_for_game(
        game
    )

    scene = engine.get_scene(
        scene_id
    )

    return engine.choices_for_role(
        scene,
        role_id,
        game.get(
            "world_state"
        )
        or {},
    )

# ============================================================
# GAME EVENTS
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
) -> dict[str, Any]:

    return await self.db.append_event(
        game_id=game_id,
        event_type=event_type,
        round_number=round_number,
        user_id=user_id,
        payload=payload or {},
        event_id=event_id,
    )
