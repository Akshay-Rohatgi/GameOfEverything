"""Step 1: systems + user request → list[EntityStub]."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from goe.models.system import System
from goe.planner._atom_catalog import atom_catalog, misconfig_atom_catalog
from goe.planner._utils import call_json

_SYSTEM_PROMPT_TEMPLATE = (
    Path(__file__).parent / "prompts" / "plan_entities.md"
).read_text(encoding="utf-8")


class EntityStub(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    description: str
    system_id: str
    runtime: str = "ubuntu"
    atoms: list[str] = Field(default_factory=list)


def plan_entities(request: str, systems: list[System], model: str, killchain: str = "") -> list[EntityStub]:
    # Render atom catalogs into the system prompt
    system_prompt = (
        _SYSTEM_PROMPT_TEMPLATE
        .replace("{ATOM_CATALOG}", atom_catalog())
        .replace("{MISCONFIG_ATOM_CATALOG}", misconfig_atom_catalog())
    )

    systems_json = json.dumps([s.model_dump(mode="json") for s in systems], indent=2)
    user_msg = f"## User Request\n\n{request}\n\n"
    user_msg += f"## Available Systems\n\n```json\n{systems_json}\n```\n\n"

    if killchain:
        user_msg += f"## Attack Killchain (entities should follow this ordering)\n\n{killchain}\n\n"

    user_msg += "Decompose this scenario into entity stubs."

    data = call_json(model, system_prompt, user_msg, caller="planner.plan_entities")
    return [EntityStub.model_validate(s) for s in data]
