from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


# ============================================================
# ENUMS
# ============================================================


class GameStatus(str, Enum):
    """
    Persistent lifecycle of a game.
    """

    LOBBY = "lobby"
    STARTING = "starting"
    PLAYING = "playing"
    RESOLVING = "resolving"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PlayerStatus(str, Enum):
    """
    Persistent lifecycle of a player.

    PENDING:
        Joined the game but may still need private Telegram
        onboarding.

    ACTIVE:
        Currently participating.

    ELIMINATED:
        No longer participates in normal decisions.

    NPC:
        Character has become an NPC and is controlled by the
        game engine.

    LEFT:
        Player voluntarily left the game.
    """

    PENDING = "pending"
    ACTIVE = "active"
    ELIMINATED = "eliminated"
    NPC = "npc"
    LEFT = "left"


class DecisionStatus(str, Enum):
    """
    Lifecycle of an individual decision.
    """

    OPEN = "open"
    SUBMITTED = "submitted"
    TIMED_OUT = "timed_out"
    RESOLVED = "resolved"
    INVALIDATED = "invalidated"


class ResolutionStatus(str, Enum):
    """
    State of round/scene resolution.

    This is important because a Telegram callback and a timeout
    can arrive almost simultaneously.
    """

    PENDING = "pending"
    CLAIMED = "claimed"
    RESOLVED = "resolved"


class EventType(str, Enum):
    """
    Append-only game event types.

    These provide an audit trail and make recovery/debugging
    possible after a Render restart.
    """

    GAME_CREATED = "game_created"
    GAME_CANCELLED = "game_cancelled"
    GAME_STARTED = "game_started"
    PLAYER_JOINED = "player_joined"
    PLAYER_LEFT = "player_left"
    PLAYER_ONBOARDED = "player_onboarded"
    ROLE_ASSIGNED = "role_assigned"
    SCENE_STARTED = "scene_started"
    DECISION_OPENED = "decision_opened"
    DECISION_SUBMITTED = "decision_submitted"
    DECISION_TIMEOUT = "decision_timeout"
    DECISION_RESOLVED = "decision_resolved"
    ROUND_RESOLUTION_STARTED = "round_resolution_started"
    ROUND_RESOLVED = "round_resolved"
    PLAYER_ELIMINATED = "player_eliminated"
    PLAYER_BECAME_NPC = "player_became_npc"
    GAME_COMPLETED = "game_completed"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_COMPLETED = "recovery_completed"
    ERROR = "error"


# ============================================================
# SMALL VALUE OBJECTS
# ============================================================


@dataclass(frozen=True)
class StoryMetadata:
    """
    Metadata describing a validated generated story.

    The complete story itself is stored separately as JSON.
    """

    story_id: str
    fingerprint: str
    title: str
    player_count: int
    schema_version: int = 1


@dataclass(frozen=True)
class DecisionOption:
    """
    A single playable choice.

    The game engine should work with this object instead of
    depending directly on raw Gemini dictionaries.
    """

    id: str
    text: str
    next_scene: str | None = None
    effects: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Scene:
    """
    Validated story scene.

    This is intentionally independent of Telegram and Supabase.
    """

    id: str
    title: str
    description: str
    choices: tuple[DecisionOption, ...]
    duration_seconds: int
    participants: tuple[str, ...] = ()
    ending: bool = False


@dataclass(frozen=True)
class Role:
    """
    A playable story role/character.
    """

    id: str
    name: str
    description: str
    private_information: str = ""
    objectives: tuple[str, ...] = ()
    traits: tuple[str, ...] = ()


# ============================================================
# PLAYER
# ============================================================


@dataclass
class Player:
    """
    Persistent game participant.

    This replaces the old 'active: bool' design with an explicit
    lifecycle state while retaining a compatibility property
    below for code that still expects player.active.
    """

    game_id: str
    user_id: int

    username: str = ""
    display_name: str = ""

    role_id: str | None = None

    status: PlayerStatus = PlayerStatus.PENDING

    missed_decisions: int = 0

    joined_round: int = 0

    joined_at: datetime | None = None

    eliminated_at: datetime | None = None

    became_npc_at: datetime | None = None

    last_decision_at: datetime | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    # --------------------------------------------------------
    # Compatibility
    # --------------------------------------------------------

    @property
    def active(self) -> bool:
        """
        Compatibility property for older game code.

        New code should use:

            player.status == PlayerStatus.ACTIVE
        """
        return self.status == PlayerStatus.ACTIVE

    @active.setter
    def active(self, value: bool) -> None:
        """
        Compatibility setter.

        Existing code that does:

            player.active = False

        will now transition the player to ELIMINATED rather than
        maintaining a separate boolean state.
        """
        if value:
            self.status = PlayerStatus.ACTIVE
        else:
            self.status = PlayerStatus.ELIMINATED

    @property
    def participates(self) -> bool:
        """
        Whether this player participates in normal gameplay.
        """
        return self.status == PlayerStatus.ACTIVE

    @property
    def can_receive_decisions(self) -> bool:
        """
        Whether this player should currently receive decisions.
        """
        return self.status == PlayerStatus.ACTIVE


# ============================================================
# GAME
# ============================================================


