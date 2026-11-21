"""Deterministic static checks + grounding for the cross-host chain procedure.

Three Week-7 fixes live here, all derived from the P2 run-2 chain failure where the
chain attacker invented a wrong exploitation path (inject an SSH key into ``/root`` through
the ``www-data`` cmd-injection vector, then ``ssh root@webhost``) instead of using the
scenario's real privesc (passwordless sudo / SUID):

* ``privesc_grounding`` (option 1b) — assemble, from graph facts, a MANDATORY instruction
  that binds the chain attacker to the real privesc entity's technique and forbids the
  improvised root paths. Grounding, not generic prose: the starting user, the privesc
  entity, its atom, and (when extractable) its exact escalation command all come from the
  graph + per-entity procedures.
* ``lint_chain`` option-2 check — flag any step that writes a root-owned path (``/root``,
  ``/etc``) THROUGH the web-injection vector. That runs as the unprivileged web user and
  cannot write there pre-privesc; it is order-independent and unambiguous.
* ``lint_chain`` option-3 check — flag a side-effecting injection step whose assertion only
  checks transport (HTTP/exit status), not the effect, so a silently-failed injection is
  caught at that step instead of three steps later.

All functions are pure (no Docker/LLM), so they unit-test offline.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph
    from goe.models.procedure import Procedure
    from goe.models.report import BuildOutcome


# Root-owned directories the unprivileged web user cannot write before escalation.
_ROOT_OWNED = ("/root", "/etc")

# A write redirection or `tee` targeting a root-owned path.
_WRITE_ROOT_RE = re.compile(r"""(?:>>?|\btee\b(?:\s+-a)?)\s*['"]?(/root|/etc)(?:/|\b)""")

# A curl whose target is a `${system.<id>.host}` — i.e. a request through a web vector.
_WEB_CURL_RE = re.compile(r"\bcurl\b[^\n]*\$\{system\.[^.}]+\.host\}")

# Side-effecting shell verbs commonly injected through a web command-injection payload.
_SIDE_EFFECT_RE = re.compile(
    r">>?|\btee\b|\buseradd\b|\busermod\b|\bgroupadd\b|\bmkdir\b|\bchmod\b|\bchown\b|"
    r"\bcp\b|\bmv\b|\bln\b|authorized_keys"
)

# Assertion keys that only prove the request/shell was reached, not that it did anything.
_TRANSPORT_ASSERT_KEYS = {"exit_code", "status"}


# --------------------------------------------------------------------------- #
# command / step helpers
# --------------------------------------------------------------------------- #
def _command(step) -> str | None:
    return getattr(getattr(step, "action", None), "command", None)


def _writes_root_path(cmd: str | None) -> str | None:
    if not cmd:
        return None
    m = _WRITE_ROOT_RE.search(cmd)
    return m.group(1) if m else None


def _is_web_injection(cmd: str | None) -> bool:
    return bool(cmd) and bool(_WEB_CURL_RE.search(cmd))


def _is_side_effecting(cmd: str | None) -> bool:
    return bool(cmd) and bool(_SIDE_EFFECT_RE.search(cmd))


def _assertion_is_transport_only(step) -> bool:
    """True when the step has no assertion, or only asserts transport (HTTP/exit status).

    A side-effecting injection with such an assertion is a false-positive risk: the curl
    returns 200 / exit 0 even when the injected write was denied."""
    exp = getattr(step, "expect", None)
    if exp is None:
        return True
    try:
        dumped = exp.model_dump(exclude_none=True) if hasattr(exp, "model_dump") else {}
    except Exception:
        return False
    keys = set(dumped)
    return bool(keys) and keys.issubset(_TRANSPORT_ASSERT_KEYS)


# --------------------------------------------------------------------------- #
# Options 2 + 3 — static lint of a synthesised chain procedure
# --------------------------------------------------------------------------- #
def lint_chain(procedure: "Procedure") -> list[str]:
    """Return human-readable issues a chain procedure will provably fail on at runtime.

    Each issue is phrased as actionable guidance so it can be fed straight to
    ``chain_attacker.fix_chain`` before the first (wasted) execution attempt."""
    issues: list[str] = []
    for step in getattr(procedure, "procedure", None) or []:
        cmd = _command(step)
        if not cmd:
            continue
        sid = getattr(step, "step_id", "?")
        if not _is_web_injection(cmd):
            continue

        # Option 2: write to a root-owned path through the web-injection vector.
        root_path = _writes_root_path(cmd)
        if root_path:
            issues.append(
                f"Step '{sid}': writes the root-owned path '{root_path}...' THROUGH the "
                f"web-injection vector, which runs as the unprivileged web user and CANNOT "
                f"write {root_path} before privilege escalation. Escalate to root first using "
                f"the privilege-escalation entity's own technique (sudo/SUID), then do any "
                f"root-owned write from that root shell — or drop this step. (This is exactly "
                f"why a later `ssh root@<host>` fails 'Permission denied (publickey)': the key "
                f"never landed in root's authorized_keys.)"
            )
            continue

        # Option 3: side-effecting injection whose assertion only checks transport.
        if _is_side_effecting(cmd) and _assertion_is_transport_only(step):
            issues.append(
                f"Step '{sid}': performs an injected write/side-effect but asserts only on the "
                f"HTTP/exit status, not the effect — so a silently-failed injection 'passes' "
                f"here and only surfaces several steps later. Assert on the EFFECT: add a "
                f"follow-up read that confirms the write landed (e.g. read the file back / "
                f"verify the created user), and assert on that output."
            )
    return issues


# --------------------------------------------------------------------------- #
# Option 1b — privilege-escalation grounding injected into the attacker prompt
# --------------------------------------------------------------------------- #
def _edge_type(edge) -> str:
    t = getattr(edge, "type", None)
    return getattr(t, "value", t)


def _edge_user(edge) -> str | None:
    u = edge.params.get("user")
    if u is None:
        return None
    return u.concrete or u.structural


def _entity_system(graph, entity_id) -> str | None:
    if not entity_id:
        return None
    ent = graph.entity_by_id(entity_id)
    return getattr(ent, "system_id", None) if ent is not None else None


def _extract_privesc_commands(outcome) -> list[str]:
    """Pull the escalation command(s) from a privesc entity's own L2 procedure.

    Best-effort: a ``sudoers_no_passwd`` entity's procedure runs ``sudo …``; a ``set_suid``
    entity invokes the SUID binary. We surface commands that mention sudo / SUID so the
    chain attacker can copy the real technique verbatim instead of improvising."""
    if outcome is None:
        return []
    proc = getattr(outcome, "procedure", None)
    out: list[str] = []
    for s in getattr(proc, "procedure", None) or []:
        c = _command(s)
        if not c:
            continue
        low = c.lower()
        if "sudo" in low or "u+s" in c or "suid" in low or "setuid" in low:
            out.append(c.strip().replace("\n", " ")[:200])
    return out[:4]


def privesc_grounding(graph: "EntityGraph", built: dict[str, "BuildOutcome"]) -> str:
    """Build a MANDATORY privesc-grounding section for the chain-attacker prompt.

    Returns "" when the graph has no privilege escalation to ground (nothing to say)."""
    shell_edges = [e for e in graph.edges if _edge_type(e) == "shell_as"]
    unpriv = [e for e in shell_edges
              if (_edge_user(e) or "").lower() not in ("root", "")]
    root_edges = [e for e in shell_edges
                  if (_edge_user(e) or "").lower() == "root"
                  and e.from_entity not in (None, "operator")]
    if not root_edges:
        return ""

    lines = ["## Privilege Escalation — MANDATORY: use the real technique", ""]
    for e in unpriv:
        sys_id = _entity_system(graph, e.from_entity)
        lines.append(
            f"- You obtain a shell as `{_edge_user(e)}` on system `{sys_id}` via edge "
            f"`{e.id}` (from `{e.from_entity}`). This user is UNPRIVILEGED."
        )
    for e in root_edges:
        ent_id = e.from_entity
        ent = graph.entity_by_id(ent_id)
        atoms = ", ".join(getattr(ent, "atoms", None) or []) if ent is not None else ""
        sys_id = _entity_system(graph, ent_id)
        lines.append(
            f"- To become **root** on `{sys_id}`, you MUST use entity `{ent_id}`'s own "
            f"technique (atom: {atoms or 'see its per-entity procedure'}), via edge `{e.id}`. "
            f"Run its escalation from WITHIN the unprivileged shell you already have."
        )
        cmds = _extract_privesc_commands(built.get(ent_id))
        if cmds:
            lines.append("  Use these exact command(s) from its per-entity procedure to escalate:")
            for c in cmds:
                lines.append(f"    - `{c}`")

    lines += [
        "",
        "Do NOT invent an alternative root path. Specifically, do NOT inject SSH keys into "
        "`/root`, do NOT `useradd` a new privileged user, and do NOT `ssh root@<this host>` "
        "to gain root. The unprivileged web user CANNOT write root-owned paths (`/root`, "
        "`/etc`) until AFTER you have escalated. Once you are root via the technique above, "
        "read any root-owned material (e.g. the lateral SSH key) directly from the root shell.",
    ]
    return "\n".join(lines) + "\n\n"
