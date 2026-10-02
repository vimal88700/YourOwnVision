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
# GENERATED STORY SCHEMAS
# ============================================================


class GeneratedSecret(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    owner: str | None = None
    revealed: bool = False
    revealed_to: list[str] = Field(default_factory=list)


class GeneratedKnowledge(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role_id: str
    knowledge: str
    value: bool = True


class GeneratedRelationshipEffect(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role_a: str
    role_b: str
    amount: int | float = 0


class GeneratedEffects(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    model_config = ConfigDict(extra="ignore")

    type: str

    name: str | None = None
    value: Any = None

    operator: str = "eq"

    role_id: str | None = None
    knowledge: str | None = None

    role_a: str | None = None
    role_b: str | None = None


class GeneratedChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    model_config = ConfigDict(extra="ignore")

    next_scene: str

    conditions: list[GeneratedCondition] = Field(
        default_factory=list
    )


class GeneratedEnding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    text: str

    conditions: list[GeneratedCondition] = Field(
        default_factory=list
    )


class GeneratedLateJoin(BaseModel):
    model_config = ConfigDict(extra="ignore")

    allowed: bool = True

    roles: list[str] = Field(
        default_factory=list
    )

    conditions: list[GeneratedCondition] = Field(
        default_factory=list
    )

    entry_scene: str | None = None


class GeneratedRole(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    secret_description: str

    playable: bool = True


class GeneratedScene(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
    model_config = ConfigDict(extra="ignore")

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

    Gemini generates story DATA ONLY.

    GameEngine executes the story.

    This implementation is deliberately defensive:
      - retries temporary Gemini errors
      - supports model fallback
      - tries structured JSON
      - falls back to normal JSON
      - locally validates JSON with Pydantic
      - locally validates game semantics
      - retries when Gemini produces an invalid story
      - rejects duplicate story structures
    """

    # --------------------------------------------------------
    # CURRENT GEMINI MODEL
    # --------------------------------------------------------

    DEFAULT_MODEL = "gemini-3.8-flash"

    # These are current Gemini 3 Flash model IDs.
    #
    # You can override them from Render with:
    #
    # GEMINI_FALLBACK_MODELS=gemini-3.7-flash,gemini-3.6-flash
    #
    FALLBACK_MODELS = (
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    )

    # --------------------------------------------------------
    # RETRY SETTINGS
    # --------------------------------------------------------

    MAX_RETRIES_PER_REQUEST = 3

    INITIAL_RETRY_DELAY_SECONDS = 2.0

    MAX_RETRY_DELAY_SECONDS = 20.0

    # Number of times we regenerate a story after receiving
    # valid JSON that nevertheless fails our local validation.
    MAX_STORY_ATTEMPTS_PER_MODEL = 2

    # --------------------------------------------------------
    # GAME LIMITS
    # --------------------------------------------------------

    MIN_PLAYERS = 1
    MAX_PLAYERS = 20

    MIN_SCENES = 3
    MAX_SCENES = 12

    MIN_TIMER_SECONDS = 10
    MAX_TIMER_SECONDS = 3600

    # --------------------------------------------------------
    # INIT
    # --------------------------------------------------------

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
    ) -> None:

        if not api_key or not api_key.strip():
            raise ValueError(
                "Gemini API key is required."
            )

        configured_model = (
            model
            or os.getenv("GEMINI_MODEL")
            or self.DEFAULT_MODEL
        )

        self.model = configured_model.strip()

        if not self.model:
            raise ValueError(
                "Gemini model cannot be empty."
            )

        self.client = genai.Client(
            api_key=api_key.strip()
        )

        logger.info(
            "StoryGenerator initialized with model=%s",
            self.model,
        )

    # ========================================================
    # PUBLIC GENERATE METHOD
    # ========================================================

    async def generate(
        self,
        player_count: int,
        previous_fingerprints: list[str] | None = None,
    ) -> tuple[dict[str, Any], str]:

        self._validate_player_count(
            player_count
        )

        fingerprints = [
            value.strip()
            for value in (
                previous_fingerprints or []
            )
            if isinstance(value, str)
            and value.strip()
        ]

        base_prompt = self._build_prompt(
            player_count=player_count,
            previous_fingerprints=fingerprints,
        )

        models_to_try = self._get_models_to_try()

        logger.info(
            "Gemini story generation started. "
            "players=%s models=%s",
            player_count,
            models_to_try,
        )

        last_error: Exception | None = None

        # ====================================================
        # TRY EACH MODEL
        # ====================================================

        for model_name in models_to_try:

            logger.info(
                "Trying Gemini model=%s",
                model_name,
            )

            for story_attempt in range(
                1,
                self.MAX_STORY_ATTEMPTS_PER_MODEL + 1,
            ):

                logger.info(
                    "Story generation attempt "
                    "%d/%d using model=%s",
                    story_attempt,
                    self.MAX_STORY_ATTEMPTS_PER_MODEL,
                    model_name,
                )

                # ------------------------------------------------
                # First try structured output.
                # ------------------------------------------------

                response = None

                structured_prompt = base_prompt

                if story_attempt > 1:
                    structured_prompt = (
                        base_prompt
                        + "\n\n"
                        + self._repair_instruction()
                    )

                try:

                    response = await self._generate_with_retry(
                        model_name=model_name,
                        contents=structured_prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=GeneratedStory,
                            system_instruction=(
                                "You are the story-data generator "
                                "for the WHAT HAPPENS? Telegram game. "
                                "Return ONLY the requested story JSON. "
                                "Do not explain anything. "
                                "Do not return Markdown. "
                                "Do not return Python. "
                                "Do not return SQL. "
                                "Do not call tools. "
                                "Do not invent fields outside the "
                                "requested structure."
                            ),
                        ),
                        mode="structured",
                    )

                    self.model = model_name

                except Exception as exc:

                    last_error = exc

                    logger.warning(
                        "Structured Gemini generation failed. "
                        "model=%s attempt=%s error=%s",
                        model_name,
                        story_attempt,
                        exc,
                    )

                # ------------------------------------------------
                # Parse structured response if we got one.
                # ------------------------------------------------

                if response is not None:

                    try:

                        generated = self._parse_response(
                            response
                        )

                        validated_story = (
                            self._validate_generated_story(
                                generated,
                                player_count=player_count,
                                fingerprints=fingerprints,
                            )
                        )

                        fingerprint = (
                            StoryValidator.structural_fingerprint(
                                validated_story
                            )
                        )

                        logger.info(
                            "Story generation succeeded using "
                            "structured output. "
                            "model=%s fingerprint=%s",
                            model_name,
                            fingerprint,
                        )

                        return (
                            validated_story,
                            fingerprint,
                        )

                    except StoryValidationError as exc:

                        last_error = exc

                        logger.warning(
                            "Structured story failed local "
                            "validation. model=%s "
                            "attempt=%s error=%s",
                            model_name,
                            story_attempt,
                            exc,
                        )

                    except ValidationError as exc:

                        last_error = exc

                        logger.warning(
                            "Structured story failed Pydantic "
                            "validation. model=%s "
                            "attempt=%s error=%s",
                            model_name,
                            story_attempt,
                            exc,
                        )

                    except Exception as exc:

                        last_error = exc

                        logger.exception(
                            "Unexpected structured story "
                            "validation failure."
                        )

                # =================================================
                # PLAIN JSON FALLBACK
                # =================================================

                plain_prompt = (
                    base_prompt
                    + "\n\n"
                    + self._plain_json_instruction()
                )

                if story_attempt > 1:
                    plain_prompt += (
                        "\n\n"
                        + self._repair_instruction()
                    )

                try:

                    response = await self._generate_with_retry(
                        model_name=model_name,
                        contents=plain_prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            system_instruction=(
                                "Return exactly ONE valid JSON "
                                "object describing the requested "
                                "story. "
                                "No Markdown. "
                                "No code fences. "
                                "No commentary. "
                                "No explanation."
                            ),
                        ),
                        mode="plain-json",
                    )

                    self.model = model_name

                except Exception as exc:

                    last_error = exc

                    logger.warning(
                        "Plain JSON Gemini generation failed. "
                        "model=%s attempt=%s error=%s",
                        model_name,
                        story_attempt,
                        exc,
                    )

                    # Move to next story attempt/model.
                    continue

                # ------------------------------------------------
                # Parse plain JSON.
                # ------------------------------------------------

                try:

                    generated = self._parse_response(
                        response
                    )

                    validated_story = (
                        self._validate_generated_story(
                            generated,
                            player_count=player_count,
                            fingerprints=fingerprints,
                        )
                    )

                    fingerprint = (
                        StoryValidator.structural_fingerprint(
                            validated_story
                        )
                    )

                    logger.info(
                        "Story generation succeeded using "
                        "plain JSON. "
                        "model=%s fingerprint=%s",
                        model_name,
                        fingerprint,
                    )

                    return (
                        validated_story,
                        fingerprint,
                    )

                except StoryValidationError as exc:

                    last_error = exc

                    logger.warning(
                        "Plain JSON story failed local "
                        "validation. model=%s "
                        "attempt=%s error=%s",
                        model_name,
                        story_attempt,
                        exc,
                    )

                except ValidationError as exc:

                    last_error = exc

                    logger.warning(
                        "Plain JSON story failed Pydantic "
                        "validation. model=%s "
                        "attempt=%s error=%s",
                        model_name,
                        story_attempt,
                        exc,
                    )

                except Exception as exc:

                    last_error = exc

                    logger.exception(
                        "Unexpected plain JSON validation "
                        "failure."
                    )

        # ========================================================
        # COMPLETE FAILURE
        # ========================================================

        detail = (
            str(last_error).strip()
            if last_error
            else "Unknown Gemini story-generation error."
        )

        if not detail:
            detail = (
                "Unknown Gemini story-generation error."
            )

        if len(detail) > 1200:
            detail = detail[:1200] + "..."

        logger.error(
            "All Gemini story-generation attempts failed: %s",
            detail,
        )

        raise RuntimeError(
            "Gemini story generation failed after "
            "all retries and model fallbacks: "
            f"{detail}"
        ) from last_error

    # ========================================================
    # RESPONSE PARSING
    # ========================================================

    def _parse_response(
        self,
        response: Any,
    ) -> GeneratedStory:

        if response is None:
            raise StoryValidationError(
                "Gemini returned no response."
            )

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Prefer raw response.text when available.
        #
        # This avoids depending completely on the SDK's
        # automatic Pydantic parsing.
        # ----------------------------------------------------

        raw_text = getattr(
            response,
            "text",
            None,
        )

        if raw_text:
            cleaned = self._clean_json_text(
                raw_text
            )

            try:

                return GeneratedStory.model_validate_json(
                    cleaned
                )

            except ValidationError:
                # Continue to parsed object fallback below.
                pass

        # ----------------------------------------------------
        # SDK parsed response fallback
        # ----------------------------------------------------

        parsed = getattr(
            response,
            "parsed",
            None,
        )

        if parsed is not None:

            if isinstance(
                parsed,
                GeneratedStory,
            ):
                return parsed

            try:

                return GeneratedStory.model_validate(
                    parsed
                )

            except ValidationError:
                pass

        # ----------------------------------------------------
        # If text existed but Pydantic rejected it, expose
        # a clean error instead of a cryptic failure.
        # ----------------------------------------------------

        if raw_text:

            cleaned = self._clean_json_text(
                raw_text
            )

            try:

                data = json.loads(
                    cleaned
                )

                return GeneratedStory.model_validate(
                    data
                )

            except Exception as exc:

                logger.error(
                    "Gemini returned JSON that could not "
                    "be converted into GeneratedStory: %s",
                    exc,
                )

                raise StoryValidationError(
                    "Gemini returned story data that "
                    "does not match the required schema."
                ) from exc

        raise StoryValidationError(
            "Gemini returned no usable story JSON."
        )

    @staticmethod
    def _clean_json_text(
        text: str,
    ) -> str:

        cleaned = text.strip()

        # ----------------------------------------------------
        # Remove Markdown fences.
        # ----------------------------------------------------

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
                and lines[-1]
                .strip()
                == "```"
            ):
                lines = lines[:-1]

            cleaned = "\n".join(
                lines
            ).strip()

        # ----------------------------------------------------
        # Remove accidental "json" prefix.
        # ----------------------------------------------------

        if cleaned.lower().startswith(
            "json\n"
        ):
            cleaned = cleaned[5:].strip()

        # ----------------------------------------------------
        # If Gemini added text before/after JSON, attempt to
        # extract the outer JSON object.
        # ----------------------------------------------------

        if not (
            cleaned.startswith("{")
            and cleaned.endswith("}")
        ):

            first = cleaned.find("{")
            last = cleaned.rfind("}")

            if (
                first >= 0
                and last > first
            ):
                candidate = cleaned[
                    first:last + 1
                ]

                try:
                    json.loads(candidate)
                    cleaned = candidate
                except json.JSONDecodeError:
                    pass

        return cleaned

    # ========================================================
    # STORY VALIDATION
    # ========================================================

    def _validate_generated_story(
        self,
        generated: GeneratedStory,
        *,
        player_count: int,
        fingerprints: list[str],
    ) -> dict[str, Any]:

        story = generated.model_dump(
            mode="json"
        )

        # ----------------------------------------------------
        # Validate using the project's real game validator.
        # ----------------------------------------------------

        try:

            validated_story = (
                StoryValidator.validate(
                    story,
                    player_count=player_count,
                )
            )

        except StoryValidationError:
            raise

        except Exception as exc:

            logger.exception(
                "Generated story failed semantic "
                "validation."
            )

            raise StoryValidationError(
                "Generated story failed semantic "
                "validation."
            ) from exc

        # ----------------------------------------------------
        # Duplicate structure protection.
        # ----------------------------------------------------

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

        return validated_story

    # ========================================================
    # MODEL SELECTION
    # ========================================================

    def _get_models_to_try(
        self,
    ) -> list[str]:

        configured_fallbacks = os.getenv(
            "GEMINI_FALLBACK_MODELS",
            "",
        )

        candidates: list[str] = [
            self.model
        ]

        # ----------------------------------------------------
        # Explicit Render environment-variable fallbacks
        # come first.
        # ----------------------------------------------------

        if configured_fallbacks:

            candidates.extend(
                item.strip()
                for item in configured_fallbacks.split(",")
                if item.strip()
            )

        # ----------------------------------------------------
        # Then built-in current Gemini 3 Flash fallbacks.
        # ----------------------------------------------------

        candidates.extend(
            self.FALLBACK_MODELS
        )

        models: list[str] = []

        for candidate in candidates:

            candidate = (
                candidate
                .removeprefix("models/")
                .strip()
            )

            if (
                candidate
                and candidate not in models
            ):
                models.append(
                    candidate
                )

        return models

    # ========================================================
    # GEMINI RETRY LOGIC
    # ========================================================

    @staticmethod
    def _is_retryable_error(
        exc: Exception,
    ) -> bool:

        text = str(exc).upper()

        retryable_markers = (
            "429",
            "RESOURCE_EXHAUSTED",
            "RATE_LIMIT",
            "TOO MANY REQUESTS",

            "500",
            "INTERNAL",

            "502",
            "BAD GATEWAY",

            "503",
            "UNAVAILABLE",
            "HIGH DEMAND",

            "504",
            "GATEWAY TIMEOUT",

            "DEADLINE_EXCEEDED",

            "TIMEOUT",
            "TIMED OUT",

            "TEMPORARY",
        )

        return any(
            marker in text
            for marker in retryable_markers
        )

    @staticmethod
    def _retry_after_seconds(
        exc: Exception,
    ) -> float | None:

        # ----------------------------------------------------
        # Try SDK attributes first.
        # ----------------------------------------------------

        for attr in (
            "retry_after",
            "retry_delay",
        ):

            value = getattr(
                exc,
                attr,
                None,
            )

            if value is None:
                continue

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

        # ----------------------------------------------------
        # Search text for retry-after seconds.
        # ----------------------------------------------------

        patterns = (
            r"retry[- ]after[^0-9]*(\d+(?:\.\d+)?)",
            r"retry in[^0-9]*(\d+(?:\.\d+)?)",
            r"retryDelay[^0-9]*(\d+(?:\.\d+)?)",
        )

        error_text = str(exc)

        for pattern in patterns:

            match = re.search(
                pattern,
                error_text,
                re.IGNORECASE,
            )

            if match:

                try:
                    return max(
                        0.0,
                        float(
                            match.group(1)
                        ),
                    )

                except ValueError:
                    pass

        return None

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

                retryable = (
                    self._is_retryable_error(
                        exc
                    )
                )

                # ------------------------------------------------
                # Permanent error:
                #
                # 400 invalid argument
                # invalid API key
                # bad request
                # invalid model
                #
                # Do NOT waste time retrying those four times.
                # ------------------------------------------------

                if (
                    not retryable
                    or attempt
                    >= self.MAX_RETRIES_PER_REQUEST
                ):

                    logger.error(
                        "Gemini request failed/exhausted: "
                        "model=%s mode=%s attempt=%d "
                        "retryable=%s error=%s",
                        model_name,
                        mode,
                        attempt,
                        retryable,
                        exc,
                    )

                    raise

                retry_after = (
                    self._retry_after_seconds(
                        exc
                    )
                )

                if retry_after is not None:

                    delay = min(
                        retry_after,
                        self.MAX_RETRY_DELAY_SECONDS,
                    )

                else:

                    exponential = min(
                        self.INITIAL_RETRY_DELAY_SECONDS
                        * (
                            2 ** (attempt - 1)
                        ),
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
                    "Temporary Gemini error. "
                    "model=%s mode=%s "
                    "attempt=%d/%d "
                    "retrying in %.1fs: %s",
                    model_name,
                    mode,
                    attempt,
                    self.MAX_RETRIES_PER_REQUEST,
                    delay,
                    exc,
                )

                await asyncio.sleep(
                    delay
                )

        if last_error is not None:
            raise last_error

        raise RuntimeError(
            "Gemini request failed without an exception."
        )

    # ========================================================
    # PROMPTS
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
Create ONE short, funny, absurd interactive group
story for the Telegram game WHAT HAPPENS?.

NUMBER OF HUMAN PLAYERS:
{player_count}

IMPORTANT:
Generate STORY DATA ONLY.

The Python GameEngine executes the story.

Do NOT generate:
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

==================================================
ROLES
==================================================

Create exactly {player_count} to
{max_roles(player_count)} playable roles.

Every playable role MUST contain:

- id
- name
- secret_description
- playable

Rules:

- Every role id must be unique.
- Every role name must be unique.
- playable must be true.
- Do not reference roles that do not exist.

==================================================
SCENES
==================================================

Create between
{self.MIN_SCENES}
and
{self.MAX_SCENES}
scenes.

Every scene MUST contain:

- id
- public_text
- timer_seconds
- eligible_roles
- choices
- late_join

Timer values MUST be between:

{self.MIN_TIMER_SECONDS}
and
{self.MAX_TIMER_SECONDS}

Set first_scene_id to a valid scene id.

Every scene id must be unique.

==================================================
CHOICES
==================================================

Every choice MUST contain:

- id
- label
- public_event
- effects

A choice MAY contain:

- next_scene
- conditions
- branch_priority
- public_event_variants

If next_scene is supplied,
it MUST reference an existing scene.

==================================================
TRANSITIONS
==================================================

A scene MAY contain transitions.

Every transition MUST contain:

- next_scene

If conditions are supplied,
they must use the allowed condition structure.

Every referenced scene MUST exist.

==================================================
ENDING
==================================================

A scene MAY contain an ending.

An ending MUST contain:

- id
- text

Every story path must eventually reach an ending.

==================================================
CONDITIONS
==================================================

Allowed condition types:

- flag
- variable
- knowledge
- relationship

Allowed operators:

- eq
- ne
- gt
- gte
- lt
- lte
- contains

Do not invent other condition types.

==================================================
EFFECTS
==================================================

Allowed effect groups:

- set_flags
- remove_flags
- set_variables
- add_variables
- relationships
- knowledge
- secrets
- character_state
- text
- description

All role references must reference real roles.

All scene references must reference real scenes.

==================================================
STORY GRAPH
==================================================

The generated story MUST satisfy:

- first_scene_id is valid.
- Every scene is reachable.
- Every scene eventually reaches an ending.
- No dead-end non-ending scenes.
- No duplicate ids.
- No invalid scene references.
- No invalid role references.
- The graph must be playable by the existing GameEngine.

==================================================
LATE JOIN
==================================================

Every scene MUST contain late_join.

late_join MUST contain:

- allowed
- roles
- conditions
- entry_scene

All referenced roles and scenes must exist.

==================================================
PREVIOUS STORY FINGERPRINTS
==================================================

Do not intentionally reproduce the same structure as any
of these previous story fingerprints:

{previous_text}

==================================================
FINAL REQUIREMENT
==================================================

Return ONLY ONE JSON OBJECT.

No Markdown.
No ```json.
No explanation.
No commentary.
No text before the JSON.
No text after the JSON.
"""

    @staticmethod
    def _plain_json_instruction() -> str:

        return """
IMPORTANT JSON OUTPUT RULES:

Return ONLY one valid JSON object.

Do NOT use Markdown fences.

Do NOT write:
```json

Do NOT write:
