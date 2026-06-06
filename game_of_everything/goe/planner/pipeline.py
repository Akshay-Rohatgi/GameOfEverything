"""Planning pipeline — orchestrates Steps 0-4 with validator retry loop."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from goe.graph.models import EntityGraph, ValidationResult, Violation
from goe.graph.validator import validate
from goe.planner.connect_edges import connect_edges
from goe.planner.design_systems import design_systems
from goe.planner.plan_entities import plan_entities
from goe.planner.resolve import resolve
from goe.planner.specify_entities import specify_entities

_MAX_EDGE_RETRIES = 2   # max retries at connect_edges step
_MAX_FULL_REPLANS = 1   # max full re-plans (back to plan_entities)


class PlanResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    graph: EntityGraph | None = None
    success: bool
    attempts: int
    final_violations: list[Violation] = []


def plan(request: str, verbose: bool = False) -> PlanResult:
    """Full planning pipeline: user request → validated EntityGraph."""
    from goe.config import GoEConfig

    cfg = GoEConfig.get()
    model = cfg.model_for("planner")

    def _log(msg: str) -> None:
        if verbose:
            print(msg)

    _log(f"[planner] Step 0: designing systems for request...")
    systems = design_systems(request, model=model)
    _log(f"[planner] designed {len(systems)} system(s)")

    total_attempts = 0

    for full_attempt in range(_MAX_FULL_REPLANS + 1):
        _log(f"[planner] Step 1: planning entities (full_attempt={full_attempt})...")
        stubs = plan_entities(request, systems, model=model)
        _log(f"[planner] planned {len(stubs)} entity stub(s)")

        _log(f"[planner] Step 2: specifying entities (parallel)...")
        entities = specify_entities(stubs, systems, request, model=model)
        _log(f"[planner] specified {len(entities)} entities")

        prior_violations: list[Violation] | None = None

        for edge_attempt in range(_MAX_EDGE_RETRIES + 1):
            total_attempts += 1
            _log(f"[planner] Step 3: connecting edges (edge_attempt={edge_attempt})...")
            edges = connect_edges(entities, systems, model=model, violations=prior_violations)
            _log(f"[planner] created {len(edges)} edge(s)")

            _log("[planner] Step 4: resolving structural params...")
            graph = EntityGraph(systems=systems, entities=entities, edges=edges)
            graph = resolve(graph)

            _log("[planner] Step 5: validating graph...")
            result = validate(graph)

            if result.valid:
                _log(f"[planner] graph validated successfully after {total_attempts} attempt(s)")
                return PlanResult(graph=graph, success=True, attempts=total_attempts)

            _log(f"[planner] validation failed with {len(result.violations)} violation(s):")
            for v in result.violations:
                _log(f"  [{v.check}] {v.message}")

            prior_violations = result.violations

    # All retries exhausted
    return PlanResult(
        graph=None,
        success=False,
        attempts=total_attempts,
        final_violations=result.violations,  # type: ignore[possibly-undefined]
    )
