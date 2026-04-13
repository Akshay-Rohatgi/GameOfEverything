"""Step 3: Two-layer Docker testing with diagnostic retry loop.

Hybrid architecture:
  - Python loop controls progression (apply snippet, run checks, decide stop/continue).
  - LLM (Testing Agent) judges each command's output — no hardcoded output parsing.

Incremental cumulative testing:
  - After applying snippet N and passing Layer 1, re-run Layer 2 probes for
    ALL snippets 0..N that have an attack_snippet.
"""

import json
import time
from typing import List, Optional, TYPE_CHECKING


def _si(s: str) -> str:
    """Sanitize a string for use as a crewAI crew.kickoff() input value."""
    return s.replace("{{", "{ {").replace("}}", "} }")

from crewai import Agent, Task, Crew, Process

from game_of_everything.state import GoEState
from game_of_everything.models import (
    TestVerdict, TestResult, DiagnosticResult,
)
from game_of_everything.tools.read_atom_tool import ReadAtomTool
from game_of_everything.tools.exec_in_container_tool import ExecInContainerTool
from game_of_everything.tools.test_environment import TestEnvironmentTool
from game_of_everything.llm_factory import make_llm

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole


# ---------------------------------------------------------------------------
# Helper crews
# ---------------------------------------------------------------------------

def _run_verdict_crew(
    agents_config: dict,
    tasks_config: dict,
    atom_name: str,
    atom_context: str,
    layer: str,
    snippet_executed: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    ui: Optional["GoEConsole"] = None,
) -> TestVerdict:
    """Kick off a one-task Testing Agent crew to judge command output."""
    llm = make_llm("testing_agent")

    tester = Agent(
        config=agents_config["testing_agent"],
        llm=llm,
        verbose=False,
    )  # type: ignore

    verdict_task = Task(
        config=tasks_config["validate_snippets_task"],  # type: ignore
        agent=tester,
        output_pydantic=TestVerdict,
    )

    verdict_crew = Crew(
        agents=[tester],
        tasks=[verdict_task],
        process=Process.sequential,
        verbose=False,
        function_calling_llm=llm,
    )

    inputs = {
        "atom_name": atom_name,
        "atom_context": _si(atom_context),
        "layer": layer,
        "snippet_executed": _si(snippet_executed),
        "exit_code": str(exit_code),
        "stdout": _si(stdout or "(empty)"),
        "stderr": _si(stderr or "(empty)"),
    }

    if ui:
        with ui.capture():
            verdict_crew.kickoff(inputs=inputs)
    else:
        verdict_crew.kickoff(inputs=inputs)

    if verdict_task.output.pydantic:  # type: ignore
        return verdict_task.output.pydantic  # type: ignore

    return TestVerdict(
        passed=False,
        reasoning="Failed to parse LLM verdict output into TestVerdict.",
    )


def _run_diagnostic_crew(
    agents_config: dict,
    tasks_config: dict,
    atom_name: str,
    atom_context: str,
    atom_parameters: Optional[str],
    original_code_snippet: str,
    original_testing_snippet: str,
    apply_stderr: str,
    l1_exit_code: int,
    l1_stdout: str,
    l1_stderr: str,
    verdict_reasoning: str,
    attempt_number: int,
    ui: Optional["GoEConsole"] = None,
) -> DiagnosticResult:
    """Kick off a one-task Diagnostic Agent crew to diagnose and fix a failing snippet."""
    llm = make_llm("diagnostic_agent")

    diagnostician = Agent(
        config=agents_config["diagnostic_agent"],
        llm=llm,
        tools=[ReadAtomTool(), ExecInContainerTool()],
        verbose=False,
    )  # type: ignore

    diag_task = Task(
        config=tasks_config["diagnose_snippet_task"],  # type: ignore
        agent=diagnostician,
        output_pydantic=DiagnosticResult,
    )

    diag_crew = Crew(
        agents=[diagnostician],
        tasks=[diag_task],
        process=Process.sequential,
        verbose=False,
        function_calling_llm=llm,
    )

    inputs = {
        "atom_name": atom_name,
        "atom_context": _si(atom_context),
        "atom_parameters": _si(atom_parameters or "(none)"),
        "original_code_snippet": _si(original_code_snippet),
        "original_testing_snippet": _si(original_testing_snippet),
        "apply_stderr": _si(apply_stderr or "(empty)"),
        "l1_exit_code": str(l1_exit_code),
        "l1_stdout": _si(l1_stdout or "(empty)"),
        "l1_stderr": _si(l1_stderr or "(empty)"),
        "verdict_reasoning": _si(verdict_reasoning),
        "attempt_number": str(attempt_number),
    }

    if ui:
        with ui.capture():
            diag_crew.kickoff(inputs=inputs)
    else:
        diag_crew.kickoff(inputs=inputs)

    if diag_task.output.pydantic:  # type: ignore
        return diag_task.output.pydantic  # type: ignore

    return DiagnosticResult(
        fixed_code_snippet=original_code_snippet,
        fixed_testing_snippet=original_testing_snippet,
        diagnosis="Failed to parse diagnostic agent output. Returning original snippets unchanged.",
        confidence="low",
    )


