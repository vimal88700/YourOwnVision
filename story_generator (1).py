from __future__ import annotations

import asyncio
import hashlib
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
# PYDANTIC STORY SCHEMA
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
# INTERNAL ERROR TYPES
# ============================================================


class GeminiQuotaExhausted(RuntimeError):
    """
    Gemini returned a quota/rate-limit exhaustion error.

    IMPORTANT:
    A daily free-tier quota error must NOT be retried repeatedly.
    """


class GeminiTemporaryError(RuntimeError):
    """
    Gemini returned a temporary server/capacity error.
    """


# ============================================================
# STORY GENERATOR
# ============================================================


class StoryGenerator:
    """
    Generates WHAT HAPPENS? story content.

    Architecture:

        Gemini available
            |
            v
        generate JSON
            |
            v
        Pydantic validation
            |
            v
        StoryValidator
            |
            v
        game

    If Gemini is unavailable, quota-exhausted, invalid, or temporarily
    overloaded, a deterministic local fallback story is generated and
    passed through the EXACT SAME validation pipeline.

    This means Gemini is an enhancement, not a single point of failure.
    """

    DEFAULT_MODEL = "gemini-3.8-flash"

    FALLBACK_MODELS = (
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    )

    # --------------------------------------------------------
    # IMPORTANT:
    # Never do 4 retries for a daily quota error.
    # --------------------------------------------------------

    MAX_RETRIES_PER_MODEL = 2

    INITIAL_RETRY_DELAY_SECONDS = 2.0
    MAX_RETRY_DELAY_SECONDS = 8.0

    MIN_PLAYERS = 1
    MAX_PLAYERS = 20

    MIN_SCENES = 3
    MAX_SCENES = 12

    MIN_TIMER_SECONDS = 10
    MAX_TIMER_SECONDS = 3600

    # Number of different local fallback structures that may
    # be attempted if one already exists in story_history.
    MAX_FALLBACK_VARIANTS = 20

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

    # ========================================================
    # PUBLIC GENERATION API
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

        logger.info(
            "Starting story generation. "
            "players=%s models=%s",
            player_count,
            self._get_models_to_try(),
        )

        # ----------------------------------------------------
        # GEMINI
        # ----------------------------------------------------

        gemini_story = await self._try_gemini(
            player_count=player_count,
            previous_fingerprints=fingerprints,
        )

        if gemini_story is not None:

            try:
                validated_story, fingerprint = (
                    self._validate_and_fingerprint(
                        gemini_story,
                        player_count=player_count,
                        previous_fingerprints=fingerprints,
                    )
                )

                logger.info(
                    "Gemini story generation succeeded. "
                    "model=%s fingerprint=%s",
                    self.model,
                    fingerprint,
                )

                return (
                    validated_story,
                    fingerprint,
                )

            except StoryValidationError as exc:

                logger.warning(
                    "Gemini story failed local validation: %s",
                    exc,
                )

            except Exception:

                logger.exception(
                    "Unexpected error while validating "
                    "Gemini story."
                )

        # ----------------------------------------------------
        # LOCAL FALLBACK
        # ----------------------------------------------------

        logger.warning(
            "Gemini unavailable or unusable. "
            "Switching to local fallback story."
        )

        fallback = self._generate_fallback_story(
            player_count=player_count,
            previous_fingerprints=fingerprints,
        )

        validated_story, fingerprint = (
            self._validate_and_fingerprint(
                fallback,
                player_count=player_count,
                previous_fingerprints=fingerprints,
            )
        )

        logger.warning(
            "Local fallback story ready. "
            "fingerprint=%s",
            fingerprint,
        )

        return (
            validated_story,
            fingerprint,
        )

    # ========================================================
    # GEMINI PIPELINE
    # ========================================================

    async def _try_gemini(
        self,
        *,
        player_count: int,
        previous_fingerprints: list[str],
    ) -> dict[str, Any] | None:

        prompt = self._build_prompt(
            player_count=player_count,
            previous_fingerprints=previous_fingerprints,
        )

        last_error: Exception | None = None

        for model_name in self._get_models_to_try():

            try:

                response = await self._generate_with_retry(
                    model_name=model_name,
                    contents=prompt,
                )

                self.model = model_name

                story = self._parse_gemini_response(
                    response
                )

                if story is None:
                    raise StoryValidationError(
                        "Gemini returned no usable story."
                    )

                return story

            except GeminiQuotaExhausted as exc:

                # ------------------------------------------------
                # CRITICAL FIX:
                #
                # DO NOT retry daily quota errors.
                #
                # We also do not continue hammering every model
                # after the same project quota is exhausted.
                # ------------------------------------------------

                last_error = exc

                logger.warning(
                    "Gemini daily quota exhausted. "
                    "Stopping Gemini attempts immediately. "
                    "model=%s error=%s",
                    model_name,
                    exc,
                )

                break

            except GeminiTemporaryError as exc:

                last_error = exc

                logger.warning(
                    "Gemini temporary failure after bounded "
                    "retries. Moving to next model. "
                    "model=%s error=%s",
                    model_name,
                    exc,
                )

                continue

            except StoryValidationError as exc:

                last_error = exc

                logger.warning(
                    "Gemini response could not be parsed. "
                    "model=%s error=%s",
                    model_name,
                    exc,
                )

                continue

            except Exception as exc:

                last_error = exc

                logger.warning(
                    "Gemini generation failed. "
                    "model=%s error=%s",
                    model_name,
                    exc,
                )

                # Invalid API key / invalid model / bad request:
                # don't waste time retrying the same request.
                if self._is_non_retryable_api_error(
                    exc
                ):
                    continue

        if last_error is not None:
            logger.warning(
                "Gemini generation unavailable. "
                "Local fallback will be used. "
                "last_error=%s",
                last_error,
            )

        return None

    async def _generate_with_retry(
        self,
        *,
        model_name: str,
        contents: str,
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

                response = (
                    await self.client.aio.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            system_instruction=(
                                "You are the story-content "
                                "generator for the "
                                "WHAT HAPPENS? Telegram game. "
                                "Generate story DATA ONLY. "
                                "Return one valid JSON object. "
                                "Never generate Python, SQL, "
                                "Telegram API calls, tools, "
                                "or executable code."
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

                error_kind = (
                    self._classify_gemini_error(exc)
                )

                # ------------------------------------------------
                # DAILY QUOTA:
                # ZERO RETRIES.
                # ------------------------------------------------

                if error_kind == "quota":

                    raise GeminiQuotaExhausted(
                        str(exc)
                    ) from exc

                # ------------------------------------------------
                # TEMPORARY 503 / server overload:
                # limited retry.
                # ------------------------------------------------

                if error_kind == "temporary":

                    if (
                        attempt
                        >= self.MAX_RETRIES_PER_MODEL
                    ):
                        raise GeminiTemporaryError(
                            str(exc)
                        ) from exc

                    delay = self._calculate_retry_delay(
                        attempt,
                        exc,
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

                    await asyncio.sleep(delay)
                    continue

                # ------------------------------------------------
                # Authentication / bad model / bad request:
                # don't retry.
                # ------------------------------------------------

                raise exc

        if last_error is not None:
            raise last_error

        raise RuntimeError(
            "Gemini request ended without a result."
        )

    # ========================================================
    # GEMINI ERROR CLASSIFICATION
    # ========================================================

    @staticmethod
    def _classify_gemini_error(
        exc: Exception,
    ) -> str:

        text = str(exc).upper()

        # Daily/free-tier quota exhaustion.
        quota_markers = (
            "RESOURCE_EXHAUSTED",
            "GENERATE_CONTENT_FREE_TIER_REQUESTS",
            "QUOTA EXCEEDED",
            "QUOTAEXCEEDED",
            "GENERATEREQUESTSPERDAYPERPROJECT",
        )

        if any(
            marker in text
            for marker in quota_markers
        ):
            return "quota"

        # 429 can mean different things.
        #
        # If it says quota, it was already classified above.
        # A generic 429 without quota text is treated as temporary.
        if (
            "429" in text
            or "TOO MANY REQUESTS" in text
        ):
            return "temporary"

        temporary_markers = (
            "503",
            "UNAVAILABLE",
            "SERVICE UNAVAILABLE",
            "500 INTERNAL",
            "INTERNAL",
            "502",
            "504",
            "DEADLINE_EXCEEDED",
            "TIMED OUT",
            "TIMEOUT",
        )

        if any(
            marker in text
            for marker in temporary_markers
        ):
            return "temporary"

        return "permanent"

    @staticmethod
    def _is_non_retryable_api_error(
        exc: Exception,
    ) -> bool:

        text = str(exc).upper()

        markers = (
            "400 INVALID_ARGUMENT",
            "API_KEY_INVALID",
            "API KEY NOT VALID",
            "401",
            "403",
            "404 NOT_FOUND",
            "NOT_FOUND",
            "INVALID_ARGUMENT",
        )

        return any(
            marker in text
            for marker in markers
        )

    def _calculate_retry_delay(
        self,
        attempt: int,
        exc: Exception,
    ) -> float:

        retry_after = (
            self._retry_after_seconds(exc)
        )

        if retry_after is not None:
            return min(
                retry_after,
                self.MAX_RETRY_DELAY_SECONDS,
            )

        exponential = min(
            self.INITIAL_RETRY_DELAY_SECONDS
            * (2 ** (attempt - 1)),
            self.MAX_RETRY_DELAY_SECONDS,
        )

        jitter = random.uniform(
            0.0,
            exponential * 0.25,
        )

        return min(
            exponential + jitter,
            self.MAX_RETRY_DELAY_SECONDS,
        )

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

        text = str(exc)

        # Examples:
        # retryDelay: 27190s
        # retry in 30s
        patterns = (
            r"retryDelay[^0-9]*(\d+(?:\.\d+)?)s",
            r"retry\s+in\s+(\d+(?:\.\d+)?)s",
            r"retry[- ]after[^0-9]*(\d+(?:\.\d+)?)",
        )

        for pattern in patterns:

            match = re.search(
                pattern,
                text,
                re.IGNORECASE,
            )

            if match:

                try:
                    return max(
                        0.0,
                        float(match.group(1)),
                    )

                except ValueError:
                    pass

        return None

    # ========================================================
    # MODEL LIST
    # ========================================================

    def _get_models_to_try(self) -> list[str]:

        configured = os.getenv(
            "GEMINI_FALLBACK_MODELS",
            "",
        )

        candidates: list[str] = [
            self.model,
        ]

        if configured.strip():

            candidates.extend(
                item.strip()
                for item in configured.split(",")
                if item.strip()
            )

        else:

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
                models.append(candidate)

        return models

    # ========================================================
    # GEMINI RESPONSE PARSING
    # ========================================================

    def _parse_gemini_response(
        self,
        response: Any,
    ) -> dict[str, Any] | None:

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

        # Structured response if the SDK provides one.
        if parsed is not None:

            try:

                if isinstance(
                    parsed,
                    GeneratedStory,
                ):
                    return parsed.model_dump(
                        mode="json"
                    )

                generated = (
                    GeneratedStory.model_validate(
                        parsed
                    )
                )

                return generated.model_dump(
                    mode="json"
                )

            except ValidationError as exc:

                raise StoryValidationError(
                    "Gemini structured response "
                    "did not match the story schema."
                ) from exc

        # Plain JSON response.
        if raw_text:

            cleaned = raw_text.strip()

            # Remove Markdown fences if a model ignored
            # the instruction.
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

            try:

                data = json.loads(
                    cleaned
                )

            except json.JSONDecodeError as exc:

                raise StoryValidationError(
                    "Gemini returned invalid JSON."
                ) from exc

            try:

                generated = (
                    GeneratedStory.model_validate(
                        data
                    )
                )

            except ValidationError as exc:

                raise StoryValidationError(
                    "Gemini returned JSON that "
                    "does not match the story schema."
                ) from exc

            return generated.model_dump(
                mode="json"
            )

        return None

    # ========================================================
    # VALIDATION
    # ========================================================

    def _validate_and_fingerprint(
        self,
        story: dict[str, Any],
        *,
        player_count: int,
        previous_fingerprints: list[str],
    ) -> tuple[dict[str, Any], str]:

        # Normalize the story into the exact graph format expected by
        # GameEngine. In particular, terminal-scene choices must point
        # to a real top-level ending. Gemini/local fallback stories may
        # describe the ending only on the scene itself.
        story = self._normalize_engine_targets(story)

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
                "Story failed semantic validation."
            )

            raise StoryValidationError(
                "Story failed semantic validation."
            ) from exc

        fingerprint = (
            StoryValidator.structural_fingerprint(
                validated_story
            )
        )

        if fingerprint in previous_fingerprints:

            raise StoryValidationError(
                "Story structure duplicates "
                "a previous story."
            )

        return (
            validated_story,
            fingerprint,
        )

    # ========================================================
    # ENGINE COMPATIBILITY NORMALIZATION
    # ========================================================

    @staticmethod
    def _normalize_engine_targets(
        story: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Convert scene-local endings into the explicit target format
        required by GameEngine.

        GameEngine accepts these choice targets:
            next_scene / next_scene_id / target_scene / ending_id / ending

        StoryValidator intentionally allows a terminal scene to carry
        its ending on the scene itself. The engine, however, still
        requires every choice to have a target. Therefore a choice in
        a terminal scene is explicitly pointed at that scene's ending.
        """
        normalized = json.loads(json.dumps(story, ensure_ascii=False))

        if not isinstance(normalized, dict):
            raise StoryValidationError("Story must be an object.")

        scenes = normalized.get("scenes")
        if not isinstance(scenes, list):
            raise StoryValidationError("Story scenes must be a list.")

        endings = normalized.get("endings", [])
        if not isinstance(endings, list):
            endings = []

        ending_ids = {
            str(item.get("id")).strip()
            for item in endings
            if isinstance(item, dict) and item.get("id")
        }

        for scene in scenes:
            if not isinstance(scene, dict):
                continue

            scene_id = str(scene.get("id", "")).strip()
            ending = scene.get("ending")
            explicit_terminal = bool(
                scene.get("is_ending", False)
                or scene.get("terminal", False)
            )

            if ending is None and not explicit_terminal:
                continue

            # Build a stable ending ID for this terminal scene.
            if isinstance(ending, dict):
                ending_id = str(
                    ending.get("id") or f"{scene_id}_ending"
                ).strip()
                ending_text = str(
                    ending.get("text") or "The story ends."
                ).strip()
                ending_conditions = ending.get("conditions", [])
            elif isinstance(ending, str) and ending.strip():
                ending_id = ending.strip()
                ending_text = ending.strip()
                ending_conditions = []
            else:
                ending_id = f"{scene_id}_ending"
                ending_text = str(
                    scene.get("public_text") or "The story ends."
                ).strip()
                ending_conditions = []

            scene["ending"] = {
                "id": ending_id,
                "text": ending_text,
                "conditions": ending_conditions
                if isinstance(ending_conditions, list)
                else [],
            }

            if ending_id not in ending_ids:
                endings.append(
                    {
                        "id": ending_id,
                        "text": ending_text,
                        "conditions": scene["ending"]["conditions"],
                    }
                )
                ending_ids.add(ending_id)

            choices = scene.get("choices", [])
            if not isinstance(choices, list):
                raise StoryValidationError(
                    f"Scene '{scene_id}' choices must be a list."
                )

            for choice in choices:
                if not isinstance(choice, dict):
                    continue

                # Never leave a terminal choice targetless.
                has_target = any(
                    choice.get(key) not in (None, "")
                    for key in (
                        "next_scene",
                        "next_scene_id",
                        "target_scene",
                        "target_scene_id",
                        "ending_id",
                        "ending",
                    )
                )

                if not has_target:
                    choice["ending_id"] = ending_id

        normalized["endings"] = endings
        return normalized

    # ========================================================
    # LOCAL FALLBACK STORY
    # ========================================================

    def _generate_fallback_story(
        self,
        *,
        player_count: int,
        previous_fingerprints: list[str],
    ) -> dict[str, Any]:

        logger.warning(
            "Building local fallback story. "
            "player_count=%s",
            player_count,
        )

        for variant in range(
            self.MAX_FALLBACK_VARIANTS
        ):

            story = self._build_fallback_variant(
                player_count=player_count,
                variant=variant,
            )

            try:

                validated, fingerprint = (
                    self._validate_and_fingerprint(
                        story,
                        player_count=player_count,
                        previous_fingerprints=(
                            previous_fingerprints
                        ),
                    )
                )

                logger.info(
                    "Validated local fallback story. "
                    "variant=%s fingerprint=%s",
                    variant,
                    fingerprint,
                )

                return validated

            except Exception as exc:

                logger.warning(
                    "Fallback variant %s rejected: %s",
                    variant,
                    exc,
                )

        raise StoryValidationError(
            "Unable to create a valid local fallback story."
        )

    def _build_fallback_variant(
        self,
        *,
        player_count: int,
        variant: int,
    ) -> dict[str, Any]:

        variant_pack = self._fallback_variant_pack(
            variant
        )

        prefix = (
            f"local_{variant}_"
        )

        roles: list[dict[str, Any]] = []

        role_names = [
            "Suspicious Intern",
            "Overconfident Detective",
            "Snack Scientist",
            "Confused Mayor",
            "Time Traveler",
            "Professional Nodding Expert",
            "Unlicensed Magician",
            "Emergency Penguin Consultant",
            "Mystery Gardener",
            "Captain of Bad Ideas",
            "Accidental Billionaire",
            "Neighborhood Wizard",
            "Invisible Accountant",
            "Drama Specialist",
            "Chief Button Presser",
            "Part-Time Astronaut",
            "Secret Karaoke Judge",
            "Professional Queue Skipper",
            "Museum Escape Artist",
            "Department of Weird Things",
        ]

        role_secrets = [
            "You secretly believe every problem can be solved with snacks.",
            "You are convinced someone here is hiding a ridiculous secret.",
            "You have been pretending to understand everything.",
            "You accidentally promised to fix the situation.",
            "You know one completely unnecessary fact that may become important.",
            "You are trying very hard to look responsible.",
            "You suspect the most obvious answer is probably wrong.",
            "You desperately want everyone to think your plan was intentional.",
        ]

        for index in range(
            player_count
        ):

            roles.append(
                {
                    "id": f"{prefix}role_{index + 1}",
                    "name": (
                        role_names[
                            (
                                index + variant
                            )
                            % len(role_names)
                        ]
                        + (
                            f" #{index + 1}"
                            if player_count > 1
                            else ""
                        )
                    ),
                    "secret_description": (
                        role_secrets[
                            (
                                index + variant
                            )
                            % len(role_secrets)
                        ]
                    ),
                    "playable": True,
                }
            )

        role_ids = [
            role["id"]
            for role in roles
        ]

        # ----------------------------------------------------
        # Scene 1
        # ----------------------------------------------------

        scene_one = {
            "id": f"{prefix}scene_1",
            "public_text": (
                f"{variant_pack['opening']} "
                f"There are {player_count} suspicious people "
                "in the room, and everyone insists this is "
                "completely normal."
            ),
            "timer_seconds": 45,
            "eligible_roles": role_ids,
            "choices": [
                {
                    "id": f"{prefix}choice_1a",
                    "label": variant_pack["choice_a"],
                    "public_event": (
                        "Someone makes a bold move "
                        "with absolutely no evidence."
                    ),
                    "next_scene": f"{prefix}scene_2",
                    "effects": {
                        "set_flags": {
                            f"{prefix}bold_move": True
                        },
                        "text": (
                            "A bold move has been made."
                        ),
                    },
                    "conditions": [],
                    "branch_priority": 0,
                },
                {
                    "id": f"{prefix}choice_1b",
                    "label": variant_pack["choice_b"],
                    "public_event": (
                        "Someone chooses caution "
                        "and immediately regrets it."
                    ),
                    "next_scene": f"{prefix}scene_2",
                    "effects": {
                        "set_flags": {
                            f"{prefix}cautious": True
                        },
                        "text": (
                            "Caution has entered the room."
                        ),
                    },
                    "conditions": [],
                    "branch_priority": 0,
                },
            ],
            "transitions": [],
            "ending": None,
            "late_join": {
                "allowed": True,
                "roles": role_ids,
                "conditions": [],
                "entry_scene": f"{prefix}scene_1",
            },
        }

        # ----------------------------------------------------
        # Scene 2
        # ----------------------------------------------------

        scene_two = {
            "id": f"{prefix}scene_2",
            "public_text": (
                f"{variant_pack['middle']} "
                "The situation has somehow become "
                "more complicated despite nobody "
                "doing anything useful."
            ),
            "timer_seconds": 40,
            "eligible_roles": role_ids,
            "choices": [
                {
                    "id": f"{prefix}choice_2a",
                    "label": variant_pack["choice_c"],
                    "public_event": (
                        "The group chooses chaos."
                    ),
                    "next_scene": f"{prefix}scene_3",
                    "effects": {
                        "add_variables": {
                            f"{prefix}chaos": 1
                        },
                        "text": (
                            "Chaos has increased by one."
                        ),
                    },
                    "conditions": [],
                    "branch_priority": 0,
                },
                {
                    "id": f"{prefix}choice_2b",
                    "label": variant_pack["choice_d"],
                    "public_event": (
                        "The group attempts diplomacy."
                    ),
                    "next_scene": f"{prefix}scene_3",
                    "effects": {
                        "add_variables": {
                            f"{prefix}diplomacy": 1
                        },
                        "text": (
                            "Diplomacy has been attempted."
                        ),
                    },
                    "conditions": [],
                    "branch_priority": 0,
                },
            ],
            "transitions": [],
            "ending": None,
            "late_join": {
                "allowed": True,
                "roles": role_ids,
                "conditions": [],
                "entry_scene": f"{prefix}scene_2",
            },
        }

        # ----------------------------------------------------
        # Scene 3 / ENDING
        # ----------------------------------------------------

        scene_three = {
            "id": f"{prefix}scene_3",
            "public_text": (
                f"{variant_pack['ending_setup']} "
                "Everyone looks at everyone else. "
                "Nobody knows who caused this."
            ),
            "timer_seconds": 35,
            "eligible_roles": role_ids,
            "choices": [
                {
                    "id": f"{prefix}choice_3a",
                    "label": variant_pack["choice_e"],
                    "public_event": (
                        "The group proudly accepts "
                        "responsibility for absolutely nothing."
                    ),
                    "next_scene": None,
                    "effects": {
                        "set_flags": {
                            f"{prefix}accepted_blame": True
                        },
                        "text": (
                            "The group accepts responsibility."
                        ),
                    },
                    "conditions": [],
                    "branch_priority": 0,
                },
                {
                    "id": f"{prefix}choice_3b",
                    "label": variant_pack["choice_f"],
                    "public_event": (
                        "Everyone points at someone else."
                    ),
                    "next_scene": None,
                    "effects": {
                        "set_flags": {
                            f"{prefix}pointed_fingers": True
                        },
                        "text": (
                            "The blame has been redirected."
                        ),
                    },
                    "conditions": [],
                    "branch_priority": 0,
                },
            ],
            "transitions": [],
            "ending": {
                "id": f"{prefix}ending",
                "text": (
                    f"{variant_pack['ending_text']} "
                    "Against all odds, the group survives "
                    "another completely unnecessary adventure."
                ),
                "conditions": [],
            },
            "late_join": {
                "allowed": True,
                "roles": role_ids,
                "conditions": [],
                "entry_scene": f"{prefix}scene_3",
            },
        }

        return {
            "title": variant_pack["title"],
            "first_scene_id": f"{prefix}scene_1",
            "roles": roles,
            "scenes": [
                scene_one,
                scene_two,
                scene_three,
            ],
        }

    # ========================================================
    # FALLBACK VARIANTS
    # ========================================================

    @staticmethod
    def _fallback_variant_pack(
        variant: int,
    ) -> dict[str, str]:

        packs = [
            {
                "title": "The Extremely Suspicious Sandwich",
                "opening": (
                    "A sandwich has appeared on the table "
                    "with no explanation."
                ),
                "middle": (
                    "The sandwich has somehow acquired "
                    "political influence."
                ),
                "ending_setup": (
                    "The sandwich is now demanding answers."
                ),
                "choice_a": "Interrogate the sandwich",
                "choice_b": "Pretend the sandwich is normal",
                "choice_c": "Hold a sandwich meeting",
                "choice_d": "Offer the sandwich a promotion",
                "choice_e": "Take credit for the sandwich",
                "choice_f": "Blame the sandwich",
                "ending_text": (
                    "The sandwich is promoted to honorary team leader."
                ),
            },
            {
                "title": "The Button Nobody Should Press",
                "opening": (
                    "A giant red button appears beside the door."
                ),
                "middle": (
                    "The button is now humming ominously."
                ),
                "ending_setup": (
                    "The button begins counting down from three."
                ),
                "choice_a": "Press the button immediately",
                "choice_b": "Stand several steps away",
                "choice_c": "Ask the button politely",
                "choice_d": "Put a hat on the button",
                "choice_e": "Press it together",
                "choice_f": "Accuse someone of pressing it",
                "ending_text": (
                    "The button does nothing, which is somehow worse."
                ),
            },
            {
                "title": "The Mystery Meeting",
                "opening": (
                    "Everyone has been invited to a meeting "
                    "that nobody remembers scheduling."
                ),
                "middle": (
                    "The meeting agenda simply says: 'Probably You.'"
                ),
                "ending_setup": (
                    "A mysterious clipboard arrives."
                ),
                "choice_a": "Volunteer immediately",
                "choice_b": "Hide behind someone",
                "choice_c": "Invent an agenda",
                "choice_d": "Vote for the clipboard",
                "choice_e": "Declare the meeting successful",
                "choice_f": "Declare someone else responsible",
                "ending_text": (
                    "The meeting ends with absolutely no useful result."
                ),
            },
            {
                "title": "The Emergency Pigeon",
                "opening": (
                    "An extremely serious-looking pigeon "
                    "has entered the room."
                ),
                "middle": (
                    "The pigeon appears to know more than everyone."
                ),
                "ending_setup": (
                    "The pigeon places one mysterious object "
                    "on the table."
                ),
                "choice_a": "Trust the pigeon",
                "choice_b": "Question the pigeon",
                "choice_c": "Offer the pigeon a chair",
                "choice_d": "Pretend not to notice",
                "choice_e": "Elect the pigeon",
                "choice_f": "Blame the pigeon",
                "ending_text": (
                    "The pigeon leaves without explaining anything."
                ),
            },
            {
                "title": "Operation: Find the Missing Spoon",
                "opening": (
                    "The only spoon in the building has disappeared."
                ),
                "middle": (
                    "Everyone suddenly has a different theory."
                ),
                "ending_setup": (
                    "A spoon-shaped shadow appears on the wall."
                ),
                "choice_a": "Launch an investigation",
                "choice_b": "Declare the spoon irrelevant",
                "choice_c": "Search the obvious place",
                "choice_d": "Search the least obvious place",
                "choice_e": "Declare victory",
                "choice_f": "Blame the nearest person",
                "ending_text": (
                    "The spoon is eventually found somewhere "
                    "that nobody checked."
                ),
            },
            {
                "title": "The World's Least Secret Secret",
                "opening": (
                    "Someone announces that they have a secret."
                ),
                "middle": (
                    "Unfortunately, everyone already knows it."
                ),
                "ending_setup": (
                    "The secret becomes dramatically less secret."
                ),
                "choice_a": "Pretend to be surprised",
                "choice_b": "Ask for the secret anyway",
                "choice_c": "Create a bigger secret",
                "choice_d": "Write it on a giant sign",
                "choice_e": "Reveal the secret dramatically",
                "choice_f": "Pretend nobody heard it",
                "ending_text": (
                    "The secret is officially classified as "
                    "not very secret."
                ),
            },
            {
                "title": "The Elevator to Somewhere",
                "opening": (
                    "An elevator opens even though nobody pressed it."
                ),
                "middle": (
                    "The elevator has no buttons except one "
                    "labelled 'Definitely'."
                ),
                "ending_setup": (
                    "The elevator doors begin closing."
                ),
                "choice_a": "Enter confidently",
                "choice_b": "Wait for instructions",
                "choice_c": "Press Definitely",
                "choice_d": "Ask where it goes",
                "choice_e": "Take the mysterious elevator",
                "choice_f": "Let someone else go first",
                "ending_text": (
                    "The elevator opens on the same floor."
                ),
            },
            {
                "title": "The Completely Normal Treasure Map",
                "opening": (
                    "A treasure map appears underneath the snacks."
                ),
                "middle": (
                    "The map claims the treasure is nearby."
                ),
                "ending_setup": (
                    "The map points directly at the group."
                ),
                "choice_a": "Follow the map",
                "choice_b": "Follow the snacks instead",
                "choice_c": "Dig immediately",
                "choice_d": "Ask for directions",
                "choice_e": "Declare yourselves treasure",
                "choice_f": "Hide the map",
                "ending_text": (
                    "The treasure turns out to be teamwork, "
                    "which nobody requested."
                ),
            },
        ]

        selected = packs[
            variant % len(packs)
        ]

        cycle = (
            variant // len(packs)
        )

        if cycle == 0:
            return dict(selected)

        # Make subsequent fallback structures different enough
        # to avoid repeatedly producing the exact same fingerprint.
        suffix = f" Variant {cycle + 1}"

        result = dict(selected)

        result["title"] = (
            selected["title"]
            + suffix
        )

        result["opening"] = (
            selected["opening"]
            + f" This is incident #{cycle + 1}."
        )

        result["middle"] = (
            selected["middle"]
            + " The situation somehow feels familiar."
        )

        result["ending_setup"] = (
            selected["ending_setup"]
            + " Everyone becomes suspicious again."
        )

        result["ending_text"] = (
            selected["ending_text"]
            + f" This was attempt #{cycle + 1}."
        )

        return result

    # ========================================================
    # PROMPT
    # ========================================================

    def _build_prompt(
        self,
        *,
        player_count: int,
        previous_fingerprints: list[str],
    ) -> str:

        previous_text = json.dumps(
            previous_fingerprints,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        return f"""
Create one short, funny, absurd interactive group story
for the Telegram game WHAT HAPPENS?.

NUMBER OF HUMAN PLAYERS:
{player_count}

Generate STORY DATA ONLY.

The Python GameEngine executes the story deterministically.

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
- flag
- variable
- knowledge
- relationship

ALLOWED OPERATORS:
- eq
- ne
- gt
- gte
- lt
- lte
- contains

ALLOWED EFFECT GROUPS:
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

STORY GRAPH:
- first_scene_id must be valid
- every scene must be reachable
- no unreachable scenes
- no dead-end non-ending scenes
- no duplicate IDs
- no invalid references
- the final path must contain an ending

LATE JOIN:

Every scene must contain late_join.

Late-join role references must be valid.

Previous structural fingerprints:

{previous_text}

Return ONLY one valid JSON object.
Do NOT use Markdown fences.
Do NOT add explanations.
Do NOT add commentary.
"""

    # ========================================================
    # PLAYER VALIDATION
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
    # LIFECYCLE
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

        if close_method is None:
            return

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
