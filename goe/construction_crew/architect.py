"""Architect agent — turns an entity spec into an architecture plan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from goe.models.entity import Entity

from goe.construction_crew.atoms import load_atom

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "architect_system.md"
).read_text(encoding="utf-8")


class EndpointSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    method: str
    path: str
    description: str
    vulnerable: bool = False
    vulnerability_notes: str = ""


class TableSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    columns: list[str]
    seed_data: str = ""


class DataModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    tables: list[TableSpec] = []


class ArchitectPlan(BaseModel):
    model_config = ConfigDict(extra="allow")
    summary: str
    runtime: str
    app_description: str = ""
    endpoints: list[EndpointSpec] = []
    vulnerability_placement: str
    data_model: DataModel = DataModel()
    attack_entry_point: str
    success_indicator: str
    extra_npm_packages: list[str] = []
    extra_pip_packages: list[str] = []
    notes: str = ""


def plan(entity: "Entity", incoming_edges: dict, system_context: str | None = None) -> ArchitectPlan:
    """Call the Architect LLM to produce an architecture plan for the entity.

    Args:
        system_context: Optional rendered markdown describing this entity's system, the
            services the platform already provides there, and sibling entities — so the plan
            covers only this entity's own link in the chain.
    """
    from goe.bedrock import call
    from goe.config import GoEConfig

    cfg = GoEConfig.get()
    model = cfg.model_for("architect")

    atom_content = "\n\n".join(
        f"## Atom: {a}\n{load_atom(a)}" for a in (entity.atoms or [])
    )

    context_section = f"{system_context}\n" if system_context else ""

    user_msg = f"""## Entity Spec

```json
{entity.model_dump_json(indent=2)}
```

{context_section}## Incoming Edge Values

```json
{json.dumps(incoming_edges, indent=2)}
```

## Relevant Atom Content

{atom_content or "(no atoms specified)"}

Design an architecture plan for this entity. Use the `runtime` field to determine the type:
- Web runtime (express/flask/apache_php): plan a vulnerable web application.
- Ubuntu runtime: plan an OS-level misconfiguration or system vulnerability.
Output ONLY valid JSON matching the schema in the system prompt."""

    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": user_msg}], caller="architect")

    # Strip markdown fences if the model added them
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    try:
        data = json.loads(raw)
        return ArchitectPlan.model_validate(data)
    except Exception as e:
        # Retry once with the error
        retry_msg = f"{user_msg}\n\nYour previous response failed to parse: {e}\n\nOutput ONLY valid JSON."
        raw2 = call(model_id=model, system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": retry_msg}], caller="architect.retry")
        raw2 = raw2.strip()
        if raw2.startswith("```"):
            raw2 = raw2.split("\n", 1)[1]
            raw2 = raw2.rsplit("```", 1)[0]
        data = json.loads(raw2)
        return ArchitectPlan.model_validate(data)
