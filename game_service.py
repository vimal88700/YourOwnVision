from future import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from database import Database
from engine import GameEngine
from story_generator import StoryGenerator

logger = logging.getLogger("YourOwnVision.game_service")

class GameService:
"""
Application/game orchestration layer.

Architecture:

    Telegram
        ↓
    GameService
        ↓
    Database ← authoritative persistent state
        ↑
    GameEngine ← deterministic gameplay
        ↑
    StoryGenerator ← Gemini content preparation only

GameService is intentionally independent of Telegram.

It does not:
    - import telegram
    - send Telegram messages
    - run Telegram polling
    - own persistent timers
    - keep authoritative game state in Python
    - call Gemini for player decisions
"""

ACTIVE_STATUSES = {
    "lobby",
    "active",
    "resolving",
}

LOBBY_STATUS = "lobby"
ACTIVE_STATUS = "active"
RESOLVING_STATUS = "resolving"
ENDED_STATUS = "ended"

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
    join_seconds: int = 45,
    default_decision_seconds: int = 35,
):
    self.db = db
    self.story_generator = story_generator

    self.join_seconds = max(
        1,
        int(join_seconds),
    )

    self.default_decision_seconds = max(
        1,
        int(default_decision_seconds),
    )

# ============================================================
# TIME
# ============================================================

@staticmethod
def now_utc() -> datetime:
    return datetime.now(
        timezone.utc
    )

@staticmethod
def iso(
    value: datetime,
) -> str:
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
        raise ValueError(
            "There is already an active game "
            "in this chat."
        )

    join_deadline = self.now_utc() + timedelta(
        seconds=self.join_seconds
    )

    # --------------------------------------------------------
    # At creation time there are no players yet.
    #
    # The story is generated once for the game, then frozen.
    # --------------------------------------------------------

    story, fingerprint = (
        await self._generate_unique_story(
            player_count=1
        )
    )

    engine = GameEngine(
        story
    )

    world_state = (
        engine.initial_world_state()
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # The final database implementation should enforce:
    #   - one active game per chat
    #   - unique story fingerprint
    #   - atomic creation
    #
    # If the RPC reports a conflict, another worker won the
    # race and this operation must not create a second game.
    # --------------------------------------------------------

    game = await self.db.start_game_atomic(
        chat_id=chat_id,
        creator_id=creator_id,
        story=story,
        story_fingerprint=fingerprint,
        join_deadline=self.iso(
            join_deadline
        ),
        world_state=world_state,
    )

    if not game:
        raise RuntimeError(
            "Database did not return the created game."
        )

    return game

async def _generate_unique_story(
    self,
    *,
    player_count: int,
    max_attempts: int = 5,
) -> tuple[
    dict[str, Any],
    str,
]:

    recent = await self.db.get_recent_fingerprints(
        limit=50
    )

    fingerprints = list(
        recent
    )

    for _ in range(
        max_attempts
    ):

        story, fingerprint = (
            await self.story_generator.generate(
                player_count=player_count,
                previous_fingerprints=fingerprints,
            )
        )

        # Defensive validation before GameEngine sees it.
        engine = GameEngine(
            story
        )

        # Player-count validation happens before the story
        # can become gameplay state.
        self.validate_player_count(
            player_count,
            engine,
        )

        if (
            fingerprint
            in fingerprints
        ):
            continue

        if await self.db.fingerprint_exists(
            fingerprint
        ):
            fingerprints.append(
                fingerprint
            )
            continue

        return (
            story,
            fingerprint,
        )

    raise RuntimeError(
        "Could not generate a unique validated story "
        "after several attempts."
    )

# ============================================================
# GAME LOADING
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

    return (
        game,
        players,
    )

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
            "Game has no valid story."
        )

    return GameEngine(
        story
    )

