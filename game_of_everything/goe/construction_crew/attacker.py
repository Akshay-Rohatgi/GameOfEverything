"""Attacker agent — turns a BuildArtifact into an attack Procedure."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from goe.models.entity import Entity
    from goe.models.artifacts import BuildArtifact
    from goe.construction_crew.engineer import EngineerPlan

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "attacker_system.md").read_text()
_RUNTIMES_DIR = Path(__file__).resolve().parent.parent / "runtimes" / "templates"


def _load_attacker_rules(runtime_id: str) -> str:
    path = _RUNTIMES_DIR / f"{runtime_id}.yaml"
    if not path.exists():
        return ""
    import yaml as _yaml
    data = _yaml.safe_load(path.read_text())
    return data.get("attacker_rules", "")


def attack(
    entity: "Entity",
    plan: "EngineerPlan",
    artifact: "BuildArtifact",
    outgoing_values: dict,
) -> "Procedure":
    """Call the Attacker LLM to produce a Procedure that exploits the vulnerability."""
    from goe.bedrock import call
    from goe.config import GoEConfig
    from goe.models.procedure import Procedure

    cfg = GoEConfig.get()
    model = cfg.model_for("attacker")

    # Provide all source files so the attacker can see exact endpoints/params
    source_listing = "\n\n".join(
        f"### {fname}\n```\n{content}\n```"
        for fname, content in artifact.source_files.items()
    )

    runtime_id = entity.app_spec.runtime if entity.app_spec else ""
    attacker_rules = _load_attacker_rules(runtime_id)
    rules_section = f"\n## Runtime-Specific Rules\n\n{attacker_rules}\n" if attacker_rules else ""

    user_msg = f"""## Entity Spec

Goal: {entity.description}
App spec: {entity.app_spec.model_dump_json() if entity.app_spec else 'N/A'}

## Architecture Plan

- Attack entry point: {plan.attack_entry_point}
- Success indicator: {plan.success_indicator}
- Vulnerability: {plan.vulnerability_placement}

## Application Source Code

{source_listing}

## Concrete Edge Values (outgoing)

{yaml.dump(outgoing_values) if outgoing_values else "(none)"}

## Runtime

The app listens on port ${{target_port}} on host ${{target_host}}.
{rules_section}
Write a YAML procedure that exploits the vulnerability and verifies success.
Output ONLY valid YAML (no markdown fences)."""

    def _parse(raw: str) -> "Procedure":
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]
        data = yaml.safe_load(raw)
        return Procedure.model_validate(data)

    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": user_msg}])

    try:
        return _parse(raw)
    except Exception as e:
        retry_msg = f"{user_msg}\n\nYour previous YAML failed to parse: {e}\n\nOutput ONLY valid YAML."
        raw2 = call(model_id=model, system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": retry_msg}])
        return _parse(raw2)


def fix_procedure(
    procedure: "Procedure",
    diagnosis: str,
) -> "Procedure":
    """Ask the Attacker LLM to fix a specific bug in an existing procedure.

    Used by the retry router for `procedure_bug` diagnoses — targeted patch
    rather than full regeneration from source.

    Args:
        procedure: The current (failing) procedure.
        diagnosis: Human-readable description of what is wrong and how to fix it.

    Returns:
        Updated Procedure.
    """
    from goe.bedrock import call
    from goe.config import GoEConfig
    from goe.models.procedure import Procedure

    cfg = GoEConfig.get()
    model = cfg.model_for("attacker")

    current_yaml = yaml.dump(procedure.model_dump(), default_flow_style=False)

    user_msg = f"""## Current Procedure (failing)

```yaml
{current_yaml}
```

## Diagnosis — What Is Wrong

{diagnosis}

Fix the procedure to address exactly this issue. Do not change steps that are working correctly.
Output ONLY valid YAML (no markdown fences, no explanation)."""

    def _parse(raw: str) -> "Procedure":
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]
        data = yaml.safe_load(raw)
        return Procedure.model_validate(data)

    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": user_msg}])

    try:
        return _parse(raw)
    except Exception as e:
        retry_msg = f"{user_msg}\n\nYour previous YAML failed to parse: {e}\n\nOutput ONLY valid YAML."
        raw2 = call(model_id=model, system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": retry_msg}])
        return _parse(raw2)
