"""Planning pipeline — orchestrates Steps 0-4 with validator retry loop."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from goe.graph.models import EntityGraph, ValidationResult, Violation
from goe.graph.validator import validate
from goe.planner.assign_os import assign_os
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


def _attach_required_services(entities, systems):
    """Union each entity's atoms' declared required services into its System.

    The atom->service bridge. Atoms declare ``requires_service`` in their
    frontmatter (see ``construction_crew.atoms.required_services``); here we ensure
    those recipes are present on the entity's system so the deterministic,
    distro-aware service layer installs the package — instead of the atom
    hardcoding ``apt-get`` in its prose. Idempotent: a service already declared on
    a system is never duplicated. Returns a new systems list; inputs are unchanged.
    """
    from goe.construction_crew.atoms import resolve_required_services
    from goe.models.system import ServiceSpec

    sys_ids = {s.id for s in systems}
    needed: dict[str, list[str]] = {}
    for e in entities:
        if e.system_id not in sys_ids:
            continue
        for atom_id in (e.atoms or []):
            # Resolve both static (requires_service) and dynamic
            # (requires_service_by, keyed off the entity's planner-pinned config)
            # service needs, so parameterized atoms install the recipe matching the
            # service the planner actually chose.
            for svc in resolve_required_services(atom_id, getattr(e, "config", None)):
                bucket = needed.setdefault(e.system_id, [])
                if svc not in bucket:
                    bucket.append(svc)

    if not needed:
        return systems

    updated = []
    for s in systems:
        extra = needed.get(s.id, [])
        have = {spec.id for spec in s.services}
        new_specs = list(s.services) + [
            ServiceSpec(id=svc) for svc in extra if svc not in have
        ]
        updated.append(
            s if len(new_specs) == len(s.services)
            else s.model_copy(update={"services": new_specs})
        )
    return updated


def plan(request: str, verbose: bool = False, console=None, os_override: str | None = None) -> PlanResult:
    """Full planning pipeline: user request → validated EntityGraph.

    os_override: if set, force every designed system's OS to this distro instead
        of whatever design_systems inferred from the request. This makes the OS a
        deterministic build input (user/agent-chosen) rather than LLM inference.
    """
    from goe.config import GoEConfig

    cfg = GoEConfig.get()
    model = cfg.model_for("planner")

    def _log(msg: str) -> None:
        if verbose:
            print(msg)

    # Normalize the override up front so an invalid --os fails fast with a clear error.
    if os_override is not None:
        from goe.distros import normalize_os

        os_override = normalize_os(os_override)

    # Step 0: Design systems
    if console:
        console.plan_step("Designing systems", "analyzing infrastructure needs")
    _log(f"[planner] Step 0: designing systems for request...")
    systems = design_systems(request, model=model)

    # Authoritative OS selection: override the LLM's inferred os with the
    # explicit choice so System.os — not the prompt text — drives the build.
    if os_override is not None:
        systems = [s.model_copy(update={"os": os_override}) for s in systems]
        _log(f"[planner] OS override applied: all systems -> {os_override!r}")

    _log(f"[planner] designed {len(systems)} system(s)")
    if console:
        console.plan_result(f"Designed {len(systems)} system(s)")

    # Step 0.25: Assign a (possibly diverse) OS per host for multi-host setups.
    # Skipped when --os forces one distro everywhere (explicit choice wins) and a
    # no-op for single-host graphs. Runs before killchain/entity/edge steps so they
    # all see the final per-host OS. Haiku-tier via the dedicated os_planner role.
    if os_override is None and len(systems) > 1:
        if console:
            console.plan_step("Assigning OSes", "choosing a distro per host")
        _log("[planner] Step 0.25: assigning OSes across hosts...")
        systems = assign_os(request, systems, model=cfg.model_for("os_planner"))
        os_summary = ", ".join(f"{s.id}={s.os}" for s in systems)
        _log(f"[planner] OS assignment: {os_summary}")
        if console:
            console.plan_result(f"Assigned OSes: {os_summary}")

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

        # Step 2.5: Bridge atoms -> services. Union each entity's atoms' declared
        # required services into its system, so the deterministic, distro-aware
        # recipe layer installs them instead of the atom hardcoding apt-get in its
        # prose. Idempotent, so re-running on a full replan is safe.
        systems = _attach_required_services(entities, systems)

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
