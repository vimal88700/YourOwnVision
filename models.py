from dataclasses import dataclass
from typing import Any


@dataclass
class Player:
    game_id: str
    user_id: int
    username: str
    display_name: str
    role_id: str | None = None
    missed_decisions: int = 0
    active: bool = True
    joined_round: int = 0


@dataclass
class ChoiceResult:
    text: str
    next_scene: str | None
    effects: dict[str, Any]
    public_event: str
