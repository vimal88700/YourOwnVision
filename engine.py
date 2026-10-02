from future import annotations

import copy
import hashlib
import json
from typing import Any, Iterable

class GameEngine:
"""
Deterministic gameplay engine for WHAT HAPPENS?

IMPORTANT ARCHITECTURE RULE:

    Gemini
        ↓
    validated story JSON
        ↓
    GameEngine
        ↓
    deterministic result
        ↓
    Supabase

This class does NOT:
    - call Telegram
    - call Gemini
    - call Supabase
    - create background tasks
    - own timers
    - store authoritative game state
    - decide based on arrival/click order

The database is the persistent source of truth.

The engine receives persisted state as arguments and returns
new state/results. The caller is responsible for atomically
persisting those results.
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
):
    self.story = copy.deepcopy(story)

    if not isinstance(
        self.story,
        dict,
    ):
        raise ValueError(
            "Story must be an object."
        )

    self.scenes = {
        scene["id"]: scene
        for scene in self.story.get(
            "scenes",
            [],
        )
        if isinstance(
            scene,
            dict,
        )
        and scene.get("id")
    }

    self.roles = {
        role["id"]: role
        for role in self.story.get(
            "roles",
            [],
        )
        if isinstance(
            role,
            dict,
        )
        and role.get("id")
    }

    self.validate_story()

# ============================================================
# STORY VALIDATION
# ============================================================

def validate_story(self) -> None:
    """
    Defensive validation performed immediately before the
    engine accepts a story.

    StoryValidator is the primary validation layer for
    Gemini output.

    This second layer protects the gameplay engine from
    accidentally receiving malformed data from any source.
    """

    if not self.story.get("title"):
        raise ValueError(
            "Story has no title."
        )

    if not isinstance(
        self.story.get("roles"),
        list,
    ):
        raise ValueError(
            "Story roles must be a list."
        )

    if not isinstance(
        self.story.get("scenes"),
        list,
    ):
        raise ValueError(
            "Story scenes must be a list."
        )

    if not self.roles:
        raise ValueError(
            "Story has no roles."
        )

    if not self.scenes:
        raise ValueError(
            "Story has no scenes."
        )

    self._validate_unique_ids()

    self._validate_roles()

    self._validate_scenes()

    self._validate_first_scene()

    self._validate_graph_reachability()

def _validate_unique_ids(self) -> None:
    role_ids: list[str] = []
    scene_ids: list[str] = []

    for role in self.story.get(
        "roles",
        [],
    ):
        role_id = role.get("id")

        if not isinstance(
            role_id,
            str,
        ) or not role_id.strip():
            raise ValueError(
                "Every role requires a non-empty id."
            )

        role_ids.append(role_id)

    for scene in self.story.get(
        "scenes",
        [],
    ):
        scene_id = scene.get("id")

        if not isinstance(
            scene_id,
            str,
        ) or not scene_id.strip():
            raise ValueError(
                "Every scene requires a non-empty id."
            )

        scene_ids.append(scene_id)

    if len(role_ids) != len(set(role_ids)):
        raise ValueError(
            "Story contains duplicate role IDs."
        )

    if len(scene_ids) != len(set(scene_ids)):
        raise ValueError(
            "Story contains duplicate scene IDs."
        )

def _validate_roles(self) -> None:
    for role in self.roles.values():

        role_id = role["id"]

        if not role.get("name"):
            raise ValueError(
                f"Role {role_id} has no name."
            )

        if not role.get(
            "secret_description"
        ):
            raise ValueError(
                f"Role {role_id} has no secret_description."
            )

        if "playable" not in role:
            raise ValueError(
                f"Role {role_id} must explicitly define "
                "playable."
            )

        if not isinstance(
            role["playable"],
            bool,
        ):
            raise ValueError(
                f"Role {role_id} playable must be boolean."
            )

def _validate_scenes(self) -> None:
    role_ids = set(
        self.roles.keys()
    )

    scene_ids = set(
        self.scenes.keys()
    )

    for scene in self.scenes.values():

        scene_id = scene["id"]

        public_text = scene.get(
            "public_text"
        )

        if not isinstance(
            public_text,
            str,
        ) or not public_text.strip():
            raise ValueError(
                f"Scene {scene_id} has no public_text."
            )

        timer = scene.get(
            "timer_seconds"
        )

        if not isinstance(
            timer,
            int,
        ) or isinstance(
            timer,
            bool,
        ):
            raise ValueError(
                f"Scene {scene_id} has invalid timer_seconds."
            )

        if not (
            self.MIN_TIMER_SECONDS
            <= timer
            <= self.MAX_TIMER_SECONDS
        ):
            raise ValueError(
                f"Scene {scene_id} timer_seconds must be "
                f"between {self.MIN_TIMER_SECONDS} and "
                f"{self.MAX_TIMER_SECONDS}."
            )

        eligible_roles = scene.get(
            "eligible_roles",
            [],
        )

        if not isinstance(
            eligible_roles,
            list,
        ):
            raise ValueError(
                f"Scene {scene_id} eligible_roles "
                "must be a list."
            )

        self._validate_string_id_list(
            eligible_roles,
            f"scene {scene_id} eligible_roles",
        )

        for role_id in eligible_roles:

            if role_id not in role_ids:
                raise ValueError(
                    f"Scene {scene_id} references "
                    f"unknown role {role_id}."
                )

        choices = scene.get(
            "choices"
        )

        if not isinstance(
            choices,
            list,
        ) or not choices:
            raise ValueError(
                f"Scene {scene_id} must contain choices."
            )

        choice_ids: set[str] = set()

        for choice in choices:

            if not isinstance(
                choice,
                dict,
            ):
                raise ValueError(
                    f"Scene {scene_id} contains "
                    "an invalid choice."
                )

            choice_id = choice.get(
                "id"
            )

            if not isinstance(
                choice_id,
                str,
            ) or not choice_id.strip():
                raise ValueError(
                    f"Scene {scene_id} contains "
                    "a choice without a valid id."
                )

            if choice_id in choice_ids:
                raise ValueError(
                    f"Duplicate choice ID {choice_id} "
                    f"in scene {scene_id}."
                )

            choice_ids.add(
                choice_id
            )

            label = choice.get(
                "label"
            )

            if not isinstance(
                label,
                str,
            ) or not label.strip():
                raise ValueError(
                    f"Choice {choice_id} in scene "
                    f"{scene_id} has no label."
                )

            next_scene = choice.get(
                "next_scene"
            )

            if next_scene is not None:

                if (
                    not isinstance(
                        next_scene,
                        str,
                    )
                    or next_scene
                    not in scene_ids
                ):
                    raise ValueError(
                        f"Choice {choice_id} in scene "
                        f"{scene_id} references unknown "
                        f"next_scene {next_scene}."
                    )

            effects = choice.get(
                "effects",
                {},
            )

            if not isinstance(
                effects,
                dict,
            ):
                raise ValueError(
                    f"Effects for choice {choice_id} "
                    f"in scene {scene_id} must be an object."
                )

            self._validate_conditions(
                choice.get(
                    "conditions",
                    [],
                ),
                context=(
                    f"choice {choice_id} "
                    f"in scene {scene_id}"
                ),
            )

            self._validate_effects(
                effects,
                context=(
                    f"choice {choice_id} "
                    f"in scene {scene_id}"
                ),
            )

        self._validate_transitions(
            scene,
            scene_ids,
        )

        self._validate_late_join(
            scene,
            role_ids,
            scene_ids,
        )

        self._validate_ending(
            scene
        )

def _validate_transitions(
    self,
    scene: dict[str, Any],
    scene_ids: set[str],
) -> None:

    transitions = scene.get(
        "transitions",
        [],
    )

    if transitions is None:
        return

    if not isinstance(
        transitions,
        list,
    ):
        raise ValueError(
            f"Scene {scene['id']} transitions "
            "must be a list."
        )

    for transition in transitions:

        if not isinstance(
            transition,
            dict,
        ):
            raise ValueError(
                f"Scene {scene['id']} contains "
                "an invalid transition."
            )

        target = transition.get(
            "next_scene"
        )

        if (
            not isinstance(
                target,
                str,
            )
            or target not in scene_ids
        ):
            raise ValueError(
                f"Scene {scene['id']} transition "
                f"references unknown scene {target}."
            )

        self._validate_conditions(
            transition.get(
                "conditions",
                [],
            ),
            context=(
                f"transition in scene "
                f"{scene['id']}"
            ),
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

    if not isinstance(
        late_join,
        dict,
    ):
        raise ValueError(
            f"Scene {scene['id']} late_join "
            "must be an object."
        )

    roles = late_join.get(
        "roles",
        [],
    )

    if not isinstance(
        roles,
        list,
    ):
        raise ValueError(
            f"Scene {scene['id']} late_join.roles "
            "must be a list."
        )

    self._validate_string_id_list(
        roles,
        f"scene {scene['id']} late_join.roles",
    )

    for role_id in roles:

        if role_id not in role_ids:
            raise ValueError(
                f"Scene {scene['id']} late_join references "
                f"unknown role {role_id}."
            )

    entry_scene = late_join.get(
        "entry_scene"
    )

    if entry_scene is not None:

        if entry_scene not in scene_ids:
            raise ValueError(
                f"Scene {scene['id']} late_join.entry_scene "
                f"references unknown scene {entry_scene}."
            )

    self._validate_conditions(
        late_join.get(
            "conditions",
            [],
        ),
        context=(
            f"late_join in scene "
            f"{scene['id']}"
        ),
    )

def _validate_ending(
    self,
    scene: dict[str, Any],
) -> None:

    ending = scene.get(
        "ending"
    )

    if ending is None:
        return

    if isinstance(
        ending,
        str,
    ):
        if not ending.strip():
            raise ValueError(
                f"Scene {scene['id']} has "
                "an empty ending."
            )
        return

    if not isinstance(
        ending,
        dict,
    ):
        raise ValueError(
            f"Scene {scene['id']} ending must "
            "be a string or object."
        )

    if not ending.get("id"):
        raise ValueError(
            f"Scene {scene['id']} ending has no id."
        )

    if not ending.get("text"):
        raise ValueError(
            f"Scene {scene['id']} ending has no text."
        )

    self._validate_conditions(
        ending.get(
            "conditions",
            [],
        ),
        context=(
            f"ending in scene "
            f"{scene['id']}"
        ),
    )

def _validate_conditions(
    self,
    conditions: Any,
    *,
    context: str,
) -> None:

    if conditions is None:
        return

    if not isinstance(
        conditions,
        list,
    ):
        raise ValueError(
            f"{context} conditions must be a list."
        )

    role_ids = set(
        self.roles.keys()
    )

    for condition in conditions:

        if not isinstance(
            condition,
            dict,
        ):
            raise ValueError(
                f"{context} contains an invalid condition."
            )

        condition_type = condition.get(
            "type"
        )

        if (
            condition_type
            not in self.ALLOWED_CONDITION_TYPES
        ):
            raise ValueError(
                f"{context} uses unsupported condition "
                f"type {condition_type}."
            )

        if condition_type == "flag":

            name = condition.get(
                "name"
            )

            if not isinstance(
                name,
                str,
            ) or not name.strip():
                raise ValueError(
                    f"{context} flag condition "
                    "requires name."
                )

        elif condition_type == "variable":

            name = condition.get(
                "name"
            )

            operator = condition.get(
                "operator",
                "eq",
            )

            if not isinstance(
                name,
                str,
            ) or not name.strip():
                raise ValueError(
                    f"{context} variable condition "
                    "requires name."
                )

            if operator not in self.ALLOWED_OPERATORS:
                raise ValueError(
                    f"{context} uses unsupported "
                    f"operator {operator}."
                )

        elif condition_type == "knowledge":

            role_id = condition.get(
                "role_id"
            )

            knowledge = condition.get(
                "knowledge"
            )

            if role_id not in role_ids:
                raise ValueError(
                    f"{context} knowledge condition "
                    f"references unknown role {role_id}."
                )

            if not isinstance(
                knowledge,
                str,
            ) or not knowledge.strip():
                raise ValueError(
                    f"{context} knowledge condition "
                    "requires knowledge."
                )

        elif condition_type == "relationship":

            role_a = condition.get(
                "role_a"
            )

            role_b = condition.get(
                "role_b"
            )

            operator = condition.get(
                "operator",
                "eq",
            )

            if role_a not in role_ids:
                raise ValueError(
                    f"{context} relationship condition "
                    f"references unknown role {role_a}."
                )

            if role_b not in role_ids:
                raise ValueError(
                    f"{context} relationship condition "
                    f"references unknown role {role_b}."
                )

            if operator not in self.ALLOWED_OPERATORS:
                raise ValueError(
                    f"{context} uses unsupported "
                    f"operator {operator}."
                )

def _validate_effects(
    self,
    effects: dict[str, Any],
    *,
    context: str,
) -> None:

    role_ids = set(
        self.roles.keys()
    )

    for key in (
        "set_flags",
        "set_variables",
        "character_state",
    ):
        value = effects.get(
            key,
            {},
        )

        if not isinstance(
            value,
            dict,
        ):
            raise ValueError(
                f"{context} {key} must be an object."
            )

    for key in (
        "remove_flags",
    ):
        value = effects.get(
            key,
            [],
        )

        if not isinstance(
            value,
            list,
        ):
            raise ValueError(
                f"{context} {key} must be a list."
            )

    add_variables = effects.get(
        "add_variables",
        {},
    )

    if not isinstance(
        add_variables,
        dict,
    ):
        raise ValueError(
            f"{context} add_variables must be an object."
        )

    relationships = effects.get(
        "relationships",
        [],
    )

    if not isinstance(
        relationships,
        list,
    ):
        raise ValueError(
            f"{context} relationships must be a list."
        )

    for relationship in relationships:

        if not isinstance(
            relationship,
            dict,
        ):
            raise ValueError(
                f"{context} contains invalid relationship."
            )

        role_a = relationship.get(
            "role_a"
        )

        role_b = relationship.get(
            "role_b"
        )

        if role_a not in role_ids:
            raise ValueError(
                f"{context} relationship references "
                f"unknown role {role_a}."
            )

        if role_b not in role_ids:
            raise ValueError(
                f"{context} relationship references "
                f"unknown role {role_b}."
            )

        amount = relationship.get(
            "amount",
            0,
        )

        if not isinstance(
            amount,
            (int, float),
        ) or isinstance(
            amount,
            bool,
        ):
            raise ValueError(
                f"{context} relationship amount "
                "must be numeric."
            )

    knowledge = effects.get(
        "knowledge",
        [],
    )

    if not isinstance(
        knowledge,
        list,
    ):
        raise ValueError(
            f"{context} knowledge must be a list."
        )

    for item in knowledge:

        if not isinstance(
            item,
            dict,
        ):
            raise ValueError(
                f"{context} contains invalid knowledge."
            )

        if item.get(
            "role_id"
        ) not in role_ids:
            raise ValueError(
                f"{context} knowledge references "
                f"unknown role {item.get('role_id')}."
            )

        if not isinstance(
            item.get("knowledge"),
            str,
        ):
            raise ValueError(
                f"{context} knowledge requires "
                "a knowledge identifier."
            )

    secrets = effects.get(
        "secrets",
        [],
    )

    if not isinstance(
        secrets,
        list,
    ):
        raise ValueError(
            f"{context} secrets must be a list."
        )

    for secret in secrets:

        if not isinstance(
            secret,
            dict,
        ):
            raise ValueError(
                f"{context} contains invalid secret."
            )

        owner = secret.get(
            "owner"
        )

        if owner is not None and owner not in role_ids:
            raise ValueError(
                f"{context} secret references "
                f"unknown owner {owner}."
            )

        revealed_to = secret.get(
            "revealed_to",
            [],
        )

        if not isinstance(
            revealed_to,
            list,
        ):
            raise ValueError(
                f"{context} secret revealed_to "
                "must be a list."
            )

        for role_id in revealed_to:

            if role_id not in role_ids:
                raise ValueError(
                    f"{context} secret references "
                    f"unknown revealed_to role {role_id}."
                )

@staticmethod
def _validate_string_id_list(
    values: list[Any],
    context: str,
) -> None:

    seen: set[str] = set()

    for value in values:

        if not isinstance(
            value,
            str,
        ) or not value.strip():
            raise ValueError(
                f"{context} contains an invalid ID."
            )

        if value in seen:
            raise ValueError(
                f"{context} contains duplicate ID "
                f"{value}."
            )

        seen.add(value)

def _validate_first_scene(self) -> None:

    first_scene_id = self.story.get(
        "first_scene_id"
    )

    if first_scene_id is None:

        if not self.story.get(
            "scenes"
        ):
            raise ValueError(
                "Story has no scenes."
            )

        return

    if first_scene_id not in self.scenes:
        raise ValueError(
            f"first_scene_id {first_scene_id} "
            "does not reference an existing scene."
        )

def _validate_graph_reachability(self) -> None:
    """
    Every scene must be reachable from the first scene.

    This catches Gemini-generated dead/unreachable scenes
    before gameplay begins.
    """

    start_id = self.first_scene_id()

    visited: set[str] = set()
    pending = [start_id]

    while pending:

        scene_id = pending.pop()

        if scene_id in visited:
            continue

        visited.add(
            scene_id
        )

        scene = self.get_scene(
            scene_id
        )

        for choice in scene.get(
            "choices",
            [],
        ):

            target = choice.get(
                "next_scene"
            )

            if target:
                pending.append(
                    target
                )

        for transition in scene.get(
            "transitions",
            [],
        ):

            target = transition.get(
                "next_scene"
            )

            if target:
                pending.append(
                    target
                )

    unreachable = (
        set(self.scenes.keys())
        - visited
    )

    if unreachable:
        raise ValueError(
            "Story contains unreachable scenes: "
            + ", ".join(
                sorted(unreachable)
            )
        )

# ============================================================
# BASIC ACCESS
# ============================================================

def first_scene_id(self) -> str:
    explicit = self.story.get(
        "first_scene_id"
    )

    if explicit:
        return explicit

    return self.story[
        "scenes"
    ][0]["id"]

def first_scene(self) -> dict[str, Any]:
    return self.get_scene(
        self.first_scene_id()
    )

def get_scene(
    self,
    scene_id: str,
) -> dict[str, Any]:

    scene = self.scenes.get(
        scene_id
    )

    if scene is None:
        raise ValueError(
            f"Unknown scene: {scene_id}"
        )

    return scene

def get_role(
    self,
    role_id: str,
) -> dict[str, Any]:

    role = self.roles.get(
        role_id
    )

    if role is None:
        raise ValueError(
            f"Unknown role: {role_id}"
        )

    return role

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
    """
    Deterministically assign unique playable roles.

    The returned mapping is:

        user_id -> role_id

    This function never persists anything.

    The caller must persist the assignments atomically.

    Sorting player IDs means assignment does not depend on
    Telegram update arrival order.
    """

    normalized_ids = [
        str(player_id)
        for player_id in player_ids
    ]

    if len(
        normalized_ids
    ) != len(
        set(normalized_ids)
    ):
        raise ValueError(
            "Duplicate player IDs cannot be assigned roles."
        )

    normalized_ids.sort()

    roles = self.playable_roles()

    if len(roles) < len(normalized_ids):
        raise ValueError(
            "Not enough playable roles for the number "
            "of players."
        )

    # Sort role IDs so assignment is stable.
    roles.sort(
        key=lambda role: role["id"]
    )

    assignments: dict[str, str] = {}

    for index, player_id in enumerate(
        normalized_ids
    ):
        assignments[player_id] = roles[
            index
        ]["id"]

    if len(
        assignments.values()
    ) != len(
        set(assignments.values())
    ):
        raise ValueError(
            "Role assignment attempted to reuse a role."
        )

    return assignments

# ============================================================
# WORLD STATE
# ============================================================

def initial_world_state(self) -> dict[str, Any]:
    """
    Returns a fresh state object.

    This object is intended to be persisted in Supabase.

    The engine does not retain it between calls.
    """

    return {
        "version": 1,
        "started": False,
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

    base = self.initial_world_state()

    if world_state:
        if not isinstance(
            world_state,
            dict,
        ):
            raise ValueError(
                "world_state must be an object."
            )

        self._deep_merge(
            base,
            world_state,
        )

    return base

@staticmethod
def _deep_merge(
    target: dict[str, Any],
    source: dict[str, Any],
) -> None:

    for key, value in source.items():

        if (
            isinstance(
                value,
                dict,
            )
            and isinstance(
                target.get(key),
                dict,
            )
        ):
            GameEngine._deep_merge(
                target[key],
                value,
            )
        else:
            target[key] = copy.deepcopy(
                value
            )

# ============================================================
# SCENE ENTRY
# ============================================================

def enter_scene(
    self,
    world_state: dict[str, Any],
    scene_id: str,
) -> dict[str, Any]:

    state = self.normalize_world_state(
        world_state
    )

    self.get_scene(
        scene_id
    )

    visits = state[
        "scene_visits"
    ]

    visits[scene_id] = (
        int(
            visits.get(
                scene_id,
                0,
            )
        )
        + 1
    )

    if scene_id not in state[
        "visited_scenes"
    ]:
        state[
            "visited_scenes"
        ].append(scene_id)

    state["started"] = True

    return state

# ============================================================
# PARTICIPATION
# ============================================================

def role_can_participate(
    self,
    scene: dict[str, Any],
    role_id: str,
    world_state: dict[str, Any] | None = None,
) -> bool:

    self.get_role(
        role_id
    )

    eligible = scene.get(
        "eligible_roles",
        [],
    )

    if eligible and role_id not in eligible:
        return False

    state = self.normalize_world_state(
        world_state
    )

    conditions = scene.get(
        "conditions",
        [],
    )

    return self.conditions_match(
        conditions,
        state,
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

    choices: list[dict[str, Any]] = []

    for choice in scene.get(
        "choices",
        [],
    ):

        if self.conditions_match(
            choice.get(
                "conditions",
                [],
            ),
            state,
        ):
            choices.append(
                copy.deepcopy(
                    choice
                )
            )

    return choices

# ============================================================
# CONDITIONS
# ============================================================

def conditions_match(
    self,
    conditions: list[Any] | None,
    world_state: dict[str, Any],
) -> bool:

    if not conditions:
        return True

    state = self.normalize_world_state(
        world_state
    )

    for condition in conditions:

        if not isinstance(
            condition,
            dict,
        ):
            raise ValueError(
                "Condition must be an object."
            )

        condition_type = condition.get(
            "type"
        )

        if condition_type == "flag":

            name = condition.get(
                "name"
            )

            expected = condition.get(
                "value",
                True,
            )

            actual = state[
                "flags"
            ].get(name)

            if actual != expected:
                return False

        elif condition_type == "variable":

            name = condition.get(
                "name"
            )

            operator = condition.get(
                "operator",
                "eq",
            )

            expected = condition.get(
                "value"
            )

            actual = state[
                "variables"
            ].get(name)

            if not self.compare(
                actual,
                operator,
                expected,
            ):
                return False

        elif condition_type == "knowledge":

            role_id = condition.get(
                "role_id"
            )

            knowledge_id = condition.get(
                "knowledge"
            )

            required = condition.get(
                "value",
                True,
            )

            role_knowledge = state[
                "knowledge"
            ].get(
                role_id,
                {},
            )

            actual = role_knowledge.get(
                knowledge_id,
                False,
            )

            if actual != required:
                return False

        elif condition_type == "relationship":

            pair = self.relationship_key(
                condition.get(
                    "role_a"
                ),
                condition.get(
                    "role_b"
                ),
            )

            operator = condition.get(
                "operator",
                "eq",
            )

            expected = condition.get(
                "value",
                0,
            )

            actual = state[
                "relationships"
            ].get(
                pair,
                0,
            )

            if not self.compare(
                actual,
                operator,
                expected,
            ):
                return False

        else:
            raise ValueError(
                f"Unknown condition type: "
                f"{condition_type}"
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
        f"Unknown comparison operator: "
        f"{operator}"
    )

# ============================================================
# CHOICE ACCESS
# ============================================================

def get_choice(
    self,
    scene: dict[str, Any],
    choice_id: str,
) -> dict[str, Any]:

    for choice in scene.get(
        "choices",
        [],
    ):
        if choice.get(
            "id"
        ) == choice_id:
            return copy.deepcopy(
                choice
            )

    raise ValueError(
        f"Invalid choice: {choice_id}"
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

        if choice.get(
            "id"
        ) == choice_id:
            return choice

    raise ValueError(
        "This choice is not available "
        "to this role."
    )

# ============================================================
# SINGLE CHOICE RESOLUTION
# ============================================================

def resolve_choice(
    self,
    scene: dict[str, Any],
    choice_id: str,
    *,
    round_number: int,
    role_id: str | None = None,
    world_state: dict[str, Any] | None = None,
) -> dict[str, Any]:

    state = self.normalize_world_state(
        world_state
    )

    if role_id is not None:

        choice = self.validate_choice_for_role(
            scene,
            role_id,
            choice_id,
            state,
        )

    else:
        choice = self.get_choice(
            scene,
            choice_id,
        )

    effects = copy.deepcopy(
        choice.get(
            "effects",
            {},
        )
    )

    self.apply_effects(
        state,
        effects,
        role_id=role_id,
    )

    public_event = self.resolve_public_event(
        scene_id=scene["id"],
        choice=choice,
        round_number=round_number,
    )

    event = {
        "round": round_number,
        "scene_id": scene["id"],
        "choice_id": choice_id,
        "role_id": role_id,
        "public_event": public_event,
        "effects": copy.deepcopy(
            effects
        ),
    }

    state[
        "choice_history"
    ].append(
        {
            "round": round_number,
            "scene_id": scene["id"],
            "choice_id": choice_id,
            "role_id": role_id,
        }
    )

    if public_event:

        state[
            "public_events"
        ].append(
            {
                "round": round_number,
                "scene_id": scene["id"],
                "text": public_event,
            }
        )

    return {
        "choice_id": choice_id,
        "public_event": public_event,
        "next_scene": choice.get(
            "next_scene"
        ),
        "effects": effects,
        "world_state": state,
        "event": event,
    }

def resolve_public_event(
    self,
    *,
    scene_id: str,
    choice: dict[str, Any],
    round_number: int,
) -> str:

    variants = choice.get(
        "public_event_variants"
    )

    if not variants:
        return str(
            choice.get(
                "public_event",
                "",
            )
            or ""
        )

    if not isinstance(
        variants,
        list,
    ):
        raise ValueError(
            "public_event_variants must be a list."
        )

    usable = [
        value
        for value in variants
        if isinstance(
            value,
            str,
        )
        and value.strip()
    ]

    if not usable:
        return str(
            choice.get(
                "public_event",
                "",
            )
            or ""
        )

    digest = hashlib.sha256(
        (
            f"{scene_id}|"
            f"{choice.get('id')}|"
            f"{round_number}"
        ).encode(
            "utf-8"
        )
    ).hexdigest()

    index = int(
        digest[:16],
        16,
    ) % len(usable)

    return usable[index]

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
    Deterministically resolve one complete round.

    IMPORTANT:

    This method does not perform the database transaction.

    The database layer must atomically claim/complete the
    round around this calculation.

    Input order is normalized before processing.

    Therefore:
        Telegram click order
        HTTP request order
        worker order

    cannot change the result.
    """

    scene = self.get_scene(
        scene_id
    )

    state = self.normalize_world_state(
        world_state
    )

    active_players = [
        player
        for player in players
        if self.player_is_eligible_for_round(
            player,
            round_number,
        )
    ]

    player_by_user_id = {
        str(
            player.get(
                "user_id"
            )
        ): player
        for player in active_players
    }

    decision_by_user_id: dict[
        str,
        dict[str, Any],
    ] = {}

    for decision in decisions:

        user_id = str(
            decision.get(
                "user_id"
            )
        )

        # Duplicate decisions are an invariant violation.
        if user_id in decision_by_user_id:
            raise ValueError(
                f"Duplicate decision for player "
                f"{user_id} in round {round_number}."
            )

        decision_by_user_id[
            user_id
        ] = decision

    missing: list[dict[str, Any]] = []

    accepted: list[dict[str, Any]] = []

    for player in sorted(
        active_players,
        key=lambda item: str(
            item.get("user_id")
        ),
    ):

        user_id = str(
            player.get(
                "user_id"
            )
        )

        decision = decision_by_user_id.get(
            user_id
        )

        if decision is None:
            missing.append(
                copy.deepcopy(
                    player
                )
            )
            continue

        role_id = player.get(
            "role_id"
        )

        if not role_id:
            raise ValueError(
                f"Player {user_id} has no role."
            )

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

    # --------------------------------------------------------
    # Apply real player decisions in deterministic user ID
    # order.
    # --------------------------------------------------------

    accepted.sort(
        key=lambda item: (
            str(item["user_id"]),
            str(item["role_id"]),
            str(item["choice_id"]),
        )
    )

    resolved_events: list[dict[str, Any]] = []
    next_scene_candidates: list[
        dict[str, Any]
    ] = []

    for item in accepted:

        result = self.resolve_choice(
            scene,
            item["choice_id"],
            round_number=round_number,
            role_id=item["role_id"],
            world_state=state,
        )

        state = result[
            "world_state"
        ]

        resolved_events.append(
            result["event"]
        )

        if result.get(
            "next_scene"
        ):
            next_scene_candidates.append(
                {
                    "source": "player",
                    "user_id": item["user_id"],
                    "role_id": item["role_id"],
                    "choice_id": item["choice_id"],
                    "next_scene": result[
                        "next_scene"
                    ],
                    "priority": int(
                        item["choice"].get(
                            "branch_priority",
                            0,
                        )
                    ),
                }
            )

    # --------------------------------------------------------
    # Missed decisions do not live only in Python.
    #
    # We calculate their identity here. The service layer
    # persists the missed count using Supabase.
    # --------------------------------------------------------

    missed_results: list[dict[str, Any]] = []

    for player in missing:

        user_id = str(
            player.get(
                "user_id"
            )
        )

        role_id = player.get(
            "role_id"
        )

        if not role_id:
            raise ValueError(
                f"Player {user_id} has no role."
            )

        missed_count = int(
            player.get(
                "missed_decisions",
                0,
            )
        )

        new_missed_count = (
            missed_count + 1
        )

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
                "previous_missed_decisions": (
                    missed_count
                ),
                "missed_decisions": (
                    new_missed_count
                ),
                "eliminate": (
                    new_missed_count
                    >= self.MAX_MISSED_DECISIONS
                ),
                "npc_choice_id": (
                    npc_choice.get("id")
                    if npc_choice
                    else None
                ),
            }
        )

    # --------------------------------------------------------
    # Resolve the next scene from all actual choices.
    #
    # This is deterministic and does not depend on which
    # Telegram request arrived first.
    # --------------------------------------------------------

    next_scene = self.resolve_next_scene(
        current_scene=scene,
        results=next_scene_candidates,
        world_state=state,
    )

    ending = self.ending_for_scene(
        scene,
        state,
    )

    if ending:
        state[
            "ending"
        ] = copy.deepcopy(
            ending
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
# ROUND PARTICIPATION
# ============================================================

@staticmethod
def player_is_eligible_for_round(
    player: dict[str, Any],
    round_number: int,
) -> bool:

    lifecycle = player.get(
        "lifecycle"
    )

    if lifecycle is not None:

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

    return joined_round <= round_number

# ============================================================
# DEFAULT / NPC CHOICES
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

    # Stable choice selection based on story structure.
    choices = sorted(
        choices,
        key=lambda choice: str(
            choice.get(
                "id"
            )
        ),
    )

    digest = hashlib.sha256(
        (
            f"npc|"
            f"{self.story.get('title', '')}|"
            f"{scene['id']}|"
            f"{role_id}|"
            f"{self._state_signature(world_state)}"
        ).encode(
            "utf-8"
        )
    ).hexdigest()

    index = int(
        digest[:16],
        16,
    ) % len(choices)

    return copy.deepcopy(
        choices[index]
    )

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

    state = self.normalize_world_state(
        world_state
    )

    # --------------------------------------------------------
    # Explicit scene transitions have priority.
    # --------------------------------------------------------

    for transition in current_scene.get(
        "transitions",
        [],
    ):

        if self.conditions_match(
            transition.get(
                "conditions",
                [],
            ),
            state,
        ):
            target = transition.get(
                "next_scene"
            )

            if target:
                return target

    candidates: list[
        dict[str, Any]
    ] = []

    for result in results:

        target = result.get(
            "next_scene"
        )

        if not target:
            continue

        candidates.append(
            {
                "target": target,
                "priority": int(
                    result.get(
                        "priority",
                        0,
                    )
                ),
                "user_id": str(
                    result.get(
                        "user_id",
                        "",
                    )
                ),
                "role_id": str(
                    result.get(
                        "role_id",
                        "",
                    )
                ),
                "choice_id": str(
                    result.get(
                        "choice_id",
                        "",
                    )
                ),
            }
        )

    if not candidates:
        return None

    highest_priority = max(
        candidate["priority"]
        for candidate in candidates
    )

    candidates = [
        candidate
        for candidate in candidates
        if candidate["priority"]
        == highest_priority
    ]

    targets = sorted(
        {
            candidate["target"]
            for candidate in candidates
        }
    )

    if len(targets) == 1:
        return targets[0]

    # --------------------------------------------------------
    # Deterministic consensus/tie resolution.
    #
    # We hash the normalized complete candidate set and the
    # resulting world state.
    #
    # This means two workers calculating the same round will
    # produce the same scene.
    # --------------------------------------------------------

    normalized = sorted(
        (
            candidate["target"],
            candidate["user_id"],
            candidate["role_id"],
            candidate["choice_id"],
        )
        for candidate in candidates
    )

    payload = {
        "scene": current_scene["id"],
        "candidates": normalized,
        "state": state,
    }

    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(
                ",",
                ":",
            ),
        ).encode(
            "utf-8"
        )
    ).hexdigest()

    index = int(
        digest[:16],
        16,
    ) % len(targets)

    return targets[index]

