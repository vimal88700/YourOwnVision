from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from story_validator import StoryValidationError, StoryValidator


logger = logging.getLogger("YourOwnVision.story_generator")


# ============================================================
# GEMINI OUTPUT MODELS
# ============================================================

class GeneratedSecret(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    owner: str | None = None
    revealed: bool = False
    revealed_to: list[str] = Field(default_factory=list)


class GeneratedKnowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str
    knowledge: str
    value: bool = True


class GeneratedRelationshipEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_a: str
    role_b: str
    amount: int | float = 0


class GeneratedEffects(BaseModel):
    model_config = ConfigDict(extra="forbid")

    set_flags: dict[str, Any] = Field(default_factory=dict)
    remove_flags: list[str] = Field(default_factory=list)
    set_variables: dict[str, Any] = Field(default_factory=dict)
    add_variables: dict[str, int | float] = Field(default_factory=dict)

    relationships: list[GeneratedRelationshipEffect] = Field(
        default_factory=list
    )

    knowledge: list[GeneratedKnowledge] = Field(
        default_factory=list
    )

    secrets: list[GeneratedSecret] = Field(
        default_factory=list
    )

    character_state: dict[str, Any] = Field(
        default_factory=dict
    )

    text: str | None = None
    description: str | None = None


class GeneratedCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    name: str | None = None
    value: Any = None
    operator: str = "eq"

    role_id: str | None = None
    knowledge: str | None = None

    role_a: str | None = None
    role_b: str | None = None


class GeneratedChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    public_event: str = ""

    next_scene: str | None = None

    effects: GeneratedEffects = Field(
        default_factory=GeneratedEffects
    )

    conditions: list[GeneratedCondition] = Field(
        default_factory=list
    )

    branch_priority: int = 0

    public_event_variants: list[str] | None = None


class GeneratedTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_scene: str

    conditions: list[GeneratedCondition] = Field(
        default_factory=list
    )


class GeneratedEnding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str

    conditions: list[GeneratedCondition] = Field(
        default_factory=list
    )


class GeneratedLateJoin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool = True

    roles: list[str] = Field(
        default_factory=list
    )

    conditions: list[GeneratedCondition] = Field(
        default_factory=list
    )

    entry_scene: str | None = None


class GeneratedRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    secret_description: str

    playable: bool = True


class GeneratedScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    public_text: str
    timer_seconds: int

    eligible_roles: list[str] = Field(
        default_factory=list
    )

    choices: list[GeneratedChoice]

    transitions: list[GeneratedTransition] = Field(
        default_factory=list
    )

    ending: GeneratedEnding | str | None = None

    late_join: GeneratedLateJoin = Field(
        default_factory=GeneratedLateJoin
    )


class GeneratedStory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str

    first_scene_id: str | None = None

    roles: list[GeneratedRole]

    scenes: list[GeneratedScene]


# ============================================================
# STORY GENERATOR
# ============================================================

class StoryGenerator:
    """
    Generates story content with Gemini.

    Gemini generates story data only.
    Gameplay is handled by GameEngine.
    """

    # Primary model.
    DEFAULT_MODEL = "gemini-3.8-flash"

    # Safe fallback chain.
    FALLBACK_MODELS = (
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    )

    # Retry settings.
    MAX_RETRIES_PER_REQUEST = 4
    INITIAL_RETRY_DELAY_SECONDS = 2.0
    MAX_RETRY_DELAY_SECONDS = 20.0

    MIN_PLAYERS = 1
    MAX_PLAYERS = 20

    MIN_SCENES = 3
    MAX_SCENES = 12

    MIN_TIMER_SECONDS = 10
    MAX_TIMER_SECONDS = 3600

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
    ) -> None:

        if not api_key or not api_key.strip():
            raise ValueError(
                "Gemini API key is required."
            )

        self.model = (
            model or self.DEFAULT_MODEL
        ).strip()

        if not self.model:
            raise ValueError(
                "Gemini model cannot be empty."
            )

        # Remove accidental models/ prefix.
        self.model = self.model.removeprefix("models/")

        self.client = genai.Client(
            api_key=api_key.strip()
        )

    # ========================================================
    # PUBLIC GENERATE METHOD
    # ========================================================

    async def generate(
        self,
        player_count: int,
        previous_fingerprints: list[str] | None = None,
    ) -> tuple[dict[str, Any], str]:

        self._validate_player_count(player_count)

        fingerprints = [
            value.strip()
            for value in (previous_fingerprints or [])
            if isinstance(value, str) and value.strip()
        ]

        prompt = self._build_prompt(
            player_count,
            fingerprints,
        )

        last_api_error: Exception | None = None

        response = None

        models_to_try = self._get_models_to_try()

        # ====================================================
        # ATTEMPT 1:
        # STRUCTURED JSON + PYDANTIC SCHEMA
        # ====================================================

        for model_name in models_to_try:
            try:
                response = await self._generate_with_retry(
                    model_name=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=GeneratedStory,
                        system_instruction=(
                            "You are the story-content generator "
                            "for the WHAT HAPPENS? Telegram game. "
                            "Generate story data only. "
                            "Never generate executable code, "
                            "Telegram API calls, SQL, or gameplay "
                            "decisions."
                        ),
                    ),
                    mode="structured",
                )

                self.model = model_name

                logger.info(
                    "Gemini structured generation succeeded "
                    "using model=%s",
                    model_name,
                )

                break

            except Exception as exc:
                last_api_error = exc

                logger.warning(
                    "Structured Gemini generation failed "
                    "for model=%s: %s",
                    model_name,
                    exc,
                )

        # ====================================================
        # ATTEMPT 2:
        # PLAIN JSON FALLBACK
        # ====================================================

        if response is None:

            for model_name in models_to_try:

                try:
                    response = await self._generate_with_retry(
                        model_name=model_name,
                        contents=(
                            prompt
                            + "\n\n"
                            "IMPORTANT:\n"
                            "Return ONLY one valid JSON object.\n"
                            "Do NOT use Markdown fences.\n"
                            "Do NOT add explanations.\n"
                            "Do NOT add commentary."
                        ),
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            system_instruction=(
                                "Return only one valid JSON "
                                "object matching the requested "
                                "story structure. "
                                "No Markdown. "
                                "No commentary."
                            ),
                        ),
                        mode="plain-json",
                    )

                    self.model = model_name

                    logger.info(
                        "Gemini plain JSON generation succeeded "
                        "using model=%s",
                        model_name,
                    )

                    break

                except Exception as exc:
                    last_api_error = exc

                    logger.warning(
                        "Gemini JSON fallback failed "
                        "for model=%s: %s",
                        model_name,
                        exc,
                    )

        # ====================================================
        # COMPLETE GEMINI FAILURE
        # ====================================================

        if response is None:

            detail = (
                str(last_api_error).strip()
                if last_api_error
                else "Unknown Gemini API error."
            )

            if not detail:
                detail = "Unknown Gemini API error."

            if len(detail) > 1000:
                detail = detail[:1000] + "..."

            raise RuntimeError(
                "Gemini story generation failed: "
                f"{detail}"
            ) from last_api_error

        # ====================================================
        # READ RESPONSE
        # ====================================================

        parsed = getattr(
            response,
            "parsed",
            None,
        )

        raw_text = getattr(
            response,
            "text",
            None,
        )

        try:

            # Gemini structured response.
            if isinstance(
                parsed,
                GeneratedStory,
            ):
                generated = parsed

            # Parsed dictionary/object.
            elif parsed is not None:
                generated = GeneratedStory.model_validate(
                    parsed
                )

            # Plain JSON response.
            elif raw_text:

                cleaned = raw_text.strip()

                # Remove accidental Markdown fences.
                if cleaned.startswith("```"):

                    lines = cleaned.splitlines()

                    if (
                        lines
                        and lines[0]
                        .strip()
                        .startswith("```")
                    ):
                        lines = lines[1:]

                    if (
                        lines
                        and lines[-1].strip() == "```"
                    ):
                        lines = lines[:-1]

                    cleaned = "\n".join(
                        lines
                    ).strip()

                generated = GeneratedStory.model_validate_json(
                    cleaned
                )

            else:

                raise StoryValidationError(
                    "Gemini returned no story content."
                )

        except ValidationError as exc:

            logger.error(
                "Gemini returned invalid story JSON/schema: %s",
                exc,
            )

            raise StoryValidationError(
                "Gemini returned story data that "
                "does not match the required schema."
            ) from exc

        # ====================================================
        # CONVERT TO NORMAL DICT
        # ====================================================

        story = generated.model_dump(
            mode="json"
        )

        # ====================================================
        # EXISTING STORY VALIDATOR
        # ====================================================

        try:

            validated_story = StoryValidator.validate(
                story,
                player_count=player_count,
            )

        except StoryValidationError:
            raise

        except Exception as exc:

            logger.exception(
                "Generated story failed semantic validation."
            )

            raise StoryValidationError(
                "Generated story failed semantic validation."
            ) from exc

        # ====================================================
        # DUPLICATE STORY CHECK
        # ====================================================

        fingerprint = (
            StoryValidator.structural_fingerprint(
                validated_story
            )
        )

        if fingerprint in fingerprints:

            raise StoryValidationError(
                "Gemini generated a story whose "
                "structure duplicates a previous story."
            )

        logger.info(
            "Story generation completed successfully. "
            "model=%s fingerprint=%s",
            self.model,
            fingerprint,
        )

        return (
            validated_story,
            fingerprint,
        )

    # ========================================================
    # MODEL FALLBACK LIST
    # ========================================================

    def _get_models_to_try(self) -> list[str]:

        configured_fallbacks = os.getenv(
            "GEMINI_FALLBACK_MODELS",
            ",".join(self.FALLBACK_MODELS),
        )

        candidates = [self.model]

        candidates.extend(
            item.strip()
            for item in configured_fallbacks.split(",")
            if item.strip()
        )

        models: list[str] = []

        for candidate in candidates:

            candidate = (
                candidate
                .removeprefix("models/")
                .strip()
            )

            if candidate and candidate not in models:
                models.append(candidate)

        return models

    # ========================================================
    # RETRY ERROR DETECTION
    # ========================================================

    @staticmethod
    def _is_retryable_error(
        exc: Exception,
    ) -> bool:

        text = str(exc).upper()

        retryable_markers = (
            "429",
            "RESOURCE_EXHAUSTED",
            "503",
            "UNAVAILABLE",
            "500",
            "INTERNAL",
            "502",
            "504",
            "DEADLINE_EXCEEDED",
            "TIMEOUT",
            "TIMED OUT",
            "TOO MANY REQUESTS",
        )

        return any(
            marker in text
            for marker in retryable_markers
        )

    # ========================================================
    # RETRY-AFTER PARSER
    # ========================================================

    @staticmethod
    def _retry_after_seconds(
        exc: Exception,
    ) -> float | None:

        for attr in (
            "retry_after",
            "retry_delay",
        ):

            value = getattr(
                exc,
                attr,
                None,
            )

            if value is not None:

                try:

                    if hasattr(
                        value,
                        "total_seconds",
                    ):
                        return max(
                            0.0,
                            float(
                                value.total_seconds()
                            ),
                        )

                    return max(
                        0.0,
                        float(value),
                    )

                except (
                    TypeError,
                    ValueError,
                ):
                    pass

        match = re.search(
            r"retry[- ]after[^0-9]*"
            r"(\d+(?:\.\d+)?)",
            str(exc),
            re.IGNORECASE,
        )

        if match:

            try:
                return max(
                    0.0,
                    float(match.group(1)),
                )

            except ValueError:
                return None

        return None

    # ========================================================
    # GEMINI REQUEST WITH RETRY / BACKOFF
    # ========================================================

    async def _generate_with_retry(
        self,
        *,
        model_name: str,
        contents: str,
        config: types.GenerateContentConfig,
        mode: str,
    ) -> Any:

        last_error: Exception | None = None

        for attempt in range(
            1,
            self.MAX_RETRIES_PER_REQUEST + 1,
        ):

            try:

                logger.info(
                    "Gemini request: "
                    "model=%s mode=%s attempt=%d/%d",
                    model_name,
                    mode,
                    attempt,
                    self.MAX_RETRIES_PER_REQUEST,
                )

                response = (
                    await self.client.aio.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=config,
                    )
                )

                logger.info(
                    "Gemini request succeeded: "
                    "model=%s mode=%s attempt=%d",
                    model_name,
                    mode,
                    attempt,
                )

                return response

            except Exception as exc:

                last_error = exc

                retryable = self._is_retryable_error(
                    exc
                )

                # Do not retry permanent errors such as:
                # invalid API key, invalid argument, etc.
                if (
                    not retryable
                    or attempt >= self.MAX_RETRIES_PER_REQUEST
                ):

                    logger.exception(
                        "Gemini request failed/exhausted: "
                        "model=%s mode=%s attempt=%d "
                        "error=%s",
                        model_name,
                        mode,
                        attempt,
                        exc,
                    )

                    raise

                retry_after = (
                    self._retry_after_seconds(exc)
                )

                if retry_after is not None:

                    delay = min(
                        retry_after,
                        self.MAX_RETRY_DELAY_SECONDS,
                    )

                else:

                    exponential = min(
                        self.INITIAL_RETRY_DELAY_SECONDS
                        * (2 ** (attempt - 1)),
                        self.MAX_RETRY_DELAY_SECONDS,
                    )

                    jitter = random.uniform(
                        0.0,
                        exponential * 0.25,
                    )

                    delay = min(
                        exponential + jitter,
                        self.MAX_RETRY_DELAY_SECONDS,
                    )

                logger.warning(
                    "Temporary Gemini error: "
                    "model=%s mode=%s attempt=%d/%d. "
                    "Retrying in %.1fs. error=%s",
                    model_name,
                    mode,
                    attempt,
                    self.MAX_RETRIES_PER_REQUEST,
                    delay,
                    exc,
                )

                await asyncio.sleep(delay)

        assert last_error is not None

        raise last_error

    # ========================================================
    # PROMPT
    # ========================================================

    def _build_prompt(
        self,
        player_count: int,
        previous_fingerprints: list[str],
    ) -> str:

        previous_text = json.dumps(
            previous_fingerprints,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        return f"""
Create one short, funny, absurd interactive group
story for the Telegram game WHAT HAPPENS?.

NUMBER OF HUMAN PLAYERS:
{player_count}

Generate story DATA ONLY.

The Python GameEngine executes the story
deterministically.

DO NOT generate:

- Python
- executable code
- Telegram API calls
- SQL
- tools
- scoring systems
- coins
- XP
- levels
- rankings
- trivia
- dangerous instructions
- sexual content
- hateful content

ROLES:

Create exactly {player_count} to
{max_roles(player_count)} playable roles.

Every playable role must have:

- unique id
- unique name
- secret_description
- playable=true

SCENES:

Create between
{self.MIN_SCENES} and
{self.MAX_SCENES}
scenes.

Every scene must contain:

- id
- public_text
- timer_seconds
- eligible_roles
- choices
- late_join

Timers must be between
{self.MIN_TIMER_SECONDS}
and
{self.MAX_TIMER_SECONDS}
seconds.

Set first_scene_id to a valid scene.

CHOICES:

Every choice must contain:

- id
- label
- public_event
- effects

next_scene may be supplied.

If supplied, next_scene MUST reference
an existing scene.

ALLOWED CONDITIONS:

flag
variable
knowledge
relationship

ALLOWED OPERATORS:

eq
ne
gt
gte
lt
lte
contains

ALLOWED EFFECT GROUPS:

set_flags
remove_flags
set_variables
add_variables
relationships
knowledge
secrets
character_state
text
description

All role references must reference real roles.

All scene references must reference real scenes.

STORY GRAPH:

- first_scene_id must be valid
- every scene must be reachable
- every scene must eventually reach an ending
- no dead-end non-ending scenes
- no duplicate IDs
- no invalid references

LATE JOIN:

Every scene must contain late_join.

Late-join role references must be valid.

Previous structural fingerprints:

{previous_text}

Return ONLY the structured JSON object.
"""

    # ========================================================
    # PLAYER COUNT VALIDATION
    # ========================================================

    @classmethod
    def _validate_player_count(
        cls,
        player_count: int,
    ) -> None:

        if (
            not isinstance(
                player_count,
                int,
            )
            or isinstance(
                player_count,
                bool,
            )
        ):

            raise ValueError(
                "player_count must be an integer."
            )

        if not (
            cls.MIN_PLAYERS
            <= player_count
            <= cls.MAX_PLAYERS
        ):

            raise ValueError(
                "player_count must be between "
                f"{cls.MIN_PLAYERS} and "
                f"{cls.MAX_PLAYERS}."
            )

    # ========================================================
    # CLIENT CLOSE
    # ========================================================

    async def close(
        self,
    ) -> None:

        aio_client = getattr(
            self.client,
            "aio",
            None,
        )

        if aio_client is None:
            return

        close_method = getattr(
            aio_client,
            "aclose",
            None,
        )

        if close_method is not None:

            result = close_method()

            if hasattr(
                result,
                "__await__",
            ):
                await result


# ============================================================
# ROLE LIMIT
# ============================================================

def max_roles(
    player_count: int,
) -> int:

    return min(
        player_count + 2,
        StoryGenerator.MAX_PLAYERS,
    )
