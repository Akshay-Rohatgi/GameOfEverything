"""Step 0: user request → list[System]."""

from __future__ import annotations

from pathlib import Path

from goe.distros import available_distros
from goe.models.system import System
from goe.planner._utils import call_json

_SYSTEM_PROMPT_TEMPLATE = (
    Path(__file__).parent / "prompts" / "design_systems.md"
).read_text(encoding="utf-8")


def _system_prompt() -> str:
    # Inject the real registered distros so the allowed-OS list tracks
    # goe.distros (e.g. Week-8 rocky) instead of a hardcoded ubuntu/debian pair.
    allowed = ", ".join(f'`"{d}"`' for d in available_distros())
    return _SYSTEM_PROMPT_TEMPLATE.replace("{ALLOWED_OSES}", allowed)


def design_systems(request: str, model: str) -> list[System]:
    user_msg = f"Design the infrastructure systems needed for this attack scenario:\n\n{request}"
    data = call_json(model, _system_prompt(), user_msg, caller="planner.design_systems")
    return [System.model_validate(s, strict=False) for s in data]