# ============================================================
# EFFECT SYSTEM
# ============================================================

def apply_effects(
    self,
    world_state: dict[str, Any],
    effects: dict[str, Any],
    *,
    role_id: str | None = None,
) -> None:

    if not effects:
        return

    state = self.normalize_world_state(
        world_state
    )

    # --------------------------------------------------------
    # FLAGS
    # --------------------------------------------------------

    set_flags = effects.get(
        "set_flags",
        {},
    )

    if isinstance(
        set_flags,
        dict,
    ):

        for name, value in set_flags.items():

            state[
                "flags"
            ][name] = copy.deepcopy(
                value
            )

    for name in effects.get(
        "remove_flags",
        [],
    ):

        state[
            "flags"
        ].pop(
            name,
            None,
        )

    # --------------------------------------------------------
    # VARIABLES
    # --------------------------------------------------------

    set_variables = effects.get(
        "set_variables",
        {},
    )

    if isinstance(
        set_variables,
        dict,
    ):

        for name, value in set_variables.items():

            state[
                "variables"
            ][name] = copy.deepcopy(
                value
            )

    add_variables = effects.get(
        "add_variables",
        {},
    )

    if isinstance(
        add_variables,
        dict,
    ):

        for name, amount in add_variables.items():

            current = state[
                "variables"
            ].get(
                name,
                0,
            )

            if not isinstance(
                current,
                (int, float),
            ) or isinstance(
                current,
                bool,
            ):
                raise ValueError(
                    f"Variable {name} is not numeric."
                )

            if not isinstance(
                amount,
                (int, float),
            ) or isinstance(
                amount,
                bool,
            ):
                raise ValueError(
                    f"Variable increment {name} "
                    "is not numeric."
                )

            state[
                "variables"
            ][name] = (
                current + amount
            )

    # --------------------------------------------------------
    # RELATIONSHIPS
    # --------------------------------------------------------

    for relationship in effects.get(
        "relationships",
        [],
    ):

        role_a = relationship.get(
            "role_a"
        )

        role_b = relationship.get(
            "role_b"
        )

        amount = relationship.get(
            "amount",
            0,
        )

        key = self.relationship_key(
            role_a,
            role_b,
        )

        current = state[
            "relationships"
        ].get(
            key,
            0,
        )

        if not isinstance(
            current,
            (int, float),
        ):
            raise ValueError(
                f"Relationship {key} is not numeric."
            )

        state[
            "relationships"
        ][key] = (
            current + amount
        )

    # --------------------------------------------------------
    # KNOWLEDGE
    # --------------------------------------------------------

    for item in effects.get(
        "knowledge",
        [],
    ):

        target_role = item.get(
            "role_id"
        )

        knowledge_id = item.get(
            "knowledge"
        )

        value = item.get(
            "value",
            True,
        )

        role_knowledge = state[
            "knowledge"
        ].setdefault(
            target_role,
            {},
        )

        role_knowledge[
            knowledge_id
        ] = value

    # --------------------------------------------------------
    # SECRETS
    # --------------------------------------------------------

    for secret in effects.get(
        "secrets",
        [],
    ):

        secret_id = secret.get(
            "id"
        )

        if not secret_id:
            continue

        stored = copy.deepcopy(
            secret
        )

        state[
            "secrets"
        ][secret_id] = stored

    # --------------------------------------------------------
    # CHARACTER STATE
    # --------------------------------------------------------

    character_state = effects.get(
        "character_state",
        {},
    )

    if isinstance(
        character_state,
        dict,
    ):

        # If an effect explicitly targets the current role,
        # store it under that role.
        if role_id is not None:

            current = state[
                "character_states"
            ].setdefault(
                role_id,
                {},
            )

            self._deep_merge(
                current,
                character_state,
            )

        else:

            self._deep_merge(
                state[
                    "character_states"
                ],
                character_state,
            )

    # --------------------------------------------------------
    # Cosmetic narrative fields are intentionally ignored by
    # the world-state mutation system.
    #
    # They are returned as part of the choice/event result.
    # --------------------------------------------------------

    world_state.clear()

    self._deep_merge(
        world_state,
        state,
    )

