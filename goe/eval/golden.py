"""Golden test case management and comparison."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph

GOLDEN_DIR = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "golden_plans"


class ExpectedEntity(BaseModel):
    """Expected entity in golden plan — matched by atom set, not exact ID."""

    model_config = ConfigDict(extra="allow")

    description: str
    runtime: str
    atoms: list[str] = []
    system_id: str | None = None


class ExpectedEdge(BaseModel):
    """Expected edge pattern in golden plan."""

    model_config = ConfigDict(extra="allow")

    source_provides: str  # edge type that source should provide
    target_requires: str  # edge type that target should require


class GoldenPlan(BaseModel):
    """Golden test case for planning evaluation."""

    model_config = ConfigDict(extra="allow")

    request: str
    expected_systems: list[str]  # system IDs or roles
    expected_entities: list[ExpectedEntity]
    expected_edges: list[ExpectedEdge] = []


class PlanAdherenceScore(BaseModel):
    """Result of comparing an actual plan against a golden baseline."""

    entity_coverage: float  # 0-1, fraction of expected entities present
    edge_coverage: float  # 0-1, fraction of expected edges present
    structural_match: bool  # topology is isomorphic
    missing_entities: list[str] = []  # descriptions of missing entities
    missing_edges: list[str] = []  # descriptions of missing edges
    extra_entities: int = 0  # entities not in golden
    extra_edges: int = 0  # edges not in golden


def load_golden(name: str) -> GoldenPlan:
    """Load a golden plan by name from tests/fixtures/golden_plans/."""
    path = GOLDEN_DIR / f"{name}.yaml"
    with open(path) as f:
        return GoldenPlan.model_validate(yaml.safe_load(f))


def compare_plan(actual: "EntityGraph", golden: GoldenPlan) -> PlanAdherenceScore:
    """Compare an actual EntityGraph against a golden baseline.

    Entities are matched by atom set (order-independent) — IDs may differ.
    Edges are matched by provides/requires types.
    """
    from goe.graph.models import EntityGraph

    # Match entities by atom set
    matched_entities = 0
    missing_entities = []

    for expected in golden.expected_entities:
        expected_atom_set = set(expected.atoms)
        found = any(
            set(e.atoms or []) == expected_atom_set for e in actual.entities
        )
        if found:
            matched_entities += 1
        else:
            missing_entities.append(
                f"{expected.runtime} with atoms {expected.atoms}"
            )

    entity_coverage = (
        matched_entities / len(golden.expected_entities)
        if golden.expected_entities
        else 1.0
    )
    extra_entities = len(actual.entities) - len(golden.expected_entities)

    # Match edges by provides/requires types
    matched_edges = 0
    missing_edges = []

    for expected_edge in golden.expected_edges:
        # An expected edge is satisfied only if a *single* real edge actually
        # connects a source that provides source_provides to a target that
        # requires target_requires. Checking provides/requires independently is
        # wrong: it would match an unrelated provider + an unrelated consumer
        # with no edge linking them.
        matched = False
        for edge in actual.edges:
            source = actual.entity_by_id(edge.from_entity)
            target = actual.entity_by_id(edge.to_entity) if edge.to_entity else None
            if source is None or target is None:
                continue
            source_provides = expected_edge.source_provides in (source.provides or [])
            target_requires = any(
                req.edge_id == expected_edge.target_requires
                for req in (target.requires or [])
            )
            if source_provides and target_requires:
                matched = True
                break
        if matched:
            matched_edges += 1
        else:
            missing_edges.append(
                f"{expected_edge.source_provides} -> {expected_edge.target_requires}"
            )

    edge_coverage = (
        matched_edges / len(golden.expected_edges)
        if golden.expected_edges
        else 1.0
    )
    extra_edges = len(actual.edges) - len(golden.expected_edges)

    # Structural match: all expected entities/edges present, no extras
    structural_match = (
        entity_coverage == 1.0
        and edge_coverage == 1.0
        and extra_entities == 0
        and extra_edges == 0
    )

    return PlanAdherenceScore(
        entity_coverage=entity_coverage,
        edge_coverage=edge_coverage,
        structural_match=structural_match,
        missing_entities=missing_entities,
        missing_edges=missing_edges,
        extra_entities=max(0, extra_entities),
        extra_edges=max(0, extra_edges),
    )
