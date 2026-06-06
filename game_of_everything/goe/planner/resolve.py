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


def resolve(graph: "EntityGraph") -> "EntityGraph":
    """Fill structural params deterministically from System definitions."""
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

    return graph


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
    if system.network.exposed_ports:
        return str(system.network.exposed_ports[0])
    return None
