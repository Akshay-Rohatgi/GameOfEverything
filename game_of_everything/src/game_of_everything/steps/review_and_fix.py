"""Step: Interactive per-box review for test failures before EC2 deployment.

When ``--review`` is passed and unvalidated snippets exist, this step:
  1. Prints a per-box failure summary (atom name, failure reason, prior
     diagnostic history).
  2. Prompts the user for extra context per box (e.g. OS quirks, version
     notes, env constraints).  Press Enter to skip a box.
  3. Spins up scoped Docker containers for each box that received context,
     replays validated snippets to rebuild state, then re-runs the diagnostic
     agent with user context injected for each failing snippet.
  4. Re-tests each fixed snippet (Layer 1 only) and updates validated flags.
  5. Re-finalizes affected per-box deploy scripts and the topology output
     package (docker-compose.yml / playbook.json / README.md).
  6. Loops: shows updated results and asks "Review again?" until the user
     is satisfied or all failures are resolved.
"""

import json
from typing import Dict, List, Optional, Tuple

import rich

from game_of_everything.models import GeneratedSnippet, TestResult
from game_of_everything.state import GoEState
from game_of_everything.steps.test_snippets import run_diagnostic_crew, run_verdict_crew
from game_of_everything.steps.finalize_script import run_finalize_script
from game_of_everything.steps.finalize_topology import run_finalize_topology
from game_of_everything.tools.test_environment import TestEnvironmentTool


# ---------------------------------------------------------------------------
# Failure detection
# ---------------------------------------------------------------------------

def _unvalidated_snippets(box_state: GoEState) -> List[GeneratedSnippet]:
    """Return snippets that failed validation (validated=False)."""
    return [s for s in (box_state.generated_snippets or []) if not s.validated]


def _collect_failures(state: GoEState) -> Dict[str, List[GeneratedSnippet]]:
    """Return {box_id: [unvalidated_snippet, ...]} across all boxes.

    Multi-box: examines ``state.box_states``.
    Single-box: examines ``state`` directly, keyed as ``"main"``.
    """
    failures: Dict[str, List[GeneratedSnippet]] = {}
    if state.box_states:
        for box_id, box_state in state.box_states.items():
            unvalidated = _unvalidated_snippets(box_state)
            if unvalidated:
                failures[box_id] = unvalidated
    else:
        unvalidated = _unvalidated_snippets(state)
        if unvalidated:
            failures["main"] = unvalidated
    return failures


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _find_test_result(box_state: GoEState, atom_name: str) -> Optional[TestResult]:
    for tr in (box_state.test_results or []):
        if tr.atom_name == atom_name:
            return tr
    return None


def _print_failure_summary(
    failures: Dict[str, List[GeneratedSnippet]],
    box_states: Dict[str, GoEState],
    main_state: GoEState,
) -> None:
    rich.print("\n[bold red]=== TEST FAILURE SUMMARY ===[/bold red]")
    for box_id, snippets in failures.items():
        source = box_states.get(box_id, main_state)
        rich.print(f"\n[bold yellow]Box: {box_id}[/bold yellow] — {len(snippets)} unvalidated snippet(s)")
        for snippet in snippets:
            tr = _find_test_result(source, snippet.atom_name)
            if tr:
                l1_ok = tr.layer1_verdict.passed
                if not l1_ok:
                    layer_tag = "[L1 fail]"
                    reason = tr.layer1_verdict.reasoning
                else:
                    layer_tag = "[L2 fail]"
                    reason = "; ".join(
                        v.reasoning for v in (tr.layer2_verdicts or []) if not v.passed
                    )
            else:
                layer_tag = "[no result]"
                reason = "(no test result recorded)"

            rich.print(f"  [red]✗[/red] {snippet.atom_name} {layer_tag}")
            rich.print(f"    Reason: {reason}")

            if tr and tr.diagnostic_results:
                rich.print(f"    [dim]{len(tr.diagnostic_results)} prior diagnostic attempt(s):[/dim]")
                for idx, dr in enumerate(tr.diagnostic_results, 1):
                    rich.print(f"      #{idx} [{dr.confidence}]: {dr.diagnosis}")


