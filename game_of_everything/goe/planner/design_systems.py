"""Step 0: user request → list[System]."""

from __future__ import annotations

from pathlib import Path

from goe.models.system import System
from goe.planner._utils import call_json

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "design_systems.md").read_text()


def design_systems(request: str, model: str) -> list[System]:
    user_msg = f"Design the infrastructure systems needed for this attack scenario:\n\n{request}"
    data = call_json(model, _SYSTEM_PROMPT, user_msg)
    return [System.model_validate(s, strict=False) for s in data]
