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
