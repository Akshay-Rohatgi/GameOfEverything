"""Topological utilities for EntityGraph: sort, reachability, dependency map."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph


class CycleError(Exception):
    def __init__(self, cycle_members: set[str]):
        self.cycle_members = cycle_members
        super().__init__(f"Cycle detected among entities: {cycle_members}")


def topological_sort(graph: "EntityGraph") -> list[str]:
    """Return entity IDs in build order (dependencies before dependents).

    Raises CycleError if the entity dependency graph is not a DAG.
    """
    deps = dependency_map(graph)
    in_degree: dict[str, int] = {e.id: 0 for e in graph.entities}
    for eid, providers in deps.items():
        for _ in providers:
            in_degree[eid] = in_degree.get(eid, 0)
    # Rebuild in-degree from deps
    in_degree = {e.id: len(deps.get(e.id, set())) for e in graph.entities}

    queue = [eid for eid, deg in in_degree.items() if deg == 0]
    queue.sort()  # deterministic ordering for ties
    result = []

    while queue:
        eid = queue.pop(0)
        result.append(eid)
        # Find entities that depend on eid and reduce their in-degree
        for other_id, providers in deps.items():
            if eid in providers:
                in_degree[other_id] -= 1
                if in_degree[other_id] == 0:
                    queue.append(other_id)
                    queue.sort()

    if len(result) != len(graph.entities):
        remaining = {e.id for e in graph.entities} - set(result)
        raise CycleError(remaining)

    return result


def reachable_from_operator(graph: "EntityGraph") -> set[str]:
    """BFS from 'operator' following edge directions. Returns set of reachable entity IDs."""
    from goe.models.entity import Entity

    reachable: set[str] = set()
    entity_map = {e.id: e for e in graph.entities}

    # Initial frontier: entities with edges from operator
    frontier = []
    for edge in graph.edges:
        if edge.from_entity == "operator":
            if edge.to_entity:
                frontier.append(edge.to_entity)
            elif edge.fan_out:
                # Fan-out edge from operator: all entities requiring it are reachable
                for entity in graph.entities:
                    if any(req.edge_id == edge.id for req in entity.requires):
                        frontier.append(entity.id)

    while frontier:
        eid = frontier.pop(0)
        if eid in reachable:
            continue
        reachable.add(eid)

        # Find outgoing edges from this entity
        for edge in graph.edges:
            if edge.from_entity == eid:
                if edge.to_entity and edge.to_entity not in reachable:
                    frontier.append(edge.to_entity)
                elif edge.fan_out:
                    # Fan-out edge: all entities requiring it become reachable
                    for entity in graph.entities:
                        if any(req.edge_id == edge.id for req in entity.requires) and entity.id not in reachable:
                            frontier.append(entity.id)

    return reachable


def dependency_map(graph: "EntityGraph") -> dict[str, set[str]]:
    """Return {entity_id: set of entity_ids it depends on via non-operator edges}."""
    deps: dict[str, set[str]] = {e.id: set() for e in graph.entities}

    for edge in graph.edges:
        if edge.from_entity != "operator" and edge.to_entity:
            deps.setdefault(edge.to_entity, set()).add(edge.from_entity)

    return deps
