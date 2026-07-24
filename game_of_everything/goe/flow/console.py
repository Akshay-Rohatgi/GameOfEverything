"""Lean progress printer for the run flow.

Deliberately small — does not import v1's GoEConsole (which pulls
game_of_everything.models). Uses Rich when available; falls back to plain
print(). When the orchestrator is given console=None it stays silent here and
relies on build_entity's own verbose logging.
"""

from __future__ import annotations

import time


class RunConsole:
    """Minimal header / per-entity / summary printer."""

    def __init__(self) -> None:
        try:
            from rich.console import Console

            self._rich = Console(force_terminal=True, force_interactive=False, markup=True)
        except Exception:  # pragma: no cover - rich is a dependency, but degrade gracefully
            self._rich = None
        self._starts: dict[str, float] = {}

    def _line(self, msg: str) -> None:
        if self._rich is not None:
            # Explicitly use markup=True for Rich to parse the markup
            self._rich.print(msg, markup=True, highlight=False)
        else:
            print(msg)

    def header(self, request: str, n_entities: int) -> None:
        self._line(f"\n[bold]GoE run[/bold] — {n_entities} entit"
                   f"{'y' if n_entities == 1 else 'ies'} to build")
        self._line(f"  request: {request}\n")

    def planning(self) -> None:
        self._line("\n[bold cyan]╔═══════════════════════════════════════╗[/bold cyan]")
        self._line("[bold cyan]║[/bold cyan]  [bold white]Planning Phase[/bold white]                  [bold cyan]║[/bold cyan]")
        self._line("[bold cyan]╚═══════════════════════════════════════╝[/bold cyan]\n")

    def plan_step(self, step: str, detail: str = "") -> None:
        """Show a planning step in progress."""
        self._line(f"[bold blue]→[/bold blue] {step}{f': {detail}' if detail else '...'}")

    def plan_result(self, result: str, color: str = "green") -> None:
        """Show a planning step result."""
        self._line(f"  [{color}]✓[/{color}] {result}")

    def plan_killchain(self, steps: list[str]) -> None:
        """Display the generated killchain."""
        self._line("\n[bold magenta]Killchain generated:[/bold magenta]")
        self._line("[dim]────────────────────────────────────────[/dim]")
        for i, step in enumerate(steps, 1):
            self._line(f"[yellow]{i:2d}.[/yellow] {step}")
        self._line("[dim]────────────────────────────────────────[/dim]")

    def plan_entities_summary(self, entity_ids: list[str]) -> None:
        """Show planned entities."""
        entities_str = ", ".join(f"[cyan]{eid}[/cyan]" for eid in entity_ids)
        self._line(f"  [green]✓[/green] Planned {len(entity_ids)} entity stub(s): {{{entities_str}}}")

    def plan_grades(self, grades: dict[str, str]) -> None:
        """Show entity grading results."""
        grades_str = ", ".join(
            f"[green]{result}[/green]" if result.lower() == "pass" else f"[yellow]{result}[/yellow]"
            for result in grades.values()
        )
        self._line(f"  [green]✓[/green] Graded each entity {{{grades_str}}}")

    def plan_failed(self, violations: list) -> None:
        self._line("\n[bold red]✗ Planning failed:[/bold red]")
        for v in violations:
            self._line(f"  [red]•[/red] [{getattr(v, 'check', '?')}] {getattr(v, 'message', v)}")

    def entity_start(self, entity_id: str) -> None:
        self._starts[entity_id] = time.time()
        self._line(f"[bold]▶ building[/bold] {entity_id}")

    def entity_done(self, entity_id: str, attempts: int) -> None:
        dt = time.time() - self._starts.get(entity_id, time.time())
        self._line(f"[green]✓ {entity_id}[/green] "
                   f"({attempts} attempt{'s' if attempts != 1 else ''}, {dt:.0f}s)")

    def entity_failed(self, entity_id: str, reason: str | None, skipped: list[str]) -> None:
        dt = time.time() - self._starts.get(entity_id, time.time())
        self._line(f"[red]✗ {entity_id}[/red] ({dt:.0f}s): {reason or 'unknown failure'}")
        if skipped:
            self._line(f"  [yellow]skipped {len(skipped)}:[/yellow] {', '.join(skipped)}")

    def chain_test_start(self, n_entities: int) -> None:
        self._line(f"\n[cyan]═══ L3 Chain Test[/cyan] [dim]({n_entities} entities)[/dim]")

    def chain_test_deploy(self, system_id: str, success: bool, stderr: str = "") -> None:
        if success:
            self._line(f"  [green]→[/green] Deployed system [cyan]{system_id}[/cyan]")
        else:
            self._line(f"  [red]✗[/red] Deploy failed for [cyan]{system_id}[/cyan]: {stderr[:100]}")

    def chain_test_healthcheck(self, n_checks: int, n_failures: int) -> None:
        if n_failures == 0:
            self._line(f"  [green]✓[/green] Healthchecks passed ({n_checks} ports)")
        else:
            self._line(f"  [yellow]⚠[/yellow] {n_failures}/{n_checks} healthcheck(s) failed")

    def chain_test_synthesizing(self) -> None:
        self._line(f"  [blue]⚙[/blue] Synthesizing chain procedure…")

    def chain_test_procedure_summary(self, n_steps: int) -> None:
        self._line(f"  [green]✓[/green] Generated chain with [cyan]{n_steps}[/cyan] steps")

    def chain_test_executing(self, attempt: int, max_retries: int) -> None:
        if attempt == 1:
            self._line(f"  [blue]▶[/blue] Executing chain procedure…")
        else:
            self._line(f"  [yellow]⟲[/yellow] Retry {attempt}/{max_retries + 1}…")

    def chain_test_step(self, step_id: str, passed: bool, reason: str = "", action_detail: str = "", stdout: str = "", stderr: str = "") -> None:
        if passed:
            self._line(f"    [dim green]✓[/dim green] [dim]{step_id}[/dim]")
        else:
            self._line(f"    [red]✗[/red] [red]{step_id}[/red]: {reason[:80]}")
            if action_detail:
                self._line(f"      [dim]action:[/dim] {action_detail[:120]}")
            if stdout:
                lines = stdout.strip().split('\n')[:3]  # First 3 lines
                for line in lines:
                    self._line(f"      [dim yellow]out:[/dim yellow] {line[:100]}")
            if stderr:
                lines = stderr.strip().split('\n')[:3]
                for line in lines:
                    self._line(f"      [dim red]err:[/dim red] {line[:100]}")

    def chain_test_resetting(self) -> None:
        self._line(f"  [yellow]⟲[/yellow] Resetting containers for retry…")

    def chain_test_diagnosis(self, diagnosis: str) -> None:
        """Show failure diagnosis (first 200 chars)."""
        lines = diagnosis.strip().split('\n')[:3]  # First 3 lines
        for line in lines:
            self._line(f"  [dim yellow]diagnosis:[/dim yellow] {line[:100]}")

    def chain_test_result(self, passed: bool, reason: str | None = None) -> None:
        if passed:
            self._line("\n[green]✓ Chain test PASSED[/green]")
        else:
            msg = f": {reason}" if reason else ""
            self._line(f"\n[red]✗ Chain test FAILED[/red]{msg}")

    def summary(self, built: int, total: int, skipped: int, out_dir, chain_test=None) -> None:
        self._line(
            f"\n[bold]Summary:[/bold] {built}/{total} entities built · {skipped} skipped"
        )
        if chain_test is not None:
            status = chain_test.status.value if hasattr(chain_test, "status") else str(chain_test)
            colour = "green" if status == "PASSED" else "red"
            self._line(f"  chain test: [{colour}]{status}[/{colour}]")
        if out_dir is not None:
            self._line(f"  output: {out_dir}")

    # ========== Construction Crew UI ==========

    def crew_phase(self, phase_name: str) -> None:
        """Show a construction phase header."""
        self._line(f"\n[bold cyan]━━━ {phase_name} ━━━[/bold cyan]")

    def crew_agent_start(self, agent_name: str, task: str = "") -> None:
        """Show agent starting."""
        task_str = f": {task}" if task else ""
        self._line(f"[bold blue]⚙[/bold blue]  {agent_name}{task_str}")

    def crew_agent_done(self, agent_name: str, duration: float) -> None:
        """Show agent completion."""
        self._line(f"  [green]✓[/green] {agent_name} complete ({duration:.1f}s)")

    def crew_plan_summary(self, runtime: str, summary: str, entry: str, file_tree: str = "") -> None:
        """Show engineer plan summary."""
        self._line(f"\n[bold]Engineer Plan:[/bold]")
        self._line(f"  [cyan]Runtime:[/cyan]     {runtime}")
        self._line(f"  [cyan]Summary:[/cyan]     {summary[:120]}{'...' if len(summary) > 120 else ''}")
        self._line(f"  [cyan]Entry:[/cyan]       {entry[:120]}{'...' if len(entry) > 120 else ''}")
        if file_tree:
            self._line(f"  [cyan]Structure:[/cyan]")
            for line in file_tree.split('\n'):
                if line.strip():
                    self._line(f"    {line}")

    def crew_source_snippet(self, filename: str, lines: list[str], lang: str = "") -> None:
        """Show a syntax-highlighted source code snippet."""
        try:
            from rich.syntax import Syntax
            if self._rich is not None:
                # Determine language from filename if not provided
                if not lang:
                    if filename.endswith('.js'):
                        lang = 'javascript'
                    elif filename.endswith('.py'):
                        lang = 'python'
                    elif filename.endswith('.php'):
                        lang = 'php'
                    elif filename.endswith('.sh'):
                        lang = 'bash'
                    elif filename.endswith('.sql'):
                        lang = 'sql'
                    elif filename.endswith('.yaml') or filename.endswith('.yml'):
                        lang = 'yaml'

                # Show first N lines with syntax highlighting
                snippet = '\n'.join(lines)
                syntax = Syntax(snippet, lang, theme="monokai", line_numbers=True)
                self._rich.print(f"\n[bold cyan]{filename}[/bold cyan] (showing {len(lines)} lines)")
                self._rich.print(syntax)
            else:
                self._line(f"\n[cyan]{filename}[/cyan]")
                for line in lines:
                    self._line(f"  {line}")
        except Exception:
            # Fallback to plain text
            self._line(f"\n[cyan]{filename}[/cyan]")
            for i, line in enumerate(lines, 1):
                self._line(f"  [dim]{i:3d}[/dim] {line}")

    def crew_procedure_summary(self, steps: list) -> None:
        """Show attack procedure summary."""
        self._line(f"\n[bold magenta]Attack Procedure:[/bold magenta] {len(steps)} step(s)")
        for step in steps[:5]:  # Show first 5 steps
            step_id = step.get('step_id', '?')
            action_type = step.get('action', {}).get('type', '?')
            self._line(f"  [yellow]•[/yellow] {step_id} ([dim]{action_type}[/dim])")
        if len(steps) > 5:
            self._line(f"  [dim]... and {len(steps) - 5} more steps[/dim]")

    def crew_deploy_start(self) -> None:
        """Show deploy phase start."""
        self._line(f"\n[bold blue]→[/bold blue] Deploying to container...")

    def crew_deploy_result(self, success: bool, duration: float = 0) -> None:
        """Show deploy result."""
        if success:
            self._line(f"  [green]✓[/green] Deploy successful ({duration:.1f}s)")
        else:
            self._line(f"  [red]✗[/red] Deploy failed ({duration:.1f}s)")

    def crew_test_start(self, attempt: int = 1) -> None:
        """Show L2 test start."""
        attempt_str = f" (attempt {attempt})" if attempt > 1 else ""
        self._line(f"\n[bold blue]→[/bold blue] Running attack procedure{attempt_str}...")

    def crew_test_step(self, step_id: str, passed: bool, reason: str = "") -> None:
        """Show individual test step result."""
        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        reason_str = f": {reason[:80]}" if reason else ""
        self._line(f"  [{status}] {step_id}{reason_str}")

    def crew_test_result(self, passed: bool, total_steps: int, passed_steps: int) -> None:
        """Show overall test result."""
        if passed:
            self._line(f"  [bold green]✓ All {total_steps} steps passed[/bold green]")
        else:
            self._line(f"  [bold red]✗ {passed_steps}/{total_steps} steps passed[/bold red]")

    def crew_retry(self, category: str, attempt: int, max_attempts: int) -> None:
        """Show retry attempt."""
        self._line(f"\n[yellow]⟲[/yellow] Retry {attempt}/{max_attempts} ([dim]{category}[/dim])")
