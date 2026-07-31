"""Single-entity build pipeline — construction crew → deploy → L2 test → retry."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.models.entity import Entity
    from goe.models.report import BuildOutcome
    from goe.construction_crew.architect import ArchitectPlan


def _generate_file_tree(plan: "ArchitectPlan", runtime: str, app_dir: str | None = None) -> str:
    """Generate a tree representation of the planned file structure."""
    lines = []

    # Start with app directory
    if runtime == "ubuntu":
        lines.append("[dim]/opt/[/dim]")
        lines.append("└── setup.sh")
    else:
        display_dir = app_dir or "/opt/webapp"
        lines.append(f"[dim]{display_dir}/[/dim]")

        items = []

        # Main app file
        if runtime == "express":
            items.append("app.js")
        elif runtime == "flask":
            items.append("app.py")
        elif runtime in ("apache_php", "php"):
            items.append("index.php")

        # Endpoints as routes/pages
        if plan.endpoints:
            endpoint_files = set()
            for ep in plan.endpoints:
                # Extract file from path
                path = ep.path.lstrip('/')
                if '/' in path:
                    endpoint_files.add(path.split('/')[0])
                elif path:
                    endpoint_files.add(path)

            if len(endpoint_files) > 1:
                items.append("views/")

        # Database
        if plan.data_model.tables:
            if runtime in ("express", "flask"):
                items.append("app.db [dim](sqlite3)[/dim]")
            else:
                items.append("[dim](MySQL database)[/dim]")

        # Package files
        if runtime == "express":
            items.append("package.json")
        elif runtime == "flask":
            items.append("requirements.txt")

        # Build tree
        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            prefix = "└──" if is_last else "├──"
            lines.append(f"{prefix} {item}")

            # If it's the views directory, show nested files
            if item == "views/" and plan.endpoints:
                endpoint_files = set()
                for ep in plan.endpoints:
                    path = ep.path.lstrip('/')
                    if '/' in path:
                        endpoint_files.add(path.split('/')[0])
                    elif path:
                        endpoint_files.add(path)

                nested_prefix = "    " if is_last else "│   "
                sorted_files = sorted(list(endpoint_files)[:3])
                for j, f in enumerate(sorted_files):
                    ext = ".ejs" if runtime == "express" else ".html"
                    is_last_file = j == len(sorted_files) - 1 and len(endpoint_files) <= 3
                    file_prefix = "└──" if is_last_file else "├──"
                    lines.append(f"{nested_prefix}{file_prefix} {f}{ext}")

                if len(endpoint_files) > 3:
                    lines.append(f"{nested_prefix}└── [dim]... {len(endpoint_files) - 3} more[/dim]")

    return "\n".join(lines)


def build_entity(
    entity: "Entity",
    incoming_edges: dict | None = None,
    scope: str = "",
    verbose: bool = True,
    env=None,  # TestEnvironment | ProgressiveEnvironment | None
    edge_schemas: dict | None = None,
    system_context: str | None = None,
    provided_values: dict | None = None,
    console=None,  # RunConsole | None
) -> "BuildOutcome":
    """Run the full build pipeline for a single entity.

    Steps:
      1. Construction crew: Architect → Developer → Attacker
      2. Runtime template → deploy script
      3. Spin up TestEnvironment (or use provided env), deploy app
      4. Run L2 procedure executor
      5. On failure: diagnose → retry (escalation ladder)
      6. Teardown environment (if we own it)

    Args:
        entity: The entity spec to build.
        incoming_edges: Concrete values for incoming edges.
        scope: Docker container name prefix (avoid collisions during parallel builds).
        verbose: Print progress to stdout.
        env: Optional pre-created environment (ProgressiveEnvironment or TestEnvironment).
            If provided, build_entity will not call setup() or teardown().
        edge_schemas: Optional {edge_id: {"type", "direction", "params"}} for the entity's
            provided/required edges — constrains the param keys the developer may emit.
        system_context: Optional rendered markdown describing this entity's system, the
            platform-provided services, and sibling entities — injected into the architect and
            developer prompts so each entity builds only its own link.
        provided_values: Optional {edge_id: {param: concrete}} of already-determined values
            for edges this entity provides (resolved hosts, materialized secrets) that the
            developer must embed verbatim instead of regenerating.

    Returns:
        BuildOutcome wrapping the EntityResult plus the final deploy script,
        procedure, and outgoing edge values (the latter three empty on failure).
    """
    from goe.construction_crew.orchestrator import build as crew_build, CrewResult
    from goe.container.environment import TestEnvironment
    from goe.executor.runner import run as run_procedure
    from goe.models.report import BuildOutcome, EntityResult, EntityStatus
    from goe.retry.diagnostician import diagnose
    from goe.retry.router import retry as retry_crew
    from goe.runtimes.registry import get_registry

    incoming_edges = incoming_edges or {}
    runtime = entity.runtime.value
    registry = get_registry()

    def log(msg: str) -> None:
        if verbose:
            print(f"[build_entity:{entity.id}] {msg}")

    def section(title: str) -> None:
        if verbose:
            print(f"\n{'='*60}\n  {title}\n{'='*60}")

    def dump(label: str, content: str, max_lines: int = 60) -> None:
        if not verbose:
            return
        lines = content.splitlines()
        truncated = len(lines) > max_lines
        shown = "\n".join(lines[:max_lines])
        print(f"\n--- {label} ---\n{shown}")
        if truncated:
            print(f"... ({len(lines) - max_lines} more lines truncated)")

    # Phase 1 — Construction crew
    if console:
        console.crew_phase("Construction Crew")
        console.crew_agent_start("Architect", "designing attack plan")
    else:
        section("PHASE 1: Construction Crew")
        log("Running architect...")

    t0 = time.time()
    crew: CrewResult = crew_build(
        entity, incoming_edges, edge_schemas=edge_schemas,
        system_context=system_context, provided_values=provided_values,
    )
    duration = time.time() - t0

    if console:
        console.crew_agent_done("Architect + Developer + Attacker", duration)
        file_tree = _generate_file_tree(crew.plan, entity.runtime.value, getattr(crew.artifact, "app_dir", None))
        console.crew_plan_summary(
            crew.plan.runtime,
            crew.plan.summary,
            crew.plan.attack_entry_point,
            file_tree=file_tree
        )
    else:
        log(f"Crew finished in {duration:.1f}s")
        section("Architect Plan")
        log(f"Runtime:    {crew.plan.runtime}")
        log(f"Summary:    {crew.plan.summary}")
        log(f"Entry point: {crew.plan.attack_entry_point}")
        log(f"Success indicator: {crew.plan.success_indicator}")
        log(f"Vulnerability: {crew.plan.vulnerability_placement}")

    # Show source code snippets (first file only, truncated)
    if console and crew.artifact.source_files:
        first_file = list(crew.artifact.source_files.keys())[0]
        content = crew.artifact.source_files[first_file]
        lines = content.splitlines()[:15]  # First 15 lines
        console.crew_source_snippet(first_file, lines)
        if len(crew.artifact.source_files) > 1:
            console._line(f"  [dim]... and {len(crew.artifact.source_files) - 1} more file(s)[/dim]")
    elif not console:
        section("Generated Source Files")
        for fname, content in crew.artifact.source_files.items():
            dump(fname, content)
        if crew.artifact.db_setup:
            dump("schema.sql", crew.artifact.db_setup.schema_sql)
            dump("seed.sql", crew.artifact.db_setup.seed_sql)

    # Show procedure summary
    if console:
        import yaml as _yaml
        proc_dict = crew.procedure.model_dump()
        steps = proc_dict.get('procedure', [])
        console.crew_procedure_summary(steps)
    else:
        section("Generated Attack Procedure")
        import yaml as _yaml
        _proc_yaml = _yaml.dump(crew.procedure.model_dump(), default_flow_style=False)
        dump("procedure.yaml", _proc_yaml)

    # Persist to run-dir if artifact capture is active
    from goe.metrics import get_session
    _session = get_session()
    _art_run_dir = getattr(_session, "artifact_run_dir", None) if _session else None
    if _art_run_dir is not None:
        from goe.artifacts.writer import save_crew_artifacts
        save_crew_artifacts(entity.id, crew, _art_run_dir)
        log(f"Artifacts written to: {_art_run_dir}/entities/{entity.id}/")

    _owns_env = env is None
    if _owns_env:
        env = TestEnvironment(runtime=runtime, scope=scope or f"build_{entity.id[:16]}")
        env.setup()

    def _make_deploy_script(artifact) -> str:
        if runtime == "ubuntu":
            return artifact.source_files[artifact.primary_source]
        return registry.deploy(runtime, artifact)

    try:
        # Phase 2 — Deploy
        deploy_script = _make_deploy_script(crew.artifact)
        if console:
            console.crew_deploy_start()
        else:
            section("PHASE 2: Deploy")
            dump("deploy_script.sh", deploy_script)
            log("Deploying app...")

        t0_deploy = time.time()
        exit_code, stdout, stderr = env.deploy(deploy_script)
        deploy_duration = time.time() - t0_deploy

        if console:
            console.crew_deploy_result(exit_code == 0, deploy_duration)
        else:
            log(f"Deploy exit code: {exit_code}")
            if stdout.strip():
                dump("deploy stdout", stdout)
            if stderr.strip() and exit_code != 0:
                dump("deploy stderr", stderr)

        # Phase 3 — L2 test
        port = None if runtime == "ubuntu" else registry.port_for(runtime)
        ctx = {
            "target_host": env.get_target_host(),
            "attacker_host": env.get_attacker_host(),
            "target_port": str(port) if port else "",
            "edges": incoming_edges,
        }

        if console:
            console.crew_test_start(attempt=1)
        else:
            section("PHASE 3: L2 Procedure Execution")

        attempt = 0
        if exit_code != 0:
            # A broken/half-deployed container must NEVER yield a PASSED entity.
            # Skip run_procedure (its assertions could pass against stale state)
            # and route straight into the retry loop as a forced design_flaw.
            from goe.executor.runner import ProcedureResult
            log(f"Deploy script exited {exit_code} — forcing design_flaw retry")
            result = ProcedureResult(
                passed=False,
                error=f"deploy script exited {exit_code}: {stderr.strip() or '(no stderr)'}",
            )
            forced_deploy_failure = True
        else:
            result = run_procedure(crew.procedure, env, ctx)
            forced_deploy_failure = False

        if console:
            # Show test steps with console UI
            passed_steps = sum(1 for s in result.steps if s.passed)
            for step in result.steps:
                console.crew_test_step(step.step_id, step.passed, step.reason)
            console.crew_test_result(result.passed, len(result.steps), passed_steps)
        else:
            log(f"L2 attempt {attempt + 1}: {'PASSED' if result.passed else 'FAILED'}")
            for step in result.steps:
                status = "PASS" if step.passed else "FAIL"
                log(f"  [{status}] {step.step_id}: {step.reason}")
                if not step.passed:
                    if step.raw.stdout:
                        dump(f"stdout ({step.step_id})", step.raw.stdout, max_lines=20)
                    if step.raw.stderr:
                        dump(f"stderr ({step.step_id})", step.raw.stderr, max_lines=10)
                    if step.raw.body:
                        dump(f"body ({step.step_id})", step.raw.body, max_lines=20)
                    if step.raw.error:
                        log(f"  error: {step.raw.error}")

        from goe.retry.diagnostician import Diagnosis, DiagnosisCategory
        diagnosis_history = []  # Track diagnosis categories to detect stuck loops
        while not result.passed:
            attempt += 1
            if forced_deploy_failure:
                # Deterministic: a non-zero deploy exit is a design_flaw by
                # definition — don't ask the LLM to second-guess a broken deploy.
                diagnosis = Diagnosis(
                    category=DiagnosisCategory.design_flaw,
                    description="Deploy script exited non-zero — app failed to deploy.",
                    evidence=result.error or "",
                )
                forced_deploy_failure = False
            else:
                if console:
                    console._line(f"\n[yellow]⚠[/yellow] Diagnosing failure...")
                else:
                    log(f"Diagnosing failure (attempt {attempt})...")
                diagnosis = diagnose(entity, crew.artifact, result, env)

            if console:
                console.crew_retry(diagnosis.category.value, attempt, 3)
                console._line(f"  [dim]{diagnosis.description[:100]}{'...' if len(diagnosis.description) > 100 else ''}[/dim]")
            else:
                log(f"Diagnosis: {diagnosis.category} — {diagnosis.description}")

            # Detect stuck retry loops: if same category 2x in a row, escalate to design_flaw
            if (len(diagnosis_history) >= 1
                and diagnosis_history[-1] == diagnosis.category
                and diagnosis.category != DiagnosisCategory.design_flaw):
                if console:
                    console._line(f"  [yellow]⚠[/yellow] [dim]Stuck retry loop detected (same category 2x) — escalating to design_flaw[/dim]")
                else:
                    log(f"Stuck retry loop detected: {diagnosis.category} → {diagnosis.category}. Escalating to design_flaw.")
                diagnosis = Diagnosis(
                    category=DiagnosisCategory.design_flaw,
                    description=f"Escalated from {diagnosis_history[-1].value} after 2 consecutive failures in same category. Original issue: {diagnosis.description}",
                    evidence=diagnosis.evidence,
                )

            diagnosis_history.append(diagnosis.category)

            new_crew = retry_crew(
                entity, incoming_edges, crew, diagnosis, attempt, edge_schemas=edge_schemas,
                system_context=system_context, provided_values=provided_values,
            )
            if new_crew is None:
                log("Max retries exceeded.")
                return BuildOutcome(
                    result=EntityResult(
                        id=entity.id,
                        status=EntityStatus.FAILED,
                        attempts=attempt + 1,
                        failure_reason=f"{diagnosis.category}: {diagnosis.description}",
                        failure_category=diagnosis.category.value,
                    ),
                )

            crew = new_crew

            # Persist retry artifacts (updated app/procedure) and diff vs previous
            if _art_run_dir is not None:
                from goe.artifacts.writer import save_attempt_artifacts, write_attempt_diff
                save_attempt_artifacts(entity.id, attempt, crew, diagnosis, _art_run_dir)
                write_attempt_diff(entity.id, attempt, _art_run_dir)
                log(f"Attempt {attempt} artifacts written")

            # Always reset the attacker — clears detached background processes
            # (listeners, netcat, etc.) that survived from the previous attempt.
            log("Resetting attacker container...")
            env.reset_attacker()

            # Re-deploy if artifact changed (implementation_bug or design_flaw).
            # Also reset the target — old app processes, port bindings, and DB
            # files from the previous attempt would otherwise persist.
            if diagnosis.category != DiagnosisCategory.procedure_bug:
                log("Resetting target container...")
                env.reset_target()
                log("Re-deploying with updated artifact...")
                env.deploy(_make_deploy_script(new_crew.artifact))

            if console:
                console.crew_test_start(attempt=attempt + 1)
            result = run_procedure(crew.procedure, env, ctx)

            if console:
                passed_steps = sum(1 for s in result.steps if s.passed)
                for step in result.steps:
                    console.crew_test_step(step.step_id, step.passed, step.reason)
                console.crew_test_result(result.passed, len(result.steps), passed_steps)
            else:
                log(f"L2 attempt {attempt + 1}: {'PASSED' if result.passed else 'FAILED'}")

        return BuildOutcome(
            result=EntityResult(
                id=entity.id,
                status=EntityStatus.PASSED,
                attempts=attempt + 1,
            ),
            deploy_script=_make_deploy_script(crew.artifact),
            procedure=crew.procedure,
            outgoing_values=crew.outgoing_values,
        )

    finally:
        if _owns_env:
            env.teardown()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse
    import sys
    import yaml
    from goe.models.entity import Entity
    from goe.artifacts.run import artifact_run

    parser = argparse.ArgumentParser(description="Build a single GoE entity")
    parser.add_argument("--spec", required=True, help="Path to entity YAML spec")
    parser.add_argument("--scope", default="", help="Docker scope prefix")
    artifact_group = parser.add_mutually_exclusive_group()
    artifact_group.add_argument(
        "--artifacts",
        dest="artifacts",
        action="store_true",
        default=None,
        help="Save workflow artifacts (overrides goe.toml [artifacts].enabled)",
    )
    artifact_group.add_argument(
        "--no-artifacts",
        dest="artifacts",
        action="store_false",
        help="Disable artifact saving for this run",
    )
    args = parser.parse_args()

    with open(args.spec) as f:
        entity = Entity.model_validate(yaml.safe_load(f))

    command = " ".join(sys.argv)
    with artifact_run("build", command, capture=args.artifacts):
        outcome = build_entity(entity, scope=args.scope, verbose=True)

    result = outcome.result
    print(f"\nResult: {result.status} (attempts: {result.attempts})")
    if result.failure_reason:
        print(f"Failure: {result.failure_reason}")


if __name__ == "__main__":
    _main()
