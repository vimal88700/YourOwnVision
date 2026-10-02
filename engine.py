from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


class GameEngine:
    """
    Deterministic sandbox simulation engine.

    Gemini creates the story universe.

    This class NEVER calls Gemini.

    It is responsible for:
        - scenes
        - roles
        - choices
        - hidden information
        - world state
        - relationships
        - knowledge
        - flags
        - consequences
        - branching
        - late joining
        - deterministic resolution
        - NPC continuation
        - endings
    """

    MAX_MISSED = 3

    def __init__(
        self,
        story: dict[str, Any],
    ):
        self.story = copy.deepcopy(story)

        self.scenes = {
            scene["id"]: scene
            for scene in self.story.get(
                "scenes",
                [],
            )
        }

        self.roles = {
            role["id"]: role
            for role in self.story.get(
                "roles",
                [],
            )
        }

        self.validate_story()

    # ============================================================
    # STORY VALIDATION
    # ============================================================

    def validate_story(self) -> None:

        if not isinstance(self.story, dict):
            raise ValueError(
                "Story must be an object."
            )

        if not self.story.get("title"):
            raise ValueError(
                "Story has no title."
            )

        if not self.roles:
            raise ValueError(
                "Story has no roles."
            )

        if not self.scenes:
            raise ValueError(
                "Story has no scenes."
            )

        role_ids = set(
            self.roles.keys()
        )

        scene_ids = set(
            self.scenes.keys()
        )

        for role_id, role in self.roles.items():

            if not role.get("name"):
                raise ValueError(
                    f"Role {role_id} has no name."
                )

            if not role.get(
                "secret_description"
            ):
                raise ValueError(
                    f"Role {role_id} has no secret."
                )

        for scene in self.scenes.values():

            scene_id = scene["id"]

            if not scene.get(
                "public_text"
            ):
                raise ValueError(
                    f"Scene {scene_id} has no public_text."
                )

            choices = scene.get(
                "choices",
                [],
            )

            if not choices:
                raise ValueError(
                    f"Scene {scene_id} has no choices."
                )

            eligible = scene.get(
                "eligible_roles",
                [],
            )

            for role_id in eligible:

                if role_id not in role_ids:
                    raise ValueError(
                        f"Scene {scene_id} "
                        f"references unknown role "
                        f"{role_id}."
                    )

            choice_ids = set()

            for choice in choices:

                choice_id = choice.get("id")

                if not choice_id:
                    raise ValueError(
                        f"Scene {scene_id} "
                        "contains choice without id."
                    )

                if choice_id in choice_ids:
                    raise ValueError(
                        f"Duplicate choice {choice_id} "
                        f"in scene {scene_id}."
                    )

                choice_ids.add(choice_id)

                target = choice.get(
                    "next_scene"
                )

                if (
                    target is not None
                    and target not in scene_ids
                ):
                    raise ValueError(
                        f"Choice {choice_id} "
                        f"points to unknown scene "
                        f"{target}."
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
                        f"Effects for "
                        f"{choice_id} "
                        "must be an object."
                    )

    # ============================================================
    # BASIC ACCESS
    # ============================================================

    def first_scene(self) -> dict[str, Any]:

        return self.story[
            "scenes"
        ][0]

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

    # ============================================================
    # WORLD STATE
    # ============================================================

    def initial_world_state(
        self,
    ) -> dict[str, Any]:

        """
        Creates the persistent sandbox state.

        The state is deliberately generic so generated
        stories can introduce different relationships,
        secrets and flags.
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

        scene = self.get_scene(
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
    # ROLE / PLAYER PARTICIPATION
    # ============================================================

    def role_can_participate(
        self,
        scene: dict[str, Any],
        role_id: str,
        world_state: dict[str, Any] | None = None,
    ) -> bool:

        eligible = scene.get(
            "eligible_roles",
            [],
        )

        if eligible:
            if role_id not in eligible:
                return False

        conditions = scene.get(
            "conditions",
            [],
        )

        state = self.normalize_world_state(
            world_state
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

        result = []

        for choice in scene.get(
            "choices",
            [],
        ):

            conditions = choice.get(
                "conditions",
                [],
            )

            if self.conditions_match(
                conditions,
                state,
            ):
                result.append(
                    choice
                )

        return result

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

        for condition in conditions:

            if not isinstance(
                condition,
                dict,
            ):
                continue

            condition_type = condition.get(
                "type"
            )

            if condition_type == "flag":

                flag = condition.get(
                    "name"
                )

                expected = condition.get(
                    "value",
                    True,
                )

                actual = world_state[
                    "flags"
                ].get(flag)

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

                actual = world_state[
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

                role_knowledge = (
                    world_state[
                        "knowledge"
                    ].get(
                        role_id,
                        {},
                    )
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

                actual = world_state[
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
            return (
                actual is not None
                and expected in actual
            )

        raise ValueError(
            f"Unknown comparison operator: "
            f"{operator}"
        )

    # ============================================================
    # CHOICE VALIDATION
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
            if choice.get("id") == choice_id:
                return choice

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

            if choice.get("id") == choice_id:
                return choice

        raise ValueError(
            "This choice is not available "
            "to this role."
        )

    # ============================================================
    # CHOICE RESOLUTION
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

            choice = (
                self.validate_choice_for_role(
                    scene,
                    role_id,
                    choice_id,
                    state,
                )
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

        seed_text = (
            f"{self.story.get('title', '')}|"
            f"{scene['id']}|"
            f"{choice_id}|"
            f"{round_number}"
        )

        seed = int(
            hashlib.sha256(
                seed_text.encode(
                    "utf-8"
                )
            ).hexdigest()[:16],
            16,
        )

        variants = choice.get(
            "public_event_variants"
        )

        public_event = choice.get(
            "public_event",
            "",
        )

        if variants:

            if not isinstance(
                variants,
                list,
            ):
                raise ValueError(
                    "public_event_variants "
                    "must be a list."
                )

            if variants:
                public_event = variants[
                    seed % len(variants)
                ]

        event = {
            "round": round_number,
            "scene_id": scene["id"],
            "choice_id": choice_id,
            "role_id": role_id,
            "public_event": public_event,
            "effects": effects,
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

        # --------------------------------------------------------
        # FLAGS
        # --------------------------------------------------------

        flags = effects.get(
            "set_flags",
            {},
        )

        if isinstance(flags, dict):

            for name, value in flags.items():

                world_state[
                    "flags"
                ][name] = value

        remove_flags = effects.get(
            "remove_flags",
            [],
        )

        if isinstance(
            remove_flags,
            list,
        ):

            for name in remove_flags:

                world_state[
                    "flags"
                ].pop(
                    name,
                    None,
                )

        # --------------------------------------------------------
        # VARIABLES
        # --------------------------------------------------------

        variables = world_state[
            "variables"
        ]

        set_variables = effects.get(
            "set_variables",
            {},
        )

        if isinstance(
            set_variables,
            dict,
        ):

            for name, value in set_variables.items():

                variables[name] = value

        add_variables = effects.get(
            "add_variables",
            {},
        )

        if isinstance(
            add_variables,
            dict,
        ):

            for name, amount in add_variables.items():

                current = variables.get(
                    name,
                    0,
                )

                if not isinstance(
                    current,
                    (int, float),
                ):
                    raise ValueError(
                        f"Variable {name} "
                        "is not numeric."
                    )

                variables[name] = (
                    current + amount
                )

        # --------------------------------------------------------
        # RELATIONSHIPS
        # --------------------------------------------------------

        relationships = effects.get(
            "relationships",
            [],
        )

        if isinstance(
            relationships,
            list,
        ):

            for relationship in relationships:

                if not isinstance(
                    relationship,
                    dict,
                ):
                    continue

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

                if not role_a or not role_b:
                    continue

                key = self.relationship_key(
                    role_a,
                    role_b,
                )

                current = world_state[
                    "relationships"
                ].get(
                    key,
                    0,
                )

                world_state[
                    "relationships"
                ][key] = (
                    current + amount
                )

        # --------------------------------------------------------
        # KNOWLEDGE
        # --------------------------------------------------------

        knowledge = effects.get(
            "knowledge",
            [],
        )

        if isinstance(
            knowledge,
            list,
        ):

            for item in knowledge:

                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                target_role = item.get(
                    "role_id"
                )

                knowledge_id = item.get(
                    "knowledge"
                )

                if (
                    not target_role
                    or not knowledge_id
                ):
                    continue

                if target_role not in world_state[
                    "knowledge"
                ]:
                    world_state[
                        "knowledge"
                    ][target_role] = {}

                world_state[
                    "knowledge"
                ][target_role][
                    knowledge_id
                ] = item.get(
                    "value",
                    True,
                )

        # --------------------------------------------------------
        # SECRETS
        # --------------------------------------------------------

        secrets = effects.get(
            "secrets",
            [],
        )

        if isinstance(
            secrets,
            list,
        ):

            for item in secrets:

                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                secret_id = item.get(
                    "id"
                )

                if not secret_id:
                    continue

                world_state[
                    "secrets"
                ][secret_id] = {
                    "owner": item.get(
                        "owner"
                    ),
                    "revealed": item.get(
                        "revealed",
                        False,
                    ),
                    "revealed_to": item.get(
                        "revealed_to",
                        [],
                    ),
                }

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
        ) and role_id:

            current = world_state[
                "character_states"
            ].setdefault(
                role_id,
                {},
            )

            self._deep_merge(
                current,
                character_state,
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
                "Relationship requires two roles."
            )

        return "|".join(
            sorted(
                [
                    role_a,
                    role_b,
                ]
            )
        )

    def relationship_value(
        self,
        world_state: dict[str, Any],
        role_a: str,
        role_b: str,
    ) -> int | float:

        key = self.relationship_key(
            role_a,
            role_b,
        )

        return world_state[
            "relationships"
        ].get(
            key,
            0,
        )

    # ============================================================
    # MULTI-PLAYER BRANCH RESOLUTION
    # ============================================================

    def next_scene_from_results(
        self,
        current_scene: dict[str, Any],
        results: list[dict[str, Any]],
        world_state: dict[str, Any] | None = None,
    ) -> str | None:

        """
        Determines the next scene.

        Priority:

        1. Explicit ending.
        2. Result with highest branch priority.
        3. Conditional next-scene rules.
        4. Deterministic combination of player choices.
        5. No target = ending.
        """

        state = self.normalize_world_state(
            world_state
        )

        # --------------------------------------------------------
        # Explicit ending
        # --------------------------------------------------------

        if current_scene.get(
            "ending"
        ):
            return None

        # --------------------------------------------------------
        # Conditional scene rules
        # --------------------------------------------------------

        transitions = current_scene.get(
            "transitions",
            [],
        )

        if isinstance(
            transitions,
            list,
        ):

            for transition in transitions:

                if not isinstance(
                    transition,
                    dict,
                ):
                    continue

                conditions = transition.get(
                    "conditions",
                    [],
                )

                if self.conditions_match(
                    conditions,
                    state,
                ):
                    target = transition.get(
                        "next_scene"
                    )

                    if target:
                        return target

        # --------------------------------------------------------
        # Gather result targets
        # --------------------------------------------------------

        candidates = []

        for result in results:

            target = result.get(
                "next_scene"
            )

            if target:
                candidates.append(
                    {
                        "target": target,
                        "priority": int(
                            result.get(
                                "branch_priority",
                                0,
                            )
                        ),
                    }
                )

        if not candidates:
            return None

        # --------------------------------------------------------
        # Highest explicit branch priority
        # --------------------------------------------------------

        highest_priority = max(
            item["priority"]
            for item in candidates
        )

        priority_targets = [
            item["target"]
            for item in candidates
            if item["priority"]
            == highest_priority
        ]

        # --------------------------------------------------------
        # If all same target
        # --------------------------------------------------------

        if len(
            set(priority_targets)
        ) == 1:

            return priority_targets[0]

        # --------------------------------------------------------
        # Deterministic group resolution
        #
        # Never let "last player to click" win.
        # --------------------------------------------------------

        combined = "|".join(
            sorted(priority_targets)
        )

        # World state is included so that two otherwise
        # identical choice sets can diverge after previous
        # consequences.

        state_signature = json.dumps(
            state,
            sort_keys=True,
            separators=(
                ",",
                ":",
            ),
        )

        seed_text = (
            f"{current_scene['id']}|"
            f"{combined}|"
            f"{state_signature}"
        )

        digest = hashlib.sha256(
            seed_text.encode(
                "utf-8"
            )
        ).hexdigest()

        number = int(
            digest[:16],
            16,
        )

        return priority_targets[
            number
            % len(priority_targets)
        ]

    # ============================================================
    # LATE JOINING
    # ============================================================

    def can_join_now(
        self,
        scene: dict[str, Any],
        role_id: str,
        world_state: dict[str, Any],
    ) -> bool:

        """
        A late player can join only if the story allows
        their character to enter the current/next part
        of the world.
        """

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

        if allowed_roles:
            if role_id not in allowed_roles:
                return False

        conditions = late_join.get(
            "conditions",
            [],
        )

        return self.conditions_match(
            conditions,
            world_state,
        )

    def late_join_entry_scene(
        self,
        current_scene: dict[str, Any],
        world_state: dict[str, Any],
    ) -> str | None:

        late_join = current_scene.get(
            "late_join",
            {}
        )

        return late_join.get(
            "entry_scene"
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

        state[
            "late_joiners"
        ].append(
            {
                "role_id": role_id,
                "joined_round": round_number,
            }
        )

        return state

    # ============================================================
    # NPC SUPPORT
    # ============================================================

    def player_missed(
        self,
        missed_count: int,
    ) -> bool:

        return (
            missed_count
            >= self.MAX_MISSED
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
    # TIMERS
    # ============================================================

    def timer_seconds(
        self,
        scene: dict[str, Any],
        default_seconds: int,
    ) -> int:

        value = scene.get(
            "timer_seconds"
        )

        if value is None:
            value = default_seconds

        try:
            value = int(value)
        except (
            TypeError,
            ValueError,
        ):
            value = default_seconds

        # Never allow absurd or invalid timers.
        return max(
            10,
            min(
                value,
                3600,
            ),
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

            conditions = ending.get(
                "conditions",
                [],
            )

            if self.conditions_match(
                conditions,
                world_state,
            ):
                return copy.deepcopy(
                    ending
                )

        return None

    # ============================================================
    # STORY FINGERPRINT
    # ============================================================

    def structural_fingerprint(
        self,
    ) -> str:

        """
        Used to detect duplicate generated story structures.

        Text alone is not enough.
        """

        structural = {
            "roles": sorted(
                self.roles.keys()
            ),
            "scenes": [],
        }

        for scene in self.story.get(
            "scenes",
            [],
        ):

            structural[
                "scenes"
            ].append(
                {
                    "id": scene.get(
                        "id"
                    ),
                    "eligible_roles": sorted(
                        scene.get(
                            "eligible_roles",
                            [],
                        )
                    ),
                    "choices": [
                        {
                            "id": choice.get(
                                "id"
                            ),
                            "next_scene": choice.get(
                                "next_scene"
                            ),
                            "effects": self._effect_signature(
                                choice.get(
                                    "effects",
                                    {},
                                )
                            ),
                        }
                        for choice in scene.get(
                            "choices",
                            [],
                        )
                    ],
                }
            )

        serialized = json.dumps(
            structural,
            sort_keys=True,
            separators=(
                ",",
                ":",
            ),
        )

        return hashlib.sha256(
            serialized.encode(
                "utf-8"
            )
        ).hexdigest()

    def _effect_signature(
        self,
        effects: dict[str, Any],
    ) -> Any:

        """
        Remove cosmetic text while retaining structural
        consequence information.
        """

        if not isinstance(
            effects,
            dict,
        ):
            return effects

        return {
            key: effects[key]
            for key in sorted(
                effects.keys()
            )
            if key
            not in {
                "text",
                "description",
            }
        }
