from future import annotations

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

The caller supplies the persisted game state and is
responsible for atomically saving the returned result.
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
            raise ValueError("Every role requires a non-empty id.")

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

        if scene_id in self.scenes:
            raise ValueError(
                f"Duplicate scene id: {scene_id}"
            )

        self.scenes[scene_id] = copy.deepcopy(scene)

    self.validate_story()

# ============================================================
# STORY VALIDATION
# ============================================================

def validate_story(self) -> None:
    if not self.story.get("title"):
        raise ValueError("Story has no title.")

    if not self.roles:
        raise ValueError("Story has no roles.")

    if not self.scenes:
        raise ValueError("Story has no scenes.")

    self._validate_roles()
    self._validate_scenes()
    self._validate_first_scene()
    self._validate_graph()

def _validate_roles(self) -> None:
    names: set[str] = set()

    for role_id, role in self.roles.items():
        name = role.get("name")

        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"Role {role_id} has no valid name."
            )

        if name.strip().lower() in names:
            raise ValueError(
                f"Duplicate role name: {name}"
            )

        names.add(name.strip().lower())

        if "playable" not in role:
            role["playable"] = True

        if not isinstance(role["playable"], bool):
            raise ValueError(
                f"Role {role_id}.playable must be boolean."
            )

        if role["playable"]:
            secret = role.get("secret_description")

            if not isinstance(secret, str) or not secret.strip():
                raise ValueError(
                    f"Playable role {role_id} requires "
                    "secret_description."
                )

def _validate_scenes(self) -> None:
    role_ids = set(self.roles)
    scene_ids = set(self.scenes)

    for scene_id, scene in self.scenes.items():

        public_text = scene.get("public_text")

        if not isinstance(public_text, str) or not public_text.strip():
            raise ValueError(
                f"Scene {scene_id} requires public_text."
            )

        timer = scene.get("timer_seconds")

        if (
            not isinstance(timer, int)
            or isinstance(timer, bool)
            or not (
                self.MIN_TIMER_SECONDS
                <= timer
                <= self.MAX_TIMER_SECONDS
            )
        ):
            raise ValueError(
                f"Scene {scene_id} has invalid timer_seconds."
            )

        eligible = scene.get(
            "eligible_roles",
            [],
        )

        if not isinstance(eligible, list):
            raise ValueError(
                f"Scene {scene_id}.eligible_roles must be a list."
            )

        self._validate_unique_ids(
            eligible,
            f"Scene {scene_id}.eligible_roles",
        )

        for role_id in eligible:
            if role_id not in role_ids:
                raise ValueError(
                    f"Scene {scene_id} references unknown role "
                    f"{role_id}."
                )

        choices = scene.get("choices")

        if not isinstance(choices, list) or not choices:
            raise ValueError(
                f"Scene {scene_id} requires choices."
            )

        choice_ids: set[str] = set()

        for choice in choices:
            if not isinstance(choice, dict):
                raise ValueError(
                    f"Scene {scene_id} contains an invalid choice."
                )

            choice_id = choice.get("id")

            if (
                not isinstance(choice_id, str)
                or not choice_id.strip()
            ):
                raise ValueError(
                    f"Scene {scene_id} contains a choice "
                    "without a valid id."
                )

            if choice_id in choice_ids:
                raise ValueError(
                    f"Duplicate choice id {choice_id} "
                    f"in scene {scene_id}."
                )

            choice_ids.add(choice_id)

            if not isinstance(
                choice.get("label"),
                str,
            ):
                raise ValueError(
                    f"Choice {choice_id} requires label."
                )

            target = choice.get("next_scene")

            if target is not None and target not in scene_ids:
                raise ValueError(
                    f"Choice {choice_id} references unknown "
                    f"scene {target}."
                )

            self._validate_conditions(
                choice.get("conditions", []),
                f"choice {choice_id}",
            )

            self._validate_effects(
                choice.get("effects", {}),
                f"choice {choice_id}",
            )

        transitions = scene.get(
            "transitions",
            [],
        )

        if not isinstance(transitions, list):
            raise ValueError(
                f"Scene {scene_id}.transitions must be a list."
            )

        for transition in transitions:
            if not isinstance(transition, dict):
                raise ValueError(
                    f"Scene {scene_id} contains an invalid transition."
                )

            target = transition.get("next_scene")

            if target not in scene_ids:
                raise ValueError(
                    f"Scene {scene_id} transition references "
                    f"unknown scene {target}."
                )

            self._validate_conditions(
                transition.get("conditions", []),
                f"transition in scene {scene_id}",
            )

        self._validate_ending(
            scene
        )

        self._validate_late_join(
            scene,
            role_ids,
            scene_ids,
        )

