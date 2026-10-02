from __future__ import annotations

import copy
import hashlib
import json
from collections import deque
from typing import Any


class StoryValidationError(ValueError):
    """Raised when generated story content is invalid."""


class StoryValidator:
    """
    Strict semantic validator for Gemini-generated WHAT HAPPENS? stories.

    Pipeline:

        Gemini
          ↓
        Pydantic structured output
          ↓
        StoryValidator
          ↓
        validated story JSON
          ↓
        deterministic GameEngine

    This class never calls Gemini, Telegram, Supabase, or background jobs.
    """

    MIN_TIMER_SECONDS = 10
    MAX_TIMER_SECONDS = 3600

    MIN_TITLE_LENGTH = 1
    MAX_TITLE_LENGTH = 200

    MIN_TEXT_LENGTH = 1
    MAX_TEXT_LENGTH = 4000

    MIN_ROLES = 1
    MAX_ROLES = 50

    MIN_SCENES = 1
    MAX_SCENES = 200

    MIN_CHOICES_PER_SCENE = 1
    MAX_CHOICES_PER_SCENE = 50

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

    ALLOWED_EFFECT_KEYS = {
        "set_flags",
        "remove_flags",
        "set_variables",
        "add_variables",
        "relationships",
        "knowledge",
        "secrets",
        "character_state",
        "text",
        "description",
    }

    PLAYER_COUNT_FIELDS = (
        "minimum_players",
        "min_players",
    )

    MAX_PLAYER_COUNT_FIELDS = (
        "maximum_players",
        "max_players",
    )

    # ============================================================
    # PUBLIC API
    # ============================================================

    @classmethod
    def validate(
        cls,
        story: dict[str, Any],
        player_count: int | None = None,
    ) -> dict[str, Any]:
        """
        Validate a complete story and return a defensive copy.

        Nothing returned by Gemini should reach GameEngine without
        passing through this method.
        """

        if not isinstance(story, dict):
            cls.fail("Story must be a JSON object.")

        normalized = copy.deepcopy(story)

        cls._validate_top_level(normalized)

        roles = cls._validate_roles(normalized)
        scenes = cls._validate_scenes(normalized)

        role_ids = set(roles)
        scene_ids = set(scenes)

        cls._validate_first_scene(
            normalized,
            scene_ids,
        )

        cls._validate_scene_references(
            scenes,
            role_ids,
            scene_ids,
        )

        cls._validate_cross_references(
            normalized,
            roles,
            scenes,
        )

        cls._validate_story_graph(
            normalized,
            scenes,
        )

        cls._validate_player_count(
            normalized,
            roles,
            player_count,
        )

        cls._validate_late_join(
            scenes,
            role_ids,
            scene_ids,
        )

        cls._validate_endings(
            scenes,
        )

        return normalized

    @classmethod
    def structural_fingerprint(
        cls,
        story: dict[str, Any],
    ) -> str:
        """
        Produce a deterministic fingerprint.

        Textual flavor can change without necessarily creating a
        structurally different game. The fingerprint therefore focuses
        primarily on gameplay structure.
        """

        if not isinstance(story, dict):
            cls.fail("Cannot fingerprint a non-object story.")

        structural = {
            "first_scene_id": story.get("first_scene_id"),
            "roles": cls._fingerprint_roles(
                story.get("roles", [])
            ),
            "scenes": cls._fingerprint_scenes(
                story.get("scenes", [])
            ),
        }

        encoded = json.dumps(
            structural,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def fail(cls, message: str) -> None:
        raise StoryValidationError(message)

    # ============================================================
    # TOP LEVEL
    # ============================================================

    @classmethod
    def _validate_top_level(
        cls,
        story: dict[str, Any],
    ) -> None:
        required_fields = {
            "title",
            "roles",
            "scenes",
        }

        missing = sorted(
            field
            for field in required_fields
            if field not in story
        )

        if missing:
            cls.fail(
                "Story is missing required fields: "
                + ", ".join(missing)
            )

        title = story.get("title")

        if not isinstance(title, str):
            cls.fail("Story title must be a string.")

        title = title.strip()

        if not title:
            cls.fail("Story title cannot be empty.")

        if not (
            cls.MIN_TITLE_LENGTH
            <= len(title)
            <= cls.MAX_TITLE_LENGTH
        ):
            cls.fail(
                f"Story title must contain between "
                f"{cls.MIN_TITLE_LENGTH} and "
                f"{cls.MAX_TITLE_LENGTH} characters."
            )

        roles = story.get("roles")

        if not isinstance(roles, list):
            cls.fail("Story roles must be a list.")

        if not roles:
            cls.fail("Story must contain at least one role.")

        if len(roles) > cls.MAX_ROLES:
            cls.fail(
                f"Story contains too many roles: {len(roles)}."
            )

        scenes = story.get("scenes")

        if not isinstance(scenes, list):
            cls.fail("Story scenes must be a list.")

        if not scenes:
            cls.fail("Story must contain at least one scene.")

        if len(scenes) > cls.MAX_SCENES:
            cls.fail(
                f"Story contains too many scenes: {len(scenes)}."
            )

        first_scene_id = story.get("first_scene_id")

        if first_scene_id is not None:
            cls._require_id(
                first_scene_id,
                "first_scene_id",
            )

        for field_name in cls.PLAYER_COUNT_FIELDS:
            if field_name in story:
                cls._require_positive_integer(
                    story[field_name],
                    field_name,
                )

        for field_name in cls.MAX_PLAYER_COUNT_FIELDS:
            if field_name in story:
                cls._require_positive_integer(
                    story[field_name],
                    field_name,
                )

    # ============================================================
    # ROLES
    # ============================================================

    @classmethod
    def _validate_roles(
        cls,
        story: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        names: set[str] = set()

        for index, role in enumerate(story["roles"]):
            path = f"roles[{index}]"

            if not isinstance(role, dict):
                cls.fail(f"{path} must be an object.")

            role_id = role.get("id")

            cls._require_id(
                role_id,
                f"{path}.id",
            )

            if role_id in result:
                cls.fail(
                    f"Duplicate role id: {role_id}"
                )

            name = role.get("name")

            if not isinstance(name, str) or not name.strip():
                cls.fail(
                    f"{path}.name must be a non-empty string."
                )

            normalized_name = name.strip().casefold()

            if normalized_name in names:
                cls.fail(
                    f"Duplicate role name: {name}"
                )

            names.add(normalized_name)

            secret_description = role.get(
                "secret_description",
                role.get(
                    "private_information",
                    "",
                ),
            )

            if not isinstance(
                secret_description,
                str,
            ) or not secret_description.strip():
                cls.fail(
                    f"{path}.secret_description must be a "
                    "non-empty string."
                )

            playable = role.get(
                "playable",
                True,
            )

            if not isinstance(playable, bool):
                cls.fail(
                    f"{path}.playable must be boolean."
                )

            result[role_id] = role

        playable_count = sum(
            1
            for role in result.values()
            if role.get("playable", True)
        )

        if playable_count < 1:
            cls.fail(
                "Story must contain at least one playable role."
            )

        return result

    # ============================================================
    # SCENES
    # ============================================================

    @classmethod
    def _validate_scenes(
        cls,
        story: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        for index, scene in enumerate(story["scenes"]):
            path = f"scenes[{index}]"

            if not isinstance(scene, dict):
                cls.fail(f"{path} must be an object.")

            scene_id = scene.get("id")

            cls._require_id(
                scene_id,
                f"{path}.id",
            )

            if scene_id in result:
                cls.fail(
                    f"Duplicate scene id: {scene_id}"
                )

            public_text = scene.get(
                "public_text",
                scene.get(
                    "description",
                    "",
                ),
            )

            if not isinstance(
                public_text,
                str,
            ) or not public_text.strip():
                cls.fail(
                    f"{path}.public_text must be a "
                    "non-empty string."
                )

            if len(public_text) > cls.MAX_TEXT_LENGTH:
                cls.fail(
                    f"{path}.public_text exceeds "
                    f"{cls.MAX_TEXT_LENGTH} characters."
                )

            timer_seconds = scene.get(
                "timer_seconds"
            )

            cls._require_integer(
                timer_seconds,
                f"{path}.timer_seconds",
            )

            if not (
                cls.MIN_TIMER_SECONDS
                <= timer_seconds
                <= cls.MAX_TIMER_SECONDS
            ):
                cls.fail(
                    f"{path}.timer_seconds must be between "
                    f"{cls.MIN_TIMER_SECONDS} and "
                    f"{cls.MAX_TIMER_SECONDS}."
                )

            choices = scene.get("choices")

            if not isinstance(choices, list):
                cls.fail(
                    f"{path}.choices must be a list."
                )

            if not (
                cls.MIN_CHOICES_PER_SCENE
                <= len(choices)
                <= cls.MAX_CHOICES_PER_SCENE
            ):
                cls.fail(
                    f"{path}.choices must contain between "
                    f"{cls.MIN_CHOICES_PER_SCENE} and "
                    f"{cls.MAX_CHOICES_PER_SCENE} choices."
                )

            eligible_roles = scene.get(
                "eligible_roles",
                [],
            )

            if not isinstance(
                eligible_roles,
                list,
            ):
                cls.fail(
                    f"{path}.eligible_roles must be a list."
                )

            seen_roles: set[str] = set()

            for role_index, role_id in enumerate(
                eligible_roles
            ):
                cls._require_id(
                    role_id,
                    f"{path}.eligible_roles[{role_index}]",
                )

                if role_id in seen_roles:
                    cls.fail(
                        f"{path}.eligible_roles contains "
                        f"duplicate role: {role_id}"
                    )

                seen_roles.add(role_id)

            transitions = scene.get(
                "transitions",
                [],
            )

            if not isinstance(
                transitions,
                list,
            ):
                cls.fail(
                    f"{path}.transitions must be a list."
                )

            ending = scene.get("ending")

            if ending is not None:
                cls._validate_ending(
                    ending,
                    path,
                )

            result[scene_id] = scene

        return result

    # ============================================================
    # FIRST SCENE
    # ============================================================

    @classmethod
    def _validate_first_scene(
        cls,
        story: dict[str, Any],
        scene_ids: set[str],
    ) -> None:
        first_scene_id = story.get(
            "first_scene_id"
        )

        if first_scene_id is None:
            # Compatibility rule:
            # when omitted, the first listed scene is used.
            if not story["scenes"]:
                cls.fail(
                    "Story has no scenes."
                )

            return

        if first_scene_id not in scene_ids:
            cls.fail(
                "first_scene_id references unknown scene: "
                f"{first_scene_id}"
            )

    # ============================================================
    # CHOICE / TRANSITION REFERENCES
    # ============================================================

    @classmethod
    def _validate_scene_references(
        cls,
        scenes: dict[str, dict[str, Any]],
        role_ids: set[str],
        scene_ids: set[str],
    ) -> None:
        global_choice_ids: set[str] = set()

        for scene_id, scene in scenes.items():
            path = f"scene[{scene_id}]"

            for role_id in scene.get(
                "eligible_roles",
                [],
            ):
                if role_id not in role_ids:
                    cls.fail(
                        f"{path}.eligible_roles references "
                        f"unknown role: {role_id}"
                    )

            for choice_index, choice in enumerate(
                scene.get("choices", [])
            ):
                choice_path = (
                    f"{path}.choices[{choice_index}]"
                )

                if not isinstance(
                    choice,
                    dict,
                ):
                    cls.fail(
                        f"{choice_path} must be an object."
                    )

                choice_id = choice.get("id")

                cls._require_id(
                    choice_id,
                    f"{choice_path}.id",
                )

                if choice_id in global_choice_ids:
                    cls.fail(
                        "Duplicate choice id across story: "
                        f"{choice_id}"
                    )

                global_choice_ids.add(choice_id)

                label = choice.get("label")

                if not isinstance(
                    label,
                    str,
                ) or not label.strip():
                    cls.fail(
                        f"{choice_path}.label must be a "
                        "non-empty string."
                    )

                public_event = choice.get(
                    "public_event",
                    "",
                )

                if not isinstance(
                    public_event,
                    str,
                ):
                    cls.fail(
                        f"{choice_path}.public_event must be a string."
                    )

                next_scene = choice.get(
                    "next_scene"
                )

                if next_scene is not None:
                    cls._require_id(
                        next_scene,
                        f"{choice_path}.next_scene",
                    )

                    if next_scene not in scene_ids:
                        cls.fail(
                            f"{choice_path}.next_scene references "
                            f"unknown scene: {next_scene}"
                        )

                effects = choice.get(
                    "effects",
                    {},
                )

                cls._validate_effects(
                    effects,
                    role_ids,
                    choice_path,
                )

                conditions = choice.get(
                    "conditions",
                    [],
                )

                cls._validate_conditions(
                    conditions,
                    role_ids,
                    choice_path,
                )

                variants = choice.get(
                    "public_event_variants"
                )

                if variants is not None:
                    if not isinstance(
                        variants,
                        list,
                    ) or not variants:
                        cls.fail(
                            f"{choice_path}.public_event_variants "
                            "must be a non-empty list."
                        )

                    for variant_index, variant in enumerate(
                        variants
                    ):
                        if not isinstance(
                            variant,
                            str,
                        ) or not variant.strip():
                            cls.fail(
                                f"{choice_path}.public_event_variants"
                                f"[{variant_index}] must be a "
                                "non-empty string."
                            )

                branch_priority = choice.get(
                    "branch_priority",
                    0,
                )

                cls._require_integer(
                    branch_priority,
                    f"{choice_path}.branch_priority",
                )

            for transition_index, transition in enumerate(
                scene.get("transitions", [])
            ):
                transition_path = (
                    f"{path}.transitions[{transition_index}]"
                )

                if not isinstance(
                    transition,
                    dict,
                ):
                    cls.fail(
                        f"{transition_path} must be an object."
                    )

                next_scene = transition.get(
                    "next_scene"
                )

                cls._require_id(
                    next_scene,
                    f"{transition_path}.next_scene",
                )

                if next_scene not in scene_ids:
                    cls.fail(
                        f"{transition_path}.next_scene references "
                        f"unknown scene: {next_scene}"
                    )

                cls._validate_conditions(
                    transition.get(
                        "conditions",
                        [],
                    ),
                    role_ids,
                    transition_path,
                )

    # ============================================================
    # EFFECTS
    # ============================================================

    @classmethod
    def _validate_effects(
        cls,
        effects: Any,
        role_ids: set[str],
        path: str,
    ) -> None:
        if effects is None:
            return

        if not isinstance(
            effects,
            dict,
        ):
            cls.fail(
                f"{path}.effects must be an object."
            )

        unknown_keys = set(effects) - cls.ALLOWED_EFFECT_KEYS

        if unknown_keys:
            cls.fail(
                f"{path}.effects contains unsupported keys: "
                + ", ".join(sorted(unknown_keys))
            )

        set_flags = effects.get(
            "set_flags",
            {},
        )

        if not isinstance(
            set_flags,
            dict,
        ):
            cls.fail(
                f"{path}.effects.set_flags must be an object."
            )

        remove_flags = effects.get(
            "remove_flags",
            [],
        )

        if not isinstance(
            remove_flags,
            list,
        ):
            cls.fail(
                f"{path}.effects.remove_flags must be a list."
            )

        set_variables = effects.get(
            "set_variables",
            {},
        )

        if not isinstance(
            set_variables,
            dict,
        ):
            cls.fail(
                f"{path}.effects.set_variables must be an object."
            )

        add_variables = effects.get(
            "add_variables",
            {},
        )

        if not isinstance(
            add_variables,
            dict,
        ):
            cls.fail(
                f"{path}.effects.add_variables must be an object."
            )

        for key, value in add_variables.items():
            if not isinstance(
                value,
                (int, float),
            ) or isinstance(
                value,
                bool,
            ):
                cls.fail(
                    f"{path}.effects.add_variables[{key!r}] "
                    "must be numeric."
                )

        relationships = effects.get(
            "relationships",
            [],
        )

        if not isinstance(
            relationships,
            list,
        ):
            cls.fail(
                f"{path}.effects.relationships must be a list."
            )

        for index, relation in enumerate(
            relationships
        ):
            relation_path = (
                f"{path}.effects.relationships[{index}]"
            )

            if not isinstance(
                relation,
                dict,
            ):
                cls.fail(
                    f"{relation_path} must be an object."
                )

            role_a = relation.get("role_a")
            role_b = relation.get("role_b")

            cls._require_id(
                role_a,
                f"{relation_path}.role_a",
            )

            cls._require_id(
                role_b,
                f"{relation_path}.role_b",
            )

            if role_a not in role_ids:
                cls.fail(
                    f"{relation_path}.role_a references "
                    f"unknown role: {role_a}"
                )

            if role_b not in role_ids:
                cls.fail(
                    f"{relation_path}.role_b references "
                    f"unknown role: {role_b}"
                )

            amount = relation.get(
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
                cls.fail(
                    f"{relation_path}.amount must be numeric."
                )

        knowledge = effects.get(
            "knowledge",
            [],
        )

        if not isinstance(
            knowledge,
            list,
        ):
            cls.fail(
                f"{path}.effects.knowledge must be a list."
            )

        for index, item in enumerate(
            knowledge
        ):
            item_path = (
                f"{path}.effects.knowledge[{index}]"
            )

            if not isinstance(
                item,
                dict,
            ):
                cls.fail(
                    f"{item_path} must be an object."
                )

            role_id = item.get("role_id")

            cls._require_id(
                role_id,
                f"{item_path}.role_id",
            )

            if role_id not in role_ids:
                cls.fail(
                    f"{item_path}.role_id references "
                    f"unknown role: {role_id}"
                )

            knowledge_text = item.get(
                "knowledge"
            )

            if not isinstance(
                knowledge_text,
                str,
            ) or not knowledge_text.strip():
                cls.fail(
                    f"{item_path}.knowledge must be non-empty."
                )

        secrets = effects.get(
            "secrets",
            [],
        )

        if not isinstance(
            secrets,
            list,
        ):
            cls.fail(
                f"{path}.effects.secrets must be a list."
            )

        for index, secret in enumerate(
            secrets
        ):
            secret_path = (
                f"{path}.effects.secrets[{index}]"
            )

            if not isinstance(
                secret,
                dict,
            ):
                cls.fail(
                    f"{secret_path} must be an object."
                )

            secret_id = secret.get(
                "id"
            )

            cls._require_id(
                secret_id,
                f"{secret_path}.id",
            )

            owner = secret.get(
                "owner"
            )

            if owner is not None:
                cls._require_id(
                    owner,
                    f"{secret_path}.owner",
                )

                if owner not in role_ids:
                    cls.fail(
                        f"{secret_path}.owner references "
                        f"unknown role: {owner}"
                    )

            revealed_to = secret.get(
                "revealed_to",
                [],
            )

            if not isinstance(
                revealed_to,
                list,
            ):
                cls.fail(
                    f"{secret_path}.revealed_to must be a list."
                )

            for role_id in revealed_to:
                cls._require_id(
                    role_id,
                    f"{secret_path}.revealed_to",
                )

                if role_id not in role_ids:
                    cls.fail(
                        f"{secret_path}.revealed_to references "
                        f"unknown role: {role_id}"
                    )

        for key in (
            "text",
            "description",
        ):
            value = effects.get(key)

            if value is not None and not isinstance(
                value,
                str,
            ):
                cls.fail(
                    f"{path}.effects.{key} must be a string."
                )

    # ============================================================
    # CONDITIONS
    # ============================================================

    @classmethod
    def _validate_conditions(
        cls,
        conditions: Any,
        role_ids: set[str],
        path: str,
    ) -> None:
        if conditions is None:
            return

        if not isinstance(
            conditions,
            list,
        ):
            cls.fail(
                f"{path}.conditions must be a list."
            )

        for index, condition in enumerate(
            conditions
        ):
            condition_path = (
                f"{path}.conditions[{index}]"
            )

            if not isinstance(
                condition,
                dict,
            ):
                cls.fail(
                    f"{condition_path} must be an object."
                )

            condition_type = condition.get(
                "type"
            )

            if condition_type not in (
                cls.ALLOWED_CONDITION_TYPES
            ):
                cls.fail(
                    f"{condition_path}.type must be one of: "
                    + ", ".join(
                        sorted(
                            cls.ALLOWED_CONDITION_TYPES
                        )
                    )
                )

            operator = condition.get(
                "operator",
                "eq",
            )

            if operator not in cls.ALLOWED_OPERATORS:
                cls.fail(
                    f"{condition_path}.operator is unsupported: "
                    f"{operator}"
                )

            role_id = condition.get(
                "role_id"
            )

            if role_id is not None:
                cls._require_id(
                    role_id,
                    f"{condition_path}.role_id",
                )

                if role_id not in role_ids:
                    cls.fail(
                        f"{condition_path}.role_id references "
                        f"unknown role: {role_id}"
                    )

            for field_name in (
                "role_a",
                "role_b",
            ):
                value = condition.get(
                    field_name
                )

                if value is not None:
                    cls._require_id(
                        value,
                        f"{condition_path}.{field_name}",
                    )

                    if value not in role_ids:
                        cls.fail(
                            f"{condition_path}.{field_name} references "
                            f"unknown role: {value}"
                        )

            if condition_type == "relationship":
                if not condition.get("role_a"):
                    cls.fail(
                        f"{condition_path} relationship condition "
                        "requires role_a."
                    )

                if not condition.get("role_b"):
                    cls.fail(
                        f"{condition_path} relationship condition "
                        "requires role_b."
                    )

            if condition_type in {
                "flag",
                "variable",
                "knowledge",
            }:
                name = condition.get(
                    "name"
                )

                if not isinstance(
                    name,
                    str,
                ) or not name.strip():
                    cls.fail(
                        f"{condition_path}.name must be non-empty "
                        f"for condition type {condition_type}."
                    )

    # ============================================================
    # ENDINGS
    # ============================================================

    @classmethod
    def _validate_ending(
        cls,
        ending: Any,
        path: str,
    ) -> None:
        if isinstance(
            ending,
            str,
        ):
            if not ending.strip():
                cls.fail(
                    f"{path}.ending cannot be empty."
                )
            return

        if not isinstance(
            ending,
            dict,
        ):
            cls.fail(
                f"{path}.ending must be a string or object."
            )

        ending_id = ending.get(
            "id"
        )

        if ending_id is not None:
            cls._require_id(
                ending_id,
                f"{path}.ending.id",
            )

        text = ending.get(
            "text"
        )

        if not isinstance(
            text,
            str,
        ) or not text.strip():
            cls.fail(
                f"{path}.ending.text must be non-empty."
            )

        conditions = ending.get(
            "conditions",
            [],
        )

        if not isinstance(
            conditions,
            list,
        ):
            cls.fail(
                f"{path}.ending.conditions must be a list."
            )

    @classmethod
    def _validate_endings(
        cls,
        scenes: dict[str, dict[str, Any]],
    ) -> None:
        ending_count = 0

        for scene_id, scene in scenes.items():
            ending = scene.get(
                "ending"
            )

            explicit_terminal = bool(
                scene.get(
                    "is_ending",
                    False,
                )
            ) or bool(
                scene.get(
                    "terminal",
                    False,
                )
            )

            if ending is not None or explicit_terminal:
                ending_count += 1

        if ending_count == 0:
            cls.fail(
                "Story must contain at least one ending scene."
            )

    # ============================================================
    # LATE JOIN
    # ============================================================

    @classmethod
    def _validate_late_join(
        cls,
        scenes: dict[str, dict[str, Any]],
        role_ids: set[str],
        scene_ids: set[str],
    ) -> None:
        for scene_id, scene in scenes.items():
            path = f"scene[{scene_id}].late_join"

            late_join = scene.get(
                "late_join",
                {},
            )

            if not isinstance(
                late_join,
                dict,
            ):
                cls.fail(
                    f"{path} must be an object."
                )

            allowed = late_join.get(
                "allowed",
                True,
            )

            if not isinstance(
                allowed,
                bool,
            ):
                cls.fail(
                    f"{path}.allowed must be boolean."
                )

            roles = late_join.get(
                "roles",
                [],
            )

            if not isinstance(
                roles,
                list,
            ):
                cls.fail(
                    f"{path}.roles must be a list."
                )

            if len(roles) != len(
                set(roles)
            ):
                cls.fail(
                    f"{path}.roles contains duplicate role ids."
                )

            for role_id in roles:
                cls._require_id(
                    role_id,
                    f"{path}.roles",
                )

                if role_id not in role_ids:
                    cls.fail(
                        f"{path}.roles references unknown role: "
                        f"{role_id}"
                    )

            entry_scene = late_join.get(
                "entry_scene"
            )

            if entry_scene is not None:
                cls._require_id(
                    entry_scene,
                    f"{path}.entry_scene",
                )

                if entry_scene not in scene_ids:
                    cls.fail(
                        f"{path}.entry_scene references unknown scene: "
                        f"{entry_scene}"
                    )

            cls._validate_conditions(
                late_join.get(
                    "conditions",
                    [],
                ),
                role_ids,
                path,
            )

    # ============================================================
    # CROSS REFERENCES
    # ============================================================

    @classmethod
    def _validate_cross_references(
        cls,
        story: dict[str, Any],
        roles: dict[str, dict[str, Any]],
        scenes: dict[str, dict[str, Any]],
    ) -> None:
        role_ids = set(roles)
        scene_ids = set(scenes)

        endings = story.get(
            "endings",
            [],
        )

        if isinstance(
            endings,
            list,
        ):
            ending_ids: set[str] = set()

            for index, ending in enumerate(endings):
                if isinstance(
                    ending,
                    str,
                ):
                    ending_id = ending
                elif isinstance(
                    ending,
                    dict,
                ):
                    ending_id = ending.get(
                        "id"
                    )
                else:
                    cls.fail(
                        f"endings[{index}] must be an object or string."
                    )

                cls._require_id(
                    ending_id,
                    f"endings[{index}]",
                )

                if ending_id in ending_ids:
                    cls.fail(
                        f"Duplicate ending id: {ending_id}"
                    )

                ending_ids.add(ending_id)

        elif isinstance(
            endings,
            dict,
        ):
            for ending_id, ending in endings.items():
                cls._require_id(
                    ending_id,
                    "endings"
                )

                if not isinstance(
                    ending,
                    dict,
                ):
                    cls.fail(
                        f"endings[{ending_id}] must be an object."
                    )

        elif endings:
            cls.fail(
                "Story endings must be a list or object."
            )

        # Validate optional global references.
        initial_scene = story.get(
            "first_scene_id"
        )

        if initial_scene is not None and initial_scene not in scene_ids:
            cls.fail(
                f"first_scene_id references unknown scene: "
                f"{initial_scene}"
            )

        playable_roles = [
            role_id
            for role_id, role in roles.items()
            if role.get("playable", True)
        ]

        if not playable_roles:
            cls.fail(
                "No playable roles exist."
            ]

    # ============================================================
    # GRAPH VALIDATION
    # ============================================================

    @classmethod
    def _validate_story_graph(
        cls,
        story: dict[str, Any],
        scenes: dict[str, dict[str, Any]],
    ) -> None:
        if not scenes:
            cls.fail(
                "Story has no scenes."
            )

        first_scene_id = story.get(
            "first_scene_id"
        )

        if first_scene_id is None:
            first_scene_id = next(
                iter(scenes)
            )

        # Build directed graph.
        graph: dict[str, set[str]] = {
            scene_id: set()
            for scene_id in scenes
        }

        terminal_scenes: set[str] = set()

        for scene_id, scene in scenes.items():
            if (
                scene.get("ending") is not None
                or scene.get("is_ending") is True
                or scene.get("terminal") is True
            ):
                terminal_scenes.add(scene_id)

            for choice in scene.get(
                "choices",
                [],
            ):
                next_scene = choice.get(
                    "next_scene"
                )

                if next_scene is not None:
                    graph[scene_id].add(
                        next_scene
                    )

            for transition in scene.get(
                "transitions",
                [],
            ):
                next_scene = transition.get(
                    "next_scene"
                )

                if next_scene is not None:
                    graph[scene_id].add(
                        next_scene
                    )

        if not terminal_scenes:
            cls.fail(
                "Story graph contains no terminal scene."
            )

        # Reachability from first scene.
        reachable: set[str] = set()
        queue: deque[str] = deque(
            [first_scene_id]
        )

        while queue:
            current = queue.popleft()

            if current in reachable:
                continue

            reachable.add(current)

            for target in graph.get(
                current,
                set(),
            ):
                if target not in reachable:
                    queue.append(target)

        unreachable = set(scenes) - reachable

        if unreachable:
            cls.fail(
                "Story contains unreachable scenes: "
                + ", ".join(sorted(unreachable))
            )

        # Every reachable scene must be able to eventually reach
        # a terminal scene.
        reverse_graph: dict[str, set[str]] = {
            scene_id: set()
            for scene_id in scenes
        }

        for source, targets in graph.items():
            for target in targets:
                reverse_graph[target].add(
                    source
                )

        can_reach_ending: set[str] = set(
            terminal_scenes
        )

        queue = deque(
            terminal_scenes
        )

        while queue:
            current = queue.popleft()

            for previous in reverse_graph.get(
                current,
                set(),
            ):
                if previous not in can_reach_ending:
                    can_reach_ending.add(
                        previous
                    )
                    queue.append(
                        previous
                    )

        dead_end_scenes = (
            reachable - can_reach_ending
        )

        if dead_end_scenes:
            cls.fail(
                "Reachable scenes cannot reach an ending: "
                + ", ".join(
                    sorted(dead_end_scenes)
                )
            )

    # ============================================================
    # PLAYER COUNT
    # ============================================================

    @classmethod
    def _validate_player_count(
        cls,
        story: dict[str, Any],
        roles: dict[str, dict[str, Any]],
        player_count: int | None,
    ) -> None:
        if player_count is None:
            return

        if not isinstance(
            player_count,
            int,
        ) or isinstance(
            player_count,
            bool,
        ):
            cls.fail(
                "player_count must be an integer."
            )

        if player_count < 1:
            cls.fail(
                "player_count must be at least 1."
            )

        playable_count = sum(
            1
            for role in roles.values()
            if role.get("playable", True)
        )

        if playable_count < player_count:
            cls.fail(
                f"Story provides {playable_count} playable roles "
                f"but requires {player_count} players."
            )

        minimum = None

        for field_name in cls.PLAYER_COUNT_FIELDS:
            if field_name in story:
                minimum = int(
                    story[field_name]
                )
                break

        maximum = None

        for field_name in cls.MAX_PLAYER_COUNT_FIELDS:
            if field_name in story:
                maximum = int(
                    story[field_name]
                )
                break

        if minimum is not None and player_count < minimum:
            cls.fail(
                f"player_count {player_count} is below "
                f"story minimum {minimum}."
            )

        if maximum is not None and player_count > maximum:
            cls.fail(
                f"player_count {player_count} exceeds "
                f"story maximum {maximum}."
            )

    # ============================================================
    # SMALL VALIDATION HELPERS
    # ============================================================

    @classmethod
    def _require_id(
        cls,
        value: Any,
        path: str,
    ) -> str:
        if not isinstance(
            value,
            str,
        ):
            cls.fail(
                f"{path} must be a string."
            )

        value = value.strip()

        if not value:
            cls.fail(
                f"{path} cannot be empty."
            )

        if len(value) > 200:
            cls.fail(
                f"{path} is too long."
            )

        return value

    @classmethod
    def _require_integer(
        cls,
        value: Any,
        path: str,
    ) -> int:
        if not isinstance(
            value,
            int,
        ) or isinstance(
            value,
            bool,
        ):
            cls.fail(
                f"{path} must be an integer."
            )

        return value

    @classmethod
    def _require_positive_integer(
        cls,
        value: Any,
        path: str,
    ) -> int:
        value = cls._require_integer(
            value,
            path,
        )

        if value < 1:
            cls.fail(
                f"{path} must be >= 1."
            )

        return value

    # ============================================================
    # FINGERPRINT HELPERS
    # ============================================================

    @classmethod
    def _fingerprint_roles(
        cls,
        roles: Any,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        if not isinstance(
            roles,
            list,
        ):
            return result

        for role in roles:
            if not isinstance(
                role,
                dict,
            ):
                continue

            result.append(
                {
                    "id": role.get("id"),
                    "playable": role.get(
                        "playable",
                        True,
                    ),
                }
            )

        return sorted(
            result,
            key=lambda item: str(
                item["id"]
            ),
        )

    @classmethod
    def _fingerprint_scenes(
        cls,
        scenes: Any,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        if not isinstance(
            scenes,
            list,
        ):
            return result

        for scene in scenes:
            if not isinstance(
                scene,
                dict,
            ):
                continue

            choices = []

            for choice in scene.get(
                "choices",
                [],
            ):
                if not isinstance(
                    choice,
                    dict,
                ):
                    continue

                choices.append(
                    {
                        "id": choice.get("id"),
                        "next_scene": choice.get(
                            "next_scene"
                        ),
                        "conditions": choice.get(
                            "conditions",
                            [],
                        ),
                    }
                )

            result.append(
                {
                    "id": scene.get("id"),
                    "timer_seconds": scene.get(
                        "timer_seconds"
                    ),
                    "eligible_roles": sorted(
                        scene.get(
                            "eligible_roles",
                            [],
                        )
                    ),
                    "choices": sorted(
                        choices,
                        key=lambda item: str(
                            item["id"]
                        ),
                    ),
                    "has_ending": bool(
                        scene.get("ending") is not None
                        or scene.get("is_ending") is True
                        or scene.get("terminal") is True
                    ),
                }
            )

        return sorted(
            result,
            key=lambda item: str(
                item["id"]
            ),
        )
