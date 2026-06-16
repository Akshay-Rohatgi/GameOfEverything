"""Step 1.5: grade and correct entity stubs against atom catalog and runtime rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from goe.planner._atom_catalog import atom_catalog
from goe.planner._utils import call_json

if TYPE_CHECKING:
    from goe.models.system import System
    from goe.planner.plan_entities import EntityStub

_SYSTEM_PROMPT_TEMPLATE = (Path(__file__).parent / "prompts" / "grade_stubs.md").read_text()


def grade_stubs(
    stubs: list["EntityStub"],
    systems: list["System"],
    model: str,
) -> list["EntityStub"]:
    """Grade and correct entity stubs via LLM review.

    Sends stubs + atom catalog + rubric to the LLM. Returns corrected stubs.
    The LLM may fix mistakes (wrong runtime, invalid atom) or remove unsalvageable stubs.
    """
    from goe.planner.plan_entities import EntityStub

    # Render atom catalog into the system prompt
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.replace("{ATOM_CATALOG}", atom_catalog())

    stubs_json = json.dumps([s.model_dump(mode="json") for s in stubs], indent=2)
    systems_json = json.dumps([s.model_dump(mode="json") for s in systems], indent=2)

    user_msg = (
        f"## Systems\n\n```json\n{systems_json}\n```\n\n"
        f"## Entity Stubs to Grade\n\n```json\n{stubs_json}\n```\n\n"
        "Grade and correct these stubs. Return the corrected JSON array."
    )

    data = call_json(model, system_prompt, user_msg, caller="planner.grade_stubs")

    # Parse corrected stubs
    # The LLM may have added "_fix_note" fields; drop them
    corrected = []
    for item in data:
        item.pop("_fix_note", None)  # Remove comment field if present
        corrected.append(EntityStub.model_validate(item, strict=False))

    return corrected
