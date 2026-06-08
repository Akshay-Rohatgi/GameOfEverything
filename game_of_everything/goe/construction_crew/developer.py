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

    is_ubuntu = entity.runtime.value == "ubuntu"
    runtime_spec = "" if is_ubuntu else _load_runtime_spec(entity.runtime.value)

    spec_section = "" if is_ubuntu else f"""## Runtime Spec

```yaml
{runtime_spec}
```

"""

    if is_ubuntu:
        implement_line = "Implement the bash setup script exactly as specified in the architecture plan."
    else:
        implement_line = "Implement the application exactly as specified in the architecture plan."

    user_msg = f"""## Entity Spec

```json
{entity.model_dump_json(indent=2)}
```

## Architecture Plan

```json
{plan.model_dump_json(indent=2)}
```

{spec_section}## Incoming Edge Values

```json
{json.dumps(incoming_edges, indent=2)}
```

{implement_line}
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
            port=data.get("port"),
            app_dir=data.get("app_dir", "/opt/webapp"),
            system_deps=data.get("system_deps", []),
            extra_deps=data.get("extra_deps", []),
            db_setup=DBSetup(**db_data) if db_data else None,
        )
        return artifact, outgoing

    if is_ubuntu:
        review_msg = """Review your bash setup script against these correctness checks before finalising:

1. **Self-contained**: Does the script set up the misconfiguration from scratch without external dependencies?
2. **Vulnerability present**: Is the misconfiguration from the plan actually applied and not accidentally fixed?
3. **Idempotent**: Does the script avoid errors if run a second time (use -f for rm, || true for commands that may fail)?
4. **Outgoing values**: Are all outgoing_edge_values set to concrete values (not placeholders)?

If any check fails, output the corrected JSON. If all checks pass, output the original JSON unchanged.
Output ONLY valid JSON."""
    else:
        review_msg = """Review your implementation against these correctness checks before finalising:

1. **Binding**: Does the app listen on 0.0.0.0, not 127.0.0.1?
2. **DB writes**: Are all INSERT/UPDATE/DELETE operations using parameterised queries or prepared statements — never raw string concatenation with user input?
3. **DB file location**: If using SQLite, is the database file outside the webroot (not inside /var/www/html)?
4. **Seeding**: Is there exactly ONE seeding approach — either inline startup OR db_setup, never both?
5. **Vulnerability present**: Is the vulnerability from the plan actually present and not accidentally sanitized?
6. **Single file**: Is the entire app in a single source file?

If any check fails, output the corrected JSON. If all checks pass, output the original JSON unchanged.
Output ONLY valid JSON."""

    messages = [{"role": "user", "content": user_msg}]
    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=messages)
    messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": review_msg}]
    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=messages)

    try:
        return _parse(raw)
    except Exception as e:
        retry_msg = f"Your previous response failed to parse: {e}\n\nOutput ONLY valid JSON."
        messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": retry_msg}]
        raw2 = call(model_id=model, system=_SYSTEM_PROMPT, messages=messages)
        return _parse(raw2)
