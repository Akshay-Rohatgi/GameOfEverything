"""Developer agent — turns an architecture plan into BuildArtifact + source code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.models.entity import Entity
    from goe.construction_crew.engineer import EngineerPlan

from goe.construction_crew.atoms import load_logic_requirements, load_synthesis_guidance

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "developer_system.md").read_text()
_RUNTIMES_DIR = Path(__file__).resolve().parent.parent / "runtimes" / "templates"


def validate_outgoing_edge_values(outgoing: dict, provided_schema: dict) -> None:
    """Constrain developer-emitted outgoing edge values to the plan-time edge schema.

    Each edge_id must be one this entity provides, and every param key must be a declared
    param. Keys are fixed at plan time — the developer fills values, it never invents
    structure. Raises ValueError on any unknown edge_id, non-dict payload, or undeclared
    param key. A no-op when provided_schema is empty (e.g. standalone single-entity builds).
    """
    if not provided_schema:
        return
    allowed_ids = set(provided_schema)
    for eid, payload in outgoing.items():
        if eid not in allowed_ids:
            raise ValueError(
                f"outgoing_edge_values has unknown edge '{eid}'; "
                f"this entity only provides {sorted(allowed_ids)}"
            )
        if not isinstance(payload, dict):
            raise ValueError(
                f"outgoing_edge_values['{eid}'] must be a dict of params, "
                f"got {type(payload).__name__}"
            )
        declared = set(provided_schema[eid].get("params", []))
        extra = set(payload) - declared
        if extra:
            raise ValueError(
                f"outgoing_edge_values['{eid}'] has undeclared params "
                f"{sorted(extra)}; declared params are {sorted(declared)}"
            )
        for param, value in payload.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"outgoing_edge_values['{eid}']['{param}'] is empty; emit the real "
                    f"concrete value your script created, never a blank or placeholder"
                )


def _load_runtime_spec(runtime_id: str) -> str:
    path = _RUNTIMES_DIR / f"{runtime_id}.yaml"
    return path.read_text() if path.exists() else f"(runtime spec for {runtime_id!r} not found)"


def develop(
    entity: "Entity",
    plan: "EngineerPlan",
    incoming_edges: dict,
    edge_schemas: dict | None = None,
    system_context: str | None = None,
    provided_values: dict | None = None,
) -> tuple["BuildArtifact", dict[str, dict[str, str]]]:
    """Call the Developer LLM to produce source code and a BuildArtifact.

    Args:
        edge_schemas: optional {edge_id: {"type", "direction", "params": [names]}} for the
            entity's provided/required edges. Constrains the param keys the developer may
            emit in outgoing_edge_values (keys are fixed at plan time, never invented).
        system_context: optional rendered markdown describing this entity's system, the
            platform-provided services, and sibling entities — so it builds only its own link.
        provided_values: optional {edge_id: {param: concrete}} of already-determined values for
            edges this entity provides (resolved hosts, materialized secrets). The developer
            must embed these EXACTLY (e.g. base64-decode an SSH key to the served path) instead
            of generating fresh material; these params are pre-filled and never re-emitted.

    Returns:
        (BuildArtifact, outgoing_values: dict[str, dict[str, str]])
    """
    from goe.bedrock import call
    from goe.config import GoEConfig
    from goe.models.artifacts import BuildArtifact, DBSetup

    cfg = GoEConfig.get()
    model = cfg.model_for("developer")
    review_model = cfg.model_for("developer_review")

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

    # Inject Logic Requirements as constraints (before generation)
    logic_reqs = "\n\n".join(
        f"### Atom: {a}\n{load_logic_requirements(a)}"
        for a in (entity.atoms or [])
        if load_logic_requirements(a).strip()
    )
    constraints_section = f"""\n## Vulnerability Constraints

These constraints MUST be satisfied for the vulnerability to work:

{logic_reqs}

""" if logic_reqs else ""

    edge_schemas = edge_schemas or {}
    provided_values = provided_values or {}
    provided_schema = {
        eid: s for eid, s in edge_schemas.items() if s.get("direction") == "provides"
    }
    schema_section = f"""## Edge Schemas (declared param keys — fill these EXACTLY, never invent keys)

```json
{json.dumps(edge_schemas, indent=2)}
```

For every edge in your `provides`, `outgoing_edge_values[edge_id]` MUST be a dict whose keys
are exactly the declared params above, each set to a concrete value you actually built.

""" if edge_schemas else ""

    prefilled_section = f"""## Pre-Filled Provided Edge Values (ALREADY DETERMINED — embed verbatim)

```json
{json.dumps(provided_values, indent=2)}
```

These params for your provided edges are already decided for you (resolved hostnames, and
secrets — such as SSH keys — generated once so the consuming entity authorizes the SAME key).
You MUST use these exact values in your implementation and must NOT regenerate them:
- For an SSH key `secret`, the value is **base64 of an OpenSSH private key**. Write it to the
  file you expose with: `echo '<value>' | base64 -d > /path/you/serve/id_rsa` (then `chmod 644`).
  Do NOT run `ssh-keygen` to make a new key — that would not match the other system.
