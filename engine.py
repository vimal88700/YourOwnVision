from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class EngineEvent:
    event_type: str
    payload: dict[str, Any]


class EngineResult(dict):
    """Dict-compatible result used by game_service.py."""

    def __init__(
        self,
        *,
        state: dict[str, Any],
        events: list[EngineEvent],
        changed: bool = True,
        missed_results: list[dict[str, Any]] | None = None,
    ) -> None:
        self.state = state
        self.events = events
        self.changed = changed
        self.missed_results = missed_results or []

        ending = state.get("ending_id")
        next_scene = None if ending else state.get("scene_id")

        super().__init__(
            state=state,
            world_state=copy.deepcopy(state.get("world_state", {})),
            events=events,
            changed=changed,
            ending=ending,
            next_scene=next_scene,
            missed_results=self.missed_results,
        )


class GameEngine:
    """
    Deterministic gameplay engine for WHAT HAPPENS?

    No Telegram, Gemini, or Supabase calls belong here.
    The service/database layer owns persistence and networking.

    This implementation deliberately accepts both:
      - normal scene choices with next_scene_id/next_scene
      - terminal choices with ending_id/ending
      - scenes marked is_ending=True

    That prevents the previous crash:
        Choice '...' has no target.
    """

    MIN_TIMER_SECONDS = 10
    MAX_TIMER_SECONDS = 3600
    MAX_MISSED_DECISIONS = 3

    PLAYER_STATUSES = {
        "pending",
        "active",
        "eliminated",
        "npc",
        "left",
    }

    def __init__(self, story: dict[str, Any]) -> None:
        if not isinstance(story, dict):
            raise ValueError("Story must be an object.")

        self.story = copy.deepcopy(story)

        roles = self.story.get("roles", [])
        scenes = self.story.get("scenes", [])

        if not isinstance(roles, list):
            raise ValueError("Story roles must be a list.")
        if not isinstance(scenes, list):
            raise ValueError("Story scenes must be a list.")

        self.roles = self._index_list(roles, "role")
        self.scenes = self._index_list(scenes, "scene")
        self.characters = self._index_characters()
        self.endings = self._index_endings()

        # Compatibility with the previous engine/game_service code.
        self._roles = self.roles
        self._scenes = self.scenes
        self._characters = self.characters
        self._endings = self.endings

        self.validate_story()

    # ============================================================
    # BASIC HELPERS
    # ============================================================

    @staticmethod
    def _deepcopy(value: Any) -> Any:
        return copy.deepcopy(value)

    @staticmethod
    def _require_string(value: Any, field_name: str) -> str:
        if value is None:
            raise ValueError(f"Missing required field: {field_name}")
        result = str(value).strip()
        if not result:
            raise ValueError(f"Field '{field_name}' cannot be empty.")
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
            raise ValueError(f"{field_name} must be an integer.")
        if isinstance(value, bool):
            raise ValueError(f"{field_name} must be an integer.")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be an integer.") from exc

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            value = value.strip().lower()
            if value in {"true", "1", "yes", "y"}:
                return True
            if value in {"false", "0", "no", "n"}:
                return False
        return bool(value)

    @staticmethod
    def _fingerprint(value: Any) -> str:
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @classmethod
    def _index_list(
        cls,
        values: Any,
        name: str,
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        for raw in values:
            if not isinstance(raw, dict):
                raise ValueError(f"Every {name} must be an object.")

            object_id = cls._require_string(
                raw.get("id"),
                f"{name}.id",
            )

            if object_id in result:
                raise ValueError(f"Duplicate {name} id: {object_id}")

            result[object_id] = copy.deepcopy(raw)

        return result

    def _index_characters(self) -> dict[str, dict[str, Any]]:
        values = self.story.get("characters")
        if values is None:
            values = self.story.get("playable_characters", [])
        return self._index_list(values or [], "character")

    def _index_endings(self) -> dict[str, dict[str, Any]]:
        endings = self.story.get("endings", [])
        result: dict[str, dict[str, Any]] = {}

        if isinstance(endings, dict):
            for key, raw in endings.items():
                if not isinstance(raw, dict):
                    raise ValueError("Every ending must be an object.")
                item = copy.deepcopy(raw)
                item.setdefault("id", str(key))
                ending_id = self._require_string(
                    item.get("id"),
                    "ending.id",
                )
                if ending_id in result:
                    raise ValueError(f"Duplicate ending id: {ending_id}")
                result[ending_id] = item
            return result

        if not isinstance(endings, list):
            raise ValueError("endings must be a list or object.")

        for raw in endings:
            if not isinstance(raw, dict):
                raise ValueError("Every ending must be an object.")
            ending_id = self._require_string(raw.get("id"), "ending.id")
            if ending_id in result:
                raise ValueError(f"Duplicate ending id: {ending_id}")
            result[ending_id] = copy.deepcopy(raw)

        return result

    # ============================================================
    # STORY API
    # ============================================================

    def story_fingerprint(self) -> str:
        return self._fingerprint(self.story)

    def get_scene(self, scene_id: str) -> dict[str, Any]:
        scene_id = self._require_string(scene_id, "scene_id")
        try:
            return copy.deepcopy(self._scenes[scene_id])
        except KeyError as exc:
            raise ValueError(f"Unknown scene: {scene_id}") from exc

    def get_role(self, role_id: str) -> dict[str, Any]:
        role_id = self._require_string(role_id, "role_id")
        try:
            return copy.deepcopy(self._roles[role_id])
        except KeyError as exc:
            raise ValueError(f"Unknown role: {role_id}") from exc

    def get_character(self, character_id: str) -> dict[str, Any]:
        character_id = self._require_string(character_id, "character_id")
        try:
            return copy.deepcopy(self._characters[character_id])
        except KeyError as exc:
            raise ValueError(f"Unknown character: {character_id}") from exc

    def get_ending(self, ending_id: str) -> dict[str, Any]:
        ending_id = self._require_string(ending_id, "ending_id")
        try:
            return copy.deepcopy(self._endings[ending_id])
        except KeyError as exc:
            raise ValueError(f"Unknown ending: {ending_id}") from exc

    def first_scene_id(self) -> str:
        return self.get_first_scene_id()

    def get_first_scene_id(self) -> str:
        first = self.story.get(
            "first_scene_id",
            self.story.get("first_scene"),
        )
        first = self._require_string(first, "first_scene_id")

        if first not in self._scenes:
            raise ValueError(f"First scene does not exist: {first}")

        return first

    def initial_world_state(self) -> dict[str, Any]:
        value = self.story.get("initial_world_state", {})
        if not isinstance(value, dict):
            raise ValueError("initial_world_state must be an object.")
        return copy.deepcopy(value)

    # ============================================================
    # ROLES / PLAYERS
    # ============================================================

    def playable_roles(self) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(role)
            for role in self._roles.values()
            if self._as_bool(
                role.get("playable", role.get("is_playable", True)),
                True,
            )
        ]

    def playable_role_ids(self) -> list[str]:
        return [role["id"] for role in self.playable_roles()]

    def playable_character_ids(self) -> list[str]:
        return [
            key
            for key, value in self._characters.items()
            if self._as_bool(
                value.get("playable", value.get("is_playable", True)),
                True,
            )
        ]

    def validate_player_count(self, player_count: int) -> None:
        player_count = self._as_int(player_count, "player_count")

        minimum = self._as_int(
            self.story.get(
                "minimum_players",
                self.story.get("min_players", 1),
            ),
            "minimum_players",
        )

        maximum = self._as_int(
            self.story.get(
                "maximum_players",
                self.story.get(
                    "max_players",
                    len(self.playable_roles()),
                ),
            ),
            "maximum_players",
        )

        if player_count < minimum:
            raise ValueError(
                f"Player count {player_count} is below minimum {minimum}."
            )

        if player_count > maximum:
            raise ValueError(
                f"Player count {player_count} exceeds maximum {maximum}."
            )

        if player_count > len(self.playable_roles()):
            raise ValueError(
                "Player count exceeds available playable roles."
            )

    def validate_role_assignments(
        self,
        players: Iterable[dict[str, Any]],
    ) -> None:
        seen: set[str] = set()

        for player in players:
            role_id = player.get("role_id")
            if role_id is None:
                continue

            role_id = self._require_string(
                role_id,
                "player.role_id",
            )

            role = self.get_role(role_id)

            if not self._as_bool(
                role.get("playable", role.get("is_playable", True)),
                True,
            ):
                raise ValueError(f"Role '{role_id}' is not playable.")

            if role_id in seen:
                raise ValueError(
                    f"Role '{role_id}' has been assigned more than once."
                )

            seen.add(role_id)

    # ============================================================
    # VALIDATION
    # ============================================================

    def validate_story(self) -> None:
        if not self._roles:
            raise ValueError("Story must contain at least one role.")
        if not self._scenes:
            raise ValueError("Story must contain at least one scene.")

        self.get_first_scene_id()

        for scene_id, scene in self._scenes.items():
            choices = self._scene_choices(scene)
            seen: set[str] = set()

            for choice in choices:
                choice_id = self._require_string(
                    choice.get("id"),
                    f"scene[{scene_id}].choice.id",
                )

                if choice_id in seen:
                    raise ValueError(
                        f"Duplicate choice '{choice_id}' in scene '{scene_id}'."
                    )
                seen.add(choice_id)

                # A choice is allowed to terminate the game.
                # ending_id/ending is a valid target.
                target = self._choice_target(choice)

                if not target:
                    # Some generated stories use:
                    # {"ends_game": true}
                    # or {"terminal": true}.
                    # Treat these as terminal choices and normalize them.
                    if self._choice_is_terminal(choice):
                        continue

                    raise ValueError(
                        f"Choice '{choice_id}' in scene '{scene_id}' "
                        "has no target."
                    )

                if (
                    target not in self._scenes
                    and target not in self._endings
                ):
                    raise ValueError(
                        f"Choice '{choice_id}' references unknown target "
                        f"'{target}'."
                    )

                timer = choice.get(
                    "timer_seconds",
                    choice.get("time_limit"),
                )

                if timer is not None:
                    self._validate_timer(
                        timer,
                        f"choice '{choice_id}'",
                    )

            ending = self._scene_ending_id(scene)

            if ending and ending not in self._endings:
                raise ValueError(
                    f"Scene '{scene_id}' references unknown ending '{ending}'."
                )

    def _validate_timer(self, value: Any, label: str) -> int:
        seconds = self._as_int(value, label)
        if not (
            self.MIN_TIMER_SECONDS
            <= seconds
            <= self.MAX_TIMER_SECONDS
        ):
            raise ValueError(
                f"{label} timer must be between "
                f"{self.MIN_TIMER_SECONDS} and "
                f"{self.MAX_TIMER_SECONDS} seconds."
            )
        return seconds

    # ============================================================
    # SCENES / CHOICES
    # ============================================================

    @staticmethod
    def _scene_choices(scene: dict[str, Any]) -> list[dict[str, Any]]:
        choices = scene.get("choices", [])
        if not isinstance(choices, list):
            raise ValueError("Scene choices must be a list.")
        return [
            choice
            for choice in choices
            if isinstance(choice, dict)
        ]

    @staticmethod
    def _choice_target(choice: dict[str, Any]) -> str | None:
        for key in (
            "next_scene_id",
            "next_scene",
            "target_scene_id",
            "target_scene",
            "ending_id",
            "ending",
        ):
            value = choice.get(key)

            if isinstance(value, dict):
                value = value.get("id")

            if value is not None:
                result = str(value).strip()
                if result:
                    return result

        return None

    @staticmethod
    def _choice_is_terminal(choice: dict[str, Any]) -> bool:
        return GameEngine._as_bool(
            choice.get(
                "ends_game",
                choice.get(
                    "terminal",
                    choice.get(
                        "is_terminal",
                        False,
                    ),
                ),
            ),
            False,
        )

    @staticmethod
    def _choice_ending_id(choice: dict[str, Any]) -> str | None:
        value = choice.get(
            "ending_id",
            choice.get("ending"),
        )

        if isinstance(value, dict):
            value = value.get("id")

        if value is None:
            return None

        value = str(value).strip()
        return value or None

    @staticmethod
    def _scene_ending_id(scene: dict[str, Any]) -> str | None:
        value = scene.get(
            "ending_id",
            scene.get("ending"),
        )

        if isinstance(value, dict):
            value = value.get("id")

        if value is not None:
            value = str(value).strip()
            return value or None

        if GameEngine._as_bool(scene.get("is_ending"), False):
            scene_id = str(scene.get("id", "")).strip()
            return scene_id or None

        return None

    def timer_seconds(self, scene: dict[str, Any]) -> int:
        value = scene.get(
            "timer_seconds",
            scene.get("decision_timer_seconds"),
        )

        if value is None:
            value = self.story.get(
                "timer_seconds",
                self.story.get("decision_timer_seconds", 60),
            )

        return self._validate_timer(value, "scene")

    def enter_scene(
        self,
        world_state: dict[str, Any],
        scene_id: str,
    ) -> dict[str, Any]:
        self.get_scene(scene_id)

        if not isinstance(world_state, dict):
            raise ValueError("world_state must be an object.")

        result = copy.deepcopy(world_state)
        result["scene_id"] = scene_id
        return result

    # ============================================================
    # CONDITIONS
    # ============================================================

    def _lookup_condition_value(
        self,
        state: dict[str, Any],
        condition: dict[str, Any],
    ) -> Any:
        condition_type = str(
            condition.get("type", "flag")
        ).strip().lower()

        key = condition.get(
            "key",
            condition.get("name"),
        )

        if condition_type == "flag":
            return state.get("flags", {}).get(str(key))

        if condition_type == "variable":
            return state.get("variables", {}).get(str(key))

        if condition_type == "knowledge":
            knowledge = state.get("knowledge", [])

            if isinstance(knowledge, dict):
                return knowledge.get(str(key))

            if isinstance(knowledge, list):
                return key in knowledge

            return None

        if condition_type == "relationship":
            relationships = state.get("relationships", {})

            if isinstance(key, list) and len(key) >= 2:
                key = f"{key[0]}:{key[1]}"

            return relationships.get(str(key))

        return None

    @staticmethod
    def _compare(
        actual: Any,
        expected: Any,
        operator: str,
    ) -> bool:
        try:
            if operator == "eq":
                return actual == expected
            if operator == "ne":
                return actual != expected
            if operator == "gt":
                return actual > expected
            if operator == "gte":
                return actual >= expected
            if operator == "lt":
                return actual < expected
            if operator == "lte":
                return actual <= expected
            if operator == "contains":
                return expected in actual if actual is not None else False
        except (TypeError, ValueError):
            return False

        return False

    def _conditions_met(
        self,
        state: dict[str, Any],
        conditions: Any,
    ) -> bool:
        if not conditions:
            return True

        if not isinstance(conditions, list):
            return False

        for condition in conditions:
            if not isinstance(condition, dict):
                return False

            actual = self._lookup_condition_value(
                state,
                condition,
            )
            expected = condition.get("value")
            operator = str(
                condition.get("operator", "eq")
            ).lower()

            if not self._compare(
                actual,
                expected,
                operator,
            ):
                return False

        return True

    # ============================================================
    # EFFECTS
    # ============================================================

    def _apply_effects(
        self,
        state: dict[str, Any],
        choice: dict[str, Any],
    ) -> None:
        effects = choice.get("effects") or {}

        if not isinstance(effects, dict):
            return

        flags = state.setdefault("flags", {})
        variables = state.setdefault("variables", {})
        relationships = state.setdefault("relationships", {})
        knowledge = state.setdefault("knowledge", [])

        for key, value in (effects.get("set_flags") or {}).items():
            flags[str(key)] = value

        for key in effects.get("remove_flags") or []:
            flags.pop(str(key), None)

        for key, value in (effects.get("set_variables") or {}).items():
            variables[str(key)] = value

        for key, value in (effects.get("add_variables") or {}).items():
            old = variables.get(str(key), 0)
            try:
                variables[str(key)] = old + value
            except TypeError:
                variables[str(key)] = value

        relationship_effects = effects.get("relationships") or []

        if isinstance(relationship_effects, dict):
            relationship_effects = [relationship_effects]

        for relationship in relationship_effects:
            if not isinstance(relationship, dict):
                continue

            source = relationship.get(
                "from",
                relationship.get("source"),
            )
            target = relationship.get(
                "to",
                relationship.get("target"),
            )

            key = (
                f"{source}:{target}"
                if source is not None and target is not None
                else str(relationship.get("key", ""))
            )

            if not key:
                continue

            if relationship.get("set") is not None:
                relationships[key] = relationship["set"]
                continue

            delta = relationship.get(
                "change",
                relationship.get(
                    "delta",
                    relationship.get("value", 0),
                ),
            )

            try:
                relationships[key] = (
                    relationships.get(key, 0) + delta
                )
            except TypeError:
                relationships[key] = delta

        for item in effects.get("knowledge") or []:
            if item not in knowledge:
                knowledge.append(item)

        if effects.get("character_state") is not None:
            state["character_state"] = copy.deepcopy(
                effects["character_state"]
            )

        if effects.get("text") is not None:
            state["last_effect_text"] = effects["text"]
        elif effects.get("description") is not None:
            state["last_effect_text"] = effects["description"]

    # ============================================================
    # INITIAL STATE
    # ============================================================

    def create_initial_state(
        self,
        players: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        player_list = [
            copy.deepcopy(player)
            for player in players
        ]

        if not player_list:
            raise ValueError("At least one player is required.")

        self.validate_player_count(len(player_list))
        self.validate_role_assignments(player_list)

        seen: set[str] = set()
        normalized: list[dict[str, Any]] = []

        for player in player_list:
            user_id = self._require_string(
                player.get("user_id"),
                "player.user_id",
            )

            if user_id in seen:
                raise ValueError(
                    f"Duplicate player user_id: {user_id}"
                )

            seen.add(user_id)

            status = player.get("status", "active")

            if status not in self.PLAYER_STATUSES:
                raise ValueError(
                    f"Invalid player status '{status}' for {user_id}."
                )

            normalized.append(
                {
                    **player,
                    "user_id": user_id,
                    "status": status,
                    "missed_decisions": self._as_int(
                        player.get("missed_decisions", 0),
                        "player.missed_decisions",
                    ),
                    "last_decision_round": player.get(
                        "last_decision_round"
                    ),
                    "last_choice_id": player.get(
                        "last_choice_id"
                    ),
                }
            )

        return {
            "status": "active",
            "round_number": 1,
            "scene_id": self.first_scene_id(),
            "players": normalized,
            "world_state": self.initial_world_state(),
            "relationships": copy.deepcopy(
                self.story.get("initial_relationships", {})
            ),
            "flags": copy.deepcopy(
                self.story.get("initial_flags", {})
            ),
            "variables": copy.deepcopy(
                self.story.get("initial_variables", {})
            ),
            "knowledge": copy.deepcopy(
                self.story.get("initial_knowledge", [])
            ),
            "history": [],
            "ending_id": None,
            "story_fingerprint": self.story_fingerprint(),
        }

    # ============================================================
    # PLAYER HELPERS
    # ============================================================

    def _find_player(
        self,
        state: dict[str, Any],
        user_id: str,
    ) -> tuple[int, dict[str, Any]]:
        uid = str(user_id)

        for index, player in enumerate(
            state.get("players", [])
        ):
            if str(player.get("user_id")) == uid:
                return index, player

        raise ValueError(f"Unknown player: {uid}")

    def active_players(
        self,
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return [
            player
            for player in state.get("players", [])
            if player.get("status", "active") == "active"
        ]

    # ============================================================
    # ROLE UI / CHOICES
    # ============================================================

    def choices_for_role(
        self,
        scene: dict[str, Any],
        role_id: str,
        world_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        state = {
            "world_state": copy.deepcopy(world_state),
            "flags": (
                world_state.get("flags", {})
                if isinstance(world_state, dict)
                else {}
            ),
            "variables": (
                world_state.get("variables", {})
                if isinstance(world_state, dict)
                else {}
            ),
            "relationships": (
                world_state.get("relationships", {})
                if isinstance(world_state, dict)
                else {}
            ),
            "knowledge": (
                world_state.get("knowledge", [])
                if isinstance(world_state, dict)
                else []
            ),
        }

        return [
            copy.deepcopy(choice)
            for choice in self._scene_choices(scene)
            if self._conditions_met(
                state,
                choice.get("conditions", []),
            )
        ]

    def validate_choice_for_role(
        self,
        scene: dict[str, Any],
        role_id: str,
        choice_id: str,
        world_state: dict[str, Any],
    ) -> dict[str, Any]:
        for choice in self.choices_for_role(
            scene,
            role_id,
            world_state,
        ):
            if (
                str(choice.get("id", "")).strip()
                == str(choice_id).strip()
            ):
                return choice

        raise ValueError(
            f"Choice '{choice_id}' is not available "
            f"in scene '{scene.get('id')}'."
        )

    # ============================================================
    # DECISION VALIDATION
    # ============================================================

    def validate_decision(
        self,
        state: dict[str, Any],
        user_id: str,
        choice_id: str,
        *,
        scene: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _, player = self._find_player(
            state,
            user_id,
        )

        if player.get("status", "active") != "active":
            raise ValueError("Player is not active.")

        current_round = self._as_int(
            state.get("round_number", 1),
            "state.round_number",
        )

        if player.get("last_decision_round") == current_round:
            raise ValueError(
                "Player has already submitted a decision for this round."
            )

        scene = scene or self.get_scene(
            state.get("scene_id")
        )

        for choice in self._scene_choices(scene):
            if (
                str(choice.get("id", "")).strip()
                == str(choice_id).strip()
            ):
                if not self._conditions_met(
                    state,
                    choice.get("conditions", []),
                ):
                    raise ValueError(
                        "This choice is not currently available."
                    )

                return copy.deepcopy(choice)

        raise ValueError(
            f"Choice '{choice_id}' is not available "
            f"in scene '{scene.get('id')}'."
        )

    # ============================================================
    # SCENE TRANSITIONS
    # ============================================================

    @staticmethod
    def _automatic_scene_target(
        scene: dict[str, Any],
    ) -> str | None:
        for key in (
            "next_scene_id",
            "next_scene",
            "target_scene_id",
            "target_scene",
        ):
            value = scene.get(key)

            if isinstance(value, dict):
                value = value.get("id")

            if value is not None:
                result = str(value).strip()
                if result:
                    return result

        return None

    def _transition_scene(
        self,
        state: dict[str, Any],
        target_scene: str,
        events: list[EngineEvent],
    ) -> None:
        target_scene = self._require_string(
            target_scene,
            "target_scene",
        )

        if target_scene not in self._scenes:
            raise ValueError(
                f"Cannot transition to unknown scene '{target_scene}'."
            )

        old_scene = state.get("scene_id")
        state["scene_id"] = target_scene

        events.append(
            EngineEvent(
                "scene_transition",
                {
                    "from_scene": old_scene,
                    "to_scene": target_scene,
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
        events: list[EngineEvent],
    ) -> None:
        ending_id = self._require_string(
            ending_id,
            "ending_id",
        )

        if ending_id not in self._endings:
            raise ValueError(
                f"Unknown ending: {ending_id}"
            )

        state["status"] = "finished"
        state["ending_id"] = ending_id

        ending = self._endings[ending_id]

        if isinstance(ending, dict):
            text = ending.get(
                "text",
                ending.get(
                    "description",
                ),
            )
            if text is not None:
                state["ending_text"] = str(text)

        events.append(
            EngineEvent(
                "game_finished",
                {
                    "ending_id": ending_id,
                },
            )
        )

    # ============================================================
    # ROUND RESOLUTION
    # ============================================================

    def resolve_round(
        self,
        state: dict[str, Any] | None = None,
        decisions: Iterable[dict[str, Any]] | None = None,
        *,
        scene_id: str | None = None,
        round_number: int | None = None,
        world_state: dict[str, Any] | None = None,
        players: Iterable[dict[str, Any]] | None = None,
    ) -> EngineResult:
        """
        Supports both APIs:

        resolve_round(state, decisions)

        and:

        resolve_round(
            scene_id=...,
            round_number=...,
            world_state=...,
            players=...,
            decisions=...,
        )
        """

        if state is None:
            if scene_id is None or round_number is None:
                raise ValueError(
                    "state or scene_id/round_number is required."
                )

            supplied_world = copy.deepcopy(world_state or {})

            state = {
                "status": "active",
                "round_number": int(round_number),
                "scene_id": str(scene_id),
                "players": copy.deepcopy(list(players or [])),
                "world_state": supplied_world,
                "flags": copy.deepcopy(
                    supplied_world.get("flags", {})
                ),
                "variables": copy.deepcopy(
                    supplied_world.get("variables", {})
                ),
                "relationships": copy.deepcopy(
                    supplied_world.get("relationships", {})
                ),
                "knowledge": copy.deepcopy(
                    supplied_world.get("knowledge", [])
                ),
                "history": [],
                "ending_id": None,
            }
        else:
            state = copy.deepcopy(state)

        decisions_list = list(decisions or [])

        current_round = self._as_int(
            state.get("round_number", 1),
            "state.round_number",
        )

        decision_map = {
            str(decision.get("user_id")): decision
            for decision in decisions_list
            if isinstance(decision, dict)
        }

        missed_results: list[dict[str, Any]] = []
        events: list[EngineEvent] = []

        # Process each active player's decision.
        for player in state.get("players", []):
            if player.get("status", "active") != "active":
                continue

            user_id = str(player.get("user_id"))
            decision = decision_map.get(user_id)

            if decision is None:
                missed = (
                    self._as_int(
                        player.get("missed_decisions", 0),
                        "player.missed_decisions",
                    )
                    + 1
                )

                player["missed_decisions"] = missed

                missed_results.append(
                    {
                        "user_id": user_id,
                        "missed_decisions": missed,
                    }
                )

                # Do not automatically kill a player unless the
                # service explicitly wants that behavior. Preserve
                # the original MAX_MISSED_DECISIONS policy by
                # marking the player after the configured threshold.
                if missed >= self.MAX_MISSED_DECISIONS:
                    player["status"] = "eliminated"

                    events.append(
                        EngineEvent(
                            "player_eliminated",
                            {
                                "user_id": user_id,
                                "reason": "missed_decisions",
                            },
                        )
                    )

                continue

            choice_id = self._require_string(
                decision.get("choice_id"),
                "decision.choice_id",
            )

            scene = self.get_scene(
                state["scene_id"]
            )

            choice = self.validate_decision(
                state,
                user_id,
                choice_id,
                scene=scene,
            )

            self._apply_effects(
                state,
                choice,
            )

            player["last_choice_id"] = choice_id
            player["last_decision_round"] = current_round
            player["missed_decisions"] = 0

            events.append(
                EngineEvent(
                    "round_decision_applied",
                    {
                        "round_number": current_round,
                        "user_id": user_id,
                        "choice_id": choice_id,
                    },
                )
            )

            ending = self._choice_ending_id(choice)
            target = self._choice_target(choice)

            # Terminal choice with an explicit ending.
            if ending:
                self._finish_game(
                    state,
                    ending,
                    events,
                )
                break

            # Terminal choice without an ending id:
            # use a generated fallback ending id.
            if (
                not target
                and self._choice_is_terminal(choice)
            ):
                fallback_ending = self._ensure_terminal_choice_ending(
                    choice
                )
                self._finish_game(
                    state,
                    fallback_ending,
                    events,
                )
                break

            # Target points to an ending.
            if target in self._endings:
                self._finish_game(
                    state,
                    target,
                    events,
                )
                break

            # Target points to another scene.
            if target:
                self._transition_scene(
                    state,
                    target,
                    events,
                )
                break

        # Resolve automatic scene endings/transitions.
        if state.get("status") != "finished":
            scene = self.get_scene(
                state["scene_id"]
            )

            scene_ending = self._scene_ending_id(scene)

            if scene_ending:
                self._finish_game(
                    state,
                    scene_ending,
                    events,
                )
            else:
                automatic_target = self._automatic_scene_target(scene)

                if automatic_target:
                    if automatic_target in self._endings:
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
                    state["round_number"] = current_round + 1

        # Keep database-facing world_state synchronized.
        state["world_state"] = copy.deepcopy(
            state.get("world_state", {})
        )
        state["world_state"]["flags"] = copy.deepcopy(
            state.get("flags", {})
        )
        state["world_state"]["variables"] = copy.deepcopy(
            state.get("variables", {})
        )
        state["world_state"]["relationships"] = copy.deepcopy(
            state.get("relationships", {})
        )
        state["world_state"]["knowledge"] = copy.deepcopy(
            state.get("knowledge", [])
        )

        state.setdefault("history", []).append(
            {
                "round_number": current_round,
                "decisions": copy.deepcopy(decisions_list),
                "missed_results": copy.deepcopy(missed_results),
                "scene_id": state.get("scene_id"),
                "ending_id": state.get("ending_id"),
            }
        )

        return EngineResult(
            state=state,
            events=events,
            changed=True,
            missed_results=missed_results,
        )

    def _ensure_terminal_choice_ending(
        self,
        choice: dict[str, Any],
    ) -> str:
        """
        Convert a terminal choice with no ending_id into a stable
        ending object. This makes older/local fallback stories safe.
        """
        choice_id = str(
            choice.get("id", "terminal")
        ).strip()

        ending_id = f"generated_{choice_id}_ending"

        if ending_id not in self._endings:
            self._endings[ending_id] = {
                "id": ending_id,
                "title": choice.get(
                    "ending_title",
                    "The End",
                ),
                "text": choice.get(
                    "ending_text",
                    choice.get(
                        "text",
                        choice.get(
                            "description",
                            "The story reaches its conclusion.",
                        ),
                    ),
                ),
            }

        return ending_id

    # ============================================================
    # SERIALIZATION HELPERS
    # ============================================================

    @staticmethod
    def events_to_dict(
        events: Iterable[EngineEvent],
    ) -> list[dict[str, Any]]:
        return [
            {
                "event_type": event.event_type,
                "payload": copy.deepcopy(event.payload),
            }
            for event in events
        ]

    def scene_public_view(
        self,
        scene_id: str,
    ) -> dict[str, Any]:
        scene = self.get_scene(scene_id)

        result = copy.deepcopy(scene)

        # Keep internal conditions/effects out of simple UI payloads
        # only when explicitly requested by the caller; by default
        # preserve them because game_service.py may need them.
        return result
