"""Chain-attacker agent — synthesizes one end-to-end cross-system attack Procedure.

Given a fully-resolved EntityGraph (with concrete edge values) and the per-entity
Procedures produced during the build phase, the chain-attacker writes a single
YAML procedure that chains all entities together into one unbroken attack path
across all systems.

Follows the same generate → self-review → parse-retry pattern as attacker.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph
    from goe.models.procedure import Procedure
    from goe.models.report import BuildOutcome

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "chain_attacker_system.md"
).read_text(encoding="utf-8")


def _graph_summary(graph: "EntityGraph") -> str:
    """Render a compact summary of the graph for the LLM prompt."""
    lines: list[str] = ["## Systems", ""]
    for s in graph.systems:
        exposed = ", ".join(str(p) for p in s.network.exposed_ports) or "none"
        internal = ", ".join(str(p) for p in s.network.internal_ports) or "none"
        lines.append(
            f"- **{s.id}** — hostname: `{s.network.hostname}`, "
            f"exposed ports: {exposed}, internal ports: {internal}"
        )
    lines += ["", "## Entities", ""]
    for e in graph.entities:
        atoms = ", ".join(e.atoms) or "(none)"
        lines.append(f"- **{e.id}** (system: `{e.system_id}`, runtime: `{e.runtime.value}`)")
        lines.append(f"  - Description: {e.description}")
        lines.append(f"  - Atoms: {atoms}")
        lines.append(f"  - Requires edges: {[r.edge_id for r in e.requires]}")
        lines.append(f"  - Provides edges: {e.provides}")
    lines += ["", "## Edges (with concrete values)", ""]
    for edge in graph.edges:
        dst = edge.to_entity or "(terminal)"
        lines.append(f"- **{edge.id}**: `{edge.from_entity}` → `{dst}` ({edge.type.value})")
        for param, pv in edge.params.items():
            concrete = pv.concrete if pv.concrete is not None else "(not yet set)"
            lines.append(f"  - {param}: `{concrete}` (structural: {pv.structural})")
    return "\n".join(lines)


def _per_entity_procedures(built: dict[str, "BuildOutcome"]) -> str:
    """Render per-entity procedures as reference for the chain attacker."""
    lines: list[str] = ["## Per-Entity Attack Procedures (reference)", ""]
    for eid, outcome in built.items():
        proc = outcome.procedure
        if proc is None:
            lines.append(f"### {eid}: (no procedure)")
            continue
        lines.append(f"### {eid}")
        lines.append("```yaml")
        lines.append(yaml.dump(proc.model_dump(), default_flow_style=False).strip())
        lines.append("```")
    return "\n".join(lines)


def _parse(raw: str) -> "Procedure":
    from goe.models.procedure import Procedure
    from goe.construction_crew._yaml_repair import safe_parse_yaml
    data = safe_parse_yaml(raw)
    return Procedure.model_validate(data)


def attack(
    graph: "EntityGraph",
    built: dict[str, "BuildOutcome"],
) -> "Procedure":
    """Call the chain-attacker LLM to produce one end-to-end Procedure.

    Args:
        graph: The fully-resolved EntityGraph (concrete edge values populated).
        built: Dict of entity_id → BuildOutcome for all PASSED entities.

    Returns:
        A single Procedure that chains all entities across all systems.
    """
    from goe.bedrock import call
    from goe.config import GoEConfig

    cfg = GoEConfig.get()
    model = cfg.model_for("chain_attacker")

    graph_summary = _graph_summary(graph)
    entity_procedures = _per_entity_procedures(built)

    user_msg = f"""{graph_summary}

{entity_procedures}

Write a single end-to-end YAML procedure that exploits each entity in order, chaining
outputs across systems to complete the full attack path. The final step must verify
that the attacker has achieved the terminal goal (the last entity in the chain).

Use `${{system.<system_id>.host}}` and `${{system.<system_id>.port}}` to address each
system. Use `${{edge.<edge_id>.<param>}}` for concrete edge values.

Output ONLY valid YAML (no markdown fences, no explanation)."""

    review_msg = """Review your chain procedure against these checks before finalising:

1. **System addressing**: every step that targets a specific host uses `${system.<id>.host}` — not a hardcoded hostname or `${target_host}`.
2. **No exec_target**: all steps use `exec_attacker` or `http_request`. Chain procedures must run entirely from the attacker.
3. **Edge value usage**: stolen credentials and other artefacts are referenced via `${edge.<id>.<param>}` or `${steps.<step_id>.<output>}` — not hardcoded.
4. **Final step assertion**: the last step explicitly verifies the end-to-end attack succeeded (command output, credential visible, etc.).
5. **Topological order**: entities are exploited in dependency order (upstream entity before downstream).
6. **Distinct technique per entity**: each entity's specific attack technique must be exercised IN ITS OWN CONTEXT. For privilege escalation on the same host, run the privesc from within the obtained shell — NOT through the original injection. For lateral movement to a different host, SSH/SMB to that host IS correct and expected. Do not collapse multiple entities into a single step or bypass an entity's technique entirely.