# ============================================================
# RELATIONSHIPS
# ============================================================

@staticmethod
def relationship_key(
    role_a: str | None,
    role_b: str | None,
) -> str:

    if not role_a or not role_b:
        raise ValueError(
            "Relationship requires role_a and role_b."
        )

    return "|".join(
        sorted(
            [
                role_a,
                role_b,
            ]
        )
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

    if isinstance(
        ending,
        str,
    ):
        return {
            "id": ending,
            "text": ending,
        }

    if isinstance(
        ending,
        dict,
    ):

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

    self.get_role(
        role_id
    )

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

    if allowed_roles and role_id not in allowed_roles:
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

    late_join = scene.get(
        "late_join",
        {},
    )

    return late_join.get(
        "allowed",
        True,
    ) is not False

def register_late_joiner(
    self,
    world_state: dict[str, Any],
    role_id: str,
    round_number: int,
) -> dict[str, Any]:

    state = self.normalize_world_state(
        world_state
    )

    existing = {
        (
            item.get("role_id"),
            int(
                item.get(
                    "joined_round",
                    0,
                )
            ),
        )
        for item in state[
            "late_joiners"
        ]
        if isinstance(
            item,
            dict,
        )
    }

    marker = (
        role_id,
        int(round_number),
    )

    if marker not in existing:

        state[
            "late_joiners"
        ].append(
            {
                "role_id": role_id,
                "joined_round": int(
                    round_number
                ),
            }
        )

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

    character[
        "control"
    ] = "npc"

    character[
        "human_active"
    ] = False

    return state

# ============================================================
# TIMER DATA
# ============================================================

def timer_seconds(
    self,
    scene: dict[str, Any],
) -> int:

    value = scene.get(
        "timer_seconds"
    )

    if not isinstance(
        value,
        int,
    ) or isinstance(
        value,
        bool,
    ):
        raise ValueError(
            f"Scene {scene['id']} has invalid timer."
        )

    if not (
        self.MIN_TIMER_SECONDS
        <= value
        <= self.MAX_TIMER_SECONDS
    ):
        raise ValueError(
            f"Scene {scene['id']} timer is outside "
            "the allowed range."
        )

    return value

# ============================================================
# FINGERPRINT
# ============================================================

def structural_fingerprint(self) -> str:
    """
    Deterministic fingerprint of gameplay structure.

    Narrative wording is deliberately excluded where possible
    so the same underlying game cannot evade duplicate-story
    detection merely by changing prose.
    """

    structural = {
        "roles": [
            {
                "id": role.get(
                    "id"
                ),
                "playable": role.get(
                    "playable"
                ),
            }
            for role in sorted(
                self.story.get(
                    "roles",
                    [],
                ),
                key=lambda item: str(
                    item.get(
                        "id"
                    )
                ),
            )
        ],
        "first_scene_id": self.first_scene_id(),
        "scenes": [],
    }

    for scene in sorted(
        self.story.get(
            "scenes",
            [],
        ),
        key=lambda item: str(
            item.get(
                "id"
            )
        ),
    ):

        scene_signature = {
            "id": scene.get(
                "id"
            ),
            "timer_seconds": scene.get(
                "timer_seconds"
            ),
            "eligible_roles": sorted(
                scene.get(
                    "eligible_roles",
                    [],
                )
            ),
            "choices": [],
            "transitions": [],
            "ending": self._ending_signature(
                scene.get(
                    "ending"
                )
            ),
        }

        for choice in sorted(
            scene.get(
                "choices",
                [],
            ),
            key=lambda item: str(
                item.get(
                    "id"
                )
            ),
        ):

            scene_signature[
                "choices"
            ].append(
                {
                    "id": choice.get(
                        "id"
                    ),
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
                    "effects": self._effect_signature(
                        choice.get(
                            "effects",
                            {},
                        )
                    ),
                }
            )

        for transition in scene.get(
            "transitions",
            [],
        ):

            scene_signature[
                "transitions"
            ].append(
                {
                    "next_scene": transition.get(
                        "next_scene"
                    ),
                    "conditions": transition.get(
                        "conditions",
                        [],
                    ),
                }
            )

        structural[
            "scenes"
        ].append(
            scene_signature
        )

    serialized = json.dumps(
        structural,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        ensure_ascii=False,
    )

    return hashlib.sha256(
        serialized.encode(
            "utf-8"
        )
    ).hexdigest()

@staticmethod
def _ending_signature(
    ending: Any,
) -> Any:

    if ending is None:
        return None

    if isinstance(
        ending,
        str,
    ):
        return {
            "id": ending
        }

    if isinstance(
        ending,
        dict,
    ):
        return {
            "id": ending.get(
                "id"
            ),
            "conditions": ending.get(
                "conditions",
                [],
            ),
        }

    return ending

@staticmethod
def _effect_signature(
    effects: dict[str, Any],
) -> Any:

    if not isinstance(
        effects,
        dict,
    ):
        return effects

    return {
        key: copy.deepcopy(
            effects[key]
        )
        for key in sorted(
            effects.keys()
        )
        if key
        not in {
            "text",
            "description",
        }
    }

# ============================================================
# DETERMINISTIC SIGNATURE
# ============================================================

@staticmethod
def _state_signature(
    world_state: dict[str, Any],
) -> str:

    return json.dumps(
        world_state,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        ensure_ascii=False,
    )
