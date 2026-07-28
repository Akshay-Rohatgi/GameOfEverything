"""Step 0.5: user request + systems → killchain (ordered attack sequence)."""

from __future__ import annotations

import json
from pathlib import Path

from goe.models.system import System

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "plan_killchain.md"
).read_text(encoding="utf-8")


def plan_killchain(request: str, systems: list[System], model: str) -> str:
    """Call Bedrock to produce an ordered attack killchain (plain text, not JSON)."""
    from goe.bedrock import call

    systems_json = json.dumps([s.model_dump(mode="json") for s in systems], indent=2)
    user_msg = (
        f"## User Request\n\n{request}\n\n"
        f"## Systems\n\n```json\n{systems_json}\n```\n\n"
        "Produce the attack killchain."
    )

    return call(
        model_id=model,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        caller="planner.plan_killchain",
    )