@dataclass
class Game:
    """
    Persistent game state.

    This object represents the state that must survive a Render
    restart.
    """

    id: str

    creator_user_id: int

    status: GameStatus = GameStatus.LOBBY

    current_round: int = 0

    current_scene_id: str | None = None

    decision_deadline: datetime | None = None

    resolution_status: ResolutionStatus = (
        ResolutionStatus.PENDING
    )

    resolution_key: str | None = None

    story_id: str | None = None

    story_fingerprint: str | None = None

    story_player_count: int | None = None

    world_state: dict[str, Any] = field(
        default_factory=dict
    )

    # Database optimistic-concurrency version.

    version: int = 1

    created_at: datetime | None = None

    started_at: datetime | None = None

    completed_at: datetime | None = None

    cancelled_at: datetime | None = None

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    # --------------------------------------------------------
    # Convenience state checks
    # --------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self.status in {
            GameStatus.LOBBY,
            GameStatus.STARTING,
            GameStatus.PLAYING,
            GameStatus.RESOLVING,
        }

    @property
    def is_playing(self) -> bool:
        return self.status in {
            GameStatus.PLAYING,
            GameStatus.RESOLVING,
        }

    @property
    def is_finished(self) -> bool:
        return self.status in {
            GameStatus.COMPLETED,
            GameStatus.CANCELLED,
        }

    @property
    def has_deadline(self) -> bool:
        return self.decision_deadline is not None


# ============================================================
# DECISION
# ============================================================


@dataclass
class Decision:
    """
    Persistent player decision.

    Every submitted decision gets its own record.

    This gives us duplicate protection, auditing, recovery, and
    debugging instead of keeping decisions only in memory.
    """

    id: str

    game_id: str

    user_id: int

    round_number: int

    scene_id: str

    choice_id: str

    status: DecisionStatus = DecisionStatus.OPEN

    submitted_at: datetime | None = None

    resolved_at: datetime | None = None

    created_at: datetime | None = None

    metadata: dict[str, Any] = field(
        default_factory=dict
    )


# ============================================================
# ROUND STATE
# ============================================================


@dataclass
class RoundState:
    """
    Runtime representation of the current game round.

    The database remains the source of truth; this object is a
    convenient typed representation used by the engine.
    """

    game_id: str

    round_number: int

    scene_id: str

    decision_deadline: datetime | None = None

    resolution_status: ResolutionStatus = (
        ResolutionStatus.PENDING
    )

    resolution_key: str | None = None

    active_player_ids: tuple[int, ...] = ()

    submitted_player_ids: tuple[int, ...] = ()

    timed_out_player_ids: tuple[int, ...] = ()


# ============================================================
# GAME EVENT
# ============================================================


@dataclass(frozen=True)
class GameEvent:
    """
    Immutable append-only event.

    Event history is not the primary state store; it is the
    audit/recovery/debugging record.
    """

    id: str

    game_id: str

    event_type: EventType

    round_number: int | None = None

    user_id: int | None = None

    payload: Mapping[str, Any] = field(
        default_factory=dict
    )

    created_at: datetime | None = None


# ============================================================
# STORY
# ============================================================


@dataclass(frozen=True)
class Story:
    """
    Fully validated story consumed by the game engine.

    Gemini is allowed to produce candidate data, but the engine
    should only receive a Story after validation.
    """

    metadata: StoryMetadata

    roles: tuple[Role, ...]

    scenes: tuple[Scene, ...]

    first_scene_id: str

    endings: tuple[str, ...] = ()

    global_rules: dict[str, Any] = field(
        default_factory=dict
    )

    raw_data: dict[str, Any] = field(
        default_factory=dict
    )

    # --------------------------------------------------------
    # Lookups
    # --------------------------------------------------------

    def get_role(
        self,
        role_id: str,
    ) -> Role | None:
        for role in self.roles:
            if role.id == role_id:
                return role

        return None

    def get_scene(
        self,
        scene_id: str,
    ) -> Scene | None:
        for scene in self.scenes:
            if scene.id == scene_id:
                return scene

        return None

    @property
    def role_ids(self) -> frozenset[str]:
        return frozenset(
            role.id
            for role in self.roles
        )

    @property
    def scene_ids(self) -> frozenset[str]:
        return frozenset(
            scene.id
            for scene in self.scenes
        )


# ============================================================
# CHOICE RESULT
# ============================================================


@dataclass(frozen=True)
class ChoiceResult:
    """
    Result produced by the deterministic game engine after a
    player chooses an option.

    The engine decides what happens. Gemini does not.
    """

    text: str

    next_scene: str | None

    effects: dict[str, Any]

    public_event: str

    game_ended: bool = False

    ending_id: str | None = None


# ============================================================
# RECOVERY INFORMATION
# ============================================================


@dataclass(frozen=True)
class RecoveryTask:
    """
    Describes an active deadline that must be recovered after
    application startup.

    The task is derived from persistent database state rather
    than from an in-memory JobQueue.
    """

    game_id: str

    round_number: int

    deadline: datetime

    resolution_key: str

    scene_id: str | None = None


# ============================================================
# HELPER FUNCTIONS
# ============================================================


def is_active_player(
    player: Player,
) -> bool:
    """
    Centralized active-player check.

    This avoids different parts of the code accidentally using
    different interpretations of 'active'.
    """
    return (
        player.status
        == PlayerStatus.ACTIVE
    )


def is_terminal_game(
    game: Game,
) -> bool:
    """
    Return True when no further gameplay can occur.
    """
    return game.status in {
        GameStatus.COMPLETED,
        GameStatus.CANCELLED,
    }


def make_resolution_key(
    game_id: str,
    round_number: int,
) -> str:
    """
    Create a deterministic idempotency key for round resolution.

    Example:

        abc123:round:4
    """
    return (
        f"{game_id}:round:{round_number}"
    )