@staticmethod
def _validate_unique_ids(
    values: list[Any],
    context: str,
) -> None:
    seen: set[str] = set()

    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"{context} contains an invalid id."
            )

        if value in seen:
            raise ValueError(
                f"{context} contains duplicate id {value}."
            )

        seen.add(value)

def _validate_conditions(
    self,
    conditions: Any,
    context: str,
) -> None:
    if conditions is None:
        return

    if not isinstance(conditions, list):
        raise ValueError(
            f"{context}.conditions must be a list."
        )

    role_ids = set(self.roles)

    for condition in conditions:
        if not isinstance(condition, dict):
            raise ValueError(
                f"{context} contains an invalid condition."
            )

        condition_type = condition.get("type")

        if condition_type not in self.ALLOWED_CONDITION_TYPES:
            raise ValueError(
                f"{context} uses unsupported condition type "
                f"{condition_type}."
            )

        operator = condition.get(
            "operator",
            "eq",
        )

        if operator not in self.ALLOWED_OPERATORS:
            raise ValueError(
                f"{context} uses unsupported operator "
                f"{operator}."
            )

        if condition_type in {
            "flag",
            "variable",
        }:
            name = condition.get("name")

            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    f"{context} requires a condition name."
                )

        elif condition_type == "knowledge":
            role_id = condition.get("role_id")

            if role_id not in role_ids:
                raise ValueError(
                    f"{context} references unknown role "
                    f"{role_id}."
                )

            knowledge = condition.get("knowledge")

            if (
                not isinstance(knowledge, str)
                or not knowledge.strip()
            ):
                raise ValueError(
                    f"{context} requires knowledge."
                )

        elif condition_type == "relationship":
            role_a = condition.get("role_a")
            role_b = condition.get("role_b")

            if role_a not in role_ids:
                raise ValueError(
                    f"{context} references unknown role "
                    f"{role_a}."
                )

            if role_b not in role_ids:
                raise ValueError(
                    f"{context} references unknown role "
                    f"{role_b}."
                )

def _validate_effects(
    self,
    effects: Any,
    context: str,
) -> None:
    if effects is None:
        return

    if not isinstance(effects, dict):
        raise ValueError(
            f"{context}.effects must be an object."
        )

    for key in (
        "set_flags",
        "set_variables",
        "character_state",
    ):
        value = effects.get(key, {})

        if not isinstance(value, dict):
            raise ValueError(
                f"{context}.{key} must be an object."
            )

    for key in (
        "remove_flags",
    ):
        value = effects.get(key, [])

        if not isinstance(value, list):
            raise ValueError(
                f"{context}.{key} must be a list."
            )

    for key in (
        "add_variables",
    ):
        value = effects.get(key, {})

        if not isinstance(value, dict):
            raise ValueError(
                f"{context}.{key} must be an object."
            )

        for name, amount in value.items():
            if (
                not isinstance(amount, (int, float))
                or isinstance(amount, bool)
            ):
                raise ValueError(
                    f"{context}.{key}.{name} must be numeric."
                )

    for relationship in effects.get(
        "relationships",
        [],
    ):
        if not isinstance(relationship, dict):
            raise ValueError(
                f"{context} contains an invalid relationship."
            )

        role_a = relationship.get("role_a")
        role_b = relationship.get("role_b")

        if role_a not in self.roles:
            raise ValueError(
                f"{context} relationship references unknown "
                f"role {role_a}."
            )

        if role_b not in self.roles:
            raise ValueError(
                f"{context} relationship references unknown "
                f"role {role_b}."
            )

        amount = relationship.get("amount", 0)

        if (
            not isinstance(amount, (int, float))
            or isinstance(amount, bool)
        ):
            raise ValueError(
                f"{context} relationship amount must be numeric."
            )

    for item in effects.get(
        "knowledge",
        [],
    ):
        if not isinstance(item, dict):
            raise ValueError(
                f"{context} contains invalid knowledge."
            )

        role_id = item.get("role_id")

        if role_id not in self.roles:
            raise ValueError(
                f"{context} knowledge references unknown role "
                f"{role_id}."
            )

        if not isinstance(
            item.get("knowledge"),
            str,
        ):
            raise ValueError(
                f"{context} knowledge text must be a string."
            )

    for secret in effects.get(
        "secrets",
        [],
    ):
        if not isinstance(secret, dict):
            raise ValueError(
                f"{context} contains an invalid secret."
            )

        owner = secret.get("owner")

        if owner is not None and owner not in self.roles:
            raise ValueError(
                f"{context} secret references unknown owner "
                f"{owner}."
            )

        revealed_to = secret.get(
            "revealed_to",
            [],
        )

        if not isinstance(revealed_to, list):
            raise ValueError(
                f"{context} secret.revealed_to must be a list."
            )

        for role_id in revealed_to:
            if role_id not in self.roles:
                raise ValueError(
                    f"{context} secret references unknown role "
                    f"{role_id}."
                )

