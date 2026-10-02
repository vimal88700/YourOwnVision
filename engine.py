from __future__ import annotations

import hashlib
from typing import Any


class GameEngine:

    MAX_MISSED = 3

    def __init__(self, story: dict[str, Any]):
        self.story = story

        self.scenes = {
            scene["id"]: scene
            for scene in story["scenes"]
        }

        self.roles = {
            role["id"]: role
            for role in story["roles"]
        }

    def first_scene(self) -> dict:
        return self.story["scenes"][0]

    def get_scene(
        self,
        scene_id: str,
    ) -> dict:

        if scene_id not in self.scenes:
            raise ValueError(
                f"Unknown scene: {scene_id}"
            )

        return self.scenes[scene_id]

    def get_role(
        self,
        role_id: str,
    ) -> dict:

        return self.roles[role_id]

    def choices_for_role(
        self,
        scene: dict,
        role_id: str,
    ) -> list[dict]:

        choices = scene.get("choices", [])

        # If the scene specifies roles, filter them.
        eligible = scene.get(
            "eligible_roles",
            [],
        )

        if eligible and role_id not in eligible:
            return []

        return choices

    def resolve_choice(
        self,
        scene: dict,
        choice_id: str,
        *,
        round_number: int,
    ) -> dict:

        choice = next(
            (
                item
                for item in scene["choices"]
                if item["id"] == choice_id
            ),
            None,
        )

        if choice is None:
            raise ValueError(
                "Invalid choice"
            )

        effects = choice.get(
            "effects",
            {},
        )

        seed_text = (
            f"{scene['id']}|"
            f"{choice_id}|"
            f"{round_number}"
        )

        seed = int(
            hashlib.sha256(
                seed_text.encode()
            ).hexdigest()[:12],
            16,
        )

        # Deterministic pseudo-random number.
        # It never calls an external API.
        variant = seed % 3

        public_event = choice[
            "public_event"
        ]

        # Optional deterministic variant
        # support.
        variants = choice.get(
            "public_event_variants"
        )

        if variants:
            public_event = variants[
                variant % len(variants)
            ]

        return {
            "choice_id": choice_id,
            "public_event": public_event,
            "next_scene": choice.get(
                "next_scene"
            ),
            "effects": effects,
        }

    def next_scene_from_results(
        self,
        current_scene: dict,
        results: list[dict],
    ) -> str | None:

        targets = [
            result["next_scene"]
            for result in results
            if result.get("next_scene")
        ]

        if not targets:
            return None

        # Deterministic branch resolution.
        #
        # We don't let the last person to click
        # control the story.
        combined = "|".join(
            sorted(targets)
        )

        number = int(
            hashlib.sha256(
                combined.encode()
            ).hexdigest()[:8],
            16,
        )

        return targets[
            number % len(targets)
        ]

    @staticmethod
    def missed_player(
        missed_count: int,
    ) -> bool:

        return missed_count >= GameEngine.MAX_MISSED
