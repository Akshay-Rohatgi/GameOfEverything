"""Construction crew orchestrator — Architect → Developer → Attacker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.models.entity import Entity
    from goe.models.artifacts import BuildArtifact
    from goe.models.procedure import Procedure
    from goe.construction_crew.architect import ArchitectPlan


@dataclass
class CrewResult:
    artifact: "BuildArtifact"
    procedure: "Procedure"
    outgoing_values: dict[str, dict[str, str]]  # edge_id → {param: concrete value}
    plan: "ArchitectPlan"


def build(
    entity: "Entity",
    incoming_edges: dict,
    edge_schemas: dict | None = None,
    system_context: str | None = None,
    provided_values: dict | None = None,
) -> CrewResult:
    """Run the full construction crew for a single entity.

    Sequence: Architect (plan) → Developer (code) → Attacker (procedure).
    No retry logic here — callers (retry router) handle escalation.

    Args:
        entity: The entity to build.
        incoming_edges: Concrete values for incoming edges {edge_id: {param: value}}.
        edge_schemas: Declared param schema for the entity's provided/required edges.
        system_context: Rendered system + chain context injected into agent prompts.
        provided_values: Already-determined values (resolved hosts, materialized secrets) for
            edges this entity provides, to be embedded verbatim by the developer.

    Returns:
        CrewResult with artifact, procedure, outgoing edge values, and architect plan.
    """
    from goe.construction_crew import architect, developer, attacker

    architect_plan = architect.plan(entity, incoming_edges, system_context=system_context)
    artifact, outgoing_values = developer.develop(
        entity, architect_plan, incoming_edges, edge_schemas=edge_schemas,
        system_context=system_context, provided_values=provided_values,
    )
    procedure = attacker.attack(entity, architect_plan, artifact, outgoing_values)

    return CrewResult(
        artifact=artifact,
        procedure=procedure,
        outgoing_values=outgoing_values,
        plan=architect_plan,
    )
