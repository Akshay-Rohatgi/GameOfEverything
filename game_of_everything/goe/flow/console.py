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

            self._rich = Console()
        except Exception:  # pragma: no cover - rich is a dependency, but degrade gracefully
            self._rich = None
        self._starts: dict[str, float] = {}

    def _line(self, msg: str) -> None:
        if self._rich is not None:
            self._rich.print(msg)
        else:
            print(msg)

    def header(self, request: str, n_entities: int) -> None:
        self._line(f"\n[bold]GoE run[/bold] — {n_entities} entit"
                   f"{'y' if n_entities == 1 else 'ies'} to build")
        self._line(f"  request: {request}\n")

    def planning(self) -> None:
        self._line("[cyan]Planning…[/cyan]")

    def plan_failed(self, violations: list) -> None:
        self._line("[red]Planning failed:[/red]")
        for v in violations:
            self._line(f"  [{getattr(v, 'check', '?')}] {getattr(v, 'message', v)}")

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

    def summary(self, built: int, total: int, skipped: int, out_dir) -> None:
        self._line(
            f"\n[bold]Summary:[/bold] {built}/{total} entities built · {skipped} skipped"
        )
        if out_dir is not None:
            self._line(f"  output: {out_dir}")
