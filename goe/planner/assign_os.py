"""Step 0.25: assign a (possibly diverse) OS to each host in a multi-host graph.

A small, constrained Haiku-tier planner call. ``design_systems`` already infers an
OS per system, and the ``--os`` flag can force one distro everywhere; this step sits
between them for the *unforced, multi-host* case, deliberately spreading the
registered distros across hosts so topologies aren't uniformly one OS.

Single-host scenarios are a no-op (nothing to diversify) and issue no LLM call.
Only distros with a registered ``DistroProfile`` (``goe.distros.available_distros``)
are ever assigned; an invalid or missing entry leaves that host's inferred OS intact
so a bad model response can never fail the run.
"""

from __future__ import annotations

from pathlib import Path

from goe.distros import available_distros, normalize_os
from goe.models.system import System
from goe.planner._utils import call_json

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "assign_os.md"
).read_text(encoding="utf-8")


def _render_user_msg(request: str, systems: list[System]) -> str:
    lines = [
        "Assign one operating system to each host for this attack scenario.",
        "",
        f"Scenario:\n{request}",
        "",
        "Hosts:",
    ]
    for s in systems:
        svcs = ", ".join(spec.id for spec in s.services) or "(none)"
        lines.append(f"- {s.id} (hostname: {s.network.hostname}; services: {svcs})")
    lines += [
        "",
        f"Allowed OSes: {', '.join(available_distros())}",
        "",
        "Return a JSON object mapping each host id above to one allowed OS.",
    ]
    return "\n".join(lines)


def assign_os(request: str, systems: list[System], model: str) -> list[System]:
    """Assign an OS per host via Haiku, diversifying across a multi-host topology.

    Returns a new systems list; the input is unchanged. A no-op (returns ``systems``
    as-is, no LLM call) for single-host scenarios.
    """
    if len(systems) <= 1:
        return systems

    data = call_json(
        model, _SYSTEM_PROMPT, _render_user_msg(request, systems),
        caller="planner.assign_os",
    )
    mapping = data if isinstance(data, dict) else {}

    updated: list[System] = []
    for s in systems:
        chosen = mapping.get(s.id)
        if isinstance(chosen, str):
            try:
                canonical = normalize_os(chosen)
            except ValueError:
                canonical = None
            if canonical and canonical != s.os:
                s = s.model_copy(update={"os": canonical})
        updated.append(s)
    return updated
