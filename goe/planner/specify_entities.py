"""Step 2: entity stubs → fully specified Entities (single coordinated call)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from goe.models.entity import Entity
from goe.models.system import System
from goe.planner._atom_catalog import atom_catalog, misconfig_atom_catalog
from goe.planner._utils import call_json, render_system_prompt

if TYPE_CHECKING:
    from goe.planner.plan_entities import EntityStub

_SYSTEM_PROMPT_TEMPLATE = (Path(__file__).parent / "prompts" / "specify_entities.md").read_text()


def specify_entities(
    stubs: list["EntityStub"],
    systems: list[System],
    request: str,
    model: str,
) -> list[Entity]:
    systems_json = json.dumps([s.model_dump(mode="json") for s in systems], indent=2)
    all_stubs_json = json.dumps([s.model_dump(mode="json") for s in stubs], indent=2)

    from goe.planner._context import edge_type_list, runtime_list

    system_prompt = (
        _SYSTEM_PROMPT_TEMPLATE
        .replace("{ATOMS}", atom_catalog())
        .replace("{MISCONFIG_ATOMS}", misconfig_atom_catalog())
        .replace("{RUNTIMES}", runtime_list())
        .replace("{EDGE_TYPES}", edge_type_list())
    )

    user_msg = (
        f"## User Request\n\n{request}\n\n"
        f"## Available Systems\n\n```json\n{systems_json}\n```\n\n"
        f"## Entity Stubs to Specify\n\n```json\n{all_stubs_json}\n```\n\n"
        "Fully specify ALL entities in a single JSON array. "
        "Edge IDs must be consistent across all entities: every edge_id that appears in one "
        "entity's `provides` must appear verbatim in the downstream entity's `requires`, "
        "and vice versa. Invent the edge IDs once and reuse them — do not use different "
        "IDs for the same relationship."
    )
    data = call_json(model, system_prompt, user_msg, caller="planner.specify_entities")

    # LLM may return a list or a dict with a key
    if isinstance(data, dict):
        data = next(iter(data.values()))

    return [Entity.model_validate(e, strict=False) for e in data]
