"""Pure utility functions for multi-box topology processing.

Functions here have no side effects and no I/O — they transform topology
models and return results. Safe to import from anywhere in the pipeline.
"""

import re
from collections import deque
from typing import Dict, List, Set

from game_of_everything.models import (
    BoxDefinition,
    ChainProbe,
    NetworkTopology,
    PivotLink,
    SharedSecret,
)

# ---------------------------------------------------------------------------
# Pivot command templates
# ---------------------------------------------------------------------------

PIVOT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "credential_reuse": {
        "ssh": "sshpass -p '{value}' ssh -o StrictHostKeyChecking=no {user}@{hostname} 'whoami && hostname'",
        "mysql": "mysql -h {hostname} -u {user} -p'{value}' -e 'SELECT 1'",
        "ftp": "curl -s ftp://{user}:{value}@{hostname}/",
        "smb": "smbclient -L //{hostname} -U {user}%{value} -N",
        "web_login": "curl -s -d 'username={user}&password={value}' http://{hostname}/login",
    },
    "ssh_key_reuse": {
        "ssh": "ssh -i /tmp/stolen_key_{key} -o StrictHostKeyChecking=no {user}@{hostname} 'whoami && hostname'",
    },
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _all_scope_text(box: BoxDefinition) -> str:
    """Return all credential-bearing scope text for a box, for secret value searching."""
    parts = [box.misconfig_scope or ""]
    if box.custom_app_scope:
        parts.append(box.custom_app_scope)
    for cv in box.custom_vectors:
        if cv.synthesis_context:
            parts.append(cv.synthesis_context)
        if cv.seed_password:
            parts.append(cv.seed_password)
        if cv.db_password:
            parts.append(cv.db_password)
    return " ".join(parts)


def _bfs_reachable(entry_point: str, pivots: List[PivotLink]) -> Set[str]:
    """BFS from entry_point over pivot edges. Returns set of reachable box_ids."""
    reachable: Set[str] = {entry_point}
    queue: deque = deque([entry_point])
    while queue:
        current = queue.popleft()
        for pivot in pivots:
            if pivot.from_box == current and pivot.to_box not in reachable:
                reachable.add(pivot.to_box)
                queue.append(pivot.to_box)
    return reachable


# ---------------------------------------------------------------------------
# Topology validation
# ---------------------------------------------------------------------------

def validate_topology(topo: NetworkTopology) -> List[str]:
    """Programmatic consistency checks. Returns list of error strings (empty = valid)."""
    errors: List[str] = []
    box_ids = {b.box_id for b in topo.boxes}

    # Every pivot references valid box_ids
    for pivot in topo.pivots:
        if pivot.from_box not in box_ids:
            errors.append(f"Pivot references unknown from_box: {pivot.from_box!r}")
        if pivot.to_box not in box_ids:
            errors.append(f"Pivot references unknown to_box: {pivot.to_box!r}")

    # Every shared secret references valid box_ids and has a non-empty value
    for secret in topo.shared_secrets:
        if secret.source_box not in box_ids:
            errors.append(
                f"SharedSecret {secret.key!r} references unknown source_box: {secret.source_box!r}"
            )
        if secret.target_box not in box_ids:
            errors.append(
                f"SharedSecret {secret.key!r} references unknown target_box: {secret.target_box!r}"
            )
        if not secret.value.strip():
            errors.append(f"SharedSecret {secret.key!r} has an empty value")

        # Secret value must appear in BOTH boxes' scope text (synthesis consistency check)
        if secret.source_box in box_ids and secret.target_box in box_ids and secret.value.strip():
            src = next(b for b in topo.boxes if b.box_id == secret.source_box)
            tgt = next(b for b in topo.boxes if b.box_id == secret.target_box)
            if secret.value not in _all_scope_text(src):
                errors.append(
                    f"Secret {secret.key!r} value not found in source box {src.box_id!r} scope — "
                    f"the attacker cannot discover it there"
                )
            if secret.value not in _all_scope_text(tgt):
                errors.append(
                    f"Secret {secret.key!r} value not found in target box {tgt.box_id!r} scope — "
                    f"the credential won't work on that box"
                )

    # All entry points must be valid box_ids
    for ep in topo.entry_point:
        if ep not in box_ids:
            errors.append(f"Entry point {ep!r} is not a valid box_id")

    # Every box must be reachable from at least one entry point
    valid_entries = [ep for ep in topo.entry_point if ep in box_ids]
    if valid_entries:
        reachable: Set[str] = set()
        for ep in valid_entries:
            reachable |= _bfs_reachable(ep, topo.pivots)
        unreachable = box_ids - reachable
        if unreachable:
            errors.append(
                f"Boxes not reachable from any entry point {topo.entry_point!r}: {sorted(unreachable)}"
            )

    return errors


