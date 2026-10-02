from __future__ import annotations

import hashlib
import json
import random
from typing import Any

from google import genai
from google.genai import types


class StoryGenerator:

    def __init__(
        self,
        api_key: str,
        model: str,
    ):
        self.client = genai.Client(
            api_key=api_key
        )

        self.model = model

    async def generate(
        self,
        player_count: int,
        previous_fingerprints: list[str] | None = None,
    ) -> tuple[dict[str, Any], str]:

        previous_fingerprints = previous_fingerprints or []

        prompt = f"""
Create a completely new short interactive Telegram group story.

Game name:
WHAT HAPPENS?

Players:
{player_count}

Important design rules:

1. This is a casual comedy game.
2. It must be fun to press buttons and discover consequences.
3. Do NOT create scoring.
4. Do NOT create coins.
5. Do NOT create XP.
6. Do NOT create levels.
7. Do NOT create rankings.
8. Do NOT create leaderboards.
9. Do NOT require strategy.
10. Do NOT require trivia.
11. Do NOT require knowledge.
12. Keep the story short.
13. The story should work in a Telegram group.
14. Roles are hidden from other players.
15. Each role gets decisions privately.
16. Public story events are visible to everybody.
17. Choices must have deterministic consequences.
18. Different choices must lead to different events.
19. Include branching.
20. Include a funny ending.
21. Make the situation absurd but understandable.
22. Avoid requiring real-world personal information.
23. Avoid political persuasion.
24. Avoid dangerous instructions.
25. Avoid sexual content.
26. Avoid hateful content.

The runtime engine will execute this story deterministically.

The AI must NOT be responsible for gameplay decisions after this response.

Create exactly 5 scenes.

Each scene must have:
- id
- public_text
- timer_seconds
- eligible_roles
- choices

Each choice must have:
- id
- label
- public_event
- next_scene
- effects

The effects object can contain simple values such as:
{{
    "chaos": 1,
    "suspicion": 0,
    "friendship": -1
}}

Do not put executable code in the story.

Create 4 roles.

Each role:
- id
- name
- secret_description

At least one scene must have different next_scene values depending on the choice.

The final scene must end the story.

Previous story fingerprints that should NOT be structurally copied:

{previous_fingerprints}

Return ONLY valid JSON.
"""

        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=1.0,
                response_mime_type="application/json",
            ),
        )

        raw = response.text

        story = json.loads(raw)

        self.validate_story(story)

        fingerprint = self.fingerprint(story)

        return story, fingerprint

    @staticmethod
    def validate_story(
        story: dict[str, Any],
    ) -> None:

        required = {
            "title",
            "roles",
            "scenes",
        }

        if not required.issubset(story):
            raise ValueError(
                "Story missing required fields"
            )

        roles = story["roles"]
        scenes = story["scenes"]

        if len(roles) != 4:
            raise ValueError(
                "Story must contain exactly 4 roles"
            )

        if len(scenes) != 5:
            raise ValueError(
                "Story must contain exactly 5 scenes"
            )

        role_ids = {
            role["id"]
            for role in roles
        }

        scene_ids = {
            scene["id"]
            for scene in scenes
        }

        for scene in scenes:
            if not scene.get("choices"):
                raise ValueError(
                    "Scene has no choices"
                )

            for choice in scene["choices"]:
                target = choice.get("next_scene")

                if target is not None and target not in scene_ids:
                    raise ValueError(
                        f"Invalid next_scene: {target}"
                    )

            for role in scene.get(
                "eligible_roles",
                [],
            ):
                if role not in role_ids:
                    raise ValueError(
                        f"Unknown role: {role}"
                    )

    @staticmethod
    def fingerprint(
        story: dict[str, Any],
    ) -> str:

        structural = {
            "title": story.get("title"),
            "roles": [
                role["id"]
                for role in story["roles"]
            ],
            "scenes": [
                {
                    "id": scene["id"],
                    "choice_ids": [
                        choice["id"]
                        for choice in scene["choices"]
                    ],
                    "targets": [
                        choice.get("next_scene")
                        for choice in scene["choices"]
                    ],
                }
                for scene in story["scenes"]
            ],
        }

        serialized = json.dumps(
            structural,
            sort_keys=True,
            separators=(",", ":"),
        )

        return hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()
