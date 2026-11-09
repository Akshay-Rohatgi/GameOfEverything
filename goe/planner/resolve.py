"""Deterministic structural param resolution — no LLM.

Fills params that can be resolved from System definitions:
- host params → system network.hostname
- port params on network_reach edges → first exposed port of target system
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from goe.models.edge import EdgeType

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph


# Edge types that carry a credential an entity leaks and a later step consumes.
_CRED_EDGE_TYPES = {EdgeType.creds_for, EdgeType.db_session, EdgeType.token_for}


def resolve(graph: "EntityGraph") -> "EntityGraph":
    """Fill structural params deterministically from System definitions and pinned config."""
    for edge in graph.edges:
        # Resolve 'host' param → hostname of the target entity's system
        if "host" in edge.params and edge.params["host"].concrete is None:
            hostname = _resolve_host(graph, edge)
            if hostname:
                edge.params["host"].concrete = hostname

        # Resolve 'port' param on network_reach → first exposed port of target system
        if edge.type == EdgeType.network_reach and "port" in edge.params:
            if edge.params["port"].concrete is None:
                port = _resolve_port(graph, edge)
                if port:
                    edge.params["port"].concrete = port

        # Resolve credential params (user/secret/password) from the planner-pinned
        # weak-credential config on an entity in this edge. Without this the value is
        # left for the producing entity to invent at build time, so the leaked creds
        # (and the downstream step that uses them) don't match the service's real
        # account. Pinning here makes the leak and the attacker's login consistent.
        if edge.type in _CRED_EDGE_TYPES:
            _resolve_creds(graph, edge)

    return graph


def _resolve_creds(graph: "EntityGraph", edge) -> None:
    """Fill user/secret/password concretes from a pinned weak-credential entity config.

    The creds are defined by the entity carrying ``weak_service_password`` (its config
    pins ``username``/``password``). Prefer the edge's target (the service the creds are
    *for*), then its source. Only fills params the edge actually declares and that are
    still unset — never overwrites a concrete the planner already chose.
    """
    cfg = None
    for eid in (edge.to_entity, edge.from_entity):
        if not eid or eid == "operator":
            continue
        ent = graph.entity_by_id(eid)
        c = getattr(ent, "config", None) if ent is not None else None
        if c and (c.get("password") or c.get("username")):
            cfg = c
            break
    if not cfg:
        return

    username = cfg.get("username")
    password = cfg.get("password")
    if username and "user" in edge.params and edge.params["user"].concrete is None:
        edge.params["user"].concrete = username
    if password:
        # Different edge schemas name the secret param 'secret' or 'password'.
        for key in ("secret", "password"):
            if key in edge.params and edge.params[key].concrete is None:
                edge.params[key].concrete = password


def _resolve_host(graph: "EntityGraph", edge) -> str | None:
    if edge.to_entity is None:
        return None
    entity = graph.entity_by_id(edge.to_entity)
    if entity is None:
        return None
    system = graph.system_by_id(entity.system_id)
    if system is None:
        return None
    return system.network.hostname


def _resolve_port(graph: "EntityGraph", edge) -> str | None:
    if edge.to_entity is None:
        return None
    entity = graph.entity_by_id(edge.to_entity)
    if entity is None:
        return None
    system = graph.system_by_id(entity.system_id)
    if system is None:
        return None
    if not system.network.exposed_ports:
        return None

    # If the structural port matches an exposed port, use it
    structural_port_str = edge.params["port"].structural
    try:
        structural_port_int = int(structural_port_str)
        if structural_port_int in system.network.exposed_ports:
            return structural_port_str
    except (ValueError, AttributeError):
        pass

    # Otherwise fall back to the first exposed port
    return str(system.network.exposed_ports[0])
