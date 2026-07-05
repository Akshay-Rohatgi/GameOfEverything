"""Variable interpolation for procedure YAML.

Supported syntax:
  ${target_host}                    — built-in variable
  ${attacker_host}                  — built-in variable
  ${target_port}                    — built-in variable
  ${edge.<edge_id>.<param>}         — concrete value from a resolved edge
  ${steps.<step_id>.<output_name>}  — captured output from a previous step
  ${system.<system_id>.host}        — hostname of a system in a multi-system topology
  ${system.<system_id>.port}        — primary port of a system in a multi-system topology
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph

_VAR_RE = re.compile(r'\$\{([^}]+)\}')


def resolve_static_ctx(graph: "EntityGraph", logger: logging.Logger | None = None) -> dict:
    """Resolve the build-time-static half of the interpolation ctx from a graph.

    Returns ``{"systems": {...}, "edges": {...}}`` where:

      * ``systems[sid]`` → ``{"host": hostname, "port": str(first exposed port | "")}``
      * ``edges[edge_id]`` → ``{param: pv.concrete or pv.structural}``

    These are the values that are fixed at package/build time (as opposed to
    ``steps.*`` which are captured live during execution). Both the L3 chain test
    and the standalone ``solve.sh`` transpiler build their commands from this same
    resolution so their emitted commands are identical.

    If an edge param has no concrete value, its structural placeholder is used as a
    fallback and (when ``logger`` is provided) a warning is emitted — a structural
    placeholder leaking into an executed command is a partial-information bug.
    """
    systems: dict[str, dict[str, str]] = {}
    for s in graph.systems:
        port = str(s.network.exposed_ports[0]) if s.network.exposed_ports else ""
        systems[s.id] = {"host": s.network.hostname, "port": port}

    edges: dict[str, dict[str, str]] = {}
    for edge in graph.edges:
        resolved: dict[str, str] = {}
        for p, pv in edge.params.items():
            if pv.concrete is not None:
                resolved[p] = pv.concrete
            elif logger is not None:
                logger.warning(
                    "resolve_static_ctx: edge %s param '%s' has no concrete value; "
                    "falling back to structural placeholder '%s'",
                    edge.id, p, pv.structural,
                )
                resolved[p] = pv.structural
            else:
                resolved[p] = pv.structural
        edges[edge.id] = resolved

    return {"systems": systems, "edges": edges}


def interpolate(template: str, ctx: dict) -> str:
    """Replace all ${...} references in template using ctx."""
    def _replace(m: re.Match) -> str:
        key = m.group(1)
        parts = key.split(".")

        if len(parts) == 1:
            val = ctx.get(parts[0])
        elif parts[0] == "edge" and len(parts) == 3:
            val = ctx.get("edges", {}).get(parts[1], {}).get(parts[2])
        elif parts[0] == "steps" and len(parts) == 3:
            val = ctx.get("steps", {}).get(parts[1], {}).get(parts[2])
        elif parts[0] == "system" and len(parts) == 3:
            val = ctx.get("systems", {}).get(parts[1], {}).get(parts[2])
        else:
            val = None

        if val is None:
            return m.group(0)  # leave unresolved references as-is
        return str(val)

    return _VAR_RE.sub(_replace, template)
