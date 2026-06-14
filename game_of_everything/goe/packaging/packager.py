"""Single-system packager — concatenate built entities into one deploy package.

Single-system = all entities deploy onto one box, so the per-entity deploy
scripts (each already self-contained, with source files embedded via base64)
are concatenated into one ``deploy.sh``. The attack steps for every entity are
collected into ``playbook.yaml`` and a human-readable ``README.md`` is emitted.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from goe.graph.topology import topological_sort
from goe.packaging.postprocessor import apply_post_processors

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph
    from goe.models.report import BuildOutcome


def _ordered_built(graph: "EntityGraph", built: dict[str, "BuildOutcome"]) -> list[str]:
    """Entity IDs that were built successfully, in topological (build) order."""
    return [eid for eid in topological_sort(graph) if eid in built]


def _detect_port_collisions(graph: "EntityGraph", order: list[str]) -> list[str]:
    """Return warning lines for web entities that would collide on a port.

    Single-system means every entity shares one box; two web entities bound to
    the same port (or app_dir) cannot coexist. Phase-3 scenarios are 1-2
    entities, so we warn rather than fail.
    """
    from goe.runtimes.registry import get_registry

    registry = get_registry()
    seen: dict[int, str] = {}
    warnings: list[str] = []
    for eid in order:
        entity = graph.entity_by_id(eid)
        runtime = entity.runtime.value
        if runtime == "ubuntu":
            continue
        try:
            port = registry.port_for(runtime)
        except Exception:
            continue
        if port in seen:
            warnings.append(
                f"Entities `{seen[port]}` and `{eid}` both bind port {port} "
                f"({runtime}); they will collide on a single box."
            )
        else:
            seen[port] = eid
    return warnings


def _build_deploy_sh(graph: "EntityGraph", built: dict[str, "BuildOutcome"], order: list[str]) -> str:
    sections: list[str] = []
    for eid in order:
        script = built[eid].deploy_script or ""
        sections.append(f"# --- {eid} ---\n{script.strip()}\n")
    combined = "\n".join(sections)
    return apply_post_processors(combined) + "\n"


def _build_playbook(built: dict[str, "BuildOutcome"], order: list[str]) -> str:
    steps = []
    for eid in order:
        proc = built[eid].procedure
        steps.append({
            "entity_id": eid,
            "procedure": proc.model_dump(mode="json") if proc is not None else None,
        })
    return yaml.dump(steps, default_flow_style=False, sort_keys=False)


def _build_readme(
    graph: "EntityGraph",
    built: dict[str, "BuildOutcome"],
    order: list[str],
    request: str,
    warnings: list[str],
) -> str:
    lines: list[str] = ["# GoE Build Package", ""]
    if request:
        lines += ["## Request", "", f"> {request}", ""]

    systems = ", ".join(s.id for s in graph.systems) or "(none)"
    lines += ["## System", "", f"Systems: {systems}", ""]

    if warnings:
        lines += ["## ⚠ Warnings", ""]
        lines += [f"- {w}" for w in warnings]
        lines += [""]

    lines += [
        "## Entities",
        "",
        "| Entity | Runtime | Atoms | Status | Attempts |",
        "| --- | --- | --- | --- | --- |",
    ]
    for eid in order:
        entity = graph.entity_by_id(eid)
        result = built[eid].result
        atoms = ", ".join(entity.atoms) or "—"
        lines.append(
            f"| {eid} | {entity.runtime.value} | {atoms} | "
            f"{result.status.value} | {result.attempts} |"
        )
    lines.append("")

    lines += ["## Edge Chain", ""]
    if graph.edges:
        for edge in graph.edges:
            dst = edge.to_entity or "(terminal)"
            lines.append(f"- `{edge.from_entity}` → `{dst}` ({edge.type.value})")
    else:
        lines.append("(no edges)")
    lines.append("")

    lines += [
        "## Running",
        "",
        "Deploy the full single-box environment:",
        "",
        "```bash",
        "bash deploy.sh",
        "```",
        "",
        "Attack steps for each entity are in `playbook.yaml` "
        "(executable via the GoE procedure runner).",
        "",
    ]
    return "\n".join(lines)


def package(
    graph: "EntityGraph",
    built: dict[str, "BuildOutcome"],
    out_dir: Path,
    request: str = "",
) -> Path:
    """Assemble PASSED entities into a self-contained package under ``out_dir``.

    Writes ``deploy.sh`` (one concatenated, post-processed script), ``playbook.yaml``
    (per-entity attack procedures), and ``README.md``. Returns ``out_dir``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    order = _ordered_built(graph, built)
    warnings = _detect_port_collisions(graph, order)

    deploy_sh = _build_deploy_sh(graph, built, order)
    deploy_path = out_dir / "deploy.sh"
    deploy_path.write_text(deploy_sh, encoding="utf-8")
    deploy_path.chmod(0o755)

    (out_dir / "playbook.yaml").write_text(
        _build_playbook(built, order), encoding="utf-8"
    )
    (out_dir / "README.md").write_text(
        _build_readme(graph, built, order, request, warnings), encoding="utf-8"
    )

    return out_dir
