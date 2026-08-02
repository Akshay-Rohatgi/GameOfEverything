"""L3 chain test — end-to-end multi-system attack validation.

Brings up a TopologyEnvironment (one shared network, one ubuntu container per
system, one shared Kali attacker), deploys all per-system build scripts, calls
the chain-attacker LLM to synthesise a single cross-system attack Procedure,
executes it, and retries up to MAX_RETRIES times on failure.

The chain test gates the overall run: FAILED chain test → RunResult.success=False.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph
    from goe.models.procedure import Procedure
    from goe.models.report import BuildOutcome, ChainTestResult

logger = logging.getLogger(__name__)

MAX_RETRIES = 4  # Increased from 2: multi-entity chains need more attempts


@dataclass
class ChainTestOutcome:
    """Wraps ChainTestResult plus the synthesised Procedure (for packaging)."""

    result: "ChainTestResult"
    procedure: "Procedure | None" = None


def _build_systems_ctx(graph: "EntityGraph") -> dict[str, dict[str, str]]:
    """Build the ``ctx["systems"]`` map for multi-target interpolation.

    Returns ``{system_id: {"host": hostname, "port": str(first_exposed_port | "")}}``
    """
    result: dict[str, dict[str, str]] = {}
    for s in graph.systems:
        port = str(s.network.exposed_ports[0]) if s.network.exposed_ports else ""
        result[s.id] = {"host": s.network.hostname, "port": port}
    return result


def _build_per_system_scripts(
    graph: "EntityGraph",
    built: dict[str, "BuildOutcome"],
) -> dict[str, str]:
    """Concatenate deploy scripts for built entities, grouped by system_id.

    Runs each system's sections through ``assemble_deploy_script`` (the same grader the
    packager uses) so the chain test deploys the exact conflict-resolved script that gets
    packaged — not an ungraded concatenation where duplicate users / password overwrites
    silently break the attack chain.

    Returns ``{system_id: combined_deploy_script}``.
    """
    from goe.packaging.grader import assemble_deploy_script, service_section

    grouped = graph.entities_by_system()
    per_system: dict[str, str] = {}
    for sid, entities in grouped.items():
        sections = [
            (entity.id, built[entity.id].deploy_script or "")
            for entity in entities
            if entity.id in built and (built[entity.id].deploy_script or "").strip()
        ]
        # Prepend the system's declared services (same helper the packager uses) so the
        # chain test deploys the exact script that gets packaged — services included.
        system = graph.system_by_id(sid)
        if system is not None:
            svc = service_section(system)
            if svc is not None:
                sections.insert(0, svc)
        if sections:
            combined, warnings = assemble_deploy_script(sections)
            if warnings:
                logger.warning(
                    f"ChainTest: grader fixed {len(warnings)} conflict(s) on system "
                    f"'{sid}': {warnings}"
                )
            per_system[sid] = combined
    return per_system


def _summarise_failure(result: "ProcedureResult", env=None, procedure=None) -> str:  # type: ignore[name-defined]
    """Turn a failed ProcedureResult into a diagnosis string for fix_chain.

    Args:
        result: The failed procedure execution result.
        env: Optional TopologyEnvironment for probing containers.
        procedure: Optional Procedure to extract system context from steps.

    Returns:
        Diagnosis string with context, failures, and optional probe evidence.
    """
    lines: list[str] = []

    if result.error:
        lines.append(f"Executor error: {result.error}")

    # Show last 3 successful steps for context
    successes = [s for s in result.steps if s.passed][-3:]
    if successes:
        lines.append("\n## Recent Successful Steps (context)")
        for s in successes:
            lines.append(f"  ✓ {s.step_id}")

    # Show ALL failures, not just first
    failures = [s for s in result.steps if not s.passed]
    if failures:
        lines.append("\n## Failed Steps")
        for s in failures:
            lines.append(f"  ✗ {s.step_id}: {s.reason}")
            if s.raw.stdout:
                lines.append(f"    stdout: {s.raw.stdout[:800]}")  # Increased from 400
            if s.raw.stderr:
                lines.append(f"    stderr: {s.raw.stderr[:400]}")  # Increased from 200
            if s.raw.error:
                lines.append(f"    error: {s.raw.error}")
            if s.raw.body:
                lines.append(f"    body: {s.raw.body[:400]}")

    # Gather evidence from containers if environment is available
    if env and failures:
        lines.append("\n## Container Evidence")
        first_failure = failures[0]

        # Universal attacker probes
        lines.append("  [Attacker Container]")
        _, ps_out, _ = env.exec_in("attacker", "ps aux 2>/dev/null | head -15 || echo '(ps failed)'")
        lines.append(f"    Processes: {ps_out[:300]}")
        _, ss_out, _ = env.exec_in("attacker", "ss -tn 2>/dev/null | head -10 || echo '(ss failed)'")
        lines.append(f"    Connections: {ss_out[:300]}")

        # Try to identify which system the failed step was targeting
        target_system = _extract_target_system(first_failure, procedure)
        if target_system:
            lines.append(f"  [{target_system} Container]")
            _, sys_ps, _ = env.exec_in(target_system, "ps aux 2>/dev/null | head -15 || echo '(ps failed)'")
            lines.append(f"    Processes: {sys_ps[:300]}")
            _, sys_ss, _ = env.exec_in(target_system, "ss -tlnp 2>/dev/null | head -10 || echo '(ss failed)'")
            lines.append(f"    Listening: {sys_ss[:300]}")

    return "\n".join(lines) or "Unknown failure — no step details available."


def _extract_target_system(failed_step, procedure) -> str | None:
    """Extract system_id from a failed step by looking at the action.

    Returns the system_id if we can infer it from ${system.<id>.host} in the command/URL.
    """
    if not procedure:
        return None

    # Find the step in the procedure
    proc_step = next((s for s in procedure.procedure if s.step_id == failed_step.step_id), None)
    if not proc_step or not proc_step.action:
        return None

    action = proc_step.action

    # Check command for exec_attacker
    if hasattr(action, 'command') and action.command:
        import re
        # Match ${system.<id>.host}
        match = re.search(r'\$\{system\.([^.}]+)\.host\}', action.command)
        if match:
            return match.group(1)

    # Check URL for http_request
    if hasattr(action, 'url') and action.url:
        import re
        match = re.search(r'\$\{system\.([^.}]+)\.host\}', action.url)
        if match:
            return match.group(1)

    return None


def run_chain_test(
    graph: "EntityGraph",
    built: dict[str, "BuildOutcome"],
    console=None,
) -> "ChainTestOutcome":
    """Run the L3 chain test.

    Steps:
      1. Build per-system deploy scripts (grouped by system_id).
      2. Bring up TopologyEnvironment (one container per system + attacker).
      3. Deploy each system's script into its container.
      4. Call chain_attacker.attack() to synthesise an end-to-end Procedure.
      5. Execute the procedure; retry up to MAX_RETRIES times on failure.
      6. Return ChainTestResult (always teardown in finally).

    Args:
        graph: The EntityGraph (with concrete edge values populated by the build phase).
        built: All PASSED BuildOutcomes, keyed by entity_id.
        console: Optional RunConsole for progress output.

    Returns:
        ChainTestResult with PASSED/FAILED status.
    """
    from goe.construction_crew import chain_attacker
    from goe.container.topology_environment import TopologyEnvironment
    from goe.executor.interpolation import resolve_static_ctx
    from goe.executor.runner import run as run_procedure
    from goe.models.report import ChainTestResult, ChainTestStatus

    per_system_scripts = _build_per_system_scripts(graph, built)

    env = TopologyEnvironment(graph, scope="chain")
    chain_procedure: Procedure | None = None  # type: ignore[name-defined]
    exec_result = None

    try:
        # ---- Phase 1: bring up topology + deploy ----------------------------
        logger.info("ChainTest: setting up topology environment…")
        env.setup()

        deploy_failures: list[str] = []
        for system_id, script in per_system_scripts.items():
            ec, _stdout, stderr = env.deploy_system(system_id, script)
            if console:
                console.chain_test_deploy(system_id, ec == 0, stderr)
            if ec != 0:
                deploy_failures.append(
                    f"System '{system_id}' deploy failed (exit {ec}): {stderr[:300]}"
                )

        if deploy_failures:
            reason = "; ".join(deploy_failures)
            logger.warning(f"ChainTest: deploy failures: {reason}")
            return ChainTestOutcome(
                result=ChainTestResult(status=ChainTestStatus.FAILED, reason=reason)
            )

        # Healthcheck: verify exposed ports are responding
        logger.info("ChainTest: running healthchecks on exposed ports…")
        healthcheck_failures: list[str] = []
        n_checks = 0
        for system in graph.systems:
            if system.network.exposed_ports:
                for port in system.network.exposed_ports:
                    n_checks += 1
                    # Try both root and /health endpoints
                    check_cmd = (
                        f"curl -sf --max-time 5 http://{system.network.hostname}:{port}/ || "
                        f"curl -sf --max-time 5 http://{system.network.hostname}:{port}/health"
                    )
                    ec, _stdout, _stderr = env.exec_in("attacker", check_cmd)
                    if ec != 0:
                        healthcheck_failures.append(
                            f"System '{system.id}' healthcheck failed on port {port}"
                        )

        if console and n_checks > 0:
            console.chain_test_healthcheck(n_checks, len(healthcheck_failures))

        if healthcheck_failures:
            reason = "; ".join(healthcheck_failures)
            logger.warning(f"ChainTest: healthcheck failures (may be expected for non-web systems): {reason}")
            # Don't fail the chain test on healthcheck — some systems are SSH-only, no HTTP

        # ---- Phase 2: synthesise the chain procedure ------------------------
        logger.info("ChainTest: synthesising chain procedure…")
        if console:
            console.chain_test_synthesizing()
        from goe.construction_crew._yaml_repair import ProcedureParseError

        try:
            chain_procedure = chain_attacker.attack(graph, built)
            if console:
                console.chain_test_procedure_summary(len(chain_procedure.procedure))
        except ProcedureParseError as e:
            logger.warning(f"ChainTest: chain_attacker failed to produce parseable YAML: {e}")
            return ChainTestOutcome(
                result=ChainTestResult(
                    status=ChainTestStatus.FAILED,
                    reason=f"Chain attacker YAML parse failure: {e}",
                )
            )

        # ---- Phase 3: execute + retry loop ----------------------------------
        # Resolve the build-time-static ctx (systems + edge concrete values). Every
        # consumed edge param should carry a concrete value by now (resolve.py + the
        # build's edge propagation); a missing one falls back to its structural
        # placeholder and is logged loudly (partial-information bug).
        static_ctx = resolve_static_ctx(graph, logger=logger)
        ctx: dict = {
            "target_host": env.get_target_host(),
            "attacker_host": env.get_attacker_host(),
            "target_port": "",
            "systems": static_ctx["systems"],
            "edges": static_ctx["edges"],
        }

        attempt = 0
        while attempt <= MAX_RETRIES:
            logger.info(f"ChainTest: executing chain procedure (attempt {attempt + 1})…")
            if console:
                console.chain_test_executing(attempt + 1, MAX_RETRIES)
            exec_result = run_procedure(chain_procedure, env, ctx)

            # Show step results if console is available
            if console and exec_result.steps:
                for step in exec_result.steps:
                    # Extract action detail for failed steps
                    action_detail = ""
                    if not step.passed:
                        # Get the corresponding step from procedure to show action
                        proc_step = next((s for s in chain_procedure.procedure if s.step_id == step.step_id), None)
                        if proc_step and proc_step.action:
                            action = proc_step.action
                            # Format action detail based on type
                            if hasattr(action, 'command'):
                                action_detail = action.command
                            elif hasattr(action, 'url'):
                                action_detail = f"{action.method} {action.url}"

                    console.chain_test_step(
                        step.step_id,
                        step.passed,
                        step.reason,
                        action_detail=action_detail,
                        stdout=step.raw.stdout if not step.passed else "",
                        stderr=step.raw.stderr if not step.passed else ""
                    )

            if exec_result.passed:
                logger.info("ChainTest: PASSED")
                return ChainTestOutcome(
                    result=ChainTestResult(status=ChainTestStatus.PASSED),
                    procedure=chain_procedure,
                )

            attempt += 1
            if attempt > MAX_RETRIES:
                break

            diagnosis = _summarise_failure(exec_result, env=env, procedure=chain_procedure)
            logger.info(f"ChainTest: attempt {attempt} failed — diagnosing and retrying…\n{diagnosis}")
            if console:
                console.chain_test_diagnosis(diagnosis)

            # Reset containers to clear stale state from the failed attempt
            logger.info("ChainTest: resetting all system containers…")
            if console:
                console.chain_test_resetting()
            reset_results = env.reset_all_systems(per_system_scripts)
            reset_failures = [
                f"System '{sid}' re-deploy failed (exit {ec}): {stderr[:300]}"
                for sid, (ec, _, stderr) in reset_results.items()
                if ec != 0
            ]
            if reset_failures:
                reason = "; ".join(reset_failures)
                logger.warning(f"ChainTest: reset failed, aborting retries: {reason}")
                return ChainTestOutcome(
                    result=ChainTestResult(status=ChainTestStatus.FAILED, reason=f"Reset failed: {reason}")
                )

            # Fix the procedure based on the diagnosis
            try:
                chain_procedure = chain_attacker.fix_chain(chain_procedure, diagnosis)
            except ProcedureParseError as e:
                logger.warning(f"ChainTest: fix_chain failed to produce parseable YAML: {e}")
                break  # exhaust retries gracefully

        # All retries exhausted
        diagnosis = _summarise_failure(exec_result, env=env, procedure=chain_procedure) if exec_result else "No result"
        broken_step = exec_result.failed_step if exec_result else None
        return ChainTestOutcome(
            result=ChainTestResult(
                status=ChainTestStatus.FAILED,
                broken_edge=broken_step,
                reason=diagnosis,
            ),
            procedure=chain_procedure,
        )

    except Exception as e:
        logger.exception("ChainTest: unexpected error")
        return ChainTestOutcome(
            result=ChainTestResult(
                status=ChainTestStatus.FAILED,
                reason=f"Unexpected error: {e}",
            )
        )

    finally:
        env.teardown()
