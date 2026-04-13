"""Step: Per-box pipeline — run all boxes in parallel.

Each box in the topology runs the full atom pipeline (resolve_custom_apps →
engineer_requirements → generate_implementation → test_snippets →
finalize_script) inside a ThreadPoolExecutor so boxes execute concurrently.

Each box receives an isolated GoEState populated with its own misconfig_scope
plus a cross-box dependency map injected into the scope text. The dependency
map describes shared credentials and neighbouring services so the per-box
engineer_requirements step can sequence atoms correctly (e.g. install MySQL
before creating DB users that another box depends on).

After all boxes finish, shared-secret credentials are validated against the
produced deploy scripts and any mismatches are surfaced as warnings.
"""

from concurrent.futures import ThreadPoolExecutor, wait, Future, ALL_COMPLETED
from datetime import datetime
from pathlib import Path
from typing import Optional, Set
import threading

import rich

from game_of_everything.models import (
    BoxDefinition,
    NetworkTopology,
    SynthesizedScenario,
)
from game_of_everything.state import GoEState
from game_of_everything.steps.resolve_custom_apps import run_resolve_custom_apps
from game_of_everything.steps.engineer_requirements import run_engineer_requirements
from game_of_everything.steps.generate_implementation import run_generate_implementation
from game_of_everything.steps.test_snippets import run_test_snippets
from game_of_everything.steps.finalize_script import run_finalize_script
from game_of_everything.tools.test_environment import TestEnvironmentTool
from game_of_everything.topology_utils import validate_deploy_script_credentials


# ---------------------------------------------------------------------------
# Per-box logger
# ---------------------------------------------------------------------------

_BOX_COLORS = ["cyan", "magenta", "yellow", "green", "blue", "red"]


class _BoxLog:
    """Color-coded, prefixed logger for one box pipeline.

    Writes phase boundaries and key events to the main console with a
    box_id prefix and a unique color so interleaved parallel output can
    be visually attributed. All output is also written to a per-box log
    file for post-run review.
    """

    def __init__(self, box_id: str, hostname: str, log_path: Path, color: str) -> None:
        self._id = box_id
        self._hostname = hostname
        self._color = color
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = log_path.open("w", encoding="utf-8", buffering=1)

    # ------------------------------------------------------------------ public

    def header(self, title: str) -> None:
        """Print a top-level box banner (start of pipeline)."""
        bar = "═" * 68
        rich.print(f"[bold {self._color}]{bar}[/bold {self._color}]")
        rich.print(f"[bold {self._color}]  {self._tag}  {title}[/bold {self._color}]")
        rich.print(f"[bold {self._color}]{bar}[/bold {self._color}]")
        self._write(f"\n{'═'*68}\n  {self._id} | {title}\n{'═'*68}")

    def phase(self, name: str) -> None:
        """Print a phase-start line with a timestamp."""
        ts = datetime.now().strftime("%H:%M:%S")
        rich.print(
            f"[{self._color}]┌─ {self._tag}[/{self._color}] "
            f"[bold]{name}[/bold]  [dim]{ts}[/dim]"
        )
        self._write(f"\n── {self._id} ► {name}  {ts}")

    def phase_done(self, name: str) -> None:
        """Print a phase-completion line."""
        rich.print(f"[{self._color}]└─ {self._tag} {name} ✓[/{self._color}]")
        self._write(f"── {self._id} ✓ {name}")

    def info(self, msg: str) -> None:
        """Print a general info line prefixed with the box tag."""
        rich.print(f"[{self._color}]{self._tag}[/{self._color}] {msg}")
        self._write(f"[{self._id}] {msg}")

    def error(self, msg: str) -> None:
        """Print an error line."""
        rich.print(f"[bold red]{self._tag}[/bold red] [red]{msg}[/red]")
        self._write(f"[{self._id}] ERROR: {msg}")

    def close(self) -> None:
        self._fh.flush()
        self._fh.close()

    # ----------------------------------------------------------------- private

    @property
    def _tag(self) -> str:
        return f"[{self._id}]"

    def _write(self, msg: str) -> None:
        self._fh.write(msg + "\n")
        self._fh.flush()


# ---------------------------------------------------------------------------
# Cross-box context builder
# ---------------------------------------------------------------------------

