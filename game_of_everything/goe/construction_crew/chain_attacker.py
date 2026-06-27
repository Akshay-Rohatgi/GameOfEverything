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

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "chain_attacker_system.md").read_text()


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
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    data = yaml.safe_load(raw)
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
6. **Distinct technique per entity**: each entity's specific attack technique must be exercised. Do NOT reuse an upstream entity's access method to bypass a downstream entity. For example, if a command injection entity provides a shell (reverse shell, bind shell, SSH), and a subsequent entity does privilege escalation, the privesc MUST run from within the obtained shell — NOT by piping commands through the original HTTP injection.

If any check fails, output the corrected YAML. If all pass, output the original YAML unchanged.
Output ONLY valid YAML (no markdown fences)."""

    messages = [{"role": "user", "content": user_msg}]
    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=messages, caller="chain_attacker")
    messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": review_msg}]
    raw = call(model_id=model, system=_SYSTEM_PROMPT, messages=messages, caller="chain_attacker.self_review")

    try:
        return _parse(raw)
    except Exception as e:
        retry_msg = f"Your previous YAML failed to parse: {e}\n\nOutput ONLY valid YAML."
        messages += [{"role": "assistant", "content": raw}, {"role": "user", "content": retry_msg}]
        raw2 = call(
            model_id=model, system=_SYSTEM_PROMPT, messages=messages,
            caller="chain_attacker.retry",
        )
        return _parse(raw2)


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
        retry_msg = f"{user_msg}\n\nYour previous YAML failed to parse: {e}\n\nOutput ONLY valid YAML."
        raw2 = call(
            model_id=model, system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": retry_msg}],
            caller="chain_attacker.fix_chain.retry",
        )
        return _parse(raw2)
