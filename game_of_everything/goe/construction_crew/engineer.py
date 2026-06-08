"""Engineer agent — turns an entity spec into an architecture plan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from goe.models.entity import Entity

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "engineer_system.md").read_text()
_ATOMS_DIR = Path(__file__).resolve().parent.parent.parent / "atoms"


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


class EngineerPlan(BaseModel):
    model_config = ConfigDict(extra="allow")
    summary: str
    runtime: str
    app_description: str
    endpoints: list[EndpointSpec]
    vulnerability_placement: str
    data_model: DataModel = DataModel()
    attack_entry_point: str
    success_indicator: str
    extra_npm_packages: list[str] = []
    extra_pip_packages: list[str] = []
    notes: str = ""


def _load_atom(atom_id: str) -> str:
    """Read atom markdown content by ID."""
    for path in _ATOMS_DIR.rglob(f"{atom_id}.md"):
        return path.read_text()
    return f"(atom {atom_id!r} not found)"


def plan(entity: "Entity", incoming_edges: dict) -> EngineerPlan:
    """Call the Engineer LLM to produce an architecture plan for the entity."""
    from goe.bedrock import call
    from goe.config import GoEConfig

    cfg = GoEConfig.get()
    model = cfg.model_for("engineer")

    atom_content = "\n\n".join(
        f"## Atom: {a}\n{_load_atom(a)}" for a in (entity.atoms or [])
    )

    user_msg = f"""## Entity Spec

```json
{entity.model_dump_json(indent=2)}
```

## Incoming Edge Values

```json
{json.dumps(incoming_edges, indent=2)}
```

## Relevant Atom Content

{atom_content or "(no atoms specified)"}

Design an architecture plan for this entity. Use the `runtime` field to determine the type:
- Web runtime (express/flask/apache_php): plan a vulnerable web application.
- Ubuntu runtime: plan an OS-level misconfiguration or system vulnerability.
Output ONLY valid JSON matching the schema in the system prompt."""

    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": user_msg}])

    # Strip markdown fences if the model added them
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    try:
        data = json.loads(raw)
        return EngineerPlan.model_validate(data)
    except Exception as e:
        # Retry once with the error
        retry_msg = f"{user_msg}\n\nYour previous response failed to parse: {e}\n\nOutput ONLY valid JSON."
        raw2 = call(model_id=model, system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": retry_msg}])
        raw2 = raw2.strip()
        if raw2.startswith("```"):
            raw2 = raw2.split("\n", 1)[1]
            raw2 = raw2.rsplit("```", 1)[0]
        data = json.loads(raw2)
        return EngineerPlan.model_validate(data)
