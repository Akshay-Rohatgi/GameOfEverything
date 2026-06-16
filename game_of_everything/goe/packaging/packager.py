"""Packager — assemble built entities into a self-contained deploy package.

Single-system: all entities deploy onto one box → one ``deploy.sh``, one
``playbook.yaml``, one ``README.md`` (unchanged from Phase 3).

Multi-system: entities grouped by system_id → per-system ``<sid>_deploy.sh``
files, a ``docker-compose.yml`` (one ubuntu:22.04 service per system on a shared
``goe_net`` network), and the same ``playbook.yaml`` / ``README.md``.

When a chain procedure is provided (from the L3 chain test), it is also written
to ``chain_playbook.yaml`` regardless of single- vs multi-system.
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
    """Return warning lines for web entities on the same system that bind the same port.

    For single-system runs every entity shares one box. For multi-system runs we
    scope the check per system_id — two boxes binding the same port is fine.
    """
    from goe.runtimes.registry import get_registry

    registry = get_registry()
    # seen: {system_id: {port: entity_id}}
    seen: dict[str, dict[int, str]] = {}
    warnings: list[str] = []
    for eid in order:
        entity = graph.entity_by_id(eid)
        runtime = entity.runtime.value
        sid = entity.system_id
        if runtime == "ubuntu":
            continue
        try:
            port = registry.port_for(runtime)
        except Exception:
            continue
        if port in seen.get(sid, {}):
            warnings.append(
                f"Entities `{seen[sid][port]}` and `{eid}` both bind port {port} "
                f"({runtime}) on system `{sid}`; they will collide."
            )
        else:
            seen.setdefault(sid, {})[port] = eid
    return warnings


def _build_docker_compose(graph: "EntityGraph", per_system_scripts: dict[str, str]) -> str:
    """Generate a docker-compose.yml for a multi-system run.

    Each system becomes a service running ubuntu:22.04. Its deploy script is
    embedded as an inline command so ``docker-compose up`` fully configures it.
    """
    import base64

    services: dict[str, dict] = {}
    for system in graph.systems:
        sid = system.id
        hostname = system.network.hostname
        script = per_system_scripts.get(sid, "")
        b64 = base64.b64encode(script.encode()).decode("ascii") if script else ""

        deploy_cmd = (
            f"bash -c 'echo {b64} | base64 -d > /deploy.sh && bash /deploy.sh && sleep infinity'"
            if b64 else "sleep infinity"
        )

        port_mappings: list[str] = []
        for p in system.network.exposed_ports:
            port_mappings.append(f"{p}:{p}")

        svc: dict = {
            "image": "ubuntu:22.04",
            "hostname": hostname,
            # Set network alias so Docker DNS resolves the hostname from other
            # containers. The `default` key matches the networks.default section.
            "networks": {
                "default": {"aliases": [hostname]},
            },
            "command": deploy_cmd,
            "environment": ["DEBIAN_FRONTEND=noninteractive"],
        }
        if port_mappings:
            svc["ports"] = port_mappings
        services[sid] = svc

    compose: dict = {
        "version": "3.8",
        "services": services,
        "networks": {
            "default": {
                "name": "goe_net",
                "driver": "bridge",
            }
        },
    }
    return yaml.dump(compose, default_flow_style=False, sort_keys=False)


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

    multi = len(graph.systems) > 1
    if multi:
        lines += [
            "## Running (multi-system)",
            "",
            "Start all systems:",
            "",
            "```bash",
            "docker-compose up -d",
            "```",
            "",
            "Or deploy each system manually:",
            "",
        ]
        for s in graph.systems:
            lines.append(f"```bash\nbash {s.id}_deploy.sh  # system: {s.id} ({s.network.hostname})\n```")
        lines.append("")
        lines += [
            "The end-to-end attack chain is in `chain_playbook.yaml`.",
            "Per-entity attack steps are in `playbook.yaml`.",
            "",
        ]
    else:
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
    chain_procedure=None,  # Procedure | None — from chain_test
) -> Path:
    """Assemble PASSED entities into a self-contained package under ``out_dir``.

    Single-system: writes ``deploy.sh``, ``playbook.yaml``, ``README.md``.
    Multi-system: writes ``<system_id>_deploy.sh`` per system, ``docker-compose.yml``,
    ``playbook.yaml``, ``README.md``.

    When ``chain_procedure`` is provided, also writes ``chain_playbook.yaml``.

    Returns ``out_dir``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    order = _ordered_built(graph, built)
    warnings = _detect_port_collisions(graph, order)
    multi = len(graph.systems) > 1

    if multi:
        # Per-system deploy scripts
        grouped = graph.entities_by_system()
        for system in graph.systems:
            sid = system.id
            entities = grouped.get(sid, [])
            built_entities = [e for e in entities if e.id in built]
            if not built_entities:
                continue
            sections: list[str] = []
            for entity in built_entities:
                script = built[entity.id].deploy_script or ""
                if script.strip():
                    sections.append(f"# --- {entity.id} ---\n{script.strip()}\n")
            if sections:
                combined = apply_post_processors("\n".join(sections)) + "\n"
                deploy_path = out_dir / f"{sid}_deploy.sh"
                deploy_path.write_text(combined, encoding="utf-8")
                deploy_path.chmod(0o755)

        # Per-system scripts dict for docker-compose generation
        per_system_scripts: dict[str, str] = {}
        for system in graph.systems:
            sid = system.id
            deploy_path = out_dir / f"{sid}_deploy.sh"
            if deploy_path.exists():
                per_system_scripts[sid] = deploy_path.read_text(encoding="utf-8")

        (out_dir / "docker-compose.yml").write_text(
            _build_docker_compose(graph, per_system_scripts), encoding="utf-8"
        )
    else:
        # Single-system: one combined deploy.sh (unchanged from Phase 3)
        deploy_sh = _build_deploy_sh(graph, built, order)
        deploy_path = out_dir / "deploy.sh"
        deploy_path.write_text(deploy_sh, encoding="utf-8")
        deploy_path.chmod(0o755)

    # Per-entity playbook (always)
    (out_dir / "playbook.yaml").write_text(
        _build_playbook(built, order), encoding="utf-8"
    )

    # Chain playbook (when chain test ran)
    if chain_procedure is not None:
        chain_data = chain_procedure.model_dump(mode="json")
        (out_dir / "chain_playbook.yaml").write_text(
            yaml.dump(chain_data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    (out_dir / "README.md").write_text(
        _build_readme(graph, built, order, request, warnings), encoding="utf-8"
    )

    return out_dir