# ============================================================
# PLAYER LIFECYCLE
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

    if status not in self.ACTIVE_STATUSES:
        raise ValueError(
            "This game is no longer accepting players."
        )

    players = await self.db.get_players(
        game_id
    )

    existing = next(
        (
            player
            for player in players
            if int(
                player.get(
                    "user_id"
                )
            )
            == int(user_id)
        ),
        None,
    )

    if existing:

        lifecycle = self.player_lifecycle(
            existing
        )

        if lifecycle == self.PLAYER_LEFT:

            raise ValueError(
                "A player who left the game "
                "cannot rejoin this game."
            )

        return existing

    active_count = sum(
        1
        for player in players
        if self.player_lifecycle(
            player
        )
        in {
            self.PLAYER_PENDING,
            self.PLAYER_ACTIVE,
            self.PLAYER_NPC,
        }
    )

    if active_count >= self.MAX_PLAYERS:
        raise ValueError(
            "The game has reached its player limit."
        )

    engine = self.engine_for_game(
        game
    )

    current_round = int(
        game.get(
            "current_round",
            0,
        )
        or 0
    )

    current_scene_id = game.get(
        "current_scene_id"
    )

    # --------------------------------------------------------
    # Lobby joiners belong to round 0.
    #
    # Late joiners join the NEXT round, not the current one.
    # This prevents them from suddenly receiving a decision
    # for a round that was already in progress.
    # --------------------------------------------------------

    if status == self.LOBBY_STATUS:

        joined_round = 0
        lifecycle = self.PLAYER_PENDING

    else:

        joined_round = (
            current_round + 1
        )
        lifecycle = self.PLAYER_PENDING

        if current_scene_id:

            scene = engine.get_scene(
                current_scene_id
            )

            # The actual role is assigned later by the role
            # assignment operation. We cannot promise a role
            # here yet.
            _ = scene

    player = await self.db.add_player_atomic(
        game_id=game_id,
        user_id=user_id,
        username=username,
        display_name=display_name,
        lifecycle=lifecycle,
        joined_round=joined_round,
    )

    if not player:
        # A concurrent JOIN won the membership race.
        existing = await self.db.get_player(
            game_id,
            user_id,
        )

        if existing:
            return existing

        raise RuntimeError(
            "Unable to add player."
        )

    return player

@classmethod
def player_lifecycle(
    cls,
    player: dict[str, Any],
) -> str:

    lifecycle = player.get(
        "lifecycle"
    )

    if lifecycle in {
        cls.PLAYER_PENDING,
        cls.PLAYER_ACTIVE,
        cls.PLAYER_ELIMINATED,
        cls.PLAYER_NPC,
        cls.PLAYER_LEFT,
    }:
        return lifecycle

    # Compatibility with old rows.
    if player.get(
        "active",
        False,
    ):
        return cls.PLAYER_ACTIVE

    return cls.PLAYER_LEFT

# ============================================================
# ROLE ASSIGNMENT
# ============================================================