If any check fails, output the corrected YAML. If all pass, output the original YAML unchanged.
Output ONLY valid YAML (no markdown fences)."""

    messages = [{"role": "user", "content": user_msg}]
    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=messages, caller="chain_attacker")
    messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": review_msg}]
    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=messages, caller="chain_attacker.self_review")

    try:
        return _parse(raw)
    except Exception as e:
        from goe.construction_crew._yaml_repair import ProcedureParseError
        retry_msg = f"Your previous YAML failed to parse: {e}\n\nOutput ONLY valid YAML."
        messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": retry_msg}]
        raw2 = call(
            model_id=model, system=_SYSTEM_PROMPT, messages=messages,
            caller="chain_attacker.retry",
        )
        try:
            return _parse(raw2)
        except Exception as e2:
            raise ProcedureParseError(
                f"chain_attacker.attack: YAML parse failed after retry: {e2}",
                raw=raw2,
                cause=e2,
            ) from e2


def fix_chain(
    procedure: "Procedure",
    diagnosis: str,
) -> "Procedure":
    """Ask the chain-attacker LLM to fix a specific bug in an existing chain procedure.

    Used by the retry loop in chain_test.py — targeted patch rather than full
    regeneration from the graph.

    Args:
        procedure: The current (failing) chain procedure.
        diagnosis: Description of what is wrong and how to fix it.

    Returns:
        Updated Procedure.
    """
    from goe.bedrock import call
    from goe.config import GoEConfig

    cfg = GoEConfig.get()
    model = cfg.model_for("chain_attacker")

    current_yaml = yaml.dump(procedure.model_dump(), default_flow_style=False)

    user_msg = f"""## Current Chain Procedure (failing)

```yaml
{current_yaml}
```

## Diagnosis — What Is Wrong

{diagnosis}

## Common Failure Patterns and Fixes

**Interpolation Issues:**
- `${{system.<id>.host}}` typo or wrong system_id → check graph for correct ID
- `${{edge.<id>.<param>}}` missing param → use `.value` fallback or check edge type
- Variable not captured in outputs → add `outputs:` to earlier step

**Service/Process Not Running:**
- If container evidence shows process is missing or port not listening, the entity deploy may have failed
- Check if service was started in deploy script (systemd won't work in Docker, need nohup/&)
- This is usually NOT fixable by patching procedure — the entity is broken

**Permission Denied on Files:**
- If accessing files in user home directories, parent directories need +x permission
- This indicates an entity implementation bug, not a procedure bug
- Cannot be fixed by patching procedure — entity needs L2 retest

**Connection Refused / Timeout:**
- Check container evidence: is the service listening on the expected port?
- Check hostname interpolation: using correct `${{system.<id>.host}}`?
- If service isn't running, entity is broken (not fixable via procedure patch)

**Assertion Failures (regex/contains):**
- Output format changed? Update regex to be more flexible
- Looking for wrong string? Check what's actually in stdout/stderr from diagnosis
- Brittle exact match? Switch to regex that captures semantic meaning

**Step Sequencing:**
- Later step fails because earlier step didn't capture needed output
- Add `outputs:` to earlier step and reference `${{steps.<id>.<var>}}`
- Or use concrete edge value if available

**When NOT to patch:**
If diagnosis shows entity-level issues (process not running, service not listening, deploy failure), you CANNOT fix this by patching the procedure. The entity itself is broken. In this case, preserve the procedure as-is or make minimal changes — the real fix requires entity L2 retest.

Fix the procedure to address exactly this issue. Do not change steps that are working.
Output ONLY valid YAML (no markdown fences, no explanation)."""

    raw = call(
        model_id=model, system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        caller="chain_attacker.fix_chain",
    )
    try:
        return _parse(raw)
    except Exception as e:
        from goe.construction_crew._yaml_repair import ProcedureParseError
        retry_msg = f"{user_msg}\n\nYour previous YAML failed to parse: {e}\n\nOutput ONLY valid YAML."
        raw2 = call(
            model_id=model, system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": retry_msg}],
            caller="chain_attacker.fix_chain.retry",
        )
        try:
            return _parse(raw2)
        except Exception as e2:
            raise ProcedureParseError(
                f"chain_attacker.fix_chain: YAML parse failed after retry: {e2}",
                raw=raw2,
                cause=e2,
            ) from e2
