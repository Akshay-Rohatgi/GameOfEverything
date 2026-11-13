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


# Maps a service recipe -> the config_var that receives an atom-pinned password.
# Only recipes whose weak-credential vuln is a password config_var appear here;
# trust/no-auth services (postgres, mongodb) are exposure-only and need no credential.
_RECIPE_PASSWORD_VAR: dict[str, str] = {
    "mysql": "root_password",   # applies to the root account
    "redis": "requirepass",
}


def _service_config_from_entity(svc_recipe_id: str, entity_config) -> dict:
    """Derive recipe config_vars from an entity's planner-pinned atom config.

    So the deterministic service layer configures the service FULLY at provision
    time (before any entity builds) — e.g. a weak root password that creates the
    remotely-reachable ``root@'%'`` account — instead of leaving it to an entity
    deploy script that runs after a cross-host consumer (a web tier) has already
    tried to connect. Returns {} when there is nothing to pin.
    """
    cfg: dict = {}
    if not entity_config:
        return cfg
    password = entity_config.get("password")
    username = (entity_config.get("username") or "").strip().lower()
    var = _RECIPE_PASSWORD_VAR.get(svc_recipe_id)
    if var and password:
        # mysql's config_var targets the root account specifically; only pin it when
        # the atom's account is root (or unspecified). A non-root pinned user is left
        # to the entity script, which the recipe doesn't model as a config_var.
        if svc_recipe_id == "mysql" and username not in ("", "root"):
            return cfg
        cfg[var] = password
    return cfg


def _attach_required_services(entities, systems):
    """Union each entity's atoms' declared required services into its System, and
    pin the atom's credential config into the recipe so provision() fully configures it.

    The atom->service bridge. Atoms declare ``requires_service`` in their
    frontmatter (see ``construction_crew.atoms.required_services``); here we ensure
    those recipes are present on the entity's system so the deterministic,
    distro-aware service layer installs the package — instead of the atom
    hardcoding ``apt-get`` in its prose. We also carry the entity's planner-pinned
    credential (e.g. a weak password) into ``ServiceSpec.config`` so the recipe sets
    it up at provision time, before any cross-host consumer builds. Idempotent: a
    service already declared on a system is merged (config filled in), not duplicated.
    Returns a new systems list; inputs are unchanged.
    """
    from goe.construction_crew.atoms import resolve_required_services
    from goe.models.system import ServiceSpec

    sys_ids = {s.id for s in systems}
    # {system_id: {service_id: merged_config_dict}}
    needed: dict[str, dict[str, dict]] = {}
    for e in entities:
        if e.system_id not in sys_ids:
            continue
        e_cfg = getattr(e, "config", None)
        for atom_id in (e.atoms or []):
            # Resolve both static (requires_service) and dynamic
            # (requires_service_by, keyed off the entity's planner-pinned config)
            # service needs, so parameterized atoms install the recipe matching the
            # service the planner actually chose.
            for svc in resolve_required_services(atom_id, e_cfg):
                bucket = needed.setdefault(e.system_id, {})
                svc_cfg = bucket.setdefault(svc, {})
                svc_cfg.update(_service_config_from_entity(svc, e_cfg))

    if not needed:
        return systems

    updated = []
    for s in systems:
        want = needed.get(s.id, {})
        if not want:
            updated.append(s)
            continue
        have = {spec.id: spec for spec in s.services}
        new_specs = []
        changed = False
        # Merge pinned config into existing specs.
        for spec in s.services:
            extra_cfg = want.get(spec.id)
            if extra_cfg and any(spec.config.get(k) != v for k, v in extra_cfg.items()):
                merged = {**(spec.config or {}), **extra_cfg}
                new_specs.append(spec.model_copy(update={"config": merged}))
                changed = True
            else:
                new_specs.append(spec)
        # Add services the system doesn't have yet, with their pinned config.
        for svc, cfg in want.items():
            if svc not in have:
                new_specs.append(ServiceSpec(id=svc, config=cfg or {}))
                changed = True
        updated.append(s.model_copy(update={"services": new_specs}) if changed else s)
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