- Do NOT list these pre-filled params again in `outgoing_edge_values`; they are already set.

""" if provided_values else ""

    context_section = f"{system_context}\n" if system_context else ""

    user_msg = f"""## Entity Spec

```json
{entity.model_dump_json(indent=2)}
```

{context_section}## Architecture Plan

```json
{plan.model_dump_json(indent=2)}
```

{spec_section}{schema_section}{prefilled_section}## Incoming Edge Values

```json
{json.dumps(incoming_edges, indent=2)}
```
{constraints_section}
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

        # Drop any pre-filled params the LLM echoed back: those values are authoritative
        # (resolved hosts, materialized secrets) and already live on the graph edge. This also
        # keeps them out of validation, which only expects the still-unfilled declared params.
        for eid, pre in provided_values.items():
            if eid in outgoing and isinstance(outgoing[eid], dict):
                for param in pre:
                    outgoing[eid].pop(param, None)
                if not outgoing[eid]:
                    outgoing.pop(eid)

        validate_outgoing_edge_values(outgoing, provided_schema)

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

    # Inject Synthesis Guidance as verification (during self-review)
    synthesis_guidance = "\n\n".join(
        f"### Atom: {a}\n{load_synthesis_guidance(a)}"
        for a in (entity.atoms or [])
        if load_synthesis_guidance(a).strip()
    )
    guidance_section = f"""\n## Verify Against Atom Guidance

Compare your implementation against the proven patterns below:

{synthesis_guidance}

""" if synthesis_guidance else ""

    if is_ubuntu:
        review_msg = f"""{guidance_section}Review your bash setup script against these correctness checks before finalising:

1. **One link only**: Does the script build ONLY this entity's vulnerability on its own system? Remove any service, account, or `authorized_keys` that belongs to another entity/system per the System & Chain Context (e.g. an SMB-share entity must not install openssh-server or create the SSH login). Do not install/restart services the platform already provides.
2. **Vulnerability present**: Is the misconfiguration from the plan actually applied and not accidentally fixed?
3. **Idempotent**: Does the script avoid errors if run a second time (use -f for rm, || true for commands that may fail)?
4. **Outgoing values**: For every provided edge, are ALL its still-unfilled declared params present in outgoing_edge_values, set to concrete values (not placeholders) that match what the script actually created? Do NOT re-emit pre-filled params (resolved hosts, materialized secrets).
5. **Shared secrets**: For an `ssh_key` secret, did you write the base64-decoded provided key to the served path (producer) or derive+authorize its public half (consumer) — and NOT run `ssh-keygen` to make a new key?
6. **Incoming values**: If incoming_edges is non-empty, did you reuse every incoming param EXACTLY (no re-invented usernames/paths/tokens)?
7. **Atom guidance**: Does your implementation satisfy the atom synthesis guidance above (if provided)? Fix any violations.

If any check fails, output the corrected JSON. If all checks pass, output the original JSON unchanged.
Output ONLY valid JSON."""
    else:
        review_msg = f"""{guidance_section}Review your implementation against these correctness checks before finalising:

1. **Binding**: Does the app listen on 0.0.0.0, not 127.0.0.1?
2. **DB writes**: Are all INSERT/UPDATE/DELETE operations using parameterised queries or prepared statements — never raw string concatenation with user input?
3. **DB file location**: If using SQLite, is the database file outside the webroot (not inside /var/www/html)?
4. **Seeding**: Is there exactly ONE seeding approach — either inline startup OR db_setup, never both?
5. **Vulnerability present**: Is the vulnerability from the plan actually present and not accidentally sanitized?
6. **Single file**: Is the entire app in a single source file?
7. **Edge values**: For every provided edge, are ALL its declared params present in outgoing_edge_values with concrete values that match what the app seeds/leaks (e.g. the exact username AND password in the DB)? And did you reuse every incoming param exactly?
8. **Atom guidance**: Does your implementation satisfy the atom synthesis guidance above (if provided)? Fix any violations.

If any check fails, output the corrected JSON. If all checks pass, output the original JSON unchanged.
Output ONLY valid JSON."""

    messages = [{"role": "user", "content": user_msg}]
    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=messages, caller="developer")
    messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": review_msg}]
    raw = call(model_id=review_model, system=_SYSTEM_PROMPT, messages=messages, caller="developer.self_review")

    try:
        return _parse(raw)
    except Exception as e:
        retry_msg = f"Your previous response failed to parse: {e}\n\nOutput ONLY valid JSON."
        messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": retry_msg}]
        raw2 = call(model_id=model, system=_SYSTEM_PROMPT, messages=messages, caller="developer.retry")
        return _parse(raw2)
