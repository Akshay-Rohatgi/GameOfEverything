"""Single-entity build pipeline — construction crew → deploy → L2 test → retry."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.models.entity import Entity
    from goe.models.report import EntityResult


def build_entity(
    entity: "Entity",
    incoming_edges: dict | None = None,
    scope: str = "",
    verbose: bool = True,
) -> "EntityResult":
    """Run the full build pipeline for a single entity.

    Steps:
      1. Construction crew: Engineer → Developer → Attacker
      2. Runtime template → deploy script
      3. Spin up TestEnvironment, deploy app
      4. Run L2 procedure executor
      5. On failure: diagnose → retry (escalation ladder)
      6. Teardown environment

    Args:
        entity: The entity spec to build.
        incoming_edges: Concrete values for incoming edges.
        scope: Docker container name prefix (avoid collisions during parallel builds).
        verbose: Print progress to stdout.

    Returns:
        EntityResult with PASSED/FAILED status and details.
    """
    from goe.construction_crew.orchestrator import build as crew_build, CrewResult
    from goe.container.environment import TestEnvironment
    from goe.executor.runner import run as run_procedure
    from goe.models.report import EntityResult, EntityStatus
    from goe.retry.diagnostician import diagnose
    from goe.retry.router import retry as retry_crew
    from goe.runtimes.registry import get_registry

    incoming_edges = incoming_edges or {}
    runtime = entity.app_spec.runtime if entity.app_spec else "ubuntu"
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
    section("PHASE 1: Construction Crew")
    log("Running engineer...")
    t0 = time.time()
    crew: CrewResult = crew_build(entity, incoming_edges)
    log(f"Crew finished in {time.time() - t0:.1f}s")

    section("Engineer Plan")
    log(f"Runtime:    {crew.plan.runtime}")
    log(f"Summary:    {crew.plan.summary}")
    log(f"Entry point: {crew.plan.attack_entry_point}")
    log(f"Success indicator: {crew.plan.success_indicator}")
    log(f"Vulnerability: {crew.plan.vulnerability_placement}")

    section("Generated Source Files")
    for fname, content in crew.artifact.source_files.items():
        dump(fname, content)

    if crew.artifact.db_setup:
        dump("schema.sql", crew.artifact.db_setup.schema_sql)
        dump("seed.sql", crew.artifact.db_setup.seed_sql)

    section("Generated Attack Procedure")
    import yaml as _yaml
    dump("procedure.yaml", _yaml.dump(crew.procedure.model_dump(), default_flow_style=False))

    env = TestEnvironment(runtime=runtime, scope=scope or f"build_{entity.id[:16]}")
    env.setup()

    try:
        # Phase 2 — Deploy
        deploy_script = registry.deploy(runtime, crew.artifact)
        section("PHASE 2: Deploy")
        dump("deploy_script.sh", deploy_script)
        log("Deploying app...")
        exit_code, stdout, stderr = env.deploy(deploy_script)
        log(f"Deploy exit code: {exit_code}")
        if stdout.strip():
            dump("deploy stdout", stdout)
        if stderr.strip() and exit_code != 0:
            dump("deploy stderr", stderr)
        if exit_code != 0:
            log(f"Deploy script exited {exit_code} — treating as design_flaw")
            # Fall through to retry with design_flaw

        # Phase 3 — L2 test
        port = registry.port_for(runtime)
        ctx = {
            "target_host": env.get_target_host(),
            "attacker_host": env.get_attacker_host(),
            "target_port": str(port),
            "edges": incoming_edges,
        }

        section("PHASE 3: L2 Procedure Execution")
        attempt = 0
        result = run_procedure(crew.procedure, env, ctx)
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

        while not result.passed:
            attempt += 1
            log(f"Diagnosing failure (attempt {attempt})...")
            diagnosis = diagnose(entity, crew.artifact, result, env)
            log(f"Diagnosis: {diagnosis.category} — {diagnosis.description}")

            new_crew = retry_crew(entity, incoming_edges, crew, diagnosis, attempt)
            if new_crew is None:
                log("Max retries exceeded.")
                return EntityResult(
                    id=entity.id,
                    status=EntityStatus.FAILED,
                    attempts=attempt + 1,
                    failure_reason=f"{diagnosis.category}: {diagnosis.description}",
                )

            crew = new_crew

            # Re-deploy if artifact changed (implementation_bug or design_flaw)
            from goe.retry.diagnostician import DiagnosisCategory
            if diagnosis.category != DiagnosisCategory.procedure_bug:
                log("Re-deploying with updated artifact...")
                new_deploy = registry.deploy(runtime, crew.artifact)
                env.deploy(new_deploy)

            result = run_procedure(crew.procedure, env, ctx)
            log(f"L2 attempt {attempt + 1}: {'PASSED' if result.passed else 'FAILED'}")

        return EntityResult(
            id=entity.id,
            status=EntityStatus.PASSED,
            attempts=attempt + 1,
        )

    finally:
        env.teardown()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse
    import yaml
    from goe.models.entity import Entity

    parser = argparse.ArgumentParser(description="Build a single GoE entity")
    parser.add_argument("--spec", required=True, help="Path to entity YAML spec")
    parser.add_argument("--scope", default="", help="Docker scope prefix")
    args = parser.parse_args()

    with open(args.spec) as f:
        entity = Entity.model_validate(yaml.safe_load(f))

    result = build_entity(entity, scope=args.scope, verbose=True)
    print(f"\nResult: {result.status} (attempts: {result.attempts})")
    if result.failure_reason:
        print(f"Failure: {result.failure_reason}")


if __name__ == "__main__":
    _main()