# ---------------------------------------------------------------------------
# Main step
# ---------------------------------------------------------------------------

def run_test_snippets(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Test generated snippets in Docker containers with two-layer validation."""
    if not state.generated_snippets:
        if ui:
            ui.log("No generated snippets to test. Skipping.")
        return

    snippets = state.generated_snippets
    env = TestEnvironmentTool()
    results: List[TestResult] = []

    if ui:
        ui.test_header()
    t0 = time.monotonic()

    try:
        if ui:
            ui.log("\n=== SETTING UP TEST ENVIRONMENT ===")
        env.setup()
        if ui:
            ui.log("Test environment ready (target + attacker on goe_test_net)")

        # Ensure attacker container has all referenced tools
        all_attack_snippets = [s.attack_snippet for s in snippets if s.attack_snippet]
        if all_attack_snippets:
            if ui:
                ui.log("Checking attacker container for required tools...")
            env.ensure_attacker_tools(all_attack_snippets)

        MAX_DIAGNOSTIC_RETRIES = 2

        for i, snippet in enumerate(snippets):
            if ui:
                ui.log(f"\n=== TESTING SNIPPET {i}: {snippet.atom_name} ===")

            diagnostic_attempts: List[DiagnosticResult] = []
            l1_passed = False
            l1_verdict = None
            apply_stderr_last = ""

            # --- Apply + Layer 1 with diagnostic retry loop ---
            for attempt in range(1 + MAX_DIAGNOSTIC_RETRIES):
                is_retry = attempt > 0

                if is_retry and ui:
                    ui.log(f"  DIAGNOSTIC RETRY {attempt}/{MAX_DIAGNOSTIC_RETRIES} for {snippet.atom_name}")

                # Apply the code_snippet on the target
                if ui:
                    ui.log(f"  {'Re-applying' if is_retry else 'Applying'} code_snippet...")
                apply_exit, apply_stdout, apply_stderr = env.exec_in_target(snippet.code_snippet)
                apply_stderr_last = apply_stderr
                if ui:
                    ui.log(f"  Apply exit code: {apply_exit}")
                    if apply_stderr:
                        ui.log(f"  Apply stderr: {apply_stderr[:500]}")

                # Layer 1: run testing_snippet, ask LLM to judge
                if ui:
                    ui.log("  Running Layer 1 (internal state check)...")
                l1_exit, l1_stdout, l1_stderr = env.exec_in_target(snippet.testing_snippet)
                if ui:
                    ui.log(f"  Layer 1 exit code: {l1_exit}")

                l1_verdict = _run_verdict_crew(
                    agents_config=agents_config,
                    tasks_config=tasks_config,
                    atom_name=snippet.atom_name,
                    atom_context=snippet.mapped_atom.context,
                    layer="internal state check",
                    snippet_executed=snippet.testing_snippet,
                    exit_code=l1_exit,
                    stdout=l1_stdout,
                    stderr=l1_stderr,
                    ui=ui,
                )

                if ui:
                    ui.log(f"  Layer 1 verdict: {'PASS' if l1_verdict.passed else 'FAIL'}")
                    ui.log(f"  Reasoning: {l1_verdict.reasoning}")

                if l1_verdict.passed:
                    l1_passed = True
                    break

                # Layer 1 failed — attempt diagnosis if retries remain
                if attempt < MAX_DIAGNOSTIC_RETRIES:
                    if ui:
                        ui.log(f"  Layer 1 FAILED. Running Diagnostic Agent (attempt {attempt + 1}/{MAX_DIAGNOSTIC_RETRIES})...")

                    diag_result = _run_diagnostic_crew(
                        agents_config=agents_config,
                        tasks_config=tasks_config,
                        atom_name=snippet.atom_name,
                        atom_context=snippet.mapped_atom.context,
                        atom_parameters=json.dumps(snippet.mapped_atom.parameters) if snippet.mapped_atom.parameters else None,
                        original_code_snippet=snippet.code_snippet,
                        original_testing_snippet=snippet.testing_snippet,
                        apply_stderr=apply_stderr,
                        l1_exit_code=l1_exit,
                        l1_stdout=l1_stdout,
                        l1_stderr=l1_stderr,
                        verdict_reasoning=l1_verdict.reasoning,
                        attempt_number=attempt + 1,
                        ui=ui,
                    )

                    diagnostic_attempts.append(diag_result)
                    if ui:
                        ui.log(f"  Diagnosis: {diag_result.diagnosis}")
                        ui.log(f"  Confidence: {diag_result.confidence}")

                    snippet.code_snippet = diag_result.fixed_code_snippet
                    snippet.testing_snippet = diag_result.fixed_testing_snippet
                else:
                    if ui:
                        ui.log(f"  Layer 1 FAILED after {MAX_DIAGNOSTIC_RETRIES} diagnostic retries. Giving up.")

            if not l1_passed:
                snippet.set_validated(False)
                results.append(TestResult(
                    atom_name=snippet.atom_name,
                    layer1_verdict=l1_verdict,  # type: ignore
                    layer2_verdicts=None,
                    diagnostic_results=diagnostic_attempts if diagnostic_attempts else None,
                    error=f"Layer 1 failed after {len(diagnostic_attempts)} diagnostic retries — chain stopped at snippet {i}.",
                ))
                if ui:
                    ui.test_result(
                        snippet.atom_name,
                        l1_pass=False,
                        retries=len(diagnostic_attempts),
                        testing_snippet=snippet.testing_snippet,
                    )
                # Mark all remaining snippets as not validated
                for remaining in snippets[i + 1:]:
                    remaining.set_validated(False)
                    if ui:
                        ui.test_skipped(remaining.atom_name)
                break

            # --- Layer 2: re-run ALL accumulated attack probes 0..i ---
            l2_verdicts: List[TestVerdict] = []
            l2_diagnostics: List[DiagnosticResult] = []

            for j in range(i + 1):
                attack = snippets[j].attack_snippet
                if attack:
                    if ui:
                        ui.log(f"  Running Layer 2 probe for snippet {j} ({snippets[j].atom_name})...")
                    a_exit, a_stdout, a_stderr = env.exec_in_attacker(attack)

                    verdict = _run_verdict_crew(
                        agents_config=agents_config,
                        tasks_config=tasks_config,
                        atom_name=snippets[j].atom_name,
                        atom_context=snippets[j].mapped_atom.context,
                        layer="external attack probe",
                        snippet_executed=attack,
                        exit_code=a_exit,
                        stdout=a_stdout,
                        stderr=a_stderr,
                        ui=ui,
                    )
                    l2_verdicts.append(verdict)

                    if ui:
                        ui.log(f"    Snippet {j} ({snippets[j].atom_name}) Layer 2: {'PASS' if verdict.passed else 'FAIL'}")
                        ui.log(f"    Reasoning: {verdict.reasoning}")

                    if not verdict.passed:
                        if j < i and ui:
                            ui.log(f"    REGRESSION: snippet {j} Layer 2 was passing, now fails after snippet {i}")

                        if ui:
                            ui.log("    Running Diagnostic Agent for L2 failure (log only)...")
                        l2_diag = _run_diagnostic_crew(
                            agents_config=agents_config,
                            tasks_config=tasks_config,
                            atom_name=snippets[j].atom_name,
                            atom_context=snippets[j].mapped_atom.context,
                            atom_parameters=json.dumps(snippets[j].mapped_atom.parameters) if snippets[j].mapped_atom.parameters else None,
                            original_code_snippet=snippets[j].code_snippet,
                            original_testing_snippet=attack,
                            apply_stderr=a_stderr,
                            l1_exit_code=a_exit,
                            l1_stdout=a_stdout,
                            l1_stderr=a_stderr,
                            verdict_reasoning=verdict.reasoning,
                            attempt_number=1,
                            ui=ui,
                        )
                        l2_diagnostics.append(l2_diag)
                        if ui:
                            ui.log(f"    L2 Diagnosis: {l2_diag.diagnosis}")

            # Set validated: both layers must pass (or Layer 2 is N/A)
            all_l2_passed = all(v.passed for v in l2_verdicts) if l2_verdicts else True
            snippet.set_validated(l1_passed and all_l2_passed)

            # Determine L2 status for UI
            l2_status: Optional[bool] = None
            if l2_verdicts:
                l2_status = all_l2_passed

            if ui:
                ui.test_result(
                    snippet.atom_name,
                    l1_pass=l1_passed,
                    l2_pass=l2_status,
                    retries=len(diagnostic_attempts),
                    testing_snippet=snippet.testing_snippet,
                    attack_snippet=snippet.attack_snippet,
                )

            # Combine all diagnostics
            all_diagnostics = diagnostic_attempts + l2_diagnostics

            results.append(TestResult(
                atom_name=snippet.atom_name,
                layer1_verdict=l1_verdict,  # type: ignore
                layer2_verdicts=l2_verdicts if l2_verdicts else None,
                diagnostic_results=all_diagnostics if all_diagnostics else None,
            ))

        state.test_results = results

        if ui:
            ui.test_done(time.monotonic() - t0)

        # Log summary
        if ui:
            ui.log("\n=== TEST SUMMARY ===")
            for result in results:
                l1_status = "PASS" if result.layer1_verdict.passed else "FAIL"
                if result.layer2_verdicts:
                    l2_all = all(v.passed for v in result.layer2_verdicts)
                    l2_status_str = f"PASS ({len(result.layer2_verdicts)} probes)" if l2_all else "FAIL"
                else:
                    l2_status_str = "N/A"
                diag_count = len(result.diagnostic_results) if result.diagnostic_results else 0
                ui.log(f"  {result.atom_name}: L1={l1_status} L2={l2_status_str} ({diag_count} diagnostic runs)")
                if result.error:
                    ui.log(f"    {result.error}")

    finally:
        if ui:
            ui.log("\n=== TEARING DOWN TEST ENVIRONMENT ===")
        env.teardown()
        if ui:
            ui.log("Test environment cleaned up.")