async def assign_roles(
    self,
    game_id: str,
) -> dict[str, str]:

    game = await self.get_game(
        game_id
    )

    engine = self.engine_for_game(
        game
    )

    players = await self.db.get_players(
        game_id
    )

    eligible = [
        player
        for player in players
        if self.player_lifecycle(
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

    player_ids = [
        int(
            player["user_id"]
        )
        for player in eligible
    ]

    assignments = engine.assign_roles(
        player_ids
    )

    # --------------------------------------------------------
    # Persist role assignment through a database transaction.
    #
    # The DB must reject:
    #   - duplicate role use
    #   - changing an already-assigned role
    #   - assignment to a non-member
    # --------------------------------------------------------

    result = await self.db.assign_roles_atomic(
        game_id=game_id,
        assignments=assignments,
    )

    if result is None:
        raise RuntimeError(
            "Role assignment did not complete."
        )

    return assignments

def validate_player_count(
    self,
    player_count: int,
    engine: GameEngine,
) -> None:

    if player_count < 1:
        raise ValueError(
            "A game requires at least one player."
        )

    playable_count = len(
        engine.playable_roles()
    )

    if player_count > playable_count:
        raise ValueError(
            f"This story supports only "
            f"{playable_count} playable players, "
            f"but {player_count} players joined."
        )

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
    ) not in {
        self.LOBBY_STATUS,
        self.ACTIVE_STATUS,
    }:
        raise ValueError(
            "Game cannot be started from its current state."
        )

    engine = self.engine_for_game(
        game
    )

    eligible = [
        player
        for player in players
        if self.player_lifecycle(
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

    # Assign only once.
    missing_roles = [
        player
        for player in eligible
        if not player.get(
            "role_id"
        )
    ]

    if missing_roles:

        assignments = await self.assign_roles(
            game_id
        )

    else:
        assignments = {
            str(
                player["user_id"]
            ): player["role_id"]
            for player in eligible
        }

    first_scene_id = (
        engine.first_scene_id()
    )

    world_state = engine.enter_scene(
        engine.initial_world_state(),
        first_scene_id,
    )

    first_scene = engine.get_scene(
        first_scene_id
    )

    decision_seconds = self.decision_seconds(
        first_scene
    )

    deadline = (
        self.now_utc()
        + timedelta(
            seconds=decision_seconds
        )
    )

    # --------------------------------------------------------
    # Atomic state transition:
    #
    # lobby → active
    # current_scene
    # current_round
    # deadline
    # player lifecycle
    #
    # must happen as one database transaction.
    # --------------------------------------------------------

    result = await self.db.activate_game_atomic(
        game_id=game_id,
        scene_id=first_scene_id,
        round_number=1,
        decision_deadline=self.iso(
            deadline
        ),
        world_state=world_state,
    )

    if not result:
        raise RuntimeError(
            "Game could not be activated."
        )

    return {
        "game": result,
        "scene": first_scene,
        "assignments": assignments,
        "decision_deadline": self.iso(
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
    ) != self.ACTIVE_STATUS:
        raise ValueError(
            "The game is not accepting decisions."
        )

    current_round = int(
        game.get(
            "current_round",
            0,
        )
        or 0
    )

    if current_round < 1:
        raise ValueError(
            "The game has not started a round."
        )

    scene_id = game.get(
        "current_scene_id"
    )

    if not scene_id:
        raise ValueError(
            "Game has no current scene."
        )

    engine = self.engine_for_game(
        game
    )

    scene = engine.get_scene(
        scene_id
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
            "This round has already ended."
        )

    player = await self.db.get_player(
        game_id,
        user_id,
    )

    if not player:
        raise ValueError(
            "You are not a player in this game."
        )

    lifecycle = self.player_lifecycle(
        player
    )

    if lifecycle != self.PLAYER_ACTIVE:
        raise ValueError(
            "You are not active in this round."
        )

    joined_round = int(
        player.get(
            "joined_round",
            0,
        )
        or 0
    )

    if joined_round > current_round:
        raise ValueError(
            "You join from the next round."
        )

    role_id = player.get(
        "role_id"
    )

    if not role_id:
        raise ValueError(
            "Your role has not been assigned."
        )

    # --------------------------------------------------------
    # Validate the choice BEFORE attempting persistence.
    # --------------------------------------------------------

    engine.validate_choice_for_role(
        scene,
        role_id,
        choice_id,
        game.get(
            "world_state"
        )
        or {},
    )

    # --------------------------------------------------------
    # Atomic DB operation.
    #
    # This operation must reject:
    #   - duplicate decision
    #   - expired round
    #   - wrong scene
    #   - stale round
    #   - inactive player
    #
    # Therefore a late click cannot overwrite the result of
    # a timeout resolver.
    # --------------------------------------------------------

    result = await self.db.submit_decision_atomic(
        game_id=game_id,
        round_number=current_round,
        scene_id=scene_id,
        user_id=user_id,
        choice_id=choice_id,
        submitted_at=self.iso(
            self.now_utc()
        ),
    )

    if not result:
        raise ValueError(
            "Your decision was already submitted "
            "or the round has closed."
        )

    return {
        "accepted": True,
        "game_id": game_id,
        "round_number": current_round,
        "scene_id": scene_id,
        "choice_id": choice_id,
        "all_decisions_submitted": bool(
            result.get(
                "all_decisions_submitted",
                False,
            )
        ),
        "result": result,
    }

# ============================================================
# ROUND RESOLUTION
# ============================================================

async def resolve_due_round(
    self,
    game_id: str,
) -> dict[str, Any] | None:

    """
    Resolve a round when its persistent database deadline
    has passed.

    This can be called:
        - after a Telegram decision
        - by a JobQueue convenience timer
        - during startup recovery
        - by a periodic recovery loop
        - after Render restarts

    Correctness never depends on which one calls it first.
    """

    game = await self.get_game(
        game_id
    )

    if game.get(
        "status"
    ) != self.ACTIVE_STATUS:
        return None

    deadline = self.parse_timestamp(
        game.get(
            "decision_deadline"
        )
    )

    if deadline is None:
        logger.error(
            "Active game %s has no decision deadline.",
            game_id,
        )
        return None

    if self.now_utc() < deadline:
        return None

    return await self.resolve_round(
        game_id
    )

async def resolve_round(
    self,
    game_id: str,
) -> dict[str, Any] | None:

    game = await self.get_game(
        game_id
    )

    if game.get(
        "status"
    ) != self.ACTIVE_STATUS:
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

    # --------------------------------------------------------
    # DATABASE CLAIM
    #
    # Only one worker may own resolution of this round.
    #
    # This is essential because:
    #   Telegram callback
    #   timeout job
    #   startup recovery
    #   second Render worker
    #
    # may all attempt resolution.
    # --------------------------------------------------------

    claim = await self.db.claim_round_resolution(
        game_id=game_id,
        round_number=round_number,
    )

    if not claim:
        return None

    try:

        # Re-read after the claim. Never resolve using stale
        # state from before claiming the round.
        game = await self.get_game(
            game_id
        )

        if game.get(
            "status"
        ) not in {
            self.ACTIVE_STATUS,
            self.RESOLVING_STATUS,
        }:
            return None

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

        # ----------------------------------------------------
        # Engine calculates the complete deterministic result.
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Persist everything as one atomic round completion.
        #
        # This is the critical transaction:
        #
        #   world state
        #   scene
        #   round
        #   missed decisions
        #   eliminations/NPC state
        #   decisions resolved
        #   events
        #   next deadline
        #
        # must move together.
        # ----------------------------------------------------

        next_scene_id = result.get(
            "next_scene"
        )

        ending = result.get(
            "ending"
        )

        if ending:
            next_status = self.ENDED_STATUS
            next_round = round_number
            next_deadline = None

        elif next_scene_id:
            next_status = self.ACTIVE_STATUS
            next_round = (
                round_number + 1
            )

            next_scene = engine.get_scene(
                next_scene_id
            )

            next_deadline_dt = (
                self.now_utc()
                + timedelta(
                    seconds=self.decision_seconds(
                        next_scene
                    )
                )
            )

            next_deadline = self.iso(
                next_deadline_dt
            )

        else:
            next_status = self.ENDED_STATUS
            next_round = round_number
            next_deadline = None

        completion = (
            await self.db.complete_round_atomic(
                game_id=game_id,
                round_number=round_number,
                scene_id=scene_id,
                next_status=next_status,
                next_scene_id=next_scene_id,
                next_round_number=next_round,
                next_decision_deadline=next_deadline,
                world_state=result[
                    "world_state"
                ],
                missed_results=result[
                    "missed_results"
                ],
                events=result[
                    "events"
                ],
                ending=ending,
            )
        )

        if not completion:
            raise RuntimeError(
                "Atomic round completion failed."
            )

        return {
            "game_id": game_id,
            "round_number": round_number,
            "scene_id": scene_id,
            "result": result,
            "completion": completion,
        }

    except Exception:

        # ----------------------------------------------------
        # If completion fails, release the claim so another
        # recovery attempt can retry the round.
        #
        # The actual database implementation should make this
        # operation safe/idempotent.
        # ----------------------------------------------------

        try:
            await self.db.release_round_resolution(
                game_id=game_id,
                round_number=round_number,
            )
        except Exception:
            logger.exception(
                "Could not release round-resolution claim "
                "for game %s round %s.",
                game_id,
                round_number,
            )

        raise

# ============================================================
# DEADLINES / RECOVERY
# ============================================================

async def recover_active_games(
    self,
) -> list[dict[str, Any]]:

    """
    Called at application startup.

    It does NOT recreate game state from Python.

    It asks Supabase for the authoritative active games and
    determines which ones require immediate recovery.
    """

    games = await self.db.get_active_games()

    recovered: list[
        dict[str, Any]
    ] = []

    now = self.now_utc()

    for game in games:

        status = game.get(
            "status"
        )

        if status == self.LOBBY_STATUS:

            join_deadline = self.parse_timestamp(
                game.get(
                    "join_deadline"
                )
            )

            if (
                join_deadline is not None
                and now >= join_deadline
            ):
                recovered.append(
                    {
                        "game_id": game["id"],
                        "action": "finish_lobby",
                    }
                )

        elif status == self.ACTIVE_STATUS:

            decision_deadline = self.parse_timestamp(
                game.get(
                    "decision_deadline"
                )
            )

            if (
                decision_deadline is not None
                and now >= decision_deadline
            ):
                recovered.append(
                    {
                        "game_id": game["id"],
                        "action": "resolve_round",
                    }
                )

        elif status == self.RESOLVING_STATUS:

            # A previous worker crashed during resolution.
            recovered.append(
                {
                    "game_id": game["id"],
                    "action": "recover_resolution",
                }
            )

    return recovered

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

    if status == self.LOBBY_STATUS:

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
        self.ACTIVE_STATUS,
        self.RESOLVING_STATUS,
    }:

        return await self.resolve_round(
            game_id
        )

    return None

async def finish_lobby(
    self,
    game_id: str,
) -> dict[str, Any] | None:

    game = await self.get_game(
        game_id
    )

    if game.get(
        "status"
    ) != self.LOBBY_STATUS:
        return None

    players = await self.db.get_players(
        game_id
    )

    eligible = [
        player
        for player in players
        if self.player_lifecycle(
            player
        )
        in {
            self.PLAYER_PENDING,
            self.PLAYER_ACTIVE,
        }
    ]

    if not eligible:
        await self.db.cancel_game_atomic(
            game_id=game_id,
            reason="no_players",
        )
        return {
            "cancelled": True,
            "reason": "no_players",
        }

    engine = self.engine_for_game(
        game
    )

    self.validate_player_count(
        len(eligible),
        engine,
    )

    return await self.start_game(
        game_id
    )

# ============================================================
# DEADLINE CALCULATION
# ============================================================

def decision_seconds(
    self,
    scene: dict[str, Any],
) -> int:

    value = scene.get(
        "timer_seconds"
    )

    if value is None:
        return self.default_decision_seconds

    value = int(
        value
    )

    if value <= 0:
        return self.default_decision_seconds

    return value

# ============================================================
# PLAYER STATUS OPERATIONS
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

    lifecycle = self.player_lifecycle(
        player
    )

    if lifecycle in {
        self.PLAYER_ELIMINATED,
        self.PLAYER_LEFT,
    }:
        return

    await self.db.update_player_lifecycle(
        game_id=game_id,
        user_id=user_id,
        lifecycle=self.PLAYER_LEFT,
    )

async def eliminate_player(
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

    await self.db.update_player_lifecycle(
        game_id=game_id,
        user_id=user_id,
        lifecycle=self.PLAYER_ELIMINATED,
    )

async def convert_player_to_npc(
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

    lifecycle = self.player_lifecycle(
        player
    )

    if lifecycle in {
        self.PLAYER_LEFT,
        self.PLAYER_ELIMINATED,
    }:
        return

    await self.db.update_player_lifecycle(
        game_id=game_id,
        user_id=user_id,
        lifecycle=self.PLAYER_NPC,
    )

# ============================================================
# GAME STOP
# ============================================================

async def stop_game(
    self,
    game_id: str,
    *,
    reason: str,
) -> None:

    await self.db.cancel_game_atomic(
        game_id=game_id,
        reason=reason,
    )

# ============================================================
# CALLBACK / TIMER COORDINATION
# ============================================================

async def resolve_if_all_decided(
    self,
    game_id: str,
) -> dict[str, Any] | None:

    """
    Convenience path after a player submits a decision.

    The database remains authoritative.

    Even if every player has decided, the final resolution
    still goes through the same atomic round claim.
    """

    game = await self.get_game(
        game_id
    )

    if game.get(
        "status"
    ) != self.ACTIVE_STATUS:
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

    decisions = await self.db.get_round_decisions(
        game_id,
        round_number,
    )

    required_players = [
        player
        for player in players
        if self.player_is_required_for_round(
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

    required_users = {
        int(
            player["user_id"]
        )
        for player in required_players
    }

    if not required_users.issubset(
        decided_users
    ):
        return None

    return await self.resolve_round(
        game_id
    )

@classmethod
def player_is_required_for_round(
    cls,
    player: dict[str, Any],
    round_number: int,
) -> bool:

    lifecycle = cls.player_lifecycle(
        player
    )

    if lifecycle != cls.PLAYER_ACTIVE:
        return False

    joined_round = int(
        player.get(
            "joined_round",
            0,
        )
        or 0
    )

    return joined_round <= round_number

# ============================================================
# GAME EVENT HELPERS
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

    await self.db.append_game_event(
        game_id=game_id,
        event_type=event_type,
        round_number=round_number,
        user_id=user_id,
        payload=payload or {},
        event_id=event_id,
    )

# ============================================================
# STORY ACCESS
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
            "Player does not have a role."
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
        raise ValueError(
            "Player does not exist."
        )

    lifecycle = self.player_lifecycle(
        player
    )

    if lifecycle != self.PLAYER_ACTIVE:
        return []

    role_id = player.get(
        "role_id"
    )

    if not role_id:
        return []

    current_round = int(
        game.get(
            "current_round",
            0,
        )
        or 0
    )

    joined_round = int(
        player.get(
            "joined_round",
            0,
        )
        or 0
    )

    if joined_round > current_round:
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

    return engine.choices_for_role(
        scene,
        role_id,
        game.get(
            "world_state"
        )
        or {},
    )

# ============================================================
# STARTUP VALIDATION
# ============================================================

async def validate_database_state(
    self,
) -> None:

    """
    Lightweight startup sanity check.

    The database migration remains responsible for enforcing
    constraints. This method catches obvious configuration
    mistakes early.
    """

    games = await self.db.get_active_games()

    for game in games:

        try:
            engine = self.engine_for_game(
                game
            )

            players = await self.db.get_players(
                game["id"]
            )

            eligible = [
                player
                for player in players
                if self.player_lifecycle(
                    player
                )
                in {
                    self.PLAYER_PENDING,
                    self.PLAYER_ACTIVE,
                    self.PLAYER_NPC,
                }
            ]

            self.validate_player_count(
                len(eligible),
                engine,
            )

        except Exception:
            logger.exception(
                "Invalid persisted game state detected "
                "for game %s.",
                game.get(
                    "id"
                ),
            )

# ============================================================
# GENERIC RECOVERY RUNNER
# ============================================================

async def recover_all_due_games(
    self,
    *,
    on_result: Callable[
        [dict[str, Any]],
        Awaitable[None],
    ]
    | None = None,
) -> list[dict[str, Any]]:

    """
    Recover all games whose persisted deadlines indicate
    work is due.

    This is safe to call repeatedly.

    It is deliberately suitable for:
        - application startup
        - periodic recovery
        - post-Restart recovery
    """

    recovery_items = (
        await self.recover_active_games()
    )

    results: list[
        dict[str, Any]
    ] = []

    for item in recovery_items:

        game_id = item[
            "game_id"
        ]

        try:

            result = await self.recover_game(
                game_id
            )

            if result is None:
                continue

            wrapped = {
                "game_id": game_id,
                "result": result,
            }

            results.append(
                wrapped
            )

            if on_result:
                await on_result(
                    wrapped
                )

        except Exception:

            logger.exception(
                "Recovery failed for game %s.",
                game_id,
            )

    return results
