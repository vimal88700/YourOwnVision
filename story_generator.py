from future import annotations

from typing import Any

from google import genai
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from story_validator import (
StoryValidationError,
StoryValidator,
)

============================================================

GEMINI STRUCTURED OUTPUT MODELS

============================================================

These Pydantic models describe the shape Gemini is allowed

to generate.

They are NOT the authoritative gameplay validator.

The authoritative validation still happens through

StoryValidator after Gemini returns the data.

============================================================

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

relationships: list[
    GeneratedRelationshipEffect
] = Field(default_factory=list)

knowledge: list[
    GeneratedKnowledge
] = Field(default_factory=list)

secrets: list[
    GeneratedSecret
] = Field(default_factory=list)

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

conditions: list[
    GeneratedCondition
] = Field(default_factory=list)

branch_priority: int = 0

public_event_variants: list[str] | None = None

class GeneratedTransition(BaseModel):
model_config = ConfigDict(extra="forbid")

next_scene: str

conditions: list[
    GeneratedCondition
] = Field(default_factory=list)

class GeneratedEnding(BaseModel):
model_config = ConfigDict(extra="forbid")

id: str
text: str

conditions: list[
    GeneratedCondition
] = Field(default_factory=list)

class GeneratedLateJoin(BaseModel):
model_config = ConfigDict(extra="forbid")

allowed: bool = True

roles: list[str] = Field(
    default_factory=list
)

conditions: list[
    GeneratedCondition
] = Field(default_factory=list)

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

choices: list[
    GeneratedChoice
]

transitions: list[
    GeneratedTransition
] = Field(default_factory=list)

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

============================================================

GENERATOR

============================================================

class StoryGenerator:
"""
Generates story content with Gemini.

Gemini is ONLY responsible for preparing story content.

The execution pipeline is:

    Gemini
      ↓
    Pydantic structured output
      ↓
    StoryValidator
      ↓
    validated story JSON
      ↓
    GameEngine

This class must NEVER decide player outcomes during gameplay.
"""

DEFAULT_MODEL = "gemini-3.8-flash"

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
):
    if not api_key or not api_key.strip():
        raise ValueError(
            "Gemini API key is required."
        )

    self.model = (
        model or self.DEFAULT_MODEL
    ).strip()

    if self.model != self.DEFAULT_MODEL:
        raise ValueError(
            "This project is configured specifically "
            "for gemini-3.8-flash. "
            f"Received: {self.model}"
        )

    self.client = genai.Client(
        api_key=api_key
    )

# ========================================================
# GENERATION
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
        if isinstance(
            value,
            str,
        )
        and value.strip()
    ]

    prompt = self._build_prompt(
        player_count=player_count,
        previous_fingerprints=fingerprints,
    )

    try:
        response = await self.client.aio.interactions.create(
            model=self.model,
            input=prompt,
            system_instruction=(
                "You are the story-content generator for "
                "the WHAT HAPPENS? Telegram game. "
                "Generate only story data. "
                "Never make gameplay decisions after generation. "
                "Never generate executable code. "
                "Never generate tool calls."
            ),
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": GeneratedStory.model_json_schema(),
            },
            generation_config={
                "thinking_level": "medium",
            },
        )

    except Exception as exc:
        raise RuntimeError(
            "Gemini story generation failed."
        ) from exc

    raw_text = getattr(
        response,
        "output_text",
        None,
    )

    if not raw_text:
        raise RuntimeError(
            "Gemini returned no story content."
        )

    # ----------------------------------------------------
    # First validation layer:
    # Pydantic structured-output validation.
    # ----------------------------------------------------

    try:
        generated = GeneratedStory.model_validate_json(
            raw_text
        )
    except ValidationError as exc:
        raise StoryValidationError(
            "Gemini returned story data that does not "
            "match the required structured schema."
        ) from exc

    story = generated.model_dump(
        mode="json"
    )

    # ----------------------------------------------------
    # Second validation layer:
    # semantic/cross-reference validation.
    # ----------------------------------------------------

    try:
        validated_story = StoryValidator.validate(
            story,
            player_count=player_count,
        )
    except StoryValidationError:
        raise
    except Exception as exc:
        raise StoryValidationError(
            "Generated story failed semantic validation."
        ) from exc

    fingerprint = (
        StoryValidator.structural_fingerprint(
            validated_story
        )
    )

    # ----------------------------------------------------
    # Never silently accept a structurally duplicated
    # story when the caller has supplied previous history.
    # ----------------------------------------------------

    if fingerprint in fingerprints:
        raise StoryValidationError(
            "Gemini generated a story whose structure "
            "duplicates a previous story."
        )

    return (
        validated_story,
        fingerprint,
    )