def _validate_ending(
    self,
    scene: dict[str, Any],
) -> None:
    ending = scene.get("ending")

    if ending is None:
        return

    if isinstance(ending, str):
        if not ending.strip():
            raise ValueError(
                f"Scene {scene['id']} has an empty ending."
            )
        return

    if not isinstance(ending, dict):
        raise ValueError(
            f"Scene {scene['id']} ending must be an object or string."
        )

    if not ending.get("id"):
        raise ValueError(
            f"Scene {scene['id']} ending requires id."
        )

    if not ending.get("text"):
        raise ValueError(
            f"Scene {scene['id']} ending requires text."
        )

    self._validate_conditions(
        ending.get("conditions", []),
        f"ending in scene {scene['id']}",
    )

def _validate_late_join(
    self,
    scene: dict[str, Any],
    role_ids: set[str],
    scene_ids: set[str],
) -> None:
    late_join = scene.get(
        "late_join",
        {},
    )

    if late_join is None:
        return

    if not isinstance(late_join, dict):
        raise ValueError(
            f"Scene {scene['id']}.late_join must be an object."
        )

    roles = late_join.get(
        "roles",
        [],
    )

    if not isinstance(roles, list):
        raise ValueError(
            f"Scene {scene['id']}.late_join.roles must be a list."
        )

    for role_id in roles:
        if role_id not in role_ids:
            raise ValueError(
                f"Scene {scene['id']}.late_join references "
                f"unknown role {role_id}."
            )

    entry_scene = late_join.get(
        "entry_scene"
    )

    if entry_scene is not None and entry_scene not in scene_ids:
        raise ValueError(
            f"Scene {scene['id']}.late_join.entry_scene "
            f"references unknown scene {entry_scene}."
        )

    self._validate_conditions(
        late_join.get("conditions", []),
        f"late_join in scene {scene['id']}",
    )

def _validate_first_scene(self) -> None:
    first = self.story.get(
        "first_scene_id"
    )

    if not isinstance(first, str) or not first:
        raise ValueError(
            "Story requires first_scene_id."
        )

    if first not in self.scenes:
        raise ValueError(
            f"first_scene_id {first} does not exist."
        )

def _validate_graph(self) -> None:
    """
    Verify:

    - every scene is reachable
    - at least one reachable ending exists
    - non-ending branches can eventually reach an ending
    """

    start = self.first_scene_id()

    reachable: set[str] = set()
    stack = [start]

    while stack:
        scene_id = stack.pop()

        if scene_id in reachable:
            continue

        reachable.add(scene_id)

        scene = self.get_scene(scene_id)

        for choice in scene.get("choices", []):
            target = choice.get("next_scene")

            if target:
                stack.append(target)

        for transition in scene.get(
            "transitions",
            [],
        ):
            target = transition.get("next_scene")

            if target:
                stack.append(target)

    unreachable = set(self.scenes) - reachable

    if unreachable:
        raise ValueError(
            "Story contains unreachable scenes: "
            + ", ".join(sorted(unreachable))
        )

    endings = {
        scene_id
        for scene_id in reachable
        if scene_has_ending(self.scenes[scene_id])
    }

    if not endings:
        raise ValueError(
            "Story contains no ending scene."
        )

    memo: dict[str, bool] = {}

    def reaches_ending(
        scene_id: str,
        visiting: set[str],
    ) -> bool:
        if scene_id in memo:
            return memo[scene_id]

        if scene_id in endings:
            memo[scene_id] = True
            return True

        if scene_id in visiting:
            return False

        visiting = set(visiting)
        visiting.add(scene_id)

        scene = self.scenes[scene_id]

        targets: list[str] = []

        for choice in scene.get("choices", []):
            target = choice.get("next_scene")
            if target:
                targets.append(target)

        for transition in scene.get(
            "transitions",
            [],
        ):
            target = transition.get("next_scene")
            if target:
                targets.append(target)

        if not targets:
            memo[scene_id] = False
            return False

        result = any(
            reaches_ending(
                target,
                visiting,
            )
            for target in targets
        )

        memo[scene_id] = result
        return result

    for scene_id in reachable:
        if not reaches_ending(
            scene_id,
            set(),
        ):
            raise ValueError(
                f"Scene {scene_id} cannot reach an ending."
            )

# ============================================================
# BASIC ACCESS
# ============================================================

def first_scene_id(self) -> str:
    return self.story["first_scene_id"]

def first_scene(self) -> dict[str, Any]:
    return self.get_scene(
        self.first_scene_id()
    )