# ---------------------------------------------------------------------------
# Deploy-script credential verification
# ---------------------------------------------------------------------------

def validate_deploy_script_credentials(
    topology: NetworkTopology,
    deploy_scripts: Dict[str, str],
) -> List[str]:
    """Check that each SharedSecret value appears in both its source and target deploy scripts.

    Returns a list of warning strings (empty list means all credentials are present).
    Does not raise — callers should warn rather than abort.
    """
    warnings: List[str] = []
    for secret in topology.shared_secrets:
        for role, box_id in (("source", secret.source_box), ("target", secret.target_box)):
            script = deploy_scripts.get(box_id, "")
            if script and secret.value not in script:
                warnings.append(
                    f"Secret {secret.key!r} (value={secret.value!r}): "
                    f"value not found in {box_id!r} deploy script ({role} box)"
                )
            elif not script:
                warnings.append(
                    f"Secret {secret.key!r}: no deploy script for {box_id!r} ({role} box) "
                    f"— cannot verify credential presence"
                )
    return warnings


# ---------------------------------------------------------------------------
# Chain probe generation
# ---------------------------------------------------------------------------

def generate_chain_probes(topo: NetworkTopology) -> List[ChainProbe]:
    """Build deterministic chain test probes from pivot + secret data. No LLM needed."""
    probes: List[ChainProbe] = []

    for i, pivot in enumerate(topo.pivots):
        secret = next(
            (s for s in topo.shared_secrets if s.key == pivot.secret_ref), None
        )
        if not secret:
            continue

        target_box = next(
            (b for b in topo.boxes if b.box_id == pivot.to_box), None
        )
        if not target_box:
            continue

        template = PIVOT_TEMPLATES.get(pivot.method, {}).get(secret.access_method)
        if not template:
            continue

        command = template.format(
            value=secret.value,
            user=secret.target_user,
            hostname=target_box.hostname,
            key=secret.key,
        )
        probes.append(ChainProbe(
            step=i + 1,
            from_container="attacker",
            target_hostname=target_box.hostname,
            command=command,
            # Expect the target hostname to appear in whoami+hostname output
            success_pattern=re.escape(target_box.hostname),
        ))

    return probes


# ---------------------------------------------------------------------------
# Topological ordering
# ---------------------------------------------------------------------------

def topological_order(topo: NetworkTopology) -> List[BoxDefinition]:
    """Return boxes in BFS dependency order starting from all entry_points."""
    box_map = {b.box_id: b for b in topo.boxes}
    visited: Set[str] = set()
    order: List[BoxDefinition] = []
    queue: deque = deque(topo.entry_point)

    while queue:
        box_id = queue.popleft()
        if box_id in visited:
            continue
        visited.add(box_id)
        if box_id in box_map:
            order.append(box_map[box_id])
        for pivot in topo.pivots:
            if pivot.from_box == box_id and pivot.to_box not in visited:
                queue.append(pivot.to_box)

    # Append any boxes unreachable via pivots (shouldn't happen in a valid topology)
    for box in topo.boxes:
        if box.box_id not in visited:
            order.append(box)

    return order
