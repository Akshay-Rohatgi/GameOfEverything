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

_SYSTEM = """You are a senior security engineer diagnosing a failed automated penetration test.

You have god-view access to the running Docker environment. Inspect the failure and categorise it.

## Diagnosis Categories

- `procedure_bug`: The app is deployed correctly, but the attack procedure is wrong (wrong URL, wrong payload, wrong assertion). Fix: re-run the attacker agent.
- `implementation_bug`: The app is running but the vulnerability is not present or not exploitable as expected (wrong query, sanitized input, wrong column names). Fix: re-run the developer and attacker agents.
- `design_flaw`: The app is fundamentally broken — wrong port, startup failure, wrong runtime behaviour. Fix: re-run the full crew.

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


def diagnose(
    entity: "Entity",
    artifact: "BuildArtifact",
    result: "ProcedureResult",
    env: "TestEnvironment",
) -> Diagnosis:
    """Run L1 god-view diagnosis on a failed procedure result."""
    from goe.bedrock import call
    from goe.config import GoEConfig

    cfg = GoEConfig.get()
    model = cfg.model_for("diagnostician")

    # Collect god-view evidence from the running containers
    _, app_log, _ = env.exec_in("target", "cat /var/log/webapp.log 2>/dev/null | tail -50 || echo '(no log)'")
    _, bot_log, _ = env.exec_in("target", "cat /tmp/adminbot.log 2>/dev/null | tail -30 || echo '(no bot log)'")
    _, ps_out, _ = env.exec_in("target", "ps aux 2>/dev/null | head -20")
    _, port_check, _ = env.exec_in("target", "ss -tlnp 2>/dev/null | head -20 || netstat -tlnp 2>/dev/null | head -20")

    # Summarise failed steps
    failed_steps = [
        f"Step '{s.step_id}': {s.reason}\n  stdout: {s.raw.stdout[:300]}\n  stderr: {s.raw.stderr[:200]}"
        for s in result.steps if not s.passed
    ]

    source_listing = "\n\n".join(
        f"### {fname}\n```\n{content[:2000]}\n```"
        for fname, content in artifact.source_files.items()
    )

    user_msg = f"""## Failed Procedure

Error: {result.error or 'assertion failure'}
Failed at step: {result.failed_step}

Failed steps:
{chr(10).join(failed_steps) or '(none recorded)'}

## App Source

{source_listing}

## God-View Evidence

### App Log (last 50 lines)
{app_log}

### Admin Bot Log (last 30 lines)
{bot_log}

### Running Processes
{ps_out}

### Listening Ports
{port_check}

Diagnose the failure and output ONLY valid JSON."""

    raw = call(model_id=model, system=_SYSTEM, messages=[{"role": "user", "content": user_msg}])
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    data = json.loads(raw)
    return Diagnosis.model_validate(data)
