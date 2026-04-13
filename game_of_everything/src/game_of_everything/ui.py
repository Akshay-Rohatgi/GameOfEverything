"""GoEConsole — clean, minimal CLI output with full logging to file.

All crewAI/liteLLM noise is redirected to a log file. The terminal shows
only structured progress output via a Rich Console bound to the real stdout.
"""

import io
import logging
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from game_of_everything.models import MappedAtom

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


# Project root for default log directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_VERSION = "0.1.0"


class GoEConsole:
    """Clean CLI output manager. Captures agent noise to log file, shows progress on terminal."""

    def __init__(self, log_dir: Optional[Path] = None):
        # Capture the real stdout/stderr BEFORE any redirection
        self._real_stdout = sys.stdout
        self._real_stderr = sys.stderr
        self._console = Console(file=self._real_stdout, highlight=False)

        # Set up log file
        if log_dir is None:
            log_dir = _PROJECT_ROOT / "output"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._log_path = log_dir / f"{self._timestamp}.log"
        self._log_file = open(self._log_path, "w", buffering=1)  # line-buffered

        # Track active spinner status
        self._active_status: Optional[str] = None
        self._status_handle = None

    @property
    def log_path(self) -> Path:
        return self._log_path

    @property
    def timestamp(self) -> str:
        return self._timestamp

    # ------------------------------------------------------------------
    # Output capture
    # ------------------------------------------------------------------

    @contextmanager
    def capture(self):
        """Redirect sys.stdout and sys.stderr to the log file.

        While active, all print() calls (from crewAI, liteLLM, etc.) go to
        the log file. GoEConsole methods still write to the real terminal.
        """
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        # Also capture logging output
        log_handler = logging.StreamHandler(self._log_file)
        log_handler.setLevel(logging.DEBUG)
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)

        sys.stdout = self._log_file
        sys.stderr = self._log_file
        try:
            yield
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            root_logger.removeHandler(log_handler)

    # ------------------------------------------------------------------
    # Terminal output
    # ------------------------------------------------------------------

    def header(self, request: str) -> None:
        """Print the app header and user request."""
        self._console.print()
        title = Text(" Game of Everything", style="bold")
        title.append(f"  v{_VERSION}", style="dim")
        self._console.print(title)
        self._console.print()
        # Truncate long requests
        display_request = request if len(request) <= 120 else request[:117] + "..."
        self._console.print(f' Request: "{display_request}"')
        self._console.print()

    def scenario_intel(self, targets: list[dict]) -> None:
        """Render the ENVIRONMENTAL INTELLIGENCE panel.

        Each target dict has keys: hostname, attack_vector, goal.
        """
        lines = []
        for i, t in enumerate(targets):
            if i > 0:
                lines.append("")
            hostname = t.get("hostname", "target")
            vector = t.get("attack_vector", "") or t.get("role", "")
            goal = t.get("goal", "") or "—"
            lines.append(f"TARGET: \\[{hostname}]")
            lines.append(f"  Vect: {vector}")
            lines.append(f"  Goal: {goal}")

        panel = Panel(
            "\n".join(lines),
            title="[bold]ENVIRONMENTAL INTELLIGENCE[/bold]",
            border_style="bold white",
            padding=(0, 2),
        )
        self._console.print()
        self._console.print(panel)

    def scenario_kill_chain(self, steps: list[dict]) -> None:
        """Render the PROPOSED KILL CHAIN panel.

        Each step dict has keys: tag, action. Step number is index + 1.
        """
        lines = []
        for i, s in enumerate(steps):
            tag = s.get("tag", "???")
            action = s.get("action", "")
            lines.append(f"{i + 1}. [{tag}] {action}")

        panel = Panel(
            "\n".join(lines),
            title="[bold]PROPOSED KILL CHAIN[/bold]",
            border_style="bold white",
            padding=(0, 2),
        )
        self._console.print()
        self._console.print(panel)

    def status(self, msg: str) -> None:
        """Show a step starting (with spinner)."""
        self._active_status = msg
        # Print the status line — will be followed by step_done or step_fail
        self._console.print(f"  [dim]●[/dim] {msg}", end="\r")

    def step_done(self, msg: str, elapsed: float) -> None:
        """Mark a step as completed with timing."""
        elapsed_str = f"{elapsed:.1f}s"
        # Pad message for alignment
        padded = msg.ljust(38)
        self._console.print(f"  [green]✓[/green] {padded} [dim]{elapsed_str}[/dim]")
        self._active_status = None

    def step_fail(self, msg: str, detail: str = "") -> None:
        """Mark a step as failed."""
        padded = msg.ljust(38)
        self._console.print(f"  [red]✗[/red] {padded} [red]FAIL[/red]")
        if detail:
            self._console.print(f"    [dim]{detail}[/dim]")
        self._active_status = None

    def test_header(self) -> None:
        """Print the testing section header."""
        self._console.print("  [dim]●[/dim] Testing in Docker")

    def display_atom(self, mapped_atom: MappedAtom, verbose=False) -> None:
        """Print a single atom, optionally with its context."""
        name_padded_with_atom = f"[blue]⚛[/blue] {mapped_atom.name}({mapped_atom.parameters})".ljust(80)
        if not verbose:
            self._console.print(f"      {name_padded_with_atom}")
        else:
            self._console.print(f"      {name_padded_with_atom}")
            self._console.print(f"          [dim]{mapped_atom.context}[/dim]".ljust(80))

    def test_result(
        self,
        atom: str,
        l1_pass: bool,
        l2_pass: Optional[bool] = None,
        retries: int = 0,
        is_app: bool = False,
        testing_snippet: Optional[str] = None,
        attack_snippet: Optional[str] = None,
    ) -> None:
        """Show a single atom's test result with command snippets."""
        icon = "[green]✓[/green]" if (l1_pass and (l2_pass is not False)) else "[red]✗[/red]"
        label = f"{atom} (app)" if is_app else atom
        l1 = "[green]✓[/green]" if l1_pass else "[red]✗[/red]"

        if l2_pass is None:
            l2 = "[dim]n/a[/dim]"
        elif l2_pass:
            l2 = "[green]✓[/green]"
        else:
            l2 = "[red]✗[/red]"

        retry_note = f"  [yellow]({retries} retries)[/yellow]" if retries else ""
        name_padded = label.ljust(26)
        self._console.print(f"    {icon} {name_padded} L1 {l1}  L2 {l2}{retry_note}")

        # Show executed commands as dim truncated lines
        for snippet in (testing_snippet, attack_snippet):
            if snippet:
                cmd = snippet.replace('\n', ' ').strip()
                if len(cmd) > 70:
                    cmd = cmd[:70] + "..."
                self._console.print(f"        [dim]> {cmd}[/dim]")

    def test_layer_status(self, atom: str, layer: str, command: str) -> None:
        """Show which test layer is running for an atom with the truncated command.

        Args:
            atom: Atom name being tested.
            layer: "L1" or "L2".
            command: The bash command being executed (will be truncated).
        """
        cmd = command.replace('\n', ' ').strip()
        if len(cmd) > 70:
            cmd = cmd[:70] + "..."
        name_padded = atom.ljust(26)
        self._console.print(f"    [dim]● {name_padded} {layer}: {cmd}[/dim]", end="\r")

    def test_skipped(self, atom: str, reason: str = "upstream failed") -> None:
        """Show a skipped atom."""
        name_padded = atom.ljust(26)
        self._console.print(f"    [dim]- {name_padded} skipped ({reason})[/dim]")

    def test_done(self, elapsed: float) -> None:
        """Close the testing section."""
        self._console.print(f"    [dim]{elapsed:.1f}s[/dim]")

    def deploy_status(self, msg: str) -> None:
        """Show EC2 deploy progress."""
        self._console.print(f"  [dim]●[/dim] {msg}", end="\r")

    def deploy_done(self, msg: str, elapsed: float) -> None:
        """Show EC2 deploy completion."""
        elapsed_str = f"{elapsed:.1f}s"
        padded = msg.ljust(38)
        self._console.print(f"  [green]✓[/green] {padded} [dim]{elapsed_str}[/dim]")

    def info(self, msg: str) -> None:
        """Print an informational line to the terminal."""
        self._console.print(f"  {msg}")

    def summary(
        self,
        validated: int,
        total: int,
        skipped: int,
        output_path: Path,
    ) -> None:
        """Print the final summary."""
        self._console.print()
        if validated == total:
            self._console.print(f"  [green]✓[/green] {validated}/{total} atoms validated")
        else:
            self._console.print(
                f"  [yellow]![/yellow] {validated}/{total} atoms validated"
                f" · {skipped} skipped"
            )
        self._console.print(f"  Output:  {output_path}")
        self._console.print(f"  Log:     {self._log_path}")
        self._console.print()

    # ------------------------------------------------------------------
    # Multi-box output (called by PipelineRenderer only)
    # ------------------------------------------------------------------

    def box_phase_started(self, box_id: str, phase: str, color: str = "white") -> None:
        """Show a box phase starting."""
        bid = f"[{color}]{box_id}[/{color}]"
        self._console.print(f"  [dim]●[/dim] {bid.ljust(20 + len(color) * 2 + 5)}  {phase}")

    def box_phase_done(self, box_id: str, phase: str, elapsed: float, color: str = "white") -> None:
        """Show a box phase completed with timing."""
        bid = f"[{color}]{box_id}[/{color}]"
        elapsed_str = f"{elapsed:.1f}s"
        phase_padded = phase.ljust(28)
        self._console.print(f"  [green]✓[/green] {bid.ljust(20 + len(color) * 2 + 5)}  {phase_padded} [dim]{elapsed_str}[/dim]")

    def box_phase_fail(self, box_id: str, phase: str, error: str, color: str = "white") -> None:
        """Show a box phase failure."""
        bid = f"[{color}]{box_id}[/{color}]"
        self._console.print(f"  [red]✗[/red] {bid.ljust(20 + len(color) * 2 + 5)}  {phase}  [red]FAIL[/red]")
        if error:
            self._console.print(f"      [dim]{error[:120]}[/dim]")

    def box_atom(self, box_id: str, atom_name: str, parameters: dict, context: str, color: str = "white") -> None:
        """Display a sequenced atom for a box."""
        params_str = ", ".join(f"{v}" for v in parameters.values()) if parameters else ""
        ctx = context.replace('\n', ' ').strip()
        if len(ctx) > 60:
            ctx = ctx[:60] + "..."
        self._console.print(f"      [blue]⚛[/blue] {atom_name}({params_str}) — [dim]{ctx}[/dim]")

    def box_test_result(
        self,
        box_id: str,
        atom_name: str,
        l1_pass: bool,
        l2_pass: Optional[bool] = None,
        retries: int = 0,
        testing_snippet: Optional[str] = None,
        attack_snippet: Optional[str] = None,
        color: str = "white",
    ) -> None:
        """Show a single atom's test result for a box, with truncated command snippets."""
        icon = "[green]✓[/green]" if (l1_pass and (l2_pass is not False)) else "[red]✗[/red]"
        l1 = "[green]✓[/green]" if l1_pass else "[red]✗[/red]"

        if l2_pass is None:
            l2 = "[dim]n/a[/dim]"
        elif l2_pass:
            l2 = "[green]✓[/green]"
        else:
            l2 = "[red]✗[/red]"

        retry_note = f"  [yellow]({retries} retries)[/yellow]" if retries else ""
        name_padded = atom_name.ljust(26)
        self._console.print(f"      {icon} {name_padded} L1 {l1}  L2 {l2}{retry_note}")

        # Show executed commands as dim truncated lines
        for snippet in (testing_snippet, attack_snippet):
            if snippet:
                cmd = snippet.replace('\n', ' ').strip()
                if len(cmd) > 70:
                    cmd = cmd[:70] + "..."
                self._console.print(f"          [dim]> {cmd}[/dim]")

    def box_test_skipped(self, box_id: str, atom_name: str, color: str = "white") -> None:
        """Show a skipped atom for a box."""
        name_padded = atom_name.ljust(26)
        self._console.print(f"      [dim]- {name_padded} skipped[/dim]")

    def box_done(self, box_id: str, hostname: str, success: bool, script_chars: int = 0, color: str = "white") -> None:
        """Show box completion summary."""
        bid = f"[{color}]{box_id}[/{color}]"
        if success:
            self._console.print(f"    [green]✓[/green] {bid} ({hostname}) — script ready")
        else:
            self._console.print(f"    [red]✗[/red] {bid} ({hostname}) — failed")

    def chain_test_header(self, scenario_name: str, num_probes: int) -> None:
        """Print the chain test section header."""
        self._console.print()
        self._console.print(f"  Chain Test: {scenario_name}")

    def chain_test_result(self, step: int, command: str, passed: bool) -> None:
        """Show a single chain test probe result."""
        icon = "[green]✓[/green]" if passed else "[red]✗[/red]"
        cmd = command.replace('\n', ' ').strip()
        if len(cmd) > 60:
            cmd = cmd[:60] + "..."
        self._console.print(f"    {icon} Step {step}: [dim]{cmd}[/dim]")

    def chain_test_done(self, passed: int, total: int) -> None:
        """Show chain test summary."""
        if passed == total:
            self._console.print(f"    Chain: {passed}/{total} probes passed [green]✓[/green]")
        else:
            self._console.print(f"    Chain: {passed}/{total} probes passed [red]✗[/red]")

    def prompt(self, msg: str) -> str:
        """Interactive prompt — restores real stdout for input()."""
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = self._real_stdout
        sys.stderr = self._real_stderr
        try:
            return input(msg)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def log(self, msg: str) -> None:
        """Write a message to the log file only."""
        self._log_file.write(msg + "\n")
        self._log_file.flush()

    def close(self) -> None:
        """Flush and close the log file."""
        if self._log_file and not self._log_file.closed:
            self._log_file.close()


# test display_atoms:
if __name__ == "__main__":
    console = GoEConsole()
    console.header("Test request for displaying atoms")
    console.status("Testing atom display")
    test_atom = MappedAtom(
        name="suid_bash",
        context="The SUID bit is set on /bin/bash with root as the owning user, allowing any local user to invoke 'bash -p' to obtain an effective root shell, matching a SUID bash privilege escalation Atom.",
        parameters={"binary_path": "/bin/bash"},
    )
    test_atom_2 = MappedAtom(
        name="ssh_login",
        context="SSH login with username 'dthompson' and password 'Harbour.2023'",
        parameters={"username": "dthompson", "password": "Harbour.2023"},
    )
    console.display_atom(test_atom, verbose=True)
    console.display_atom(test_atom_2, verbose=True)
    console.step_done("Testing atom display", elapsed=0.5)