def get_scene(
    self,
    scene_id: str,
) -> dict[str, Any]:
    try:
        return copy.deepcopy(
            self.scenes[scene_id]
        )
    except KeyError as exc:
        raise ValueError(
            f"Unknown scene: {scene_id}"
        ) from exc

def get_role(
    self,
    role_id: str,
) -> dict[str, Any]:
    try:
        return copy.deepcopy(
            self.roles[role_id]
        )
    except KeyError as exc:
        raise ValueError(
            f"Unknown role: {role_id}"
        ) from exc

def playable_roles(self) -> list[dict[str, Any]]:
    return [
        copy.deepcopy(role)
        for role in self.roles.values()
        if role.get("playable") is True
    ]

# ============================================================
# ROLE ASSIGNMENT
# ============================================================

def assign_roles(
    self,
    player_ids: Iterable[int | str],
) -> dict[str, str]:
    normalized = [
        str(value)
        for value in player_ids
    ]

    if len(normalized) != len(set(normalized)):
        raise ValueError(
            "Duplicate player IDs cannot be assigned roles."
        )

    roles = self.playable_roles()

    if len(roles) < len(normalized):
        raise ValueError(
            "Not enough playable roles for the number of players."
        )

    normalized.sort()
    roles.sort(
        key=lambda role: str(
            role["id"]
        )
    )

    return {
        player_id: roles[index]["id"]
        for index, player_id in enumerate(normalized)
    }

# ============================================================
# WORLD STATE
# ============================================================

def initial_world_state(self) -> dict[str, Any]:
    return {
        "flags": {},
        "variables": {},
        "relationships": {},
        "knowledge": {},
        "secrets": {},
        "character_states": {},
        "visited_scenes": [],
        "scene_visits": {},
        "choice_history": [],
        "public_events": [],
        "ending": None,
        "late_joiners": [],
    }

def normalize_world_state(
    self,
    world_state: dict[str, Any] | None,
) -> dict[str, Any]:
    state = self.initial_world_state()

    if world_state is not None:
        if not isinstance(world_state, dict):
            raise ValueError(
                "world_state must be an object."
            )

        self._deep_merge(
            state,
            world_state,
        )

    return state

@classmethod
def _deep_merge(
    cls,
    target: dict[str, Any],
    source: dict[str, Any],
) -> None:
    for key, value in source.items():
        if (
            isinstance(value, dict)
            and isinstance(target.get(key), dict)
        ):
            cls._deep_merge(
                target[key],
                value,
            )
        else:
            target[key] = copy.deepcopy(value)

def enter_scene(
    self,
    world_state: dict[str, Any],
    scene_id: str,
) -> dict[str, Any]:
    self.get_scene(scene_id)

    state = self.normalize_world_state(
        world_state
    )

    if scene_id not in state["visited_scenes"]:
        state["visited_scenes"].append(scene_id)

    visits = state["scene_visits"]

    visits[scene_id] = int(
        visits.get(scene_id, 0)
    ) + 1

    return state

# ============================================================
# CONDITIONS
# ============================================================

def conditions_match(
    self,
    conditions: list[dict[str, Any]] | None,
    world_state: dict[str, Any] | None,
) -> bool:
    if not conditions:
        return True

    state = self.normalize_world_state(
        world_state
    )

    for condition in conditions:
        condition_type = condition.get("type")

        if condition_type == "flag":
            actual = state["flags"].get(
                condition.get("name")
            )

            expected = condition.get(
                "value",
                True,
            )

            if actual != expected:
                return False

        elif condition_type == "variable":
            actual = state["variables"].get(
                condition.get("name")
            )

            if not self.compare(
                actual,
                condition.get(
                    "operator",
                    "eq",
                ),
                condition.get("value"),
            ):
                return False

        elif condition_type == "knowledge":
            role_id = condition.get("role_id")
            knowledge = condition.get("knowledge")

            known = knowledge in state["knowledge"].get(
                role_id,
                [],
            )

            if not self.compare(
                known,
                condition.get(
                    "operator",
                    "eq",
                ),
                condition.get(
                    "value",
                    True,
                ),
            ):
                return False

        elif condition_type == "relationship":
            key = self.relationship_key(
                condition.get("role_a"),
                condition.get("role_b"),
            )

            actual = state["relationships"].get(
                key,
                0,
            )

            if not self.compare(
                actual,
                condition.get(
                    "operator",
                    "eq",
                ),
                condition.get("value"),
            ):
                return False

        else:
            raise ValueError(
                f"Unknown condition type: {condition_type}"
            )

    return True

