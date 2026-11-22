"""Deterministic atom <-> distro-family compatibility check (fail-fast at plan time).

Some atoms are inherently tied to one distro family (e.g. ``motd_command_injection``
depends on Debian/Ubuntu's ``/etc/update-motd.d`` dynamic MOTD, absent on Red Hat).
Such an atom declares a ``families`` allowlist in its frontmatter. This module checks,
right after entities are planned (atoms attached) and each host's OS is finalized, that
no entity places a family-restricted atom on an incompatible host.

Running this before the expensive build (LLM synthesis + Docker) turns what would
otherwise be a long distro-lint retry loop that burns API calls — the architect trying
and failing to port an apt-only technique to dnf — into an instant, clear rejection.
"""
from __future__ import annotations

from goe.construction_crew.atoms import supported_families
from goe.distros import get_profile


class AtomFamilyConflict(ValueError):
    """Raised when a required atom cannot run on its host's distro family."""


def check_atom_family_compat(stubs, systems, *, os_forced: bool) -> None:
    """Raise :class:`AtomFamilyConflict` if any entity's atom is family-incompatible.

    Args:
        stubs: planned entity stubs (each with ``system_id`` and ``atoms``).
        systems: the designed systems (each with ``id`` and finalized ``os``).
        os_forced: whether the OS came from an explicit ``--os`` override. Only
            changes the remediation hint in the message (force a compatible ``--os``
            vs. let the planner choose / drop the atom), not whether we reject.
    """
    sys_by_id = {s.id: s for s in systems}
    conflicts: list[tuple[str, str, str, str, str, list[str]]] = []

    for stub in stubs:
        system = sys_by_id.get(getattr(stub, "system_id", None))
        if system is None:
            continue
        family = get_profile(system.os).family
        for atom in (getattr(stub, "atoms", None) or []):
            allowed = supported_families(atom)
            if allowed and family not in allowed:
                conflicts.append(
                    (stub.id, atom, system.id, system.os, family, allowed)
                )

    if not conflicts:
        return

    lines = [
        "Atom/OS family conflict — these atoms cannot run on the assigned host(s):",
        "",
    ]
    for entity_id, atom, sys_id, os_name, family, allowed in conflicts:
        lines.append(
            f"  - entity {entity_id!r}: atom {atom!r} supports only "
            f"{allowed}, but host {sys_id!r} is {os_name!r} (family {family!r})."
        )
    lines.append("")
    if os_forced:
        lines.append(
            "The OS was forced with --os. Re-run without it (let the planner pick a "
            "compatible distro), force a supported one (e.g. --os debian), or use a "
            "scenario that does not need these atoms."
        )
    else:
        lines.append(
            "Adjust the request so these atoms land on a compatible host, or drop them."
        )
    raise AtomFamilyConflict("\n".join(lines))
