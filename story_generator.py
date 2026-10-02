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

    set_variables: dict[str, Any] = Field(
        default_factory=dict
    )

    add_variables: dict[str, int | float] = Field(
        default_factory=dict
    )

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

    choices: list[GeneratedChoice] = Field(
        default_factory=list
    )

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
    Generates story DATA with Gemini.

    Gemini creates the story.
    GameEngine/GameService handles gameplay.

    This implementation deliberately uses normal JSON output
    and validates the result locally with Pydantic and the
    existing StoryValidator.
    """

    DEFAULT_MODEL = "gemini-3.8-flash"

    FALLBACK_MODELS = (
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    )

    # Retry temporary Gemini capacity/server errors.
    MAX_RETRIES_PER_MODEL = 3

    INITIAL_RETRY_DELAY_SECONDS = 2.0
    MAX_RETRY_DELAY_SECONDS = 12.0

    # If Gemini returns malformed JSON/schema, regenerate.
    MAX_GENERATION_ATTEMPTS = 2

    MIN_PLAYERS = 1
    MAX_PLAYERS = 20

    MIN_SCENES = 3
    MAX_SCENES = 12

    MIN_TIMER_SECONDS = 10
    MAX_TIMER_SECONDS = 3600

    # ------------------------------------------------------------
    # INITIALIZATION
    # ------------------------------------------------------------

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

        self.client = genai.Client(
            api_key=api_key.strip()
        )

    # ------------------------------------------------------------
    # PUBLIC GENERATION METHOD
    # ------------------------------------------------------------

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
            if (
                isinstance(value, str)
                and value.strip()
            )
        ]

        models = self._get_models_to_try()

        last_error: Exception | None = None

        logger.info(
            "Starting Gemini story generation. "
            "players=%s models=%s",
            player_count,
            models,
        )

        # --------------------------------------------------------
        # TRY EACH MODEL
        # --------------------------------------------------------

        for model_name in models:

            for generation_attempt in range(
                1,
                self.MAX_GENERATION_ATTEMPTS + 1,
            ):

                prompt = self._build_prompt(
                    player_count=player_count,
                    previous_fingerprints=fingerprints,
                    attempt=generation_attempt,
                )

                try:

                    response = (
                        await self._generate_json_with_retry(
                            model_name=model_name,
                            prompt=prompt,
                        )
                    )

                    story = self._parse_response(
                        response
                    )

                    validated_story = (
                        StoryValidator.validate(
                            story,
                            player_count=player_count,
                        )
                    )

                    fingerprint = (
                        StoryValidator.structural_fingerprint(
                            validated_story
                        )
                    )

                    # ------------------------------------------------
                    # DUPLICATE PROTECTION
                    # ------------------------------------------------

                    if fingerprint in fingerprints:

                        raise StoryValidationError(
                            "Gemini generated a duplicate "
                            "story structure."
                        )

                    self.model = model_name

                    logger.info(
                        "Story generation succeeded. "
                        "model=%s attempt=%s "
                        "fingerprint=%s",
                        model_name,
                        generation_attempt,
                        fingerprint,
                    )

                    return (
                        validated_story,
                        fingerprint,
                    )

                except StoryValidationError as exc:

                    last_error = exc

                    logger.warning(
                        "Story validation failed. "
                        "model=%s attempt=%s/%s error=%s",
                        model_name,
                        generation_attempt,
                        self.MAX_GENERATION_ATTEMPTS,
                        exc,
                    )

                except ValidationError as exc:

                    last_error = exc

                    logger.warning(
                        "Story schema validation failed. "
                        "model=%s attempt=%s/%s error=%s",
                        model_name,
                        generation_attempt,
                        self.MAX_GENERATION_ATTEMPTS,
                        exc,
                    )

                except Exception as exc:

                    last_error = exc

                    logger.warning(
                        "Gemini generation failed. "
                        "model=%s attempt=%s/%s error=%s",
                        model_name,
                        generation_attempt,
                        self.MAX_GENERATION_ATTEMPTS,
                        exc,
                    )

        # --------------------------------------------------------
        # EVERYTHING FAILED
        # --------------------------------------------------------

        detail = (
            str(last_error).strip()
            if last_error
            else "Unknown Gemini error."
        )

        if not detail:
            detail = "Unknown Gemini error."

        if len(detail) > 1200:
            detail = (
                detail[:1200]
                + "..."
            )

        raise RuntimeError(
            "Gemini story generation failed: "
            + detail
        ) from last_error

    # ============================================================
    # MODEL LIST
    # ============================================================

    def _get_models_to_try(self) -> list[str]:

        configured = os.getenv(
            "GEMINI_FALLBACK_MODELS",
            ",".join(
                self.FALLBACK_MODELS
            ),
        )

        candidates = [
            self.model
        ]

        for item in configured.split(","):

            item = item.strip()

            if item:
                candidates.append(item)

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
                models.append(candidate)

        return models

    # ============================================================
    # GEMINI REQUEST WITH RETRY/BACKOFF
    # ============================================================

    async def _generate_json_with_retry(
        self,
        *,
        model_name: str,
        prompt: str,
    ) -> Any:

        last_error: Exception | None = None

        for attempt in range(
            1,
            self.MAX_RETRIES_PER_MODEL + 1,
        ):

            try:

                logger.info(
                    "Gemini request. "
                    "model=%s attempt=%s/%s",
                    model_name,
                    attempt,
                    self.MAX_RETRIES_PER_MODEL,
                )

                # IMPORTANT:
                #
                # We intentionally do NOT send the complex
                # Pydantic response_schema to Gemini here.
                #
                # Gemini returns normal JSON.
                # Pydantic + StoryValidator validate it locally.
                #
                # This avoids the schema/API compatibility problem
                # that was causing:
                #
                # "story data does not match required schema"
                #
                response = (
                    await self.client.aio.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            system_instruction=(
                                "You are the story-data "
                                "generator for the "
                                "WHAT HAPPENS? Telegram game. "
                                "Return exactly one valid JSON "
                                "object. "
                                "No Markdown. "
                                "No explanation. "
                                "No executable code."
                            ),
                        ),
                    )
                )

                logger.info(
                    "Gemini request succeeded. "
                    "model=%s attempt=%s",
                    model_name,
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

                # Non-temporary errors such as:
                # invalid API key
                # invalid argument
                # permission errors
                # should NOT be retried endlessly.
                if not retryable:

                    logger.error(
                        "Permanent Gemini error. "
                        "model=%s error=%s",
                        model_name,
                        exc,
                    )

                    raise

                if (
                    attempt
                    >= self.MAX_RETRIES_PER_MODEL
                ):

                    logger.error(
                        "Gemini retries exhausted. "
                        "model=%s error=%s",
                        model_name,
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

                    base = min(
                        self.INITIAL_RETRY_DELAY_SECONDS
                        * (
                            2
                            ** (
                                attempt - 1
                            )
                        ),
                        self.MAX_RETRY_DELAY_SECONDS,
                    )

                    jitter = random.uniform(
                        0.0,
                        base * 0.25,
                    )

                    delay = min(
                        base + jitter,
                        self.MAX_RETRY_DELAY_SECONDS,
                    )

                logger.warning(
                    "Temporary Gemini error. "
                    "model=%s attempt=%s/%s "
                    "retrying in %.1fs. "
                    "error=%s",
                    model_name,
                    attempt,
                    self.MAX_RETRIES_PER_MODEL,
                    delay,
                    exc,
                )

                await asyncio.sleep(
                    delay
                )

        if last_error is not None:
            raise last_error

        raise RuntimeError(
            "Gemini request failed without an error."
        )

    # ============================================================
    # RETRYABLE ERROR DETECTION
    # ============================================================

    @staticmethod
    def _is_retryable_error(
        exc: Exception,
    ) -> bool:

        text = str(exc).upper()

        retryable_markers = (
            "429",
            "RESOURCE_EXHAUSTED",
            "TOO MANY REQUESTS",
            "500",
            "INTERNAL",
            "502",
            "503",
            "504",
            "UNAVAILABLE",
            "DEADLINE_EXCEEDED",
            "TIMEOUT",
            "TIMED OUT",
        )

        return any(
            marker in text
            for marker in retryable_markers
        )

    # ============================================================
    # RETRY-AFTER PARSER
    # ============================================================

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
                    float(
                        match.group(1)
                    ),
                )

            except ValueError:
                return None

        return None

    # ============================================================
    # RESPONSE PARSING
    # ============================================================

    @staticmethod
    def _parse_response(
        response: Any,
    ) -> dict[str, Any]:

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

        # --------------------------------------------------------
        # SDK PARSED OBJECT
        # --------------------------------------------------------

        if parsed is not None:

            if isinstance(
                parsed,
                GeneratedStory,
            ):

                return parsed.model_dump(
                    mode="json"
                )

            if isinstance(
                parsed,
                dict,
            ):

                generated = (
                    GeneratedStory.model_validate(
                        parsed
                    )
                )

                return generated.model_dump(
                    mode="json"
                )

            try:

                generated = (
                    GeneratedStory.model_validate(
                        parsed
                    )
                )

                return generated.model_dump(
                    mode="json"
                )

            except ValidationError:
                pass

        # --------------------------------------------------------
        # RAW JSON TEXT
        # --------------------------------------------------------

        if not raw_text:

            raise StoryValidationError(
                "Gemini returned no story content."
            )

        cleaned = raw_text.strip()

        # --------------------------------------------------------
        # REMOVE MARKDOWN JSON FENCES
        # --------------------------------------------------------

        if cleaned.startswith("```"):

            lines = (
                cleaned.splitlines()
            )

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

            cleaned = (
                "\n".join(lines)
                .strip()
            )

        # --------------------------------------------------------
        # JSON DECODE
        # --------------------------------------------------------

        try:

            data = json.loads(
                cleaned
            )

        except json.JSONDecodeError as exc:

            raise StoryValidationError(
                "Gemini returned invalid JSON."
            ) from exc

        if not isinstance(
            data,
            dict,
        ):

            raise StoryValidationError(
                "Gemini returned JSON that "
                "is not an object."
            )

        # --------------------------------------------------------
        # PYDANTIC VALIDATION
        # --------------------------------------------------------

        generated = (
            GeneratedStory.model_validate(
                data
            )
        )

        return generated.model_dump(
            mode="json"
        )

    # ============================================================
    # PROMPT
    # ============================================================

    def _build_prompt(
        self,
        player_count: int,
        previous_fingerprints: list[str],
        attempt: int,
    ) -> str:

        previous_text = json.dumps(
            previous_fingerprints,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        correction = ""

        if attempt > 1:

            correction = (
                "\n\nCORRECTION ATTEMPT:\n"
                "The previous story generation did "
                "not pass validation.\n"
                "Be extremely strict about the JSON "
                "structure.\n"
                "Use only the requested fields.\n"
                "Do not invent fields.\n"
                "Every scene must be reachable.\n"
                "Every non-ending scene must eventually "
                "reach an ending.\n"
            )

        # IMPORTANT:
        #
        # This is deliberately assembled from normal strings.
        # There is NO triple-quoted prompt here.
        #
        # Therefore the previous:
        #
        # SyntaxError: unterminated triple-quoted string
        #
        # cannot happen from this prompt.

        return (
            "Create one short, funny, absurd "
            "interactive group story for the "
            "Telegram game WHAT HAPPENS?.\n\n"

            f"NUMBER OF HUMAN PLAYERS: "
            f"{player_count}\n\n"

            "Generate STORY DATA ONLY.\n"

            "The Python GameEngine executes "
            "the story deterministically.\n\n"

            "DO NOT generate:\n"
            "- Python\n"
            "- executable code\n"
            "- Telegram API calls\n"
            "- SQL\n"
            "- tools\n"
            "- scoring systems\n"
            "- coins\n"
            "- XP\n"
            "- levels\n"
            "- rankings\n"
            "- trivia\n"
            "- dangerous instructions\n"
            "- sexual content\n"
            "- hateful content\n"

            f"{correction}\n"

            "ROLES:\n"
            f"Create exactly {player_count} "
            f"to {max_roles(player_count)} "
            "playable roles.\n"

            "Every playable role must have:\n"
            "- unique id\n"
            "- unique name\n"
            "- secret_description\n"
            "- playable=true\n\n"

            "SCENES:\n"
            f"Create between {self.MIN_SCENES} "
            f"and {self.MAX_SCENES} scenes.\n"

            "Every scene must contain:\n"
            "- id\n"
            "- public_text\n"
            "- timer_seconds\n"
            "- eligible_roles\n"
            "- choices\n"
            "- transitions\n"
            "- ending\n"
            "- late_join\n"

            f"timer_seconds must be between "
            f"{self.MIN_TIMER_SECONDS} and "
            f"{self.MAX_TIMER_SECONDS}.\n"

            "first_scene_id must reference "
            "an existing scene.\n\n"

            "CHOICES:\n"
            "Every choice must contain:\n"
            "- id\n"
            "- label\n"
            "- public_event\n"
            "- effects\n"

            "next_scene is optional.\n"
            "If next_scene exists, it must reference "
            "an existing scene.\n\n"

            "ALLOWED CONDITION TYPES:\n"
            "- flag\n"
            "- variable\n"
            "- knowledge\n"
            "- relationship\n\n"

            "ALLOWED OPERATORS:\n"
            "- eq\n"
            "- ne\n"
            "- gt\n"
            "- gte\n"
            "- lt\n"
            "- lte\n"
            "- contains\n\n"

            "ALLOWED EFFECT GROUPS:\n"
            "- set_flags\n"
            "- remove_flags\n"
            "- set_variables\n"
            "- add_variables\n"
            "- relationships\n"
            "- knowledge\n"
            "- secrets\n"
            "- character_state\n"
            "- text\n"
            "- description\n\n"

            "REFERENCE RULES:\n"
            "- All role references must reference "
            "real roles.\n"
            "- All scene references must reference "
            "real scenes.\n"
            "- No duplicate IDs.\n"
            "- first_scene_id must be valid.\n"
            "- Every scene must be reachable.\n"
            "- Every non-ending scene must have a "
            "path to an ending.\n"
            "- No dead-end non-ending scenes.\n"
            "- Every scene must contain late_join.\n"
            "- Late-join role references must be valid.\n\n"

            "PREVIOUS STRUCTURAL FINGERPRINTS:\n"
            f"{previous_text}\n\n"

            "FINAL OUTPUT RULE:\n"
            "Return ONLY ONE valid JSON object.\n"
            "Do not use Markdown fences.\n"
            "Do not add commentary.\n"
            "Do not add explanations.\n"
            "Do not put anything before or after "
            "the JSON object."
        )

    # ============================================================
    # PLAYER VALIDATION
    # ============================================================

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

    # ============================================================
    # CLOSE GEMINI CLIENT
    # ============================================================

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
# ROLE COUNT HELPER
# ============================================================


def max_roles(
    player_count: int,
) -> int:

    return min(
        player_count + 2,
        StoryGenerator.MAX_PLAYERS,
    )
