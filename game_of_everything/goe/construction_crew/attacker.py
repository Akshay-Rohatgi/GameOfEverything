"""Attacker agent — turns a BuildArtifact into an attack Procedure."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from goe.models.entity import Entity
    from goe.models.artifacts import BuildArtifact
    from goe.construction_crew.engineer import EngineerPlan

from goe.construction_crew.atoms import load_logic_requirements, load_testing_guidance

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

    runtime_id = entity.runtime.value
    is_ubuntu = runtime_id == "ubuntu"
    attacker_rules = _load_attacker_rules(runtime_id)
    rules_section = f"\n## Runtime-Specific Rules\n\n{attacker_rules}\n" if attacker_rules else ""

    if is_ubuntu:
        runtime_note = (
            "The target host is ${target_host}. "
            "Use `exec_target` steps to run shell commands directly on the target and verify the misconfiguration. "
            "There is no web server — do NOT use http_request or ${target_port}."
        )
        source_header = "## Setup Script"
    else:
        runtime_note = "The app listens on port ${target_port} on host ${target_host}."
        source_header = "## Application Source Code"

    # Inject Logic Requirements as constraints (before generation)
    logic_reqs = "\n\n".join(
        f"### Atom: {a}\n{load_logic_requirements(a)}"
        for a in (entity.atoms or [])
        if load_logic_requirements(a).strip()
    )
    constraints_section = f"""\n## Attack Constraints

These constraints define how exploitation must work:

{logic_reqs}

""" if logic_reqs else ""

    user_msg = f"""## Entity Spec

Description: {entity.description}

## Architecture Plan

- Attack entry point: {plan.attack_entry_point}
- Success indicator: {plan.success_indicator}
- Vulnerability: {plan.vulnerability_placement}

{source_header}

{source_listing}

## Concrete Edge Values (outgoing)

{yaml.dump(outgoing_values) if outgoing_values else "(none)"}

## Runtime

{runtime_note}
{rules_section}{constraints_section}
Write a YAML procedure that exploits the vulnerability and verifies success.
Output ONLY valid YAML (no markdown fences)."""

    def _parse(raw: str) -> "Procedure":
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]
        data = yaml.safe_load(raw)
        return Procedure.model_validate(data)

    # Inject Testing Guidance as verification (during self-review)
    testing_guidance = "\n\n".join(
        f"### Atom: {a}\n{load_testing_guidance(a)}"
        for a in (entity.atoms or [])
        if load_testing_guidance(a).strip()
    )
    guidance_section = f"""\n## Verify Against Atom Testing Guidance

Compare your procedure against the proven testing patterns below:

{testing_guidance}

""" if testing_guidance else ""

    review_msg = f"""{guidance_section}Review your procedure against these correctness checks before finalising:

1. **Payload characters**: Do any payloads contain characters (e.g. single quotes `'`) that would break the app's storage layer (raw SQL concatenation)? If so, rewrite the payload to avoid them — use `<script>alert(1)</script>` not `<script>alert('xss')</script>`.
2. **POST status codes**: If a POST step submits a form, does the app source show a redirect after success? If yes, assert `status: 302`. If the app returns 200 directly, assert `status: 200`. Check the source — don't assume.
3. **Final assertion**: Does the last step assert that the attack actually succeeded (credentials visible, command output present, payload reflected)? A procedure that only checks status codes is not sufficient.
4. **Background listeners**: Are background listener processes started with `exec_attacker_bg` (not `exec_attacker`)?
5. **Atom guidance**: Does your procedure align with the atom testing guidance above (if provided)? Fix any mismatches.

If any check fails, output the corrected YAML. If all checks pass, output the original YAML unchanged.
Output ONLY valid YAML (no markdown fences)."""

    messages = [{"role": "user", "content": user_msg}]
    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=messages, caller="attacker")
    messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": review_msg}]
    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=messages, caller="attacker.self_review")

    try:
        return _parse(raw)
    except Exception as e:
        retry_msg = f"Your previous YAML failed to parse: {e}\n\nOutput ONLY valid YAML."
        messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": retry_msg}]
        raw2 = call(model_id=model, system=_SYSTEM_PROMPT, messages=messages, caller="attacker.retry")
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

    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": user_msg}], caller="attacker.fix_procedure")

    try:
        return _parse(raw)
    except Exception as e:
        retry_msg = f"{user_msg}\n\nYour previous YAML failed to parse: {e}\n\nOutput ONLY valid YAML."
        raw2 = call(model_id=model, system=_SYSTEM_PROMPT, messages=[{"role": "user", "content": retry_msg}], caller="attacker.fix_procedure.retry")
        return _parse(raw2)
