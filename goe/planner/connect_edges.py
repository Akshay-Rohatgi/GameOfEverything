"""Step 3: entities + systems → list[Edge]."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from goe.models.edge import Edge
from goe.models.system import System
from goe.planner._utils import call_json, render_system_prompt

if TYPE_CHECKING:
    from goe.graph.models import Violation
    from goe.models.entity import Entity

_SYSTEM_PROMPT = render_system_prompt(
    (Path(__file__).parent / "prompts" / "connect_edges.md").read_text(
        encoding="utf-8"
    )
)


def connect_edges(
    entities: list["Entity"],
    systems: list[System],
    model: str,
    violations: list["Violation"] | None = None,
) -> list[Edge]:
    entities_json = json.dumps(
        [e.model_dump(mode="json") for e in entities], indent=2
    )
    systems_json = json.dumps([s.model_dump(mode="json") for s in systems], indent=2)

    user_msg = (
        f"## Entities\n\n```json\n{entities_json}\n```\n\n"
        f"## Systems\n\n```json\n{systems_json}\n```\n\n"
    )

    if violations:
        violations_text = "\n".join(
            f"- [{v.check}] {v.message}" for v in violations
        )
        user_msg += (
            f"## Validation Failures from Previous Attempt\n\n"
            f"Fix these issues when creating edges:\n{violations_text}\n\n"
        )

    user_msg += "Create the Edge objects that wire these entities together."

    data = call_json(model, _SYSTEM_PROMPT, user_msg, caller="planner.connect_edges")
    return [Edge.model_validate(e, strict=False) for e in data]
