"""Step 3: Two-layer Docker testing with diagnostic retry loop.

Hybrid architecture:
  - Python loop controls progression (apply snippet, run checks, decide stop/continue).
  - LLM (Testing Agent) judges each command's output — no hardcoded output parsing.

Incremental cumulative testing:
  - After applying snippet N and passing Layer 1, re-run Layer 2 probes for
    ALL snippets 0..N that have an attack_snippet.
"""

import json
from typing import List, Optional


def _si(s: str) -> str:
    """Sanitize a string for use as a crewAI crew.kickoff() input value.

    Replaces Jinja2/template-syntax double-braces ({{ and }}) with spaced
    equivalents so the LLM is less likely to echo them literally in its JSON
    output, which would cause crewAI's Pydantic extractor to fail parsing.
    """
    return s.replace("{{", "{ {").replace("}}", "} }")

import rich
from crewai import Agent, Task, Crew, Process

from game_of_everything.state import GoEState
from game_of_everything.models import (
    TestVerdict, TestResult, DiagnosticResult,
)
from game_of_everything.tools.read_atom_tool import ReadAtomTool
from game_of_everything.tools.exec_in_container_tool import ExecInContainerTool
from game_of_everything.tools.test_environment import TestEnvironmentTool
from game_of_everything.llm_factory import make_llm


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
    box_id: str = "",
) -> TestVerdict:
    """Kick off a one-task Testing Agent crew to judge command output."""
    llm = make_llm("testing_agent")
    _tag = f"[{box_id}][TESTER]" if box_id else "[TESTER]"

    tester = Agent(
        config=agents_config["testing_agent"],
        llm=llm,
        verbose=True,
        step_callback=lambda step: print(f"{_tag} {step}"),
    )  # type: ignore

    verdict_task = Task(
        config=tasks_config["validate_snippets_task"],  # type: ignore
        agent=tester,
        output_pydantic=TestVerdict,
    )

    verdict_crew = Crew(
        name=f"{box_id}/verdict/{atom_name}" if box_id else f"verdict/{atom_name}",
        agents=[tester],
        tasks=[verdict_task],
        process=Process.sequential,
        verbose=True,
        function_calling_llm=llm,
    )

    verdict_crew.kickoff(
        inputs={
            "atom_name": atom_name,
            "atom_context": _si(atom_context),
            "layer": layer,
            "snippet_executed": _si(snippet_executed),
            "exit_code": str(exit_code),
            "stdout": _si(stdout or "(empty)"),
            "stderr": _si(stderr or "(empty)"),
        }
    )

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
    box_id: str = "",
    target_container_name: str = "goe_target",
    attacker_container_name: str = "goe_attacker",
) -> DiagnosticResult:
    """Kick off a one-task Diagnostic Agent crew to diagnose and fix a failing snippet."""
    llm = make_llm("diagnostic_agent")
    _tag = f"[{box_id}][DIAGNOSTICIAN]" if box_id else "[DIAGNOSTICIAN]"

    diagnostician = Agent(
        config=agents_config["diagnostic_agent"],
        llm=llm,
        tools=[ReadAtomTool(), ExecInContainerTool()],
        verbose=True,
        step_callback=lambda step: print(f"{_tag} {step}"),
    )  # type: ignore

    diag_task = Task(
        config=tasks_config["diagnose_snippet_task"],  # type: ignore
        agent=diagnostician,
        output_pydantic=DiagnosticResult,
    )

    diag_crew = Crew(
        name=f"{box_id}/diagnostic/{atom_name}" if box_id else f"diagnostic/{atom_name}",
        agents=[diagnostician],
        tasks=[diag_task],
        process=Process.sequential,
        verbose=True,
        function_calling_llm=llm,
    )

    diag_crew.kickoff(
        inputs={
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
            "target_container_name": target_container_name,
            "attacker_container_name": attacker_container_name,
        }
    )

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
    env: Optional[TestEnvironmentTool] = None,
    box_id: str = "",
) -> None:
    """Test generated snippets in Docker containers with two-layer validation.

    Args:
        state: Flow state to mutate in-place.
        agents_config: Loaded agents.yaml dict.
        tasks_config: Loaded tasks.yaml dict.
        env: Optional pre-constructed TestEnvironmentTool. When supplied the
             caller owns the lifecycle (setup/teardown are NOT called here).
             When None (default), this function creates, sets up, and tears
             down its own TestEnvironmentTool() — original behaviour.
    """

    if not state.generated_snippets:
        print("No generated snippets to test. Skipping.")
        return

    snippets = state.generated_snippets
    _owns_env = env is None
    if _owns_env:
        env = TestEnvironmentTool()
    results: List[TestResult] = []

    try:
        if _owns_env:
            rich.print("\n[bold yellow]=== SETTING UP TEST ENVIRONMENT ===[/bold yellow]")
            env.setup()
            rich.print(f"[green]Test environment ready ({env.target_name} + {env.attacker_name} on {env.network_name})[/green]")

        # Ensure attacker container has all referenced tools
        all_attack_snippets = [s.attack_snippet for s in snippets if s.attack_snippet]
        if all_attack_snippets:
            rich.print("[yellow]Checking attacker container for required tools...[/yellow]")
            env.ensure_attacker_tools(all_attack_snippets)
            rich.print("[green]Attacker tools verified.[/green]")

        # Ensure target container has all tools referenced in code/testing snippets
        all_code_snippets = [s.code_snippet for s in snippets]
        all_testing_snippets = [s.testing_snippet for s in snippets]
        rich.print("[yellow]Checking target container for required tools...[/yellow]")
        env.ensure_target_tools(all_code_snippets, all_testing_snippets)
        rich.print("[green]Target tools verified.[/green]")

        MAX_DIAGNOSTIC_RETRIES = 2

        for i, snippet in enumerate(snippets):
            rich.print(f"\n[bold cyan]=== TESTING SNIPPET {i}: {snippet.atom_name} ===[/bold cyan]")

            diagnostic_attempts: List[DiagnosticResult] = []
            l1_passed = False
            l1_verdict = None
            apply_stderr_last = ""

            # --- Apply + Layer 1 with diagnostic retry loop ---
            for attempt in range(1 + MAX_DIAGNOSTIC_RETRIES):
                is_retry = attempt > 0

                if is_retry:
                    rich.print(f"\n  [bold yellow]--- DIAGNOSTIC RETRY {attempt}/{MAX_DIAGNOSTIC_RETRIES} for {snippet.atom_name} ---[/bold yellow]")

                # Apply the code_snippet on the target
                rich.print(f"  [yellow]{'Re-applying' if is_retry else 'Applying'} code_snippet...[/yellow]")
                apply_exit, apply_stdout, apply_stderr = env.exec_in_target(snippet.code_snippet)
                apply_stderr_last = apply_stderr
                rich.print(f"  Apply exit code: {apply_exit}")
                if apply_stderr:
                    rich.print(f"  [dim]Apply stderr: {apply_stderr[:500]}[/dim]")

                # Layer 1: run testing_snippet, ask LLM to judge
                rich.print(f"  [blue]Running Layer 1 (internal state check)...[/blue]")
                l1_exit, l1_stdout, l1_stderr = env.exec_in_target(snippet.testing_snippet)
                rich.print(f"  Layer 1 exit code: {l1_exit}")

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
                    box_id=box_id,
                )

                rich.print(f"  Layer 1 verdict: {'[green]PASS[/green]' if l1_verdict.passed else '[red]FAIL[/red]'}")
                rich.print(f"  Reasoning: {l1_verdict.reasoning}")

                if l1_verdict.passed:
                    l1_passed = True
                    break

                # Layer 1 failed — attempt diagnosis if retries remain
                if attempt < MAX_DIAGNOSTIC_RETRIES:
                    rich.print(f"  [yellow]Layer 1 FAILED. Running Diagnostic Agent (attempt {attempt + 1}/{MAX_DIAGNOSTIC_RETRIES})...[/yellow]")

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
                        box_id=box_id,
                        target_container_name=env.target_name,
                        attacker_container_name=env.attacker_name,
                    )

                    diagnostic_attempts.append(diag_result)

                    rich.print(f"  [cyan]Diagnosis:[/cyan] {diag_result.diagnosis}")
                    rich.print(f"  [cyan]Confidence:[/cyan] {diag_result.confidence}")

                    # Apply the fix
                    snippet.code_snippet = diag_result.fixed_code_snippet
                    snippet.testing_snippet = diag_result.fixed_testing_snippet

                    rich.print(f"  [green]Snippet updated with diagnostic fix. Retrying...[/green]")
                else:
                    rich.print(f"  [bold red]Layer 1 FAILED after {MAX_DIAGNOSTIC_RETRIES} diagnostic retries. Giving up.[/bold red]")

            if not l1_passed:
                # Retries exhausted — skip this snippet but continue testing the rest
                snippet.set_validated(False)
                results.append(TestResult(
                    atom_name=snippet.atom_name,
                    layer1_verdict=l1_verdict,  # type: ignore
                    layer2_verdicts=None,
                    diagnostic_results=diagnostic_attempts if diagnostic_attempts else None,
                    error=f"Layer 1 failed after {len(diagnostic_attempts)} diagnostic retries — snippet skipped.",
                ))
                rich.print(f"  [bold yellow]Snippet {i} ({snippet.atom_name}) skipped — continuing with remaining snippets.[/bold yellow]")
                continue

            # --- Layer 2: re-run ALL accumulated attack probes 0..i ---
            l2_verdicts: List[TestVerdict] = []
            l2_diagnostics: List[DiagnosticResult] = []

            for j in range(i + 1):
                if j < i and not snippets[j].validated:
                    continue  # skip L2 probes for snippets that failed L1
                attack = snippets[j].attack_snippet
                if attack:
                    rich.print(f"  [red]Running Layer 2 probe for snippet {j} ({snippets[j].atom_name})...[/red]")
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
                        box_id=box_id,
                    )
                    l2_verdicts.append(verdict)

                    status = '[green]PASS[/green]' if verdict.passed else '[red]FAIL[/red]'
                    rich.print(f"    Snippet {j} ({snippets[j].atom_name}) Layer 2: {status}")
                    rich.print(f"    Reasoning: {verdict.reasoning}")

                    if not verdict.passed:
                        if j < i:
                            rich.print(f"    [bold red]⚠ REGRESSION: snippet {j} Layer 2 was passing, now fails after snippet {i}[/bold red]")

                        # Run diagnostic for L2 failure (log only, no retry)
                        rich.print(f"    [yellow]Running Diagnostic Agent for L2 failure (log only)...[/yellow]")
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
                            box_id=box_id,
                            target_container_name=env.target_name,
                            attacker_container_name=env.attacker_name,
                        )
                        l2_diagnostics.append(l2_diag)
                        rich.print(f"    [cyan]L2 Diagnosis:[/cyan] {l2_diag.diagnosis}")

            # Set validated: both layers must pass (or Layer 2 is N/A)
            all_l2_passed = all(v.passed for v in l2_verdicts) if l2_verdicts else True
            snippet.set_validated(l1_passed and all_l2_passed)

            # Combine all diagnostics (L1 retries + L2 logs) for this snippet
            all_diagnostics = diagnostic_attempts + l2_diagnostics

            results.append(TestResult(
                atom_name=snippet.atom_name,
                layer1_verdict=l1_verdict,  # type: ignore
                layer2_verdicts=l2_verdicts if l2_verdicts else None,
                diagnostic_results=all_diagnostics if all_diagnostics else None,
            ))

        state.test_results = results

        # --- Summary ---
        rich.print("\n[bold magenta]=== TEST SUMMARY ===[/bold magenta]")
        for result in results:
            l1_status = '[green]PASS[/green]' if result.layer1_verdict.passed else '[red]FAIL[/red]'
            if result.layer2_verdicts:
                l2_all = all(v.passed for v in result.layer2_verdicts)
                l2_status = f"[green]PASS[/green] ({len(result.layer2_verdicts)} probes)" if l2_all else f"[red]FAIL[/red]"
            else:
                l2_status = "[dim]N/A[/dim]"
            diag_count = len(result.diagnostic_results) if result.diagnostic_results else 0
            diag_label = f" [yellow]({diag_count} diagnostic runs)[/yellow]" if diag_count else ""
            rich.print(f"  {result.atom_name}: L1={l1_status} L2={l2_status}{diag_label}")
            if result.error:
                rich.print(f"    [red]{result.error}[/red]")
            if result.diagnostic_results:
                for idx, dr in enumerate(result.diagnostic_results, 1):
                    rich.print(f"    [cyan]Diagnostic #{idx}[/cyan] (confidence: {dr.confidence}): {dr.diagnosis}")

    finally:
        if _owns_env:
            rich.print("\n[bold yellow]=== TEARING DOWN TEST ENVIRONMENT ===[/bold yellow]")
            env.teardown()
            rich.print("[green]Test environment cleaned up.[/green]")
