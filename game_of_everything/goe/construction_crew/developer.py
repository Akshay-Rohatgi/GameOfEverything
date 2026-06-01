"""Developer agent — turns an architecture plan into BuildArtifact + source code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.models.entity import Entity
    from goe.construction_crew.engineer import EngineerPlan

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "developer_system.md").read_text()
_RUNTIMES_DIR = Path(__file__).resolve().parent.parent / "runtimes" / "templates"


def _load_runtime_spec(runtime_id: str) -> str:
    path = _RUNTIMES_DIR / f"{runtime_id}.yaml"
    return path.read_text() if path.exists() else f"(runtime spec for {runtime_id!r} not found)"


def develop(
    entity: "Entity",
    plan: "EngineerPlan",
    incoming_edges: dict,
) -> tuple:
    """Call the Developer LLM to produce source code and a BuildArtifact.

    Returns:
        (BuildArtifact, outgoing_values: dict[str, str])
    """
    from goe.bedrock import call
    from goe.config import GoEConfig
    from goe.models.artifacts import BuildArtifact, DBSetup

    cfg = GoEConfig.get()
    model = cfg.model_for("developer")

    runtime_spec = _load_runtime_spec(plan.runtime)

    user_msg = f"""## Entity Spec

```json
{entity.model_dump_json(indent=2)}
```

## Architecture Plan

```json
{plan.model_dump_json(indent=2)}
```

## Runtime Spec

```yaml
{runtime_spec}
```

## Incoming Edge Values

```json
{json.dumps(incoming_edges, indent=2)}
```

Implement the application exactly as specified in the architecture plan.
Output ONLY valid JSON matching the schema in the system prompt."""

    def _parse(raw: str) -> tuple:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]
        data = json.loads(raw)

        db_data = data.pop("db_setup", None)
        outgoing = data.pop("outgoing_edge_values", {})

        artifact = BuildArtifact(
            source_files=data["source_files"],
            primary_source=data["primary_source"],
            port=data["port"],
            app_dir=data.get("app_dir", "/opt/webapp"),
            system_deps=data.get("system_deps", []),
            extra_deps=data.get("extra_deps", []),
            db_setup=DBSetup(**db_data) if db_data else None,
        )
        return artifact, outgoing

    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": user_msg}])

    try:
        return _parse(raw)
    except Exception as e:
        retry_msg = f"{user_msg}\n\nYour previous response failed to parse: {e}\n\nOutput ONLY valid JSON."
        raw2 = call(model_id=model, system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": retry_msg}])
        return _parse(raw2)
