"""ASCII visualization for EntityGraph.

render()       — handrolled fallback (no dependencies)
render_fancy() — graph-easy via subprocess (requires libgraph-easy-perl)
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from goe.graph.topology import topological_sort

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph
    from goe.models.edge import Edge

# Compact edge-type abbreviations
_TYPE_ABBREV = {
    "network_reach": "net",
    "shell_as":      "shell",
    "creds_for":     "creds",
    "db_session":    "db",
    "file_read":     "read",
    "file_write":    "write",
    "code_exec":     "exec",
    "token_for":     "token",
}


def render(graph: "EntityGraph") -> str:
    """Render an EntityGraph as a compact ASCII flow diagram."""
    try:
        order = topological_sort(graph)
    except Exception:
        order = [e.id for e in graph.entities]

    lines: list[str] = []

    # operator outgoing edges
    op_edges = [e for e in graph.edges if e.from_entity == "operator"]
    last_op = len(op_edges) - 1
    for i, edge in enumerate(op_edges):
        prefix = "└──" if i == last_op else "├──"
        lines.append(f"operator")
        lines.append(f"  {prefix} {_compact_edge(edge)} ──► {edge.to_entity}")
        break  # print "operator" once then list all edges below

    # Re-do: print operator once with all its edges
    lines.clear()
    lines.append("operator")
    for i, edge in enumerate(op_edges):
        prefix = "└──" if i == len(op_edges) - 1 else "├──"
        lines.append(f"  {prefix} {_compact_edge(edge)} ──► {edge.to_entity}")
    lines.append("")

    # Each entity in topological order
    for eid in order:
        entity = graph.entity_by_id(eid)
        if entity is None:
            continue

        tag = ""
        if entity.app_spec:
            vulns = "/".join(entity.app_spec.vulnerabilities)
            tag = f"  [{entity.app_spec.runtime} · {vulns}]"

        lines.append(f"{entity.id}{tag}")

        # One-line description (truncated)
        desc = entity.description
        if len(desc) > 80:
            desc = desc[:77] + "..."
        lines.append(f"  {desc}")

        # Outgoing edges
        outgoing = [e for e in graph.edges if e.from_entity == eid]
        for i, edge in enumerate(outgoing):
            prefix = "└──" if i == len(outgoing) - 1 else "├──"
            target = edge.to_entity or "(terminal)"
            lines.append(f"  {prefix} {_compact_edge(edge)} ──► {target}")

        lines.append("")

    return "\n".join(lines)


def render_fancy(graph: "EntityGraph") -> str:
    """Render using graph-easy (requires libgraph-easy-perl).

    Falls back to render() if graph-easy is not installed.
    """
    try:
        dot = _to_graph_easy_input(graph)
        result = subprocess.run(
            ["graph-easy"],
            input=dot,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        return render(graph)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return render(graph)


def _to_graph_easy_input(graph: "EntityGraph") -> str:
    """Emit graph-easy's simple text format: [ node ] -> { label: x } [ node ]"""
    lines = []

    def node_label(entity_id: str) -> str:
        if entity_id == "operator":
            return "operator"
        entity = graph.entity_by_id(entity_id)
        if entity is None:
            return entity_id
        if entity.app_spec:
            vulns = "/".join(entity.app_spec.vulnerabilities)
            return f"{entity.id}\\n({entity.app_spec.runtime}·{vulns})"
        return entity.id

    for edge in graph.edges:
        src = node_label(edge.from_entity)
        tgt = node_label(edge.to_entity) if edge.to_entity else "(terminal)"
        label = _compact_edge(edge)
        lines.append(f'[ {src} ] -> {{ label: {label} }} [ {tgt} ]')

    return "\n".join(lines)


def _compact_edge(edge: "Edge") -> str:
    """Short edge label: type(key=val ...)"""
    etype = _TYPE_ABBREV.get(edge.type.value, edge.type.value)
    key_params = {
        "network_reach": ["port"],
        "shell_as":      ["user"],
        "creds_for":     ["user", "cred_type"],
        "db_session":    ["db_type"],
        "file_read":     ["path"],
        "file_write":    ["path"],
        "code_exec":     ["as_user"],
        "token_for":     ["scope"],
    }
    parts = []
    for key in key_params.get(edge.type.value, []):
        if key in edge.params:
            val = edge.params[key].concrete or edge.params[key].structural
            parts.append(val)
    detail = ":".join(parts) if parts else ""
    return f"{etype}({detail})" if detail else etype