@staticmethod
def compare(
    actual: Any,
    operator: str,
    expected: Any,
) -> bool:
    if operator == "eq":
        return actual == expected

    if operator == "ne":
        return actual != expected

    if operator == "gt":
        return (
            actual is not None
            and actual > expected
        )

    if operator == "gte":
        return (
            actual is not None
            and actual >= expected
        )

    if operator == "lt":
        return (
            actual is not None
            and actual < expected
        )

    if operator == "lte":
        return (
            actual is not None
            and actual <= expected
        )

    if operator == "contains":
        if actual is None:
            return False

        try:
            return expected in actual
        except TypeError:
            return False

    raise ValueError(
        f"Unsupported operator: {operator}"
    )

# ============================================================
# PARTICIPATION / CHOICES
# ============================================================

def role_can_participate(
    self,
    scene: dict[str, Any],
    role_id: str,
    world_state: dict[str, Any] | None = None,
) -> bool:
    self.get_role(role_id)

    eligible = scene.get(
        "eligible_roles",
        [],
    )

    if eligible and role_id not in eligible:
        return False

    return self.conditions_match(
        scene.get("conditions", []),
        world_state,
    )

def choices_for_role(
    self,
    scene: dict[str, Any],
    role_id: str,
    world_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not self.role_can_participate(
        scene,
        role_id,
        world_state,
    ):
        return []

    state = self.normalize_world_state(
        world_state
    )

    result: list[dict[str, Any]] = []

    for choice in scene.get(
        "choices",
        [],
    ):
        if self.conditions_match(
            choice.get("conditions", []),
            state,
        ):
            result.append(
                copy.deepcopy(choice)
            )

    return result

def get_choice(
    self,
    scene: dict[str, Any],
    choice_id: str,
) -> dict[str, Any]:
    for choice in scene.get(
        "choices",
        [],
    ):
        if choice.get("id") == choice_id:
            return copy.deepcopy(choice)

    raise ValueError(
        f"Unknown choice {choice_id} "
        f"in scene {scene.get('id')}."
    )

def validate_choice_for_role(
    self,
    scene: dict[str, Any],
    role_id: str,
    choice_id: str,
    world_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    choices = self.choices_for_role(
        scene,
        role_id,
        world_state,
    )

    for choice in choices:
        if choice.get("id") == choice_id:
            return choice

    raise ValueError(
        f"Choice {choice_id} is not available to role "
        f"{role_id} in scene {scene.get('id')}."
    )

# ============================================================
# EFFECTS
# ============================================================

def apply_effects(
    self,
    world_state: dict[str, Any],
    effects: dict[str, Any] | None,
    *,
    role_id: str | None = None,
) -> dict[str, Any]:
    state = self.normalize_world_state(
        world_state
    )

    if not effects:
        return state

    for name, value in effects.get(
        "set_flags",
        {},
    ).items():
        state["flags"][name] = copy.deepcopy(value)

    for name in effects.get(
        "remove_flags",
        [],
    ):
        state["flags"].pop(
            name,
            None,
        )

    for name, value in effects.get(
        "set_variables",
        {},
    ).items():
        state["variables"][name] = copy.deepcopy(value)

    for name, amount in effects.get(
        "add_variables",
        {},
    ).items():
        current = state["variables"].get(
            name,
            0,
        )

        if (
            not isinstance(current, (int, float))
            or isinstance(current, bool)
        ):
            raise ValueError(
                f"Variable {name} is not numeric."
            )

        state["variables"][name] = (
            current + amount
        )

    for relationship in effects.get(
        "relationships",
        [],
    ):
        role_a = relationship["role_a"]
        role_b = relationship["role_b"]
        amount = relationship.get(
            "amount",
            0,
        )

        key = self.relationship_key(
            role_a,
            role_b,
        )

        state["relationships"][key] = (
            state["relationships"].get(
                key,
                0,
            )
            + amount
        )

    for item in effects.get(
        "knowledge",
        [],
    ):
        target_role = item["role_id"]
        knowledge = item["knowledge"]

        values = state["knowledge"].setdefault(
            target_role,
            [],
        )

        if knowledge not in values:
            values.append(knowledge)

    for secret in effects.get(
        "secrets",
        [],
    ):
        secret_id = secret.get(
            "id"
        )

        if not secret_id:
            secret_id = hashlib.sha256(
                json.dumps(
                    secret,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()[:16]

        state["secrets"][secret_id] = (
            copy.deepcopy(secret)
        )

    character_state = effects.get(
        "character_state",
        {},
    )

    for target_role, values in character_state.items():
        if not isinstance(values, dict):
            raise ValueError(
                "character_state values must be objects."
            )

        target = state[
            "character_states"
        ].setdefault(
            target_role,
            {},
        )

        self._deep_merge(
            target,
            values,
        )

    return state

# ============================================================
# CHOICE RESOLUTION
# ============================================================

def resolve_choice(
    self,
    scene: dict[str, Any],
    choice_id: str,
    *,
    round_number: int,
    role_id: str,
    world_state: dict[str, Any],
) -> dict[str, Any]:
    choice = self.validate_choice_for_role(
        scene,
        role_id,
        choice_id,
        world_state,
    )

    state = self.apply_effects(
        world_state,
        choice.get("effects", {}),
        role_id=role_id,
    )

    history_item = {
        "round_number": round_number,
        "scene_id": scene["id"],
        "role_id": role_id,
        "choice_id": choice_id,
    }

    state["choice_history"].append(
        history_item
    )

    public_event = choice.get(
        "public_event",
        "",
    )

    if public_event:
        state["public_events"].append(
            {
                "round_number": round_number,
                "scene_id": scene["id"],
                "role_id": role_id,
                "text": public_event,
            }
        )

    return {
        "world_state": state,
        "choice": choice,
        "event": {
            "type": "choice_resolved",
            "round_number": round_number,
            "scene_id": scene["id"],
            "role_id": role_id,
            "choice_id": choice_id,
            "public_event": public_event,
        },
        "next_scene": choice.get(
            "next_scene"
        ),
    }

def resolve_public_event(
    self,
    choice: dict[str, Any],
) -> str:
    return str(
        choice.get(
            "public_event",
            "",
        )
    )

# ============================================================
# ROUND RESOLUTION
# ============================================================

def resolve_round(
    self,
    *,
    scene_id: str,
    round_number: int,
    world_state: dict[str, Any] | None,
    players: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Resolve a round deterministically.

    Decisions are normalized by user_id before processing.

    Missing decisions are returned to the service layer.
    Their persistent missed-decision count is NOT stored here.
    """

    scene = self.get_scene(
        scene_id
    )

    state = self.normalize_world_state(
        world_state
    )

    active_players = [
        copy.deepcopy(player)
        for player in players
        if self.player_is_eligible_for_round(
            player,
            round_number,
        )
    ]

    active_players.sort(
        key=lambda player: str(
            player.get("user_id")
        )
    )

    decision_by_user: dict[str, dict[str, Any]] = {}

    for decision in decisions:
        user_id = str(
            decision.get("user_id")
        )

        if user_id in decision_by_user:
            raise ValueError(
                f"Duplicate decision for player "
                f"{user_id} in round {round_number}."
            )

        decision_by_user[user_id] = copy.deepcopy(
            decision
        )

    accepted: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    missed_results: list[dict[str, Any]] = []

    for player in active_players:
        user_id = str(
            player.get("user_id")
        )

        role_id = player.get(
            "role_id"
        )

        if not role_id:
            raise ValueError(
                f"Player {user_id} has no role."
            )

        decision = decision_by_user.get(
            user_id
        )

        if decision is None:
            missing.append(
                copy.deepcopy(player)
            )

            previous = int(
                player.get(
                    "missed_decisions",
                    0,
                )
                or 0
            )

            new_count = previous + 1

            npc_choice = (
                self.default_choice_for_role(
                    scene,
                    role_id,
                    state,
                )
            )

            missed_results.append(
                {
                    "user_id": user_id,
                    "role_id": role_id,
                    "previous_missed_decisions": previous,
                    "missed_decisions": new_count,
                    "eliminate": (
                        new_count
                        >= self.MAX_MISSED_DECISIONS
                    ),
                    "npc_choice_id": (
                        npc_choice.get("id")
                        if npc_choice
                        else None
                    ),
                }
            )

            continue

        choice_id = decision.get(
            "choice_id"
        )

        if not choice_id:
            raise ValueError(
                f"Decision for player {user_id} "
                "has no choice_id."
            )

        choice = self.validate_choice_for_role(
            scene,
            role_id,
            choice_id,
            state,
        )

        accepted.append(
            {
                "user_id": user_id,
                "role_id": role_id,
                "choice_id": choice_id,
                "choice": choice,
            }
        )

    accepted.sort(
        key=lambda item: (
            str(item["user_id"]),
            str(item["choice_id"]),
        )
    )

    next_scene_candidates: list[dict[str, Any]] = []
    resolved_events: list[dict[str, Any]] = []

    for item in accepted:
        result = self.resolve_choice(
            scene,
            item["choice_id"],
            round_number=round_number,
            role_id=item["role_id"],
            world_state=state,
        )

        state = result["world_state"]

        resolved_events.append(
            result["event"]
        )

        if result.get("next_scene"):
            next_scene_candidates.append(
                {
                    "source": "player",
                    "user_id": item["user_id"],
                    "role_id": item["role_id"],
                    "choice_id": item["choice_id"],
                    "next_scene": result["next_scene"],
                    "priority": int(
                        item["choice"].get(
                            "branch_priority",
                            0,
                        )
                    ),
                }
            )

    ending = self.ending_for_scene(
        scene,
        state,
    )

    if ending:
        state["ending"] = copy.deepcopy(
            ending
        )

    next_scene = None

    if not ending:
        next_scene = self.resolve_next_scene(
            current_scene=scene,
            results=next_scene_candidates,
            world_state=state,
        )

    return {
        "round_number": round_number,
        "scene_id": scene_id,
        "world_state": state,
        "accepted_decisions": accepted,
        "missing_players": missing,
        "missed_results": missed_results,
        "events": resolved_events,
        "next_scene": next_scene,
        "ending": ending,
        "complete": True,
    }

# ============================================================
# PLAYER ELIGIBILITY
# ============================================================

@staticmethod
def player_is_eligible_for_round(
    player: dict[str, Any],
    round_number: int,
) -> bool:
    status = player.get(
        "status"
    )

    lifecycle = player.get(
        "lifecycle"
    )

    if status is not None:
        if status not in {
            "active",
            "npc",
        }:
            return False

    elif lifecycle is not None:
        if lifecycle not in {
            "active",
            "npc",
        }:
            return False

    elif player.get(
        "active",
        True,
    ) is not True:
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

# ============================================================
# DEFAULT NPC CHOICE
# ============================================================

def default_choice_for_role(
    self,
    scene: dict[str, Any],
    role_id: str,
    world_state: dict[str, Any],
) -> dict[str, Any] | None:
    choices = self.choices_for_role(
        scene,
        role_id,
        world_state,
    )

    if not choices:
        return None

    choices.sort(
        key=lambda choice: str(
            choice.get("id")
        )
    )

    digest = hashlib.sha256(
        (
            "npc|"
            + self.story.get("title", "")
            + "|"
            + scene["id"]
            + "|"
            + role_id
            + "|"
            + self._state_signature(
                world_state
            )
        ).encode("utf-8")
    ).hexdigest()

    index = int(
        digest[:16],
        16,
    ) % len(choices)

    return choices[index]

# ============================================================
# NEXT SCENE
# ============================================================

def resolve_next_scene(
    self,
    *,
    current_scene: dict[str, Any],
    results: list[dict[str, Any]],
    world_state: dict[str, Any],
) -> str | None:
    candidates = [
        item
        for item in results
        if item.get("next_scene")
    ]

    candidates.sort(
        key=lambda item: (
            -int(
                item.get(
                    "priority",
                    0,
                )
            ),
            str(
                item.get(
                    "user_id",
                    "",
                )
            ),
            str(
                item.get(
                    "role_id",
                    "",
                )
            ),
            str(
                item.get(
                    "choice_id",
                    "",
                )
            ),
        )
    )

    for candidate in candidates:
        target = candidate["next_scene"]

        if target not in self.scenes:
            raise ValueError(
                f"Unknown next scene: {target}"
            )

        return target

    for transition in current_scene.get(
        "transitions",
        [],
    ):
        if self.conditions_match(
            transition.get(
                "conditions",
                [],
            ),
            world_state,
        ):
            return transition["next_scene"]

    return None

def next_scene_from_results(
    self,
    current_scene: dict[str, Any],
    results: list[dict[str, Any]],
    world_state: dict[str, Any],
) -> str | None:
    return self.resolve_next_scene(
        current_scene=current_scene,
        results=results,
        world_state=world_state,
    )

# ============================================================
# ENDINGS
# ============================================================

def ending_for_scene(
    self,
    scene: dict[str, Any],
    world_state: dict[str, Any],
) -> dict[str, Any] | None:
    ending = scene.get(
        "ending"
    )

    if ending is None:
        return None

    if isinstance(ending, str):
        return {
            "id": ending,
            "text": ending,
        }

    if isinstance(ending, dict):
        if self.conditions_match(
            ending.get(
                "conditions",
                [],
            ),
            world_state,
        ):
            return copy.deepcopy(
                ending
            )

    return None

# ============================================================
# LATE JOINING
# ============================================================

def can_join_now(
    self,
    scene: dict[str, Any],
    role_id: str,
    world_state: dict[str, Any],
) -> bool:
    self.get_role(role_id)

    late_join = scene.get(
        "late_join",
        {},
    )

    if late_join.get(
        "allowed",
        True,
    ) is False:
        return False

    allowed_roles = late_join.get(
        "roles",
        [],
    )

    if (
        allowed_roles
        and role_id not in allowed_roles
    ):
        return False

    return self.conditions_match(
        late_join.get(
            "conditions",
            [],
        ),
        world_state,
    )

def late_join_entry_scene(
    self,
    current_scene: dict[str, Any],
    world_state: dict[str, Any],
) -> str | None:
    if not self.can_use_late_join(
        current_scene
    ):
        return None

    entry_scene = current_scene.get(
        "late_join",
        {},
    ).get(
        "entry_scene"
    )

    if entry_scene is not None:
        self.get_scene(
            entry_scene
        )

    return entry_scene

@staticmethod
def can_use_late_join(
    scene: dict[str, Any],
) -> bool:
    return (
        scene.get(
            "late_join",
            {},
        ).get(
            "allowed",
            True,
        )
        is not False
    )

def register_late_joiner(
    self,
    world_state: dict[str, Any],
    role_id: str,
    round_number: int,
) -> dict[str, Any]:
    state = self.normalize_world_state(
        world_state
    )

    marker = {
        "role_id": role_id,
        "joined_round": int(
            round_number
        ),
    }

    existing = state[
        "late_joiners"
    ]

    if marker not in existing:
        existing.append(marker)

    return state

# ============================================================
# MISSED DECISIONS / NPC
# ============================================================

def player_missed(
    self,
    missed_count: int,
) -> bool:
    return (
        int(missed_count)
        >= self.MAX_MISSED_DECISIONS
    )

def convert_to_npc(
    self,
    world_state: dict[str, Any],
    role_id: str,
) -> dict[str, Any]:
    state = self.normalize_world_state(
        world_state
    )

    character = state[
        "character_states"
    ].setdefault(
        role_id,
        {},
    )

    character["control"] = "npc"
    character["human_active"] = False

    return state

# ============================================================
# TIMERS
# ============================================================

def timer_seconds(
    self,
    scene: dict[str, Any],
) -> int:
    value = scene.get(
        "timer_seconds"
    )

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
    ):
        raise ValueError(
            f"Scene {scene.get('id')} has invalid timer_seconds."
        )

    if not (
        self.MIN_TIMER_SECONDS
        <= value
        <= self.MAX_TIMER_SECONDS
    ):
        raise ValueError(
            f"Scene {scene.get('id')} timer is outside "
            "the allowed range."
        )

    return value

# ============================================================
# RELATIONSHIPS
# ============================================================

@staticmethod
def relationship_key(
    role_a: str,
    role_b: str,
) -> str:
    return "|".join(
        sorted(
            (
                str(role_a),
                str(role_b),
            )
        )
    )

# ============================================================
# FINGERPRINT
# ============================================================

def structural_fingerprint(self) -> str:
    structural = {
        "first_scene_id": self.first_scene_id(),
        "roles": [],
        "scenes": [],
    }

    for role in sorted(
        self.roles.values(),
        key=lambda item: str(
            item.get("id")
        ),
    ):
        structural["roles"].append(
            {
                "id": role.get("id"),
                "playable": role.get("playable"),
            }
        )

    for scene in sorted(
        self.scenes.values(),
        key=lambda item: str(
            item.get("id")
        ),
    ):
        structural["scenes"].append(
            {
                "id": scene.get("id"),
                "eligible_roles": sorted(
                    scene.get(
                        "eligible_roles",
                        [],
                    )
                ),
                "choices": [
                    {
                        "id": choice.get("id"),
                        "next_scene": choice.get(
                            "next_scene"
                        ),
                        "branch_priority": choice.get(
                            "branch_priority",
                            0,
                        ),
                        "conditions": choice.get(
                            "conditions",
                            [],
                        ),
                        "effects": choice.get(
                            "effects",
                            {},
                        ),
                    }
                    for choice in sorted(
                        scene.get(
                            "choices",
                            [],
                        ),
                        key=lambda item: str(
                            item.get("id")
                        ),
                    )
                ],
                "transitions": [
                    {
                        "next_scene": transition.get(
                            "next_scene"
                        ),
                        "conditions": transition.get(
                            "conditions",
                            [],
                        ),
                    }
                    for transition in scene.get(
                        "transitions",
                        [],
                    )
                ],
                "ending": self._ending_signature(
                    scene.get("ending")
                ),
            }
        )

    payload = json.dumps(
        structural,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()

@staticmethod
def _ending_signature(
    ending: Any,
) -> Any:
    if ending is None:
        return None

    if isinstance(ending, str):
        return ending

    if isinstance(ending, dict):
        return {
            "id": ending.get("id"),
            "conditions": ending.get(
                "conditions",
                [],
            ),
        }

    return str(ending)

@staticmethod
def _state_signature(
    world_state: dict[str, Any],
) -> str:
    normalized = copy.deepcopy(
        world_state
    )

    return hashlib.sha256(
        json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()

def scene_has_ending(
scene: dict[str, Any],
) -> bool:
ending = scene.get("ending")

if ending is None:
    return False

if isinstance(ending, str):
    return bool(ending.strip())

if isinstance(ending, dict):
    return bool(
        ending.get("id")
        and ending.get("text")
    )

return False