# ---------------------------------------------------------------------------
# Per-snippet re-diagnosis + test
# ---------------------------------------------------------------------------

def _retest_snippet_with_context(
    snippet: GeneratedSnippet,
    prior_verdict_reasoning: str,
    env: TestEnvironmentTool,
    agents_config: dict,
    tasks_config: dict,
    extra_context: str,
    box_id: str,
) -> bool:
    """Run diagnostic agent with user context, apply fix, Layer 1 re-test.

    Updates snippet.code_snippet, snippet.testing_snippet, and snippet.validated
    in-place.  Returns True if the snippet is now passing.
    """
    rich.print(f"\n  [cyan]Re-diagnosing {snippet.atom_name} with user context...[/cyan]")

    diag = run_diagnostic_crew(
        agents_config=agents_config,
        tasks_config=tasks_config,
        atom_name=snippet.atom_name,
        atom_context=snippet.mapped_atom.context,
        atom_parameters=(
            json.dumps(snippet.mapped_atom.parameters)
            if snippet.mapped_atom.parameters
            else None
        ),
        original_code_snippet=snippet.code_snippet,
        original_testing_snippet=snippet.testing_snippet,
        apply_stderr="",
        l1_exit_code=0,
        l1_stdout="",
        l1_stderr="",
        verdict_reasoning=prior_verdict_reasoning,
        attempt_number=1,
        box_id=box_id,
        target_container_name=env.target_name,
        attacker_container_name=env.attacker_name,
        extra_context=extra_context,
    )
    rich.print(f"  [cyan]Diagnosis:[/cyan] {diag.diagnosis}")
    rich.print(f"  [cyan]Confidence:[/cyan] {diag.confidence}")

    # Apply fixed snippets
    snippet.code_snippet = diag.fixed_code_snippet
    snippet.testing_snippet = diag.fixed_testing_snippet

    # Apply code to container
    apply_exit, _apply_stdout, apply_stderr = env.exec_in_target(snippet.code_snippet)
    rich.print(f"  Apply exit code: {apply_exit}")
    if apply_stderr:
        rich.print(f"  [dim]Apply stderr: {apply_stderr[:500]}[/dim]")

    # Layer 1 re-test
    l1_exit, l1_stdout, l1_stderr = env.exec_in_target(snippet.testing_snippet)
    rich.print(f"  Layer 1 exit code: {l1_exit}")

    verdict = run_verdict_crew(
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

    passed = verdict.passed
    status_tag = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
    rich.print(f"  Layer 1 re-test: {status_tag} — {verdict.reasoning}")

    snippet.set_validated(passed)
    return passed


# ---------------------------------------------------------------------------
# Per-box fix pass
# ---------------------------------------------------------------------------

def _fix_box(
    box_id: str,
    extra_context: str,
    snippets_to_fix: List[GeneratedSnippet],
    box_state: GoEState,
    hostname: str,
    agents_config: dict,
    tasks_config: dict,
) -> bool:
    """Spin up containers for one box, replay validated snippets, re-diagnose
    failing ones.  Returns True if at least one snippet was fixed."""
    rich.print(f"\n[bold cyan]=== RE-DIAGNOSING BOX: {box_id} ===[/bold cyan]")

    scope = box_id if box_id != "main" else ""
    env = TestEnvironmentTool(scope=scope, hostname=hostname)
    env.setup()
    rich.print(f"  Containers ready: {env.target_name} + {env.attacker_name}")

    any_fixed = False
    try:
        # Replay validated snippets to rebuild container state before re-testing
        validated = [s for s in (box_state.generated_snippets or []) if s.validated]
        if validated:
            rich.print(f"  [dim]Replaying {len(validated)} validated snippet(s) to rebuild state...[/dim]")
            for vs in validated:
                env.exec_in_target(vs.code_snippet)

        for snippet in snippets_to_fix:
            tr = _find_test_result(box_state, snippet.atom_name)
            prior_reasoning = (
                tr.layer1_verdict.reasoning
                if tr
                else "(no prior verdict available)"
            )
            passed = _retest_snippet_with_context(
                snippet=snippet,
                prior_verdict_reasoning=prior_reasoning,
                env=env,
                agents_config=agents_config,
                tasks_config=tasks_config,
                extra_context=extra_context,
                box_id=box_id,
            )
            if passed:
                any_fixed = True
                rich.print(f"  [green]✓[/green] {snippet.atom_name} now passing")
            else:
                rich.print(f"  [red]✗[/red] {snippet.atom_name} still failing after re-diagnosis")
    finally:
        env.teardown()
        rich.print(f"  Containers torn down.")

    return any_fixed


# ---------------------------------------------------------------------------
# Main step
# ---------------------------------------------------------------------------

def run_review_and_fix(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
) -> None:
    """Interactive per-box review loop.

    Presents per-box failure summaries, prompts for extra context, re-runs the
    diagnostic agent with that context injected, re-finalizes affected scripts,
    and loops until the user is satisfied or all failures are resolved.

    Args:
        state: Flow state mutated in-place.
        agents_config: Loaded agents.yaml dict.
        tasks_config: Loaded tasks.yaml dict.
    """
    loop_round = 0
    while True:
        loop_round += 1
        failures = _collect_failures(state)

        if not failures:
            if loop_round == 1:
                rich.print("[green]No unvalidated snippets — nothing to review.[/green]")
            else:
                rich.print("[bold green]All failures resolved.[/bold green]")
            return

        _print_failure_summary(failures, state.box_states, state)

        total = sum(len(v) for v in failures.values())
        rich.print(
            f"\n[bold]Review round {loop_round}[/bold] — "
            f"{total} unvalidated snippet(s) across {len(failures)} box(es).\n"
            "[dim]Provide extra context per box to guide the diagnostic agent "
            "(e.g. OS quirks, version constraints, env notes).  "
            "Leave blank to skip a box.[/dim]"
        )

        # Collect per-box context from the user
        box_contexts: Dict[str, str] = {}
        for box_id in failures:
            try:
                ctx = input(f"\nExtra context for [{box_id}] (Enter to skip): ").strip()
            except EOFError:
                ctx = ""
            if ctx:
                box_contexts[box_id] = ctx

        if not box_contexts:
            rich.print("[yellow]No context provided for any box — stopping review.[/yellow]")
            return

        # Re-diagnose each box that received context
        scripts_changed = False
        for box_id, extra_context in box_contexts.items():
            # Resolve box state + hostname
            if state.box_states:
                box_state = state.box_states.get(box_id)
                box_def = next(
                    (
                        b for b in (state.topology.boxes if state.topology else [])
                        if b.box_id == box_id
                    ),
                    None,
                )
                hostname = box_def.hostname if box_def else box_id
            else:
                box_state = state
                hostname = (
                    state.topology.boxes[0].hostname
                    if (state.topology and state.topology.boxes)
                    else box_id
                )

            if box_state is None:
                rich.print(f"  [red]No box state found for {box_id} — skipping.[/red]")
                continue

            snippets_to_fix = _unvalidated_snippets(box_state)
            if not snippets_to_fix:
                continue

            any_fixed = _fix_box(
                box_id=box_id,
                extra_context=extra_context,
                snippets_to_fix=snippets_to_fix,
                box_state=box_state,
                hostname=hostname,
                agents_config=agents_config,
                tasks_config=tasks_config,
            )

            # Re-finalize this box's deploy script if anything changed
            if state.box_states:
                run_finalize_script(box_state, agents_config, tasks_config, skip_disk_write=True)
                if box_state.final_script:
                    state.deploy_scripts[box_id] = box_state.final_script
                    scripts_changed = True
                    rich.print(
                        f"  [green]Deploy script for {box_id} updated "
                        f"({len(box_state.final_script)} chars)[/green]"
                    )
            else:
                # Single-box: write script to disk
                run_finalize_script(state, agents_config, tasks_config)
                scripts_changed = True

        # Re-generate multi-box topology package if any scripts changed
        if scripts_changed and state.box_states and state.topology and len(state.topology.boxes) > 1:
            rich.print("\n[dim]Re-generating topology output package...[/dim]")
            run_finalize_topology(state, agents_config, tasks_config)

        # Prompt to loop again
        try:
            again = input("\nReview again? (y/N): ").strip().lower()
        except EOFError:
            again = "n"
        if again != "y":
            return
