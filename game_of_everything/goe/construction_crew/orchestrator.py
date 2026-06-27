"""Construction crew orchestrator — Engineer → Developer → Attacker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.models.entity import Entity
    from goe.models.artifacts import BuildArtifact
    from goe.models.procedure import Procedure
    from goe.construction_crew.engineer import EngineerPlan


@dataclass
class CrewResult:
    artifact: "BuildArtifact"
    procedure: "Procedure"
    outgoing_values: dict[str, dict[str, str]]  # edge_id → {param: concrete value}
    plan: "EngineerPlan"


def build(
    entity: "Entity",
    incoming_edges: dict,
    ctx: dict | None = None,
    edge_schemas: dict | None = None,
) -> CrewResult:
    """Run the full construction crew for a single entity.

    Sequence: Engineer (plan) → Developer (code) → Attacker (procedure).
    No retry logic here — callers (retry router) handle escalation.

    Args:
        entity: The entity to build.
        incoming_edges: Concrete values for incoming edges {edge_id: {param: value}}.
        ctx: Optional extra context (unused currently, reserved for future).

    Returns:
        CrewResult with artifact, procedure, outgoing edge values, and engineer plan.
    """
    from goe.construction_crew import engineer, developer, attacker

    eng_plan = engineer.plan(entity, incoming_edges)
    artifact, outgoing_values = developer.develop(
        entity, eng_plan, incoming_edges, edge_schemas=edge_schemas
    )
    procedure = attacker.attack(entity, eng_plan, artifact, outgoing_values)

    return CrewResult(
        artifact=artifact,
        procedure=procedure,
        outgoing_values=outgoing_values,
        plan=eng_plan,
    )
