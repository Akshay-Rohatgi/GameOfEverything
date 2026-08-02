"""Planning pipeline — orchestrates Steps 0-4 with validator retry loop."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from goe.graph.models import EntityGraph, ValidationResult, Violation
from goe.graph.validator import validate
from goe.planner.connect_edges import connect_edges
from goe.planner.design_systems import design_systems
from goe.planner.grade_stubs import grade_stubs
from goe.planner.plan_entities import plan_entities
from goe.planner.plan_killchain import plan_killchain
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


def plan(request: str, verbose: bool = False, console=None) -> PlanResult:
    """Full planning pipeline: user request → validated EntityGraph."""
    from goe.config import GoEConfig

    cfg = GoEConfig.get()
    model = cfg.model_for("planner")

    def _log(msg: str) -> None:
        if verbose:
            print(msg)

    # Step 0: Design systems
    if console:
        console.plan_step("Designing systems", "analyzing infrastructure needs")
    _log(f"[planner] Step 0: designing systems for request...")
    systems = design_systems(request, model=model)
    _log(f"[planner] designed {len(systems)} system(s)")
    if console:
        console.plan_result(f"Designed {len(systems)} system(s)")

    # Step 0.5: Plan killchain
    if console:
        console.plan_step("Planning killchain", "sequencing attack stages")
    _log(f"[planner] Step 0.5: planning killchain...")
    killchain = plan_killchain(request, systems, model=model)
    _log(f"[planner] killchain:\n{killchain}")

    # Display killchain in UI
    if console:
        killchain_steps = [line.strip() for line in killchain.strip().split('\n') if line.strip() and not line.strip().startswith('#')]
        console.plan_killchain(killchain_steps)

    total_attempts = 0

    for full_attempt in range(_MAX_FULL_REPLANS + 1):
        # Step 1: Plan entities
        if console:
            console.plan_step("Planning entities", "selecting vulnerabilities and atoms")
        _log(f"[planner] Step 1: planning entities (full_attempt={full_attempt})...")
        stubs = plan_entities(request, systems, killchain=killchain, model=model)
        _log(f"[planner] planned {len(stubs)} entity stub(s)")
        if console:
            entity_ids = [stub.id for stub in stubs]
            console.plan_entities_summary(entity_ids)

        # Step 1.5: Grade stubs
        if console:
            console.plan_step("Grading stubs", "validating runtime and atom compatibility")
        _log(f"[planner] Step 1.5: grading stubs...")
        stubs = grade_stubs(stubs, systems, model=model)
        _log(f"[planner] graded {len(stubs)} stub(s)")
        if console:
            grades = {stub.id: "pass" for stub in stubs}
            console.plan_grades(grades)

        # Step 2: Specify entities
        if console:
            console.plan_step("Specifying entities", "adding detailed configurations")
        _log(f"[planner] Step 2: specifying entities...")
        entities = specify_entities(stubs, systems, request, model=model)
        _log(f"[planner] specified {len(entities)} entities")
        if console:
            console.plan_result(f"Specified {len(entities)} entities with full details")

        prior_violations: list[Violation] | None = None

        for edge_attempt in range(_MAX_EDGE_RETRIES + 1):
            total_attempts += 1

            # Step 3: Connect edges
            if console:
                retry_msg = f" (attempt {edge_attempt + 1})" if edge_attempt > 0 else ""
                console.plan_step("Connecting edges", f"linking entity dependencies{retry_msg}")
            _log(f"[planner] Step 3: connecting edges (edge_attempt={edge_attempt})...")
            edges = connect_edges(entities, systems, model=model, violations=prior_violations)
            _log(f"[planner] created {len(edges)} edge(s)")
            if console:
                console.plan_result(f"Created {len(edges)} edge(s)")

            # Step 4: Resolve structural params
            if console:
                console.plan_step("Resolving params", "determining network details")
            _log("[planner] Step 4: resolving structural params...")
            graph = EntityGraph(systems=systems, entities=entities, edges=edges)
            graph = resolve(graph)
            if console:
                console.plan_result("Resolved all structural parameters")

            # Step 5: Validate graph
            if console:
                console.plan_step("Validating graph", "checking completeness and consistency")
            _log("[planner] Step 5: validating graph...")
            result = validate(graph)

            if result.valid:
                _log(f"[planner] graph validated successfully after {total_attempts} attempt(s)")
                if console:
                    console.plan_result(f"Graph validated successfully after {total_attempts} attempt(s)", color="bold green")
                return PlanResult(graph=graph, success=True, attempts=total_attempts)

            _log(f"[planner] validation failed with {len(result.violations)} violation(s):")
            for v in result.violations:
                _log(f"  [{v.check}] {v.message}")
            if console and verbose:
                console._line(f"  [yellow]⚠[/yellow] Validation failed, retrying...")

            prior_violations = result.violations

    # All retries exhausted
    return PlanResult(
        graph=None,
        success=False,
        attempts=total_attempts,
        final_violations=result.violations,  # type: ignore[possibly-undefined]
    )
