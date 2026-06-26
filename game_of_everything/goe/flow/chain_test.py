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

MAX_RETRIES = 2


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
    from goe.packaging.grader import assemble_deploy_script

    grouped = graph.entities_by_system()
    per_system: dict[str, str] = {}
    for sid, entities in grouped.items():
        sections = [
            (entity.id, built[entity.id].deploy_script or "")
            for entity in entities
            if entity.id in built and (built[entity.id].deploy_script or "").strip()
        ]
        if sections:
            combined, warnings = assemble_deploy_script(sections)
            if warnings:
                logger.warning(
                    f"ChainTest: grader fixed {len(warnings)} conflict(s) on system "
                    f"'{sid}': {warnings}"
                )
            per_system[sid] = combined
    return per_system


def _summarise_failure(result: "ProcedureResult") -> str:  # type: ignore[name-defined]
    """Turn a failed ProcedureResult into a diagnosis string for fix_chain."""
    lines: list[str] = []
    if result.error:
        lines.append(f"Executor error: {result.error}")
    for step in result.steps:
        if not step.passed:
            lines.append(f"Step '{step.step_id}' failed: {step.reason}")
            if step.raw.stdout:
                lines.append(f"  stdout: {step.raw.stdout[:400]}")
            if step.raw.stderr:
                lines.append(f"  stderr: {step.raw.stderr[:200]}")
            if step.raw.error:
                lines.append(f"  error: {step.raw.error}")
            break  # report only the first failure
    return "\n".join(lines) or "Unknown failure — no step details available."


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
    from goe.executor.runner import run as run_procedure
    from goe.models.report import ChainTestResult, ChainTestStatus

    per_system_scripts = _build_per_system_scripts(graph, built)
    systems_ctx = _build_systems_ctx(graph)

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

        # ---- Phase 2: synthesise the chain procedure ------------------------
        logger.info("ChainTest: synthesising chain procedure…")
        chain_procedure = chain_attacker.attack(graph, built)

        # ---- Phase 3: execute + retry loop ----------------------------------
        ctx: dict = {
            "target_host": env.get_target_host(),
            "attacker_host": env.get_attacker_host(),
            "target_port": "",
            "systems": systems_ctx,
            "edges": {},
        }
        # Populate edge concrete values into ctx["edges"]
        for edge in graph.edges:
            ctx["edges"][edge.id] = {
                p: (pv.concrete or pv.structural)
                for p, pv in edge.params.items()
            }

        attempt = 0
        while attempt <= MAX_RETRIES:
            logger.info(f"ChainTest: executing chain procedure (attempt {attempt + 1})…")
            exec_result = run_procedure(chain_procedure, env, ctx)

            if exec_result.passed:
                logger.info("ChainTest: PASSED")
                return ChainTestOutcome(
                    result=ChainTestResult(status=ChainTestStatus.PASSED),
                    procedure=chain_procedure,
                )

            attempt += 1
            if attempt > MAX_RETRIES:
                break

            diagnosis = _summarise_failure(exec_result)
            logger.info(f"ChainTest: attempt {attempt} failed — diagnosing and retrying…\n{diagnosis}")
            chain_procedure = chain_attacker.fix_chain(chain_procedure, diagnosis)

        # All retries exhausted
        diagnosis = _summarise_failure(exec_result) if exec_result else "No result"
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
