"""Step 1: systems + user request → list[EntityStub]."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from goe.models.system import System
from goe.planner._utils import call_json

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "plan_entities.md").read_text()


class EntityStub(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    description: str
    system_id: str


def plan_entities(request: str, systems: list[System], model: str) -> list[EntityStub]:
    systems_json = json.dumps([s.model_dump(mode="json") for s in systems], indent=2)
    user_msg = (
        f"## User Request\n\n{request}\n\n"
        f"## Available Systems\n\n```json\n{systems_json}\n```\n\n"
        "Decompose this scenario into entity stubs."
    )
    data = call_json(model, _SYSTEM_PROMPT, user_msg)
    return [EntityStub.model_validate(s) for s in data]