def _build_cross_box_context(box: BoxDefinition, topology: NetworkTopology) -> str:
    """Return a structured text block describing secrets, pivots, and dependent
    services that touch *box*.

    This block is appended to misconfig_scope so the per-box LLM agent knows
    about shared credentials, adjacent boxes, and service dependencies without
    inventing its own values. Returns "" when nothing references this box.
    """
    lines: list[str] = []

    # Secrets where this box is the source (attacker discovers the cred here)
    for s in topology.shared_secrets:
        if s.source_box == box.box_id:
            lines.append(
                f'Secret "{s.key}" (EXPOSED HERE): '
                f'value="{s.value}", user={s.target_user}, '
                f'access={s.access_method}, target_box={s.target_box} '
                f'— {s.description}'
            )

    # Secrets where this box is the target (cred grants access to this box)
    for s in topology.shared_secrets:
        if s.target_box == box.box_id:
            lines.append(
                f'Secret "{s.key}" (GRANTS ACCESS HERE): '
                f'value="{s.value}", user={s.target_user}, '
                f'access={s.access_method}, source_box={s.source_box} '
                f'— {s.description}'
            )

    # Outbound pivots (attacker leaves this box)
    for p in topology.pivots:
        if p.from_box == box.box_id:
            lines.append(
                f'Pivot OUT: {p.from_box} -> {p.to_box}, '
                f'method={p.method}, secret_ref={p.secret_ref} '
                f'— {p.description}'
            )

    # Inbound pivots (attacker arrives at this box)
    for p in topology.pivots:
        if p.to_box == box.box_id:
            lines.append(
                f'Pivot IN: {p.from_box} -> {p.to_box}, '
                f'method={p.method}, secret_ref={p.secret_ref} '
                f'— {p.description}'
            )

    # Neighbouring services — what other boxes expose (service dependency map)
    neighbours = [
        b for b in topology.boxes
        if b.box_id != box.box_id and b.services
    ]
    if neighbours:
        for nb in neighbours:
            lines.append(
                f'Neighbouring box "{nb.box_id}" ({nb.hostname}) exposes: {chr(44).join(nb.services)}'
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Virtual state construction
# ---------------------------------------------------------------------------

def _make_virtual_state(
    box: BoxDefinition,
    raw_request: str,
    cross_box_context: str = "",
) -> GoEState:
    """Construct a GoEState scoped to a single box.

    Downstream steps see a normal GoEState and have no awareness of the
    multi-box context.
    """
    if cross_box_context:
        misconfig_scope = (
            box.misconfig_scope
            + "\n\n--- CROSS-BOX CONSTRAINTS (do not change these values) ---\n"
            + cross_box_context
        )
    else:
        misconfig_scope = box.misconfig_scope

    return GoEState(
        raw_request=raw_request,
        synthesized_scenario=SynthesizedScenario(
            narrative=box.role,
            attack_narrative="",
            shared_resources=[],
            explicit_decisions=[],
            misconfig_scope=misconfig_scope,
            custom_app_scope=box.custom_app_scope,
            custom_vectors=box.custom_vectors,
        ),
    )


# ---------------------------------------------------------------------------
# Per-box validation helper
# ---------------------------------------------------------------------------

def _box_has_valid_output(box_state: GoEState) -> bool:
    """True if the box produced at least one validated snippet or custom app."""
    validated_snippets = [
        s for s in (box_state.generated_snippets or []) if s.validated
    ]
    validated_apps = [
        a for a in box_state.resolved_custom_apps if a.validation_passed
    ]
    return bool(validated_snippets or validated_apps)


# ---------------------------------------------------------------------------
# Per-box pipeline
# ---------------------------------------------------------------------------

def run_box_pipeline(
    box: BoxDefinition,
    raw_request: str,
    agents_config: dict,
    tasks_config: dict,
    topology: Optional[NetworkTopology] = None,
    log: Optional[_BoxLog] = None,
) -> GoEState:
    """Run the full atom pipeline for a single box.

    Uses a scoped TestEnvironmentTool so each box's Docker containers are
    named uniquely (goe_{box_id}_target, etc.) and don't collide with other
    boxes running concurrently.

    A _BoxLog is used for all phase-boundary output so that interleaved
    parallel logs can be visually attributed to their box.

    Returns the populated GoEState for this box.
    """
    if log is None:
        log = _BoxLog(box.box_id, box.hostname, Path("output/.logs") / f"{box.box_id}.log", "cyan")

    cross_box_context = _build_cross_box_context(box, topology) if topology else ""
    state = _make_virtual_state(box, raw_request, cross_box_context)
    env = TestEnvironmentTool(scope=box.box_id, hostname=box.hostname)

    log.header(f"{box.box_id} ({box.hostname})")

    log.phase("resolve_custom_apps")
    run_resolve_custom_apps(state)
    log.phase_done("resolve_custom_apps")

    log.phase("engineer_requirements")
    run_engineer_requirements(state, agents_config, tasks_config, box_id=box.box_id)
    log.phase_done("engineer_requirements")

    log.phase("generate_implementation")
    run_generate_implementation(state, agents_config, tasks_config, box_id=box.box_id, target_hostname=box.hostname)
    log.phase_done("generate_implementation")

    log.phase("test_snippets — setting up environment")
    env.setup()
    log.info(f"containers ready: {env.target_name} + {env.attacker_name} on {env.network_name}")
    try:
        log.phase("test_snippets")
        run_test_snippets(state, agents_config, tasks_config, env=env, box_id=box.box_id)
        log.phase_done("test_snippets")
    finally:
        log.phase("test_snippets — tearing down environment")
        env.teardown()
        log.info("test environment cleaned up")

    log.phase("finalize_script")
    run_finalize_script(state, agents_config, tasks_config, skip_disk_write=True)
    log.phase_done("finalize_script")

    log.close()
    return state


# ---------------------------------------------------------------------------
# Main step
# ---------------------------------------------------------------------------

def run_box_pipelines(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
) -> None:
    """Run the full atom pipeline for every box in the topology, in parallel.

    All boxes are submitted to a ThreadPoolExecutor simultaneously. Each box
    gets its own isolated GoEState with a cross-box dependency map injected
    into its misconfig_scope. Results are collected after all threads finish.

    Post-hoc credential validation checks that shared-secret values appear in
    both source and target deploy scripts.

    Args:
        state: Flow state mutated in-place.
        agents_config: Loaded agents.yaml dict.
        tasks_config: Loaded tasks.yaml dict.
    """
    if state.topology is None:
        rich.print("[yellow]run_box_pipelines: no topology on state — skipping.[/yellow]")
        return

    topology = state.topology
    boxes = topology.boxes

    log_dir = Path("output") / ".logs" / (state.run_id or "unnamed")
    rich.print(
        f"\n[bold magenta]Running {len(boxes)} box(es) in parallel[/bold magenta]  "
        f"[dim]logs → {log_dir}[/dim]"
    )

    def _worker(box: BoxDefinition, color: str) -> tuple[str, GoEState]:
        threading.current_thread().name = f"box-{box.box_id}"
        log = _BoxLog(box.box_id, box.hostname, log_dir / f"{box.box_id}.log", color)
        return box.box_id, run_box_pipeline(
            box, state.raw_request or "", agents_config, tasks_config,
            topology=topology, log=log,
        )

    failed_boxes: Set[str] = set()

    box_colors = {box.box_id: _BOX_COLORS[i % len(_BOX_COLORS)] for i, box in enumerate(boxes)}

    with ThreadPoolExecutor(max_workers=len(boxes)) as executor:
        futures: dict[Future, BoxDefinition] = {
            executor.submit(_worker, box, box_colors[box.box_id]): box for box in boxes
        }
        # Explicit barrier — all boxes must finish before results are collected
        # and before chain_test is allowed to start.
        done, _ = wait(futures, return_when=ALL_COMPLETED)

    for future in done:
        box = futures[future]
        try:
            box_id, box_state = future.result()
            state.box_states[box_id] = box_state
            if box_state.final_script:
                state.deploy_scripts[box_id] = box_state.final_script
                rich.print(
                    f"[green]{box_id!r}: deploy script collected "
                    f"({len(box_state.final_script)} chars)[/green]"
                )
            else:
                rich.print(f"[yellow]{box_id!r}: no deploy script produced[/yellow]")
            if not _box_has_valid_output(box_state):
                rich.print(f"[bold red]{box_id!r}: no validated snippets — marking as failed[/bold red]")
                failed_boxes.add(box_id)
        except Exception as exc:
            rich.print(f"[bold red]{box.box_id!r}: pipeline raised exception: {exc}[/bold red]")
            failed_boxes.add(box.box_id)

    # Summary
    rich.print(f"\n[bold magenta]=== BOX PIPELINE SUMMARY ===[/bold magenta]")
    for box in topology.boxes:
        if box.box_id in failed_boxes:
            rich.print(f"  [red]✗[/red] {box.box_id} ({box.hostname}) — FAILED")
        elif box.box_id in state.deploy_scripts:
            rich.print(f"  [green]✓[/green] {box.box_id} ({box.hostname}) — deploy script ready")
        else:
            rich.print(f"  [yellow]?[/yellow] {box.box_id} ({box.hostname}) — no script")

    if failed_boxes:
        rich.print(f"\n[bold red]Failed boxes: {sorted(failed_boxes)}[/bold red]")

    # Post-hoc credential validation
    cred_warnings = validate_deploy_script_credentials(topology, state.deploy_scripts)
    state.credential_warnings = cred_warnings
    if cred_warnings:
        rich.print(f"\n[bold yellow]=== CREDENTIAL WARNINGS ===[/bold yellow]")
        for w in cred_warnings:
            rich.print(f"  [bold yellow]⚠[/bold yellow]  {w}")
