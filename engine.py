from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Iterable


class GameEngine:
    """
    Deterministic gameplay engine for WHAT HAPPENS?

    Architecture:

        Gemini
            ↓
        validated story JSON
            ↓
        GameEngine
            ↓
        deterministic result
            ↓
        Supabase

    This class never:

    - calls Telegram
    - calls Gemini
    - calls Supabase
    - creates background jobs
    - owns authoritative timers
    - stores authoritative game state

    The caller supplies persisted game state and is responsible
    for atomically saving the returned result.
    """

    MAX_MISSED_DECISIONS = 3

    MIN_TIMER_SECONDS = 10
    MAX_TIMER_SECONDS = 3600

    ALLOWED_CONDITION_TYPES = {
        "flag",
        "variable",
        "knowledge",
        "relationship",
    }

    ALLOWED_OPERATORS = {
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "contains",
    }

    PLAYER_STATUSES = {
        "pending",
        "active",
        "eliminated",
        "npc",
        "left",
    }

    GAME_STATUSES = {
        "waiting",
        "active",
        "finished",
        "cancelled",
    }

    def __init__(
        self,
        story: dict[str, Any],
    ) -> None:
        if not isinstance(story, dict):
            raise ValueError("Story must be an object.")

        self.story = copy.deepcopy(story)

        roles = self.story.get("roles")
        scenes = self.story.get("scenes")

        if not isinstance(roles, list):
            raise ValueError("Story roles must be a list.")

        if not isinstance(scenes, list):
            raise ValueError("Story scenes must be a list.")

        self.roles: dict[str, dict[str, Any]] = {}
        self.scenes: dict[str, dict[str, Any]] = {}

        for role in roles:
            if not isinstance(role, dict):
                raise ValueError("Every role must be an object.")

            role_id = role.get("id")

            if not isinstance(role_id, str) or not role_id.strip():
                raise ValueError(
                    "Every role requires a non-empty id."
                )

            role_id = role_id.strip()

            if role_id in self.roles:
                raise ValueError(
                    f"Duplicate role id: {role_id}"
                )

            self.roles[role_id] = copy.deepcopy(role)

        for scene in scenes:
            if not isinstance(scene, dict):
                raise ValueError("Every scene must be an object.")

            scene_id = scene.get("id")

            if not isinstance(scene_id, str) or not scene_id.strip():
                raise ValueError(
                    "Every scene requires a non-empty id."
                )

            scene_id = scene_id.strip()

            if scene_id in self.scenes:
                raise ValueError(
                    f"Duplicate scene id: {scene_id}"
                )

            self.scenes[scene_id] = copy.deepcopy(scene)

        self.validate_story()

    # ============================================================
    # BASIC HELPERS
    # ============================================================

    @staticmethod
    def _deepcopy(value: Any) -> Any:
        return copy.deepcopy(value)

    @staticmethod
    def _require_string(
        value: Any,
        field_name: str,
    ) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"{field_name} must be a non-empty string."
            )

        return value.strip()

    @staticmethod
    def _as_dict(
        value: Any,
        field_name: str,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(
                f"{field_name} must be an object."
            )

        return value

    @staticmethod
    def _as_list(
        value: Any,
        field_name: str,
    ) -> list[Any]:
        if not isinstance(value, list):
            raise ValueError(
                f"{field_name} must be a list."
            )

        return value

    @staticmethod
    def _as_int(
        value: Any,
        field_name: str,
        default: int | None = None,
    ) -> int:
        if value is None:
            if default is not None:
                return default

            raise ValueError(
                f"{field_name} must be an integer."
            )

        if isinstance(value, bool):
            raise ValueError(
                f"{field_name} must be an integer."
            )

        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{field_name} must be an integer."
            ) from exc

    @staticmethod
    def _as_bool(
        value: Any,
        default: bool = False,
    ) -> bool:
        if value is None:
            return default

        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            normalized = value.strip().lower()

            if normalized in {
                "true",
                "1",
                "yes",
                "y",
            }:
                return True

            if normalized in {
                "false",
                "0",
                "no",
                "n",
            }:
                return False

        return bool(value)

    @staticmethod
    def _stable_json(
        value: Any,
    ) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @classmethod
    def _fingerprint(
        cls,
        value: Any,
    ) -> str:
        encoded = cls._stable_json(value).encode(
            "utf-8"
        )

        return hashlib.sha256(encoded).hexdigest()

    # ============================================================
    # STORY LOOKUPS
    # ============================================================

    def get_role(
        self,
        role_id: str,
    ) -> dict[str, Any]:
        role_id = self._require_string(
            role_id,
            "role_id",
        )

        try:
            return self.roles[role_id]
        except KeyError as exc:
            raise ValueError(
                f"Unknown role id: {role_id}"
            ) from exc

    def get_scene(
        self,
        scene_id: str,
    ) -> dict[str, Any]:
        scene_id = self._require_string(
            scene_id,
            "scene_id",
        )

        try:
            return self.scenes[scene_id]
        except KeyError as exc:
            raise ValueError(
                f"Unknown scene id: {scene_id}"
            ) from exc

    def get_first_scene_id(self) -> str:
        first_scene = self.story.get(
            "first_scene_id"
        )

        if first_scene is None:
            first_scene = self.story.get(
                "first_scene"
            )

        if not isinstance(first_scene, str):
            raise ValueError(
                "Story must define first_scene_id."
            )

        first_scene = first_scene.strip()

        if not first_scene:
            raise ValueError(
                "first_scene_id cannot be empty."
            )

        if first_scene not in self.scenes:
            raise ValueError(
                f"First scene does not exist: {first_scene}"
            )

        return first_scene

    # ============================================================
    # PLAYABLE ROLES
    # ============================================================

    def playable_roles(
        self,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        for role in self.roles.values():
            playable = role.get(
                "playable",
                role.get(
                    "is_playable",
                    True,
                ),
            )

            if self._as_bool(
                playable,
                True,
            ):
                result.append(
                    self._deepcopy(role)
                )

        return result

    def playable_role_ids(
        self,
    ) -> list[str]:
        return [
            role["id"]
            for role in self.playable_roles()
        ]

    def validate_player_count(
        self,
        player_count: int,
    ) -> None:
        player_count = self._as_int(
            player_count,
            "player_count",
        )

        minimum = self.story.get(
            "minimum_players",
            self.story.get(
                "min_players",
                1,
            ),
        )

        maximum = self.story.get(
            "maximum_players",
            self.story.get(
                "max_players",
                len(self.playable_roles()),
            ),
        )

        minimum = self._as_int(
            minimum,
            "minimum_players",
        )

        maximum = self._as_int(
            maximum,
            "maximum_players",
        )

        if minimum < 1:
            raise ValueError(
                "minimum_players must be at least 1."
            )

        if maximum < minimum:
            raise ValueError(
                "maximum_players cannot be below "
                "minimum_players."
            )

        if player_count < minimum:
            raise ValueError(
                f"Player count {player_count} is below "
                f"minimum {minimum}."
            )

        if player_count > maximum:
            raise ValueError(
                f"Player count {player_count} exceeds "
                f"maximum {maximum}."
            )

        playable_count = len(
            self.playable_roles()
        )

        if player_count > playable_count:
            raise ValueError(
                f"Player count {player_count} exceeds "
                f"available playable roles "
                f"{playable_count}."
            )

    def validate_role_assignments(
        self,
        players: Iterable[dict[str, Any]],
    ) -> None:
        seen_roles: set[str] = set()

        for player in players:
            if not isinstance(player, dict):
                raise ValueError(
                    "Every player must be an object."
                )

            role_id = player.get("role_id")

            if role_id is None:
                continue

            role_id = self._require_string(
                role_id,
                "player.role_id",
            )

            self.get_role(role_id)

            if role_id in seen_roles:
                raise ValueError(
                    f"Role assigned more than once: "
                    f"{role_id}"
                )

            seen_roles.add(role_id)

    # ============================================================
    # STORY FINGERPRINT
    # ============================================================

    def story_fingerprint(self) -> str:
        """
        Return a deterministic SHA-256 fingerprint of the
        validated story.

        This can be persisted in Supabase so that the exact
        story version used by a game remains identifiable.
        """

        return self._fingerprint(
            self.story
        )

    # ============================================================
    # STORY VALIDATION
    # ============================================================

    def validate_story(self) -> None:
        """
        Validate the story at engine construction time.

        story_validator.py remains responsible for the complete
        Gemini/schema validation pipeline. This second layer
        prevents an invalid story from reaching deterministic
        gameplay if the engine is instantiated independently.
        """

        self.get_first_scene_id()

        self._validate_roles()

        self._validate_scenes()

        self._validate_story_endings()

        self._validate_story_timer()

    def _validate_roles(self) -> None:
        playable_ids: set[str] = set()

        for role_id, role in self.roles.items():
            if role.get("id") != role_id:
                raise ValueError(
                    f"Role id mismatch for role {role_id}."
                )

            playable = role.get(
                "playable",
                role.get(
                    "is_playable",
                    True,
                ),
            )

            if self._as_bool(
                playable,
                True,
            ):
                playable_ids.add(
                    role_id
                )

        if not playable_ids:
            raise ValueError(
                "Story must contain at least one "
                "playable role."
            )

    def _validate_scenes(self) -> None:
        for scene_id, scene in self.scenes.items():
            if scene.get("id") != scene_id:
                raise ValueError(
                    f"Scene id mismatch for scene "
                    f"{scene_id}."
                )

            choices = scene.get(
                "choices",
                [],
            )

            if not isinstance(
                choices,
                list,
            ):
                raise ValueError(
                    f"Scene '{scene_id}' choices "
                    "must be a list."
                )

            seen_choice_ids: set[str] = set()

            for choice in choices:
                if not isinstance(
                    choice,
                    dict,
                ):
                    raise ValueError(
                        f"Every choice in scene "
                        f"'{scene_id}' must be "
                        "an object."
                    )

                choice_id = choice.get("id")

                if not isinstance(
                    choice_id,
                    str,
                ) or not choice_id.strip():
                    raise ValueError(
                        f"Every choice in scene "
                        f"'{scene_id}' requires "
                        "a non-empty id."
                    )

                choice_id = choice_id.strip()

                if choice_id in seen_choice_ids:
                    raise ValueError(
                        f"Duplicate choice id "
                        f"'{choice_id}' in scene "
                        f"'{scene_id}'."
                    )

                seen_choice_ids.add(
                    choice_id
                )

                self._validate_choice_target(
                    scene_id,
                    choice,
                )

                self._validate_choice_timer(
                    scene_id,
                    choice,
                )

                self._validate_conditions(
                    scene_id,
                    choice.get(
                        "conditions",
                        [],
                    ),
                )

    def _validate_choice_target(
        self,
        scene_id: str,
        choice: dict[str, Any],
    ) -> None:
        target = None

        for key in (
            "next_scene_id",
            "next_scene",
            "target_scene_id",
            "target_scene",
        ):
            value = choice.get(key)

            if value is not None:
                target = value
                break

        if target is None:
            ending = choice.get(
                "ending_id",
                choice.get(
                    "ending"
                ),
            )

            if ending is not None:
                target = ending

        if target is None:
            raise ValueError(
                f"Choice '{choice.get('id')}' "
                f"in scene '{scene_id}' has no "
                "target."
            )

        if not isinstance(
            target,
            str,
        ) or not target.strip():
            raise ValueError(
                f"Choice '{choice.get('id')}' "
                f"in scene '{scene_id}' has "
                "an invalid target."
            )

    def _validate_choice_timer(
        self,
        scene_id: str,
        choice: dict[str, Any],
    ) -> None:
        timer = choice.get(
            "timer_seconds"
        )

        if timer is None:
            timer = choice.get(
                "time_limit"
            )

        if timer is None:
            return

        timer = self._as_int(
            timer,
            (
                f"scene[{scene_id}]"
                f".choice[{choice.get('id')}]"
                ".timer"
            ),
        )

        if not (
            self.MIN_TIMER_SECONDS
            <= timer
            <= self.MAX_TIMER_SECONDS
        ):
            raise ValueError(
                f"Choice '{choice.get('id')}' "
                f"in scene '{scene_id}' has "
                f"timer outside allowed range "
                f"{self.MIN_TIMER_SECONDS}-"
                f"{self.MAX_TIMER_SECONDS} seconds."
            )

    def _validate_story_endings(self) -> None:
        endings = self.story.get(
            "endings",
            [],
        )

        if isinstance(
            endings,
            dict,
        ):
            ending_ids = set(
                str(key)
                for key in endings.keys()
            )
        elif isinstance(
            endings,
            list,
        ):
            ending_ids = set()

            for ending in endings:
                if not isinstance(
                    ending,
                    dict,
                ):
                    raise ValueError(
                        "Every ending must be an object."
                    )

                ending_id = ending.get(
                    "id"
                )

                if not isinstance(
                    ending_id,
                    str,
                ) or not ending_id.strip():
                    raise ValueError(
                        "Every ending requires "
                        "a non-empty id."
                    )

                ending_ids.add(
                    ending_id.strip()
                )
        else:
            raise ValueError(
                "Story endings must be a list "
                "or object."
            )

        for scene_id, scene in self.scenes.items():
            ending_id = scene.get(
                "ending_id",
                scene.get(
                    "ending"
                ),
            )

            if ending_id is not None:
                if str(ending_id) not in ending_ids:
                    raise ValueError(
                        f"Scene '{scene_id}' references "
                        f"unknown ending "
                        f"'{ending_id}'."
                    )

    def _validate_story_timer(self) -> None:
        timer = self.story.get(
            "timer_seconds"
        )

        if timer is None:
            timer = self.story.get(
                "decision_timer_seconds"
            )

        if timer is None:
            return

        timer = self._as_int(
            timer,
            "story.timer_seconds",
        )

        if not (
            self.MIN_TIMER_SECONDS
            <= timer
            <= self.MAX_TIMER_SECONDS
        ):
            raise ValueError(
                "Story timer is outside the allowed "
                f"range {self.MIN_TIMER_SECONDS}-"
                f"{self.MAX_TIMER_SECONDS} seconds."
            )

    def _validate_conditions(
        self,
        scene_id: str,
        conditions: Any,
    ) -> None:
        if conditions is None:
            return

        if not isinstance(
            conditions,
            list,
        ):
            raise ValueError(
                f"Conditions in scene "
                f"'{scene_id}' must be a list."
            )

        for condition in conditions:
            if not isinstance(
                condition,
                dict,
            ):
                raise ValueError(
                    f"Every condition in scene "
                    f"'{scene_id}' must be "
                    "an object."
                )

            condition_type = condition.get(
                "type"
            )

            if condition_type not in (
                self.ALLOWED_CONDITION_TYPES
            ):
                raise ValueError(
                    f"Unsupported condition type "
                    f"'{condition_type}' in scene "
                    f"'{scene_id}'."
                )

            operator = condition.get(
                "operator",
                condition.get(
                    "op",
                    "eq",
                ),
            )

            if operator not in (
                self.ALLOWED_OPERATORS
            ):
                raise ValueError(
                    f"Unsupported condition "
                    f"operator '{operator}' in "
                    f"scene '{scene_id}'."
                )
                    # ============================================================
    # INITIAL GAME STATE
    # ============================================================

    def create_initial_state(
        self,
        players: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Create the deterministic initial state for a game.

        Persistence remains outside the engine. The database layer
        must persist the returned state and player membership.
        """

        player_list = [
            self._deepcopy(player)
            for player in players
        ]

        if not player_list:
            raise ValueError(
                "At least one player is required."
            )

        self.validate_player_count(
            len(player_list)
        )

        self.validate_role_assignments(
            player_list
        )

        seen_users: set[str] = set()

        normalized_players: list[
            dict[str, Any]
        ] = []

        for player in player_list:
            user_id = self._require_string(
                player.get("user_id"),
                "player.user_id",
            )

            if user_id in seen_users:
                raise ValueError(
                    f"Duplicate player user_id: "
                    f"{user_id}"
                )

            seen_users.add(user_id)

            status = player.get(
                "status",
                "active",
            )

            if status not in self.PLAYER_STATUSES:
                raise ValueError(
                    f"Invalid player status "
                    f"'{status}' for {user_id}."
                )

            missed_decisions = self._as_int(
                player.get(
                    "missed_decisions",
                    0,
                ),
                "player.missed_decisions",
            )

            if missed_decisions < 0:
                raise ValueError(
                    "player.missed_decisions "
                    "cannot be negative."
                )

            normalized_player = {
                **player,
                "user_id": user_id,
                "status": status,
                "missed_decisions": (
                    missed_decisions
                ),
                "last_decision_round": (
                    player.get(
                        "last_decision_round"
                    )
                ),
                "last_choice_id": (
                    player.get(
                        "last_choice_id"
                    )
                ),
            }

            normalized_players.append(
                normalized_player
            )

        initial_world_state = self.story.get(
            "initial_world_state",
            {},
        )

        if not isinstance(
            initial_world_state,
            dict,
        ):
            raise ValueError(
                "initial_world_state must "
                "be an object."
            )

        initial_relationships = (
            self.story.get(
                "initial_relationships",
                {},
            )
        )

        if not isinstance(
            initial_relationships,
            dict,
        ):
            raise ValueError(
                "initial_relationships must "
                "be an object."
            )

        initial_flags = self.story.get(
            "initial_flags",
            {},
        )

        if not isinstance(
            initial_flags,
            dict,
        ):
            raise ValueError(
                "initial_flags must be "
                "an object."
            )

        state: dict[str, Any] = {
            "status": "active",
            "round_number": 1,
            "scene_id": self.get_first_scene_id(),
            "players": normalized_players,
            "world_state": self._deepcopy(
                initial_world_state
            ),
            "relationships": self._deepcopy(
                initial_relationships
            ),
            "flags": self._deepcopy(
                initial_flags
            ),
            "history": [],
            "ending_id": None,
            "story_fingerprint": (
                self.story_fingerprint()
            ),
        }

        return state

    # ============================================================
    # STATE VALIDATION
    # ============================================================

    def validate_state(
        self,
        state: dict[str, Any],
    ) -> None:
        """
        Validate the persisted state before applying an operation.

        This is intentionally independent from Supabase. The service
        layer should obtain the authoritative state from the database,
        then pass it here.
        """

        if not isinstance(
            state,
            dict,
        ):
            raise ValueError(
                "Game state must be an object."
            )

        status = state.get(
            "status"
        )

        if status not in self.GAME_STATUSES:
            raise ValueError(
                f"Invalid game status: {status}"
            )

        round_number = self._as_int(
            state.get(
                "round_number",
                1,
            ),
            "state.round_number",
        )

        if round_number < 1:
            raise ValueError(
                "round_number must be at least 1."
            )

        scene_id = state.get(
            "scene_id"
        )

        if scene_id is not None:
            self.get_scene(
                str(scene_id)
            )

        players = state.get(
            "players"
        )

        if not isinstance(
            players,
            list,
        ):
            raise ValueError(
                "state.players must be a list."
            )

        seen_users: set[str] = set()
        seen_roles: set[str] = set()

        for player in players:
            if not isinstance(
                player,
                dict,
            ):
                raise ValueError(
                    "Every state player must "
                    "be an object."
                )

            user_id = self._require_string(
                player.get("user_id"),
                "state.player.user_id",
            )

            if user_id in seen_users:
                raise ValueError(
                    f"Duplicate state player: "
                    f"{user_id}"
                )

            seen_users.add(user_id)

            player_status = player.get(
                "status",
                "active",
            )

            if player_status not in (
                self.PLAYER_STATUSES
            ):
                raise ValueError(
                    f"Invalid player status "
                    f"'{player_status}'."
                )

            missed = self._as_int(
                player.get(
                    "missed_decisions",
                    0,
                ),
                "player.missed_decisions",
            )

            if missed < 0:
                raise ValueError(
                    "missed_decisions cannot "
                    "be negative."
                )

            role_id = player.get(
                "role_id"
            )

            if role_id is not None:
                role_id = self._require_string(
                    role_id,
                    "player.role_id",
                )

                self.get_role(
                    role_id
                )

                if role_id in seen_roles:
                    raise ValueError(
                        f"Role assigned more "
                        f"than once: {role_id}"
                    )

                seen_roles.add(
                    role_id
                )

            last_decision_round = (
                player.get(
                    "last_decision_round"
                )
            )

            if (
                last_decision_round
                is not None
            ):
                last_decision_round = (
                    self._as_int(
                        last_decision_round,
                        "player.last_decision_round",
                    )
                )

                if last_decision_round < 0:
                    raise ValueError(
                        "last_decision_round "
                        "cannot be negative."
                    )

    def _require_active_state(
        self,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_state(
            state
        )

        if state.get(
            "status"
        ) != "active":
            raise ValueError(
                "Game is not active."
            )

        if state.get(
            "scene_id"
        ) is None:
            raise ValueError(
                "Active game must have "
                "a scene_id."
            )

        return self._deepcopy(
            state
        )

    # ============================================================
    # PLAYER LOOKUPS
    # ============================================================

    @staticmethod
    def _find_player(
        state: dict[str, Any],
        user_id: str,
    ) -> tuple[
        int,
        dict[str, Any],
    ]:
        user_id = str(
            user_id
        ).strip()

        if not user_id:
            raise ValueError(
                "user_id cannot be empty."
            )

        players = state.get(
            "players",
            [],
        )

        for index, player in enumerate(
            players
        ):
            if (
                str(
                    player.get(
                        "user_id"
                    )
                )
                == user_id
            ):
                return (
                    index,
                    player,
                )

        raise ValueError(
            f"Player not found: {user_id}"
        )

    def active_players(
        self,
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self.validate_state(
            state
        )

        return [
            self._deepcopy(player)
            for player in state.get(
                "players",
                [],
            )
            if player.get(
                "status",
                "active",
            )
            == "active"
        ]

    def pending_players(
        self,
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self.validate_state(
            state
        )

        current_round = self._as_int(
            state.get(
                "round_number",
                1,
            ),
            "state.round_number",
        )

        result: list[
            dict[str, Any]
        ] = []

        for player in state.get(
            "players",
            [],
        ):
            if player.get(
                "status",
                "active",
            ) != "active":
                continue

            last_round = player.get(
                "last_decision_round"
            )

            if last_round is None:
                result.append(
                    self._deepcopy(
                        player
                    )
                )
                continue

            last_round = self._as_int(
                last_round,
                "player.last_decision_round",
            )

            if last_round != current_round:
                result.append(
                    self._deepcopy(
                        player
                    )
                )

        return result

    def all_active_players_decided(
        self,
        state: dict[str, Any],
    ) -> bool:
        self.validate_state(
            state
        )

        active = self.active_players(
            state
        )

        if not active:
            return False

        current_round = self._as_int(
            state.get(
                "round_number",
                1,
            ),
            "state.round_number",
        )

        for player in active:
            if self._as_int(
                player.get(
                    "last_decision_round"
                ),
                "player.last_decision_round",
                0,
            ) != current_round:
                return False

        return True

    # ============================================================
    # SCENE / CHOICE HELPERS
    # ============================================================

    @staticmethod
    def _scene_choices(
        scene: dict[str, Any],
    ) -> list[dict[str, Any]]:
        choices = scene.get(
            "choices",
            [],
        )

        if not isinstance(
            choices,
            list,
        ):
            raise ValueError(
                "Scene choices must be a list."
            )

        return [
            choice
            for choice in choices
            if isinstance(
                choice,
                dict,
            )
        ]

    @staticmethod
    def _choice_target(
        choice: dict[str, Any],
    ) -> str | None:
        for key in (
            "next_scene_id",
            "next_scene",
            "target_scene_id",
            "target_scene",
        ):
            value = choice.get(
                key
            )

            if value is not None:
                return str(
                    value
                ).strip()

        for key in (
            "ending_id",
            "ending",
        ):
            value = choice.get(
                key
            )

            if value is not None:
                return str(
                    value
                ).strip()

        return None

    @staticmethod
    def _choice_ending_id(
        choice: dict[str, Any],
    ) -> str | None:
        for key in (
            "ending_id",
            "ending",
        ):
            value = choice.get(
                key
            )

            if value is not None:
                return str(
                    value
                ).strip()

        return None

    @staticmethod
    def _scene_ending_id(
        scene: dict[str, Any],
    ) -> str | None:
        for key in (
            "ending_id",
            "ending",
        ):
            value = scene.get(
                key
            )

            if value is not None:
                return str(
                    value
                ).strip()

        if GameEngine._as_bool(
            scene.get(
                "is_ending"
            ),
            False,
        ):
            ending_id = scene.get(
                "id"
            )

            if ending_id is not None:
                return str(
                    ending_id
                ).strip()

        return None

    # ============================================================
    # DECISION VALIDATION
    # ============================================================

    def validate_decision(
        self,
        state: dict[str, Any],
        user_id: str,
        choice_id: str,
    ) -> dict[str, Any]:
        """
        Validate a player decision without modifying state.

        Database-level duplicate protection still belongs in the
        submit_decision_atomic RPC.
        """

        normalized = (
            self._require_active_state(
                state
            )
        )

        _, player = self._find_player(
            normalized,
            user_id,
        )

        if player.get(
            "status",
            "active",
        ) != "active":
            raise ValueError(
                "Player is not active."
            )

        current_round = self._as_int(
            normalized.get(
                "round_number",
                1,
            ),
            "state.round_number",
        )

        last_decision_round = (
            player.get(
                "last_decision_round"
            )
        )

        if (
            last_decision_round
            is not None
            and self._as_int(
                last_decision_round,
                "player.last_decision_round",
            )
            == current_round
        ):
            raise ValueError(
                "Player has already submitted "
                "a decision for this round."
            )

        scene_id = self._require_string(
            normalized.get(
                "scene_id"
            ),
            "state.scene_id",
        )

        scene = self.get_scene(
            scene_id
        )

        choice_id = self._require_string(
            choice_id,
            "choice_id",
        )

        for choice in self._scene_choices(
            scene
        ):
            current_choice_id = (
                choice.get("id")
            )

            if (
                isinstance(
                    current_choice_id,
                    str,
                )
                and current_choice_id.strip()
                == choice_id
            ):
                if not self._conditions_met(
                    normalized,
                    choice.get(
                        "conditions",
                        [],
                    ),
                ):
                    raise ValueError(
                        "This choice is not "
                        "currently available."
                    )

                return self._deepcopy(
                    choice
                )

        raise ValueError(
            f"Choice '{choice_id}' is not "
            f"available in scene "
            f"'{scene_id}'."
        )

    # ============================================================
    # CONDITION EVALUATION
    # ============================================================

    def _conditions_met(
        self,
        state: dict[str, Any],
        conditions: Any,
    ) -> bool:
        if conditions is None:
            return True

        if not isinstance(
            conditions,
            list,
        ):
            raise ValueError(
                "Choice conditions must "
                "be a list."
            )

        for condition in conditions:
            if not isinstance(
                condition,
                dict,
            ):
                return False

            if not self._condition_met(
                state,
                condition,
            ):
                return False

        return True

    def _condition_met(
        self,
        state: dict[str, Any],
        condition: dict[str, Any],
    ) -> bool:
        condition_type = condition.get(
            "type"
        )

        operator = condition.get(
            "operator",
            condition.get(
                "op",
                "eq",
            ),
        )

        if condition_type == "flag":
            source = state.get(
                "flags",
                {},
            )

        elif condition_type == "variable":
            source = state.get(
                "world_state",
                {},
            )

        elif condition_type == "knowledge":
            source = state.get(
                "knowledge",
                {},
            )

        elif condition_type == "relationship":
            source = state.get(
                "relationships",
                {},
            )

        else:
            return False

        key = condition.get(
            "key",
            condition.get(
                "name"
            ),
        )

        if key is None:
            return False

        actual = source.get(
            str(key)
        )

        expected = condition.get(
            "value"
        )

        return self._compare(
            actual,
            expected,
            operator,
        )

    @staticmethod
    def _compare(
        actual: Any,
        expected: Any,
        operator: str,
    ) -> bool:
        if operator == "eq":
            return actual == expected

        if operator == "ne":
            return actual != expected

        if operator == "gt":
            try:
                return actual > expected
            except TypeError:
                return False

        if operator == "gte":
            try:
                return actual >= expected
            except TypeError:
                return False

        if operator == "lt":
            try:
                return actual < expected
            except TypeError:
                return False

        if operator == "lte":
            try:
                return actual <= expected
            except TypeError:
                return False

        if operator == "contains":
            if isinstance(
                actual,
                (list, tuple, set, str),
            ):
                return expected in actual

            return False

        return False
            # ============================================================
    # APPLY PLAYER DECISION
    # ============================================================

    def apply_decision(
        self,
        state: dict[str, Any],
        user_id: str,
        choice_id: str,
    ) -> EngineResult:
        """
        Apply one player's decision.

        This method never mutates the caller's state.

        IMPORTANT:
        Database atomicity is still required around the persisted
        decision. This method only calculates the deterministic
        result after the database layer has accepted the decision.
        """

        normalized = (
            self._require_active_state(
                state
            )
        )

        choice = self.validate_decision(
            normalized,
            user_id,
            choice_id,
        )

        new_state = self._deepcopy(
            normalized
        )

        current_round = self._as_int(
            new_state.get(
                "round_number",
                1,
            ),
            "state.round_number",
        )

        scene_id = self._require_string(
            new_state.get(
                "scene_id"
            ),
            "state.scene_id",
        )

        player_index, player = (
            self._find_player(
                new_state,
                user_id,
            )
        )

        choice_id = self._require_string(
            choice.get(
                "id"
            ),
            "choice.id",
        )

        self._apply_choice_effects(
            new_state,
            player,
            choice,
        )

        player[
            "last_choice_id"
        ] = choice_id

        player[
            "last_decision_round"
        ] = current_round

        decision_event = {
            "round_number": current_round,
            "scene_id": scene_id,
            "user_id": str(
                user_id
            ),
            "choice_id": choice_id,
        }

        history = new_state.setdefault(
            "history",
            [],
        )

        history.append(
            {
                "type": "decision",
                **decision_event,
            }
        )

        events = [
            EngineEvent(
                event_type="decision_applied",
                payload=decision_event,
            )
        ]

        ending_id = (
            self._choice_ending_id(
                choice
            )
        )

        target = self._choice_target(
            choice
        )

        if ending_id is not None:
            self._finish_game(
                new_state,
                ending_id,
                events,
            )

            return EngineResult(
                state=new_state,
                events=events,
                changed=True,
            )

        if target is None:
            raise ValueError(
                f"Choice '{choice_id}' "
                "does not define a target."
            )

        if target in self._endings:
            self._finish_game(
                new_state,
                target,
                events,
            )

            return EngineResult(
                state=new_state,
                events=events,
                changed=True,
            )

        if target not in self._scenes:
            raise ValueError(
                f"Choice '{choice_id}' "
                f"references unknown target "
                f"'{target}'."
            )

        new_state[
            "scene_id"
        ] = target

        new_state[
            "round_number"
        ] = current_round + 1

        events.append(
            EngineEvent(
                event_type="scene_changed",
                payload={
                    "from_scene_id": scene_id,
                    "to_scene_id": target,
                    "round_number": (
                        current_round + 1
                    ),
                },
            )
        )

        events.append(
            EngineEvent(
                event_type="round_advanced",
                payload={
                    "round_number": (
                        current_round + 1
                    ),
                    "scene_id": target,
                },
            )
        )

        del player_index

        return EngineResult(
            state=new_state,
            events=events,
            changed=True,
        )

    # ============================================================
    # CHOICE EFFECTS
    # ============================================================

    def _apply_choice_effects(
        self,
        state: dict[str, Any],
        player: dict[str, Any],
        choice: dict[str, Any],
    ) -> None:
        effects = choice.get(
            "effects",
            {},
        )

        if effects is None:
            effects = {}

        if not isinstance(
            effects,
            dict,
        ):
            raise ValueError(
                "Choice effects must "
                "be an object."
            )

        # --------------------------------------------------------
        # World effects
        # --------------------------------------------------------

        world_effects = effects.get(
            "world"
        )

        if world_effects is None:
            world_effects = effects.get(
                "world_state"
            )

        if isinstance(
            world_effects,
            dict,
        ):
            world_state = state.setdefault(
                "world_state",
                {},
            )

            for key, value in (
                world_effects.items()
            ):
                world_state[
                    str(key)
                ] = self._deepcopy(
                    value
                )

        # --------------------------------------------------------
        # Flag effects
        # --------------------------------------------------------

        flag_effects = effects.get(
            "flags"
        )

        if isinstance(
            flag_effects,
            dict,
        ):
            flags = state.setdefault(
                "flags",
                {},
            )

            for key, value in (
                flag_effects.items()
            ):
                flags[
                    str(key)
                ] = self._deepcopy(
                    value
                )

        # --------------------------------------------------------
        # Relationship effects
        # --------------------------------------------------------

        relationship_effects = (
            effects.get(
                "relationships"
            )
        )

        if isinstance(
            relationship_effects,
            dict,
        ):
            relationships = (
                state.setdefault(
                    "relationships",
                    {},
                )
            )

            self._apply_relationship_effects(
                relationships,
                relationship_effects,
            )

        # --------------------------------------------------------
        # Player effects
        # --------------------------------------------------------

        player_effects = effects.get(
            "player"
        )

        if isinstance(
            player_effects,
            dict,
        ):
            self._apply_player_effects(
                player,
                player_effects,
            )

        # --------------------------------------------------------
        # Direct/legacy compact formats
        # --------------------------------------------------------

        for key in (
            "world",
            "world_state",
        ):
            compact = choice.get(
                key
            )

            if isinstance(
                compact,
                dict,
            ):
                world_state = (
                    state.setdefault(
                        "world_state",
                        {},
                    )
                )

                for field_name, value in (
                    compact.items()
                ):
                    world_state[
                        str(field_name)
                    ] = self._deepcopy(
                        value
                    )

        compact_flags = choice.get(
            "flags"
        )

        if isinstance(
            compact_flags,
            dict,
        ):
            flags = state.setdefault(
                "flags",
                {},
            )

            for field_name, value in (
                compact_flags.items()
            ):
                flags[
                    str(field_name)
                ] = self._deepcopy(
                    value
                )

    @staticmethod
    def _apply_relationship_effects(
        relationships: dict[str, Any],
        effects: dict[str, Any],
    ) -> None:
        for relation_key, raw_effect in (
            effects.items()
        ):
            relation_key = str(
                relation_key
            )

            current = relationships.get(
                relation_key,
                0,
            )

            if isinstance(
                raw_effect,
                dict,
            ):
                if "set" in raw_effect:
                    relationships[
                        relation_key
                    ] = GameEngine._deepcopy(
                        raw_effect["set"]
                    )
                    continue

                if "delta" in raw_effect:
                    delta = raw_effect[
                        "delta"
                    ]

                    try:
                        relationships[
                            relation_key
                        ] = (
                            float(current)
                            + float(delta)
                        )
                    except (
                        TypeError,
                        ValueError,
                    ):
                        raise ValueError(
                            "Relationship delta "
                            f"for '{relation_key}' "
                            "must be numeric."
                        )

                    continue

            if isinstance(
                raw_effect,
                (int, float),
            ) and not isinstance(
                raw_effect,
                bool,
            ):
                try:
                    relationships[
                        relation_key
                    ] = (
                        float(current)
                        + raw_effect
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    raise ValueError(
                        "Relationship value "
                        f"for '{relation_key}' "
                        "is invalid."
                    )

                continue

            relationships[
                relation_key
            ] = GameEngine._deepcopy(
                raw_effect
            )

    @staticmethod
    def _apply_player_effects(
        player: dict[str, Any],
        effects: dict[str, Any],
    ) -> None:
        for key, value in (
            effects.items()
        ):
            key = str(key)

            if key == "status":
                if value not in (
                    GameEngine.PLAYER_STATUSES
                ):
                    raise ValueError(
                        f"Invalid player status: "
                        f"{value}"
                    )

                player[
                    "status"
                ] = value

            elif key == "missed_decisions":
                missed = GameEngine._as_int(
                    value,
                    "missed_decisions",
                )

                if missed < 0:
                    raise ValueError(
                        "missed_decisions "
                        "cannot be negative."
                    )

                player[
                    "missed_decisions"
                ] = missed

            elif key == "missed_decisions_delta":
                current = GameEngine._as_int(
                    player.get(
                        "missed_decisions",
                        0,
                    ),
                    "player.missed_decisions",
                )

                delta = GameEngine._as_int(
                    value,
                    "missed_decisions_delta",
                )

                updated = (
                    current + delta
                )

                if updated < 0:
                    raise ValueError(
                        "missed_decisions "
                        "cannot become "
                        "negative."
                    )

                player[
                    "missed_decisions"
                ] = updated

            else:
                player[
                    key
                ] = GameEngine._deepcopy(
                    value
                )

    # ============================================================
    # MISSED DECISION
    # ============================================================

    def apply_missed_decision(
        self,
        state: dict[str, Any],
        user_id: str,
    ) -> EngineResult:
        """
        Calculate the result of a missed decision.

        IMPORTANT:
        The database layer must make the underlying timeout operation
        idempotent using game_id + round_number + user_id.

        This prevents two workers from incrementing the same player
        twice.
        """

        normalized = (
            self._require_active_state(
                state
            )
        )

        new_state = self._deepcopy(
            normalized
        )

        current_round = self._as_int(
            new_state.get(
                "round_number",
                1,
            ),
            "state.round_number",
        )

        _, player = self._find_player(
            new_state,
            user_id,
        )

        if player.get(
            "status",
            "active",
        ) != "active":
            return EngineResult(
                state=new_state,
                events=[],
                changed=False,
            )

        last_decision_round = (
            player.get(
                "last_decision_round"
            )
        )

        if (
            last_decision_round
            is not None
            and self._as_int(
                last_decision_round,
                "player.last_decision_round",
            )
            == current_round
        ):
            return EngineResult(
                state=new_state,
                events=[],
                changed=False,
            )

        last_missed_round = (
            player.get(
                "last_missed_round"
            )
        )

        if (
            last_missed_round
            is not None
            and self._as_int(
                last_missed_round,
                "player.last_missed_round",
            )
            == current_round
        ):
            return EngineResult(
                state=new_state,
                events=[],
                changed=False,
            )

        missed = self._as_int(
            player.get(
                "missed_decisions",
                0,
            ),
            "player.missed_decisions",
        )

        missed += 1

        player[
            "missed_decisions"
        ] = missed

        player[
            "last_missed_round"
        ] = current_round

        events = [
            EngineEvent(
                event_type="decision_missed",
                payload={
                    "user_id": str(
                        user_id
                    ),
                    "round_number": (
                        current_round
                    ),
                    "missed_decisions": (
                        missed
                    ),
                },
            )
        ]

        history = new_state.setdefault(
            "history",
            [],
        )

        history.append(
            {
                "type": "decision_missed",
                "user_id": str(
                    user_id
                ),
                "round_number": (
                    current_round
                ),
                "missed_decisions": (
                    missed
                ),
            }
        )

        if missed >= 3:
            player[
                "status"
            ] = "eliminated"

            events.append(
                EngineEvent(
                    event_type=(
                        "player_eliminated"
                    ),
                    payload={
                        "user_id": str(
                            user_id
                        ),
                        "reason": (
                            "three_missed_decisions"
                        ),
                        "round_number": (
                            current_round
                        ),
                    },
                )
            )

            history.append(
                {
                    "type": (
                        "player_eliminated"
                    ),
                    "user_id": str(
                        user_id
                    ),
                    "reason": (
                        "three_missed_decisions"
                    ),
                    "round_number": (
                        current_round
                    ),
                }
            )

        return EngineResult(
            state=new_state,
            events=events,
            changed=True,
        )

    # ============================================================
    # ROUND RESOLUTION
    # ============================================================

    def resolve_round(
        self,
        state: dict[str, Any],
        decisions: Iterable[
            dict[str, Any]
        ],
    ) -> EngineResult:
        """
        Resolve a complete round from persisted decisions.

        The database must claim the round before this result is
        committed. The engine itself remains deterministic.
        """

        normalized = (
            self._require_active_state(
                state
            )
        )

        current_round = self._as_int(
            normalized.get(
                "round_number",
                1,
            ),
            "state.round_number",
        )

        decision_map: dict[
            str,
            dict[str, Any],
        ] = {}

        for raw_decision in decisions:
            if not isinstance(
                raw_decision,
                dict,
            ):
                raise ValueError(
                    "Every decision must "
                    "be an object."
                )

            user_id = self._require_string(
                raw_decision.get(
                    "user_id"
                ),
                "decision.user_id",
            )

            if user_id in decision_map:
                raise ValueError(
                    "Duplicate decision "
                    f"for player {user_id}."
                )

            decision_round = self._as_int(
                raw_decision.get(
                    "round_number",
                    current_round,
                ),
                "decision.round_number",
            )

            if (
                decision_round
                != current_round
            ):
                raise ValueError(
                    f"Decision for {user_id} "
                    f"belongs to round "
                    f"{decision_round}, "
                    f"not {current_round}."
                )

            choice_id = self._require_string(
                raw_decision.get(
                    "choice_id"
                ),
                "decision.choice_id",
            )

            decision_map[
                user_id
            ] = {
                **self._deepcopy(
                    raw_decision
                ),
                "user_id": user_id,
                "round_number": (
                    decision_round
                ),
                "choice_id": choice_id,
            }

        new_state = self._deepcopy(
            normalized
        )

        events: list[
            EngineEvent
        ] = []

        active_players = self.active_players(
            new_state
        )

        for player in active_players:
            user_id = self._require_string(
                player.get(
                    "user_id"
                ),
                "player.user_id",
            )

            decision = decision_map.get(
                user_id
            )

            if decision is None:
                continue

            choice = self.validate_decision(
                new_state,
                user_id,
                decision[
                    "choice_id"
                ],
            )

            self._apply_choice_effects(
                new_state,
                player,
                choice,
            )

            player[
                "last_choice_id"
            ] = decision[
                "choice_id"
            ]

            player[
                "last_decision_round"
            ] = current_round

            events.append(
                EngineEvent(
                    event_type=(
                        "round_decision_applied"
                    ),
                    payload={
                        "round_number": (
                            current_round
                        ),
                        "user_id": user_id,
                        "choice_id": (
                            decision[
                                "choice_id"
                            ]
                        ),
                    },
                )
            )

        return self._resolve_after_decisions(
            new_state,
            events,
        )

    def _resolve_after_decisions(
        self,
        state: dict[str, Any],
        events: list[
            EngineEvent
        ],
    ) -> EngineResult:
        current_scene_id = (
            self._require_string(
                state.get(
                    "scene_id"
                ),
                "state.scene_id",
            )
        )

        current_scene = self.get_scene(
            current_scene_id
        )

        ending_id = (
            self._scene_ending_id(
                current_scene
            )
        )

        if ending_id is not None:
            self._finish_game(
                state,
                ending_id,
                events,
            )

            return EngineResult(
                state=state,
                events=events,
                changed=True,
            )

        automatic_target = (
            self._automatic_scene_target(
                current_scene
            )
        )

        if automatic_target is not None:
            if (
                automatic_target
                in self._endings
            ):
                self._finish_game(
                    state,
                    automatic_target,
                    events,
                )

            else:
                self._transition_scene(
                    state,
                    automatic_target,
                    events,
                )

        else:
            current_round = (
                self._as_int(
                    state.get(
                        "round_number",
                        1,
                    ),
                    "state.round_number",
                )
            )

            state[
                "round_number"
            ] = current_round + 1

            events.append(
                EngineEvent(
                    event_type=(
                        "round_advanced"
                    ),
                    payload={
                        "round_number": (
                            current_round + 1
                        ),
                        "scene_id": (
                            state[
                                "scene_id"
                            ]
                        ),
                    },
                )
            )

        return EngineResult(
            state=state,
            events=events,
            changed=True,
        )

    @staticmethod
    def _automatic_scene_target(
        scene: dict[str, Any],
    ) -> str | None:
        for key in (
            "next_scene_id",
            "next_scene",
            "auto_next_scene_id",
            "auto_next_scene",
        ):
            value = scene.get(
                key
            )

            if value is not None:
                return str(
                    value
                ).strip()

        return None

    def _transition_scene(
        self,
        state: dict[str, Any],
        target_scene: str,
        events: list[
            EngineEvent
        ],
    ) -> None:
        target_scene = str(
            target_scene
        ).strip()

        if target_scene not in (
            self._scenes
        ):
            raise ValueError(
                "Cannot transition to "
                f"unknown scene: "
                f"{target_scene}"
            )

        previous_scene = (
            self._require_string(
                state.get(
                    "scene_id"
                ),
                "state.scene_id",
            )
        )

        current_round = self._as_int(
            state.get(
                "round_number",
                1,
            ),
            "state.round_number",
        )

        state[
            "scene_id"
        ] = target_scene

        state[
            "round_number"
        ] = current_round + 1

        events.append(
            EngineEvent(
                event_type=(
                    "scene_changed"
                ),
                payload={
                    "from_scene_id": (
                        previous_scene
                    ),
                    "to_scene_id": (
                        target_scene
                    ),
                    "round_number": (
                        current_round + 1
                    ),
                },
            )
        )

    # ============================================================
    # GAME ENDING
    # ============================================================

    def _finish_game(
        self,
        state: dict[str, Any],
        ending_id: str,
        events: list[
            EngineEvent
        ],
    ) -> None:
        ending_id = str(
            ending_id
        ).strip()

        if ending_id not in (
            self._endings
        ):
            raise ValueError(
                f"Unknown ending: "
                f"{ending_id}"
            )

        if state.get(
            "status"
        ) == "finished":
            return

        state[
            "status"
        ] = "finished"

        state[
            "ending_id"
        ] = ending_id

        history = state.setdefault(
            "history",
            [],
        )

        history.append(
            {
                "type": "game_finished",
                "ending_id": ending_id,
            }
        )

        events.append(
            EngineEvent(
                event_type=(
                    "game_finished"
                ),
                payload={
                    "ending_id": ending_id,
                    "round_number": (
                        state.get(
                            "round_number"
                        )
                    ),
                },
            )
        )

    # ============================================================
    # PLAYER LIFECYCLE
    # ============================================================

    def leave_player(
        self,
        state: dict[str, Any],
        user_id: str,
    ) -> EngineResult:
        new_state = (
            self._require_active_state(
                state
            )
        )

        _, player = self._find_player(
            new_state,
            user_id,
        )

        previous_status = player.get(
            "status",
            "active",
        )

        if (
            previous_status
            == "left"
        ):
            return EngineResult(
                state=new_state,
                events=[],
                changed=False,
            )

        player[
            "status"
        ] = "left"

        event = EngineEvent(
            event_type="player_left",
            payload={
                "user_id": str(
                    user_id
                ),
                "previous_status": (
                    previous_status
                ),
            },
        )

        new_state.setdefault(
            "history",
            [],
        ).append(
            {
                "type": "player_left",
                "user_id": str(
                    user_id
                ),
                "previous_status": (
                    previous_status
                ),
            }
        )

        return EngineResult(
            state=new_state,
            events=[event],
            changed=True,
        )

    def eliminate_player(
        self,
        state: dict[str, Any],
        user_id: str,
        reason: str = "gameplay",
    ) -> EngineResult:
        new_state = (
            self._require_active_state(
                state
            )
        )

        _, player = self._find_player(
            new_state,
            user_id,
        )

        previous_status = player.get(
            "status",
            "active",
        )

        if (
            previous_status
            == "eliminated"
        ):
            return EngineResult(
                state=new_state,
                events=[],
                changed=False,
            )

        player[
            "status"
        ] = "eliminated"

        event = EngineEvent(
            event_type=(
                "player_eliminated"
            ),
            payload={
                "user_id": str(
                    user_id
                ),
                "reason": str(
                    reason
                ),
            },
        )

        new_state.setdefault(
            "history",
            [],
        ).append(
            {
                "type": "player_eliminated",
                "user_id": str(
                    user_id
                ),
                "reason": str(
                    reason
                ),
            }
        )

        return EngineResult(
            state=new_state,
            events=[event],
            changed=True,
        )

    def convert_player_to_npc(
        self,
        state: dict[str, Any],
        user_id: str,
        reason: str = "gameplay",
    ) -> EngineResult:
        new_state = (
            self._require_active_state(
                state
            )
        )

        _, player = self._find_player(
            new_state,
            user_id,
        )

        previous_status = player.get(
            "status",
            "active",
        )

        if (
            previous_status
            == "npc"
        ):
            return EngineResult(
                state=new_state,
                events=[],
                changed=False,
            )

        player[
            "status"
        ] = "npc"

        event = EngineEvent(
            event_type=(
                "player_became_npc"
            ),
            payload={
                "user_id": str(
                    user_id
                ),
                "reason": str(
                    reason
                ),
            },
        )

        new_state.setdefault(
            "history",
            [],
        ).append(
            {
                "type": (
                    "player_became_npc"
                ),
                "user_id": str(
                    user_id
                ),
                "reason": str(
                    reason
                ),
            }
        )

        return EngineResult(
            state=new_state,
            events=[event],
            changed=True,
        )
            # ============================================================
    # ROLE / CHARACTER VALIDATION
    # ============================================================

    def playable_role_ids(
        self,
    ) -> list[str]:
        """
        Return all roles that can be assigned to real players.
        """

        result: list[str] = []

        for role_id, role in (
            self._roles.items()
        ):
            playable = role.get(
                "playable",
                role.get(
                    "is_playable",
                    True,
                ),
            )

            if self._as_bool(
                playable,
                True,
            ):
                result.append(
                    role_id
                )

        return result

    def playable_character_ids(
        self,
    ) -> list[str]:
        """
        Return all playable character IDs.

        Character definitions are optional in some existing story
        formats, so an empty list is valid.
        """

        result: list[str] = []

        for character_id, character in (
            self._characters.items()
        ):
            playable = character.get(
                "playable",
                character.get(
                    "is_playable",
                    True,
                ),
            )

            if self._as_bool(
                playable,
                True,
            ):
                result.append(
                    character_id
                )

        return result

    def validate_player_count(
        self,
        player_count: int,
    ) -> None:
        """
        Ensure the number of players is compatible with the story.

        The maximum is additionally limited by available playable
        roles. This prevents accidental role reuse.
        """

        try:
            player_count = int(
                player_count
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                "player_count must be an integer."
            ) from exc

        if player_count < 1:
            raise ValueError(
                "At least one player is required."
            )

        minimum = self.story.get(
            "minimum_players",
            self.story.get(
                "min_players",
                1,
            ),
        )

        maximum = self.story.get(
            "maximum_players",
            self.story.get(
                "max_players",
                len(
                    self.playable_role_ids()
                ),
            ),
        )

        minimum = int(
            minimum
        )

        maximum = int(
            maximum
        )

        if player_count < minimum:
            raise ValueError(
                f"Player count {player_count} "
                f"is below the story minimum "
                f"of {minimum}."
            )

        if player_count > maximum:
            raise ValueError(
                f"Player count {player_count} "
                f"exceeds the story maximum "
                f"of {maximum}."
            )

        available_roles = len(
            self.playable_role_ids()
        )

        if (
            player_count
            > available_roles
        ):
            raise ValueError(
                f"Player count {player_count} "
                f"exceeds the {available_roles} "
                "available playable roles."
            )

    def validate_role_assignments(
        self,
        players: Iterable[
            dict[str, Any]
        ],
    ) -> None:
        """
        Ensure every assigned role exists and is unique.
        """

        seen_roles: set[str] = set()

        for player in players:
            if not isinstance(
                player,
                dict,
            ):
                raise ValueError(
                    "Player must be an object."
                )

            role_id = player.get(
                "role_id"
            )

            if role_id is None:
                continue

            role_id = self._require_string(
                role_id,
                "player.role_id",
            )

            role = self.get_role(
                role_id
            )

            playable = role.get(
                "playable",
                role.get(
                    "is_playable",
                    True,
                ),
            )

            if not self._as_bool(
                playable,
                True,
            ):
                raise ValueError(
                    f"Role '{role_id}' "
                    "is not playable."
                )

            if role_id in seen_roles:
                raise ValueError(
                    f"Role '{role_id}' "
                    "has been assigned "
                    "more than once."
                )

            seen_roles.add(
                role_id
            )

    # ============================================================
    # SCENE PRESENTATION
    # ============================================================

    def scene_for_player(
        self,
        state: dict[str, Any],
        user_id: str,
    ) -> dict[str, Any]:
        """
        Return the current scene for one player.

        The engine returns game data only. Telegram formatting,
        inline keyboards and messages belong to bot.py.
        """

        normalized = (
            self._require_active_state(
                state
            )
        )

        _, player = self._find_player(
            normalized,
            user_id,
        )

        scene_id = self._require_string(
            normalized.get(
                "scene_id"
            ),
            "state.scene_id",
        )

        scene = self.get_scene(
            scene_id
        )

        result = self._deepcopy(
            scene
        )

        available_choices: list[
            dict[str, Any]
        ] = []

        for choice in self._scene_choices(
            scene
        ):
            if self._conditions_met(
                normalized,
                choice.get(
                    "conditions",
                    [],
                ),
            ):
                available_choices.append(
                    self._deepcopy(
                        choice
                    )
                )

        result[
            "scene_id"
        ] = scene_id

        result[
            "round_number"
        ] = self._as_int(
            normalized.get(
                "round_number",
                1,
            ),
            "state.round_number",
        )

        result[
            "choices"
        ] = available_choices

        result[
            "player_status"
        ] = player.get(
            "status",
            "active",
        )

        result[
            "missed_decisions"
        ] = self._as_int(
            player.get(
                "missed_decisions",
                0,
            ),
            "player.missed_decisions",
        )

        return result

    # ============================================================
    # STORY FINGERPRINT
    # ============================================================

    def story_fingerprint(
        self,
    ) -> str:
        """
        Produce a deterministic SHA-256 fingerprint of the validated
        story.

        This is useful for preventing accidental mixing of different
        story versions inside one game.
        """

        import hashlib
        import json

        canonical = json.dumps(
            self.story,
            sort_keys=True,
            separators=(
                ",",
                ":",
            ),
            ensure_ascii=False,
        )

        return hashlib.sha256(
            canonical.encode(
                "utf-8"
            )
        ).hexdigest()

    # ============================================================
    # PUBLIC STORY ACCESSORS
    # ============================================================

    def get_scene(
        self,
        scene_id: str,
    ) -> dict[str, Any]:
        scene_id = str(
            scene_id
        ).strip()

        try:
            return self._deepcopy(
                self._scenes[
                    scene_id
                ]
            )
        except KeyError as exc:
            raise ValueError(
                f"Unknown scene: "
                f"{scene_id}"
            ) from exc

    def get_role(
        self,
        role_id: str,
    ) -> dict[str, Any]:
        role_id = str(
            role_id
        ).strip()

        try:
            return self._deepcopy(
                self._roles[
                    role_id
                ]
            )
        except KeyError as exc:
            raise ValueError(
                f"Unknown role: "
                f"{role_id}"
            ) from exc

    def get_character(
        self,
        character_id: str,
    ) -> dict[str, Any]:
        character_id = str(
            character_id
        ).strip()

        try:
            return self._deepcopy(
                self._characters[
                    character_id
                ]
            )
        except KeyError as exc:
            raise ValueError(
                f"Unknown character: "
                f"{character_id}"
            ) from exc

    def get_ending(
        self,
        ending_id: str,
    ) -> dict[str, Any]:
        ending_id = str(
            ending_id
        ).strip()

        try:
            return self._deepcopy(
                self._endings[
                    ending_id
                ]
            )
        except KeyError as exc:
            raise ValueError(
                f"Unknown ending: "
                f"{ending_id}"
            ) from exc

    def get_first_scene_id(
        self,
    ) -> str:
        first_scene = self.story.get(
            "first_scene_id"
        )

        if first_scene is None:
            first_scene = self.story.get(
                "first_scene"
            )

        if first_scene is None:
            raise ValueError(
                "Story does not define "
                "first_scene_id."
            )

        first_scene = str(
            first_scene
        ).strip()

        if first_scene not in (
            self._scenes
        ):
            raise ValueError(
                "First scene does not exist: "
                f"{first_scene}"
            )

        return first_scene

    # ============================================================
    # INTERNAL INDEXING
    # ============================================================

    @staticmethod
    def _index_by_id(
        values: Any,
        object_name: str,
    ) -> dict[
        str,
        dict[str, Any],
    ]:
        if values is None:
            values = []

        if not isinstance(
            values,
            list,
        ):
            raise ValueError(
                f"{object_name}s must "
                "be a list."
            )

        result: dict[
            str,
            dict[str, Any],
        ] = {}

        for raw in values:
            if not isinstance(
                raw,
                dict,
            ):
                raise ValueError(
                    f"Every {object_name} "
                    "must be an object."
                )

            object_id = raw.get(
                "id"
            )

            object_id = (
                GameEngine._require_string(
                    object_id,
                    f"{object_name}.id",
                )
            )

            if object_id in result:
                raise ValueError(
                    f"Duplicate "
                    f"{object_name} ID: "
                    f"{object_id}"
                )

            result[
                object_id
            ] = GameEngine._deepcopy(
                raw
            )

        return result

    def _index_characters(
        self,
    ) -> dict[
        str,
        dict[str, Any],
    ]:
        candidates = self.story.get(
            "characters"
        )

        if candidates is None:
            candidates = self.story.get(
                "playable_characters",
                [],
            )

        return self._index_by_id(
            candidates,
            "character",
        )

    def _index_endings(
        self,
    ) -> dict[
        str,
        dict[str, Any],
    ]:
        endings = self.story.get(
            "endings",
            [],
        )

        result: dict[
            str,
            dict[str, Any],
        ] = {}

        if isinstance(
            endings,
            dict,
        ):
            for ending_id, raw in (
                endings.items()
            ):
                if not isinstance(
                    raw,
                    dict,
                ):
                    raise ValueError(
                        "Every ending must "
                        "be an object."
                    )

                ending = (
                    self._deepcopy(
                        raw
                    )
                )

                ending.setdefault(
                    "id",
                    str(
                        ending_id
                    ),
                )

                normalized_id = (
                    self._require_string(
                        ending.get(
                            "id"
                        ),
                        "ending.id",
                    )
                )

                if normalized_id in (
                    result
                ):
                    raise ValueError(
                        "Duplicate ending "
                        f"ID: "
                        f"{normalized_id}"
                    )

                result[
                    normalized_id
                ] = ending

            return result

        if not isinstance(
            endings,
            list,
        ):
            raise ValueError(
                "endings must be "
                "a list or object."
            )

        for raw in endings:
            if not isinstance(
                raw,
                dict,
            ):
                raise ValueError(
                    "Every ending must "
                    "be an object."
                )

            ending_id = (
                self._require_string(
                    raw.get(
                        "id"
                    ),
                    "ending.id",
                )
            )

            if ending_id in (
                result
            ):
                raise ValueError(
                    "Duplicate ending "
                    f"ID: "
                    f"{ending_id}"
                )

            result[
                ending_id
            ] = self._deepcopy(
                raw
            )

        return result

    # ============================================================
    # RUNTIME STORY INDEX VALIDATION
    # ============================================================

    def _validate_story_indexes(
        self,
    ) -> None:
        """
        Perform a second safety check when the engine is created.

        story_validator.py performs the complete story validation.
        These checks protect the engine if someone constructs it
        directly.
        """

        first_scene = (
            self.story.get(
                "first_scene_id"
            )
        )

        if first_scene is None:
            first_scene = (
                self.story.get(
                    "first_scene"
                )
            )

        if first_scene is None:
            raise ValueError(
                "Story must define "
                "first_scene_id."
            )

        first_scene = str(
            first_scene
        ).strip()

        if first_scene not in (
            self._scenes
        ):
            raise ValueError(
                "first_scene_id "
                f"'{first_scene}' "
                "does not exist."
            )

        for scene_id, scene in (
            self._scenes.items()
        ):
            choices = self._scene_choices(
                scene
            )

            seen_choices: set[
                str
            ] = set()

            for choice in choices:
                choice_id = (
                    self._require_string(
                        choice.get(
                            "id"
                        ),
                        f"scene[{scene_id}]"
                        ".choice.id",
                    )
                )

                if choice_id in (
                    seen_choices
                ):
                    raise ValueError(
                        f"Duplicate choice "
                        f"'{choice_id}' "
                        f"in scene "
                        f"'{scene_id}'."
                    )

                seen_choices.add(
                    choice_id
                )

                target = (
                    self._choice_target(
                        choice
                    )
                )

                ending_id = (
                    self._choice_ending_id(
                        choice
                    )
                )

                if (
                    target is None
                    and ending_id is None
                ):
                    raise ValueError(
                        f"Choice "
                        f"'{choice_id}' "
                        f"in scene "
                        f"'{scene_id}' "
                        "has no target."
                    )

                if (
                    ending_id is not None
                    and ending_id not in (
                        self._endings
                    )
                ):
                    raise ValueError(
                        f"Choice "
                        f"'{choice_id}' "
                        "references "
                        f"unknown ending "
                        f"'{ending_id}'."
                    )

                if (
                    target is not None
                    and target not in (
                        self._scenes
                    )
                    and target not in (
                        self._endings
                    )
                ):
                    raise ValueError(
                        f"Choice "
                        f"'{choice_id}' "
                        "references "
                        f"unknown target "
                        f"'{target}'."
                    )

    # ============================================================
    # STATIC UTILITIES
    # ============================================================

    @staticmethod
    def _deepcopy(
        value: Any,
    ) -> Any:
        import copy

        return copy.deepcopy(
            value
        )

    @staticmethod
    def _require_string(
        value: Any,
        field_name: str,
    ) -> str:
        if value is None:
            raise ValueError(
                f"Missing required field: "
                f"{field_name}"
            )

        result = str(
            value
        ).strip()

        if not result:
            raise ValueError(
                f"Field '{field_name}' "
                "cannot be empty."
            )

        return result

    @staticmethod
    def _as_int(
        value: Any,
        field_name: str,
        default: int | None = None,
    ) -> int:
        if value is None:
            if default is not None:
                return default

            raise ValueError(
                f"Missing integer field: "
                f"{field_name}"
            )

        if isinstance(
            value,
            bool,
        ):
            raise ValueError(
                f"Invalid integer for "
                f"'{field_name}'."
            )

        try:
            return int(
                value
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError(
                f"Invalid integer for "
                f"'{field_name}': "
                f"{value!r}"
            ) from exc

    @staticmethod
    def _as_bool(
        value: Any,
        default: bool = False,
    ) -> bool:
        if value is None:
            return default

        if isinstance(
            value,
            bool,
        ):
            return value

        if isinstance(
            value,
            str,
        ):
            normalized = (
                value.strip()
                .lower()
            )

            if normalized in (
                "true",
                "1",
                "yes",
                "y",
            ):
                return True

            if normalized in (
                "false",
                "0",
                "no",
                "n",
            ):
                return False

        return bool(
            value
        )


# ================================================================
# COMPATIBILITY HELPER
# ================================================================

def scene_has_ending(
    scene: dict[str, Any],
) -> bool:
    """
    Compatibility helper for other project modules.

    Returns True when a scene explicitly represents a terminal
    ending.
    """

    if not isinstance(
        scene,
        dict,
    ):
        return False

    for key in (
        "ending_id",
        "ending",
    ):
        if scene.get(
            key
        ) is not None:
            return True

    if GameEngine._as_bool(
        scene.get(
            "is_ending"
        ),
        False,
    ):
        return True

    if GameEngine._as_bool(
        scene.get(
            "terminal"
        ),
        False,
    ):
        return True

    return False
