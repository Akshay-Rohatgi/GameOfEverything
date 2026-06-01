"""Retry router — escalation ladder based on diagnosis category."""

from __future__ import annotations

from typing import TYPE_CHECKING

from goe.retry.diagnostician import DiagnosisCategory

if TYPE_CHECKING:
    from goe.models.entity import Entity
    from goe.models.report import EntityResult
    from goe.construction_crew.orchestrator import CrewResult
    from goe.retry.diagnostician import Diagnosis
    from goe.container.environment import TestEnvironment

# Max attempts per escalation level
_MAX_PROCEDURE_RETRIES = 2
_MAX_IMPLEMENTATION_RETRIES = 2
_MAX_DESIGN_RETRIES = 1


def retry(
    entity: "Entity",
    incoming_edges: dict,
    crew_result: "CrewResult",
    diagnosis: "Diagnosis",
    attempt: int,
) -> "CrewResult | None":
    """Return a new CrewResult after re-running the appropriate crew members.

    Returns None if the max attempts for this category have been exceeded.

    Args:
        entity: The entity being built.
        incoming_edges: Concrete incoming edge values.
        crew_result: The last CrewResult (used as context for targeted re-runs).
        diagnosis: The diagnosis from the diagnostician.
        attempt: Current attempt number (1-based).

    Returns:
        New CrewResult, or None if max attempts exceeded.
    """
    from goe.construction_crew import attacker, developer
    from goe.construction_crew.orchestrator import CrewResult, build

    cat = diagnosis.category

    if cat == DiagnosisCategory.procedure_bug:
        if attempt > _MAX_PROCEDURE_RETRIES:
            return None
        # Re-run attacker only — app code is fine
        new_procedure = attacker.attack(
            entity, crew_result.plan, crew_result.artifact, crew_result.outgoing_values
        )
        return CrewResult(
            artifact=crew_result.artifact,
            procedure=new_procedure,
            outgoing_values=crew_result.outgoing_values,
            plan=crew_result.plan,
        )

    elif cat == DiagnosisCategory.implementation_bug:
        if attempt > _MAX_IMPLEMENTATION_RETRIES:
            return None
        # Re-run developer + attacker — engineer plan is fine
        new_artifact, new_outgoing = developer.develop(entity, crew_result.plan, incoming_edges)
        new_procedure = attacker.attack(entity, crew_result.plan, new_artifact, new_outgoing)
        return CrewResult(
            artifact=new_artifact,
            procedure=new_procedure,
            outgoing_values=new_outgoing,
            plan=crew_result.plan,
        )

    else:  # design_flaw — full crew re-run
        if attempt > _MAX_DESIGN_RETRIES:
            return None
        return build(entity, incoming_edges)
