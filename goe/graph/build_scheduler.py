"""Build scheduler with state machine and value propagation."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from goe.graph.topology import dependency_map, topological_sort

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph
    from goe.models.entity import Entity


class EntityState(str, Enum):
    pending = "pending"
    buildable = "buildable"
    building = "building"
    complete = "complete"
    failed = "failed"
    skipped = "skipped"


class BuildScheduler:
    """Topological build scheduler with value propagation between entities.

    Entities move through states: pending → buildable → building → complete|failed.
    Entities that transitively depend on a failed entity are marked skipped.

    next_buildable() returns (entity, incoming_edges_dict) where incoming_edges_dict
    is the {edge_id: {param: concrete_value}} dict expected by
    construction_crew.orchestrator.build().
    """

    def __init__(self, graph: "EntityGraph"):
        self._graph = graph
        self._order = topological_sort(graph)
        self._states: dict[str, EntityState] = {eid: EntityState.pending for eid in self._order}
        # edge_id → {param_name: concrete value string}
        self._concrete_values: dict[str, dict[str, str]] = {}
        self._refresh_buildable()

    def next_buildable(self) -> tuple["Entity", dict[str, dict[str, str]]] | None:
        """Return next entity ready to build with its resolved incoming edge values, or None."""
        for eid in self._order:
            if self._states[eid] == EntityState.buildable:
                self._states[eid] = EntityState.building
                entity = self._graph.entity_by_id(eid)
                incoming = self._gather_incoming(eid)
                return entity, incoming
        return None

    def report_complete(self, entity_id: str, outgoing_values: dict[str, dict[str, str]]) -> None:
        """Mark entity complete and propagate concrete param values to outgoing edges."""
        self._states[entity_id] = EntityState.complete
        self._concrete_values.update(outgoing_values)
        self._refresh_buildable()

    def report_failed(self, entity_id: str) -> list[str]:
        """Mark entity failed. Returns list of entity IDs transitively skipped."""
        self._states[entity_id] = EntityState.failed
        skipped = self._compute_downstream(entity_id)
        for eid in skipped:
            self._states[eid] = EntityState.skipped
        return skipped

    def is_complete(self) -> bool:
        """True when all entities have a terminal state."""
        terminal = {EntityState.complete, EntityState.failed, EntityState.skipped}
        return all(s in terminal for s in self._states.values())

    def states(self) -> dict[str, EntityState]:
        return dict(self._states)

    def _refresh_buildable(self) -> None:
        deps = dependency_map(self._graph)
        for eid in self._order:
            if self._states[eid] != EntityState.pending:
                continue
            providers = deps.get(eid, set())
            if all(self._states.get(p) == EntityState.complete for p in providers):
                self._states[eid] = EntityState.buildable

    def _gather_incoming(self, entity_id: str) -> dict[str, dict[str, str]]:
        entity = self._graph.entity_by_id(entity_id)
        incoming: dict[str, dict[str, str]] = {}
        for req in entity.requires:
            merged: dict[str, str] = {}
            # 1. Concrete params already on the graph edge: host/port resolved by resolve.py
            #    and secrets filled by graph.secrets — these may not be in a producer's
            #    emitted outgoing_values, so read them straight off the edge.
            edge = self._graph.edge_by_id(req.edge_id)
            if edge is not None:
                merged = {p: pv.concrete for p, pv in edge.params.items() if pv.concrete is not None}
            # 2. Producer-emitted values augment/override (e.g. the username it chose).
            if req.edge_id in self._concrete_values:
                merged.update(self._concrete_values[req.edge_id])
            if merged:
                incoming[req.edge_id] = merged
        return incoming

    def _compute_downstream(self, failed_entity_id: str) -> list[str]:
        """BFS: collect all entities transitively depending on failed_entity_id."""
        deps = dependency_map(self._graph)
        affected: set[str] = set()
        frontier = [failed_entity_id]

        while frontier:
            current = frontier.pop(0)
            for eid, providers in deps.items():
                if current in providers and eid not in affected and eid != failed_entity_id:
                    state = self._states.get(eid)
                    if state in (EntityState.pending, EntityState.buildable):
                        affected.add(eid)
                        frontier.append(eid)

        return list(affected)
