"""L1 diagnostician — inspects a failed procedure run and categorises the failure."""

from __future__ import annotations

import json
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from goe.models.entity import Entity
    from goe.models.artifacts import BuildArtifact
    from goe.executor.runner import ProcedureResult
    from goe.container.environment import TestEnvironment

_SYSTEM = """You are a senior security architect diagnosing a failed automated penetration test.

You have god-view access to the running Docker environment. The evidence below was gathered
specifically for this entity's runtime and vulnerability type. Inspect the failure and categorise it.

## Diagnosis Categories

- `procedure_bug`: The app is deployed correctly, but the attack procedure is wrong (wrong URL, wrong payload, wrong assertion). Fix: re-run the attacker agent.
- `implementation_bug`: The app is running but the vulnerability is not present or not exploitable as expected (wrong query, sanitized input, wrong column names). Fix: re-run the developer and attacker agents.
- `design_flaw`: The app is fundamentally broken — wrong port, startup failure, wrong runtime behaviour. Fix: re-run the full crew.

## Common Failure Patterns

**File Permission Issues (implementation_bug):**
When a vulnerability involves reading files from user home directories (e.g., .bash_history, .ssh/*, config files):
- A file can be world-readable (0644) but still inaccessible if the **parent directory** lacks execute/search permission
- www-data or attacker user needs **execute permission on ALL parent directories** in the path to traverse to the file
- Example: `/home/stacy/.bash_history` may be 0644, but if `/home/stacy` is 0750 (owner+group only), www-data cannot traverse into it
- **Fix**: The developer must `chmod 755 /home/<user>` to allow directory traversal, not just set file permissions
- This is an **implementation_bug** because the vulnerability setup is incomplete

**Permission Denied vs Wrong Path:**
- "Permission denied" → check directory permissions, not just file permissions (implementation_bug)
- "No such file or directory" → wrong path or file not created (could be implementation_bug or procedure_bug)

**Process Not Running:**
- If the app process isn't in `ps aux` or the port isn't in `ss -tlnp`, that's a **design_flaw** (startup failed)
- If the process is running but not behaving correctly, that's **implementation_bug**

## Grounding (critical)

The exact attack procedure that ran is given below under "## Attack Procedure". When you
reason about what the attacker requested — URLs, payloads, commands, assertions — quote it
from that procedure, NEVER from a log line. A log may show an unrelated or stale request
(a health check, a scanner, a prior attempt); it is NOT proof of what this procedure sent.
If you claim "the attacker requested X", X must appear verbatim in the procedure below. If
the procedure's URL/payload already matches the app and the test still fails, the cause is
NOT the procedure — look to the app (implementation_bug) or deployment (design_flaw) instead.

## Output Format

Respond with ONLY valid JSON:

```json
{
  "category": "procedure_bug|implementation_bug|design_flaw",
  "description": "specific description of what went wrong and why",
  "evidence": "relevant output or observation that supports the diagnosis"
}
```"""


class DiagnosisCategory(str, Enum):
    procedure_bug = "procedure_bug"
    implementation_bug = "implementation_bug"
    design_flaw = "design_flaw"


class Diagnosis(BaseModel):
    model_config = ConfigDict(extra="allow")
    category: DiagnosisCategory
    description: str
    evidence: str = ""


def _render_procedure(procedure) -> str:
    """Render the attacker steps that actually ran — step id, action, and assertion —
    so the diagnostician reasons from the real URLs/commands instead of inferring them
    from logs (which may carry unrelated or stale requests)."""
    steps = getattr(procedure, "procedure", None) or []
    if not steps:
        return "(no procedure recorded)"
    lines = []
    for s in steps:
        a = getattr(s, "action", None)
        atype = getattr(a, "type", "?")
        detail = (
            getattr(a, "url", None)
            or getattr(a, "command", None)
            or getattr(a, "script", None)
            or ""
        )
        exp = getattr(s, "expect", None)
        exp_s = ""
        if exp is not None and hasattr(exp, "model_dump"):
            kv = ", ".join(f"{k}={v}" for k, v in exp.model_dump(exclude_none=True).items())
            if kv:
                exp_s = f"  | expect: {kv}"
        lines.append(f"- [{getattr(s, 'step_id', '?')}] {atype}: {detail}{exp_s}")
    return "\n".join(lines)


def diagnose(
    entity: "Entity",
    artifact: "BuildArtifact",
    result: "ProcedureResult",
    env: "TestEnvironment",
    procedure=None,
) -> Diagnosis:
    """Run L1 god-view diagnosis on a failed procedure result."""
    from goe.config import GoEConfig
    from goe.retry.probes import ProbeContext, execute_probes, select_probes

    cfg = GoEConfig.get()
    model = cfg.model_for("diagnostician")

    # Build probe context from entity/artifact metadata
    ctx = ProbeContext(
        runtime=entity.runtime.value,
        atoms=entity.atoms or [],
        has_db=artifact.db_setup is not None,
        db_type=artifact.db_setup.db_type if artifact.db_setup else None,
        app_dir=artifact.app_dir or "",
        port=getattr(artifact, "port", None),
        source_files=list(artifact.source_files.keys()),
        failed_step=result.failed_step,
        system_deps=artifact.system_deps or [],
    )

    # Select and execute relevant probes
    probes = select_probes(ctx)
    evidence = execute_probes(env, probes, ctx)

    # Summarise failed steps
    failed_steps = [
        f"Step '{s.step_id}': {s.reason}\n  stdout: {s.raw.stdout[:300]}\n  stderr: {s.raw.stderr[:200]}"
        for s in result.steps if not s.passed
    ]

    source_listing = "\n\n".join(
        f"### {fname}\n```\n{content[:2000]}\n```"
        for fname, content in artifact.source_files.items()
    )

    # Format evidence dynamically based on probe results
    evidence_section = "\n\n".join(
        f"### {label}\n{output}" for label, output in evidence
    )

    user_msg = f"""## Failed Procedure

Error: {result.error or 'assertion failure'}
Failed at step: {result.failed_step}

Failed steps:
{chr(10).join(failed_steps) or '(none recorded)'}

## Attack Procedure

The EXACT steps that ran (reason about the attacker's requests from THESE, not from logs):
{_render_procedure(procedure)}

## App Source

{source_listing}

## God-View Evidence

{evidence_section}

Diagnose the failure and output ONLY valid JSON."""

    # Reuse the shared robust JSON caller (fence-stripping + one parse retry).
    from pydantic import ValidationError

    from goe.planner._utils import call_json

    try:
        data = call_json(model, _SYSTEM, user_msg, caller="diagnostician")
        return Diagnosis.model_validate(data)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        # The diagnostician runs inside the build's bounded retry ladder; a malformed
        # LLM response must not crash the entire flow. Fall back to the most
        # conservative category (re-run the full crew) and record why.
        import logging

        logging.getLogger(__name__).warning(
            "Diagnostician: could not parse a valid Diagnosis, defaulting to design_flaw: %s",
            exc,
        )
        return Diagnosis(
            category=DiagnosisCategory.design_flaw,
            description=f"Diagnostician response could not be parsed as valid JSON ({exc}).",
            evidence="",
        )