# ========================================================
# PROMPT
# ========================================================

def _build_prompt(
    self,
    player_count: int,
    previous_fingerprints: list[str],
) -> str:

    previous_text = (
        json_list(previous_fingerprints)
        if previous_fingerprints
        else "[]"
    )

    return f"""

Create one short, funny, absurd interactive group story
for the Telegram game:

WHAT HAPPENS?

NUMBER OF HUMAN PLAYERS:
{player_count}

The story will be executed by a deterministic Python game
engine after you finish generating it.

Your job is ONLY to prepare the story content.

You must NOT:

- make gameplay decisions after generation
- call tools
- generate executable code
- generate Python
- generate Telegram API calls
- generate SQL
- create scoring systems
- create coins
- create XP
- create levels
- create rankings
- create leaderboards
- require trivia
- require real-world personal information
- require political persuasion
- provide dangerous instructions
- create sexual content
- create hateful content

GAME DESIGN:

- This is a casual comedy game.
- Players receive hidden roles.
- Players make decisions privately.
- Public story events are visible to the group.
- Choices must have deterministic effects.
- The engine, not Gemini, determines gameplay.
- The story should be understandable without external
  knowledge.
- The story should be short enough for Telegram.
- The story should contain genuine branching.
- Different choices should create meaningfully different
  consequences.
- The story should eventually reach a funny ending.
- Late joining must be supported consistently.
- Every human player must be able to receive a playable role.
- There must be at least as many playable roles as players.

ROLE REQUIREMENTS:

Create exactly {player_count} to {max_roles(player_count)}
playable roles.

Every playable role must have:

- unique id
- unique name
- secret_description
- playable=true

You may include additional non-playable roles only when they
are useful to the story.

SCENE REQUIREMENTS:

Create between {self.MIN_SCENES} and {self.MAX_SCENES} scenes.

Every scene must contain:

- unique id
- public_text
- timer_seconds
- eligible_roles
- choices

Use timers between:
{self.MIN_TIMER_SECONDS}
and
{self.MAX_TIMER_SECONDS}
seconds.

The first scene must be explicitly identified using
first_scene_id.

CHOICE REQUIREMENTS:

Every choice must contain:

- unique id
- label
- public_event
- effects

A choice may contain next_scene.

If next_scene is present, it MUST reference an existing scene.

Conditions may use only:

- flag
- variable
- knowledge
- relationship

Condition operators may use only:

- eq
- ne
- gt
- gte
- lt
- lte
- contains

EFFECT REQUIREMENTS:

Effects must be plain JSON data.

Allowed effect groups are:

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

Relationships must reference real role IDs.

Knowledge must reference real role IDs.

Secrets must reference real role IDs.

STORY GRAPH:

- The first scene must be reachable.
- Every generated scene must be reachable from the first
  scene.
- Every reachable scene must eventually be able to reach
  an ending.
- At least one scene must be an ending.
- Ending scenes must terminate the story.
- Do not create branches that lead to dead ends.
- Do not reference nonexistent scenes.
- Do not reference nonexistent roles.
- Do not duplicate IDs.

LATE JOINING:

Provide a late_join object for each scene.

Late joining must not corrupt the current story.

If late joining is allowed, roles must reference real roles.

If entry_scene is supplied, it must reference a real scene.

NOVELTY:

Previous structural fingerprints are listed below.

Do NOT intentionally reproduce those structures.

Previous fingerprints:
{previous_text}

IMPORTANT:

Return ONLY the requested structured JSON object.

Do not wrap the JSON in Markdown.

Do not add commentary before or after the JSON.
"""

# ========================================================
# PLAYER COUNT
# ========================================================

@classmethod
def _validate_player_count(
    cls,
    player_count: int,
) -> None:

    if not isinstance(
        player_count,
        int,
    ) or isinstance(
        player_count,
        bool,
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
            f"player_count must be between "
            f"{cls.MIN_PLAYERS} and "
            f"{cls.MAX_PLAYERS}."
        )

# ========================================================
# CLIENT LIFECYCLE
# ========================================================

async def close(self) -> None:
    """
    Close the async Gemini client.

    The application can call this during graceful shutdown.
    """

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
        await close_method()

============================================================

SMALL PROMPT HELPERS

============================================================

def max_roles(
player_count: int,
) -> int:
"""
Keep generated stories compact.

We normally want exactly the number of players plus at most
two additional playable roles.
"""

return min(
    player_count + 2,
    StoryGenerator.MAX_PLAYERS,
)

def json_list(
values: list[str],
) -> str:
"""
Serialize fingerprints safely for inclusion in the prompt.
"""

import json

return json.dumps(
    values,
    ensure_ascii=False,
    separators=(
        ",",
        ":",
    ),
)
