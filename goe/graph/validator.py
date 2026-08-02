"""Static graph validator — 7 checks, pure code, no LLM."""

from __future__ import annotations

from typing import TYPE_CHECKING

from goe.models.edge import EdgeType
from goe.graph.topology import CycleError, reachable_from_operator, topological_sort

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph, ValidationResult, Violation


EDGE_TYPE_PARAMS: dict[EdgeType, set[str]] = {
    EdgeType.network_reach: {"host", "port"},
    EdgeType.shell_as: {"user", "host"},
    EdgeType.creds_for: {"user", "host", "cred_type", "secret"},
    EdgeType.db_session: {"db_type", "host", "user"},
    EdgeType.file_read: {"path", "host", "as_user"},
    EdgeType.file_write: {"path", "host", "as_user"},
    EdgeType.code_exec: {"runtime", "host", "as_user"},
    EdgeType.token_for: {"service", "host", "scope", "token"},
}


def validate(graph: "EntityGraph") -> "ValidationResult":
    from goe.graph.models import ValidationResult

    violations: list = []
    violations.extend(_check_edge_coverage(graph))
    violations.extend(_check_provides_wired(graph))
    violations.extend(_check_duplicate_edges(graph))
    violations.extend(_check_type_compatibility(graph))
    violations.extend(_check_reachability(graph))
    violations.extend(_check_fan_out(graph))
    violations.extend(_check_system_refs(graph))
    violations.extend(_check_no_cycles(graph))
    violations.extend(_check_initial_access(graph))

    return ValidationResult(valid=len(violations) == 0, violations=violations)


def _v(check: str, message: str, entity_id: str | None = None, edge_id: str | None = None):
    from goe.graph.models import Violation
    return Violation(check=check, entity_id=entity_id, edge_id=edge_id, message=message)


def _check_provides_wired(graph: "EntityGraph") -> list:
    """Every edge_id in an entity's provides must exist as an Edge in the graph."""
    violations = []
    edge_ids = {e.id for e in graph.edges}

    for entity in graph.entities:
        for provided_edge_id in entity.provides:
            if provided_edge_id not in edge_ids:
                violations.append(_v(
                    "provides_wired",
                    f"Entity '{entity.id}' provides edge '{provided_edge_id}' which does not exist in the graph",
                    entity_id=entity.id,
                    edge_id=provided_edge_id,
                ))

    return violations


def _check_duplicate_edges(graph: "EntityGraph") -> list:
    """No two edges should connect the same (from, to, type) triple."""
    violations = []
    seen: dict[tuple, str] = {}

    for edge in graph.edges:
        key = (edge.from_entity, edge.to_entity, edge.type)
        if key in seen:
            violations.append(_v(
                "duplicate_edges",
                f"Edges '{seen[key]}' and '{edge.id}' connect the same "
                f"{edge.from_entity!r} → {edge.to_entity!r} with type {edge.type.value!r}. "
                "Use a single edge with a consistent ID.",
                edge_id=edge.id,
            ))
        else:
            seen[key] = edge.id

    return violations


def _check_edge_coverage(graph: "EntityGraph") -> list:
    violations = []
    edge_ids = {e.id: e for e in graph.edges}

    for entity in graph.entities:
        for req in entity.requires:
            if req.edge_id not in edge_ids:
                violations.append(_v(
                    "edge_coverage",
                    f"Entity '{entity.id}' requires edge '{req.edge_id}' which does not exist",
                    entity_id=entity.id,
                    edge_id=req.edge_id,
                ))
            else:
                edge = edge_ids[req.edge_id]
                # For fan-out edges (to_entity=None), skip target check
                if edge.to_entity is not None and edge.to_entity != entity.id:
                    violations.append(_v(
                        "edge_coverage",
                        f"Entity '{entity.id}' requires edge '{req.edge_id}' but that edge targets '{edge.to_entity}'",
                        entity_id=entity.id,
                        edge_id=req.edge_id,
                    ))

    return violations


def _check_type_compatibility(graph: "EntityGraph") -> list:
    violations = []

    for edge in graph.edges:
        expected = EDGE_TYPE_PARAMS.get(edge.type)
        if expected is None:
            continue
        actual = set(edge.params.keys())
        missing = expected - actual
        extra = actual - expected
        if missing:
            violations.append(_v(
                "type_compatibility",
                f"Edge '{edge.id}' (type={edge.type.value}) missing params: {sorted(missing)}",
                edge_id=edge.id,
            ))
        if extra:
            violations.append(_v(
                "type_compatibility",
                f"Edge '{edge.id}' (type={edge.type.value}) has unexpected params: {sorted(extra)}",
                edge_id=edge.id,
            ))

    return violations


def _check_reachability(graph: "EntityGraph") -> list:
    violations = []
    reachable = reachable_from_operator(graph)

    for entity in graph.entities:
        if entity.id not in reachable:
            violations.append(_v(
                "reachability",
                f"Entity '{entity.id}' is not reachable from operator",
                entity_id=entity.id,
            ))

    return violations


def _check_fan_out(graph: "EntityGraph") -> list:
    """Each edge_id should be required by at most one entity, unless fan_out=True."""
    violations = []
    edge_consumers: dict[str, list[str]] = {}
    edge_lookup = {e.id: e for e in graph.edges}

    for entity in graph.entities:
        for req in entity.requires:
            edge_consumers.setdefault(req.edge_id, []).append(entity.id)

    for edge_id, consumers in edge_consumers.items():
        if len(consumers) > 1:
            edge = edge_lookup.get(edge_id)
            if edge and edge.fan_out:
                continue  # Explicitly allowed
            violations.append(_v(
                "fan_out_consistency",
                f"Edge '{edge_id}' is required by multiple entities: {consumers}. "
                "Each consumer needs its own edge, or set fan_out=true on the edge.",
                edge_id=edge_id,
            ))

    return violations


def _check_system_refs(graph: "EntityGraph") -> list:
    violations = []
    system_ids = {s.id for s in graph.systems}
    system_hostnames = {s.network.hostname for s in graph.systems}

    for entity in graph.entities:
        if entity.system_id not in system_ids:
            violations.append(_v(
                "system_reference_validity",
                f"Entity '{entity.id}' references unknown system '{entity.system_id}'",
                entity_id=entity.id,
            ))

    for edge in graph.edges:
        if "host" in edge.params:
            host_structural = edge.params["host"].structural
            # Structural host should match either a system ID or hostname
            if host_structural not in system_ids and host_structural not in system_hostnames:
                violations.append(_v(
                    "system_reference_validity",
                    f"Edge '{edge.id}' host param '{host_structural}' does not match any system ID or hostname",
                    edge_id=edge.id,
                ))

    return violations


def _check_no_cycles(graph: "EntityGraph") -> list:
    try:
        topological_sort(graph)
        return []
    except CycleError as e:
        return [_v(
            "no_cycles",
            f"Cycle detected among entities: {sorted(e.cycle_members)}",
        )]


def _check_initial_access(graph: "EntityGraph") -> list:
    """At least one entity must have all its required edges sourced from operator."""
    operator_edge_ids = {e.id for e in graph.edges if e.from_entity == "operator"}

    for entity in graph.entities:
        if not entity.requires:
            # Entity with no requirements is a valid initial access point
            return []
        if all(req.edge_id in operator_edge_ids for req in entity.requires if not req.optional):
            return []

    return [_v(
        "initial_access_exists",
        "No entity has all its required edges sourced from operator. "
        "At least one entity must be directly reachable from operator.",
    )]
