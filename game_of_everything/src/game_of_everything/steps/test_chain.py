"""run_chain_test — Layer 3 attack chain validation.

Executes the ChainProbes generated during synthesis against a live Docker topology
(all boxes + attacker on a shared network). Results are stored on
``state.chain_test_results`` as ``ChainTestResult`` entries.

Skip conditions (no-ops):
  - ``state.topology`` is None or has <= 1 box
  - topology has no pivots
  - topology has no chain_probes
  - any box is missing a deploy script (warning, continues with gaps)

On probe failure the remaining probes are recorded as SKIPPED (cascading failure
model — there is no point running step N+1 if step N's credential pivot failed).
"""

import logging
import re
from typing import List, Optional, TYPE_CHECKING

from game_of_everything.models import ChainTestResult
from game_of_everything.state import GoEState
from game_of_everything.tools.chain_test_environment import ChainTestEnvironment

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole

logger = logging.getLogger(__name__)


def run_chain_test(
    state: GoEState,
    agents_config: Optional[dict] = None,
    tasks_config: Optional[dict] = None,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Execute all ChainProbes against a live topology. Mutates ``state.chain_test_results``."""

    topology = state.topology

    # --- Skip guards ---
    if topology is None:
        logger.info("run_chain_test: no topology — skipping.")
        return

    if len(topology.boxes) <= 1:
        logger.info("run_chain_test: single-box topology — chain test not applicable.")
        return

    if not topology.pivots:
        logger.info("run_chain_test: no pivots defined — chain test skipped.")
        return

    if not topology.chain_probes:
        logger.info("run_chain_test: no chain_probes — skipping.")
        return

    deploy_scripts = state.deploy_scripts
    missing = [b.box_id for b in topology.boxes if b.box_id not in deploy_scripts]
    if missing:
        logger.warning(
            f"run_chain_test: missing deploy scripts for boxes {missing} — "
            "those containers will be un-configured."
        )

    env = ChainTestEnvironment(topology, deploy_scripts)
    results: List[ChainTestResult] = []
    cascade_failed = False

    print(f"\n=== CHAIN TEST: {topology.scenario_name} ===")
    print(f"Running {len(topology.chain_probes)} chain probe(s)...\n")

    try:
        env.setup()

        for probe in sorted(topology.chain_probes, key=lambda p: p.step):
            if cascade_failed:
                logger.info(f"  Step {probe.step}: SKIPPED (cascade failure from earlier probe)")
                results.append(
                    ChainTestResult(
                        step=probe.step,
                        command=probe.command,
                        passed=False,
                        stdout="",
                        stderr="SKIPPED: previous probe failed",
                    )
                )
                continue

            logger.info(
                f"  Step {probe.step}: container={probe.from_container!r} "
                f"cmd={probe.command[:80]}"
            )
            try:
                exit_code, stdout, stderr = env.exec_on(probe.from_container, probe.command)
            except KeyError as exc:
                logger.warning(f"  Step {probe.step}: container lookup failed — {exc}")
                results.append(
                    ChainTestResult(
                        step=probe.step,
                        command=probe.command,
                        passed=False,
                        stdout="",
                        stderr=str(exc),
                    )
                )
                cascade_failed = True
                continue

            passed = bool(re.search(probe.success_pattern, stdout))
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] Step {probe.step}: {probe.command[:60]}")
            if not passed:
                print(f"         Pattern  : {probe.success_pattern!r}")
                print(f"         stdout   : {stdout[:200]!r}")
                print(f"         stderr   : {stderr[:200]!r}")
                cascade_failed = True

            results.append(
                ChainTestResult(
                    step=probe.step,
                    command=probe.command,
                    passed=passed,
                    stdout=stdout[:2000],
                    stderr=stderr[:500],
                )
            )

    finally:
        env.teardown()

    state.chain_test_results = results

    passed_count = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\nChain test complete: {passed_count}/{total} probes passed.")
    if passed_count == total:
        print("Full attack chain validated!\n")
    else:
        print("Chain incomplete — review probe outputs above.\n")
