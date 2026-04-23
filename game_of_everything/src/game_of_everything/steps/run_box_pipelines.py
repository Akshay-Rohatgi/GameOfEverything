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
from typing import Optional, Set, TYPE_CHECKING
import queue
import sys
import threading

import rich

from game_of_everything.models import (
    BoxDefinition,
    NetworkTopology,
    SynthesizedScenario,
)
from game_of_everything.state import GoEState
from game_of_everything.steps.resolve_custom_apps import run_resolve_custom_apps
from game_of_everything.steps.resolve_preset_apps import run_resolve_preset_apps
from game_of_everything.steps.engineer_requirements import run_engineer_requirements
from game_of_everything.steps.generate_implementation import run_generate_implementation
from game_of_everything.steps.test_snippets import run_test_snippets
from game_of_everything.steps.finalize_script import run_finalize_script
from game_of_everything.tools.test_environment import TestEnvironmentTool
from game_of_everything.topology_utils import validate_deploy_script_credentials
from game_of_everything.ui_events import (
    BoxEventEmitter,
    PipelineRenderer,
    ThreadLocalIO,
    AllBoxesDone,
)

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole


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
        self._suppress_terminal = False
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = log_path.open("w", encoding="utf-8", buffering=1)

    # ------------------------------------------------------------------ public

    def header(self, title: str) -> None:
        """Print a top-level box banner (start of pipeline)."""
        if not self._suppress_terminal:
            bar = "═" * 68
            rich.print(f"[bold {self._color}]{bar}[/bold {self._color}]")
            rich.print(f"[bold {self._color}]  {self._tag}  {title}[/bold {self._color}]")
            rich.print(f"[bold {self._color}]{bar}[/bold {self._color}]")
        self._write(f"\n{'═'*68}\n  {self._id} | {title}\n{'═'*68}")

    def phase(self, name: str) -> None:
        """Print a phase-start line with a timestamp."""
        ts = datetime.now().strftime("%H:%M:%S")
        if not self._suppress_terminal:
            rich.print(
                f"[{self._color}]┌─ {self._tag}[/{self._color}] "
                f"[bold]{name}[/bold]  [dim]{ts}[/dim]"
            )
        self._write(f"\n── {self._id} ► {name}  {ts}")

    def phase_done(self, name: str) -> None:
        """Print a phase-completion line."""
        if not self._suppress_terminal:
            rich.print(f"[{self._color}]└─ {self._tag} {name} ✓[/{self._color}]")
        self._write(f"── {self._id} ✓ {name}")

    def info(self, msg: str) -> None:
        """Print a general info line prefixed with the box tag."""
        if not self._suppress_terminal:
            rich.print(f"[{self._color}]{self._tag}[/{self._color}] {msg}")
        self._write(f"[{self._id}] {msg}")

    def error(self, msg: str) -> None:
        """Print an error line."""
        if not self._suppress_terminal:
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
            preset_vectors=box.preset_vectors,
        ),
    )


# ---------------------------------------------------------------------------
# Per-box validation helper
# ---------------------------------------------------------------------------

def _box_has_valid_output(box_state: GoEState) -> bool:
    """True if the box produced at least one validated snippet, custom app, or preset app."""
    validated_snippets = [
        s for s in (box_state.generated_snippets or []) if s.validated
    ]
    validated_apps = [
        a for a in box_state.resolved_custom_apps if a.validation_passed
    ]
    validated_presets = [
        a for a in box_state.resolved_preset_apps if a.validation_passed
    ]
    return bool(validated_snippets or validated_apps or validated_presets)


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
    emitter: Optional[BoxEventEmitter] = None,
    ui: Optional["GoEConsole"] = None,
) -> GoEState:
    """Run the full atom pipeline for a single box.

    Uses a scoped TestEnvironmentTool so each box's Docker containers are
    named uniquely (goe_{box_id}_target, etc.) and don't collide with other
    boxes running concurrently.

    In multi-box mode, a BoxEventEmitter is used for phase/atom/test events
    (rendered by PipelineRenderer). In single-box mode, ``ui`` is passed
    directly to step functions for the same output as before.

    Returns the populated GoEState for this box.
    """
    if log is None and emitter is None:
        log = _BoxLog(box.box_id, box.hostname, Path("output/.logs") / f"{box.box_id}.log", "cyan")
        if ui:
            # When ui is present (single-box), suppress _BoxLog's rich.print() calls.
            # Phase tracking is handled by the caller / step functions via ui directly.
            log._suppress_terminal = True

    # Convenience: use emitter for phase tracking when available, else _BoxLog
    _log = emitter or log

    cross_box_context = _build_cross_box_context(box, topology) if topology else ""
    state = _make_virtual_state(box, raw_request, cross_box_context)
    env = TestEnvironmentTool(scope=box.box_id, hostname=box.hostname)

    _log.header(f"{box.box_id} ({box.hostname})")

    _log.phase("resolve_custom_apps")
    # Pass ui only in single-box mode (emitter==None); multi-box uses event queue
    run_resolve_custom_apps(state, ui=ui if emitter is None else None)
    _log.phase_done("resolve_custom_apps")

    _log.phase("resolve_preset_apps")
    run_resolve_preset_apps(state, ui=ui if emitter is None else None)
    _log.phase_done("resolve_preset_apps")

    _log.phase("engineer_requirements")
    run_engineer_requirements(state, agents_config, tasks_config, box_id=box.box_id, ui=ui)
    _log.phase_done("engineer_requirements")

    # Emit atom details after engineer_requirements (multi-box only —
    # single-box already displayed atoms inside engineer_requirements via ui)
    if emitter and state.sequenced_request:
        emitter.emit_atoms_header()
        for atom in state.sequenced_request:
            emitter.emit_atom(atom.name, atom.parameters or {}, atom.context or "")

    _log.phase("generate_implementation")
    run_generate_implementation(state, agents_config, tasks_config, box_id=box.box_id, target_hostname=box.hostname, ui=ui)
    _log.phase_done("generate_implementation")

    _log.phase("test_snippets — setting up environment")
    env.setup()
    _log.info(f"containers ready: {env.target_name} + {env.attacker_name} on {env.network_name}")
    try:
        _log.phase("test_snippets")
        # In multi-box mode, run without ui — we emit events after completion.
        # In single-box mode, pass ui directly for real-time test output.
        run_test_snippets(state, agents_config, tasks_config, env=env, box_id=box.box_id, ui=ui)
        _log.phase_done("test_snippets")
    finally:
        _log.phase("test_snippets — tearing down environment")
        env.teardown()
        _log.info("test environment cleaned up")

    _log.phase("finalize_script")
    run_finalize_script(state, agents_config, tasks_config, skip_disk_write=False, ui=ui)
    _log.phase_done("finalize_script")

    _log.close()
    return state


# ---------------------------------------------------------------------------
# Main step
# ---------------------------------------------------------------------------

def run_box_pipelines(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Run the full atom pipeline for every box in the topology, in parallel.

    Single-box topologies skip the event queue and pass ``ui`` directly to step
    functions for identical output to the pre-multi-box UI.

    Multi-box topologies use a shared ``queue.Queue`` with one
    ``BoxEventEmitter`` per thread and a single ``PipelineRenderer`` consumer
    thread that serialises all terminal output through ``GoEConsole``.

    Post-hoc credential validation checks that shared-secret values appear in
    both source and target deploy scripts.
    """
    if state.topology is None:
        if ui:
            ui.info("[yellow]run_box_pipelines: no topology on state — skipping.[/yellow]")
        else:
            rich.print("[yellow]run_box_pipelines: no topology on state — skipping.[/yellow]")
        return

    topology = state.topology
    boxes = topology.boxes
    log_dir = Path("output") / ".logs" / (state.run_id or "unnamed")

    # ------------------------------------------------------------------
    # Single-box: skip event system, pass ui directly
    # ------------------------------------------------------------------
    if len(boxes) == 1:
        box = boxes[0]
        if ui:
            ui.info(f"[dim]Running single-box pipeline for {box.box_id} ({box.hostname})[/dim]")
        box_state = run_box_pipeline(
            box, state.raw_request or "", agents_config, tasks_config,
            topology=topology, ui=ui,
        )
        state.box_states[box.box_id] = box_state
        # Propagate per-box virtual state to top-level so downstream steps
        # (summary, deploy) can find them.
        state.generated_snippets = box_state.generated_snippets
        state.test_results = box_state.test_results
        if box_state.final_script:
            state.deploy_scripts[box.box_id] = box_state.final_script
            state.final_script = box_state.final_script
            state.output_path = box_state.output_path

        # Credential validation still applies even for single-box
        cred_warnings = validate_deploy_script_credentials(topology, state.deploy_scripts)
        state.credential_warnings = cred_warnings
        if cred_warnings and ui:
            ui.info("")
            ui.info("[bold yellow]Credential Warnings:[/bold yellow]")
            for w in cred_warnings:
                ui.info(f"  [bold yellow]⚠[/bold yellow]  {w}")
        return

    # ------------------------------------------------------------------
    # Multi-box: event queue + renderer + ThreadLocalIO
    # ------------------------------------------------------------------
    if ui:
        ui.info(f"\n  Running {len(boxes)} box pipelines...")

    box_ids = [b.box_id for b in boxes]
    event_queue: queue.Queue = queue.Queue()

    # Start renderer (sole terminal writer)
    renderer: Optional[PipelineRenderer] = None
    if ui:
        renderer = PipelineRenderer(ui, event_queue, box_ids)
        renderer.start()

    # Install ThreadLocalIO to route crewAI print() noise to per-box log files
    tlio: Optional[ThreadLocalIO] = None
    saved_stdout = sys.stdout
    saved_stderr = sys.stderr
    if ui:
        tlio = ThreadLocalIO(ui._log_file)
        sys.stdout = tlio
        sys.stderr = tlio

    def _worker(box: BoxDefinition) -> tuple[str, GoEState]:
        threading.current_thread().name = f"box-{box.box_id}"
        log_path = log_dir / f"{box.box_id}.log"

        if ui:
            emitter = BoxEventEmitter(box.box_id, box.hostname, log_path, event_queue)
            # Route this thread's print() to the per-box log file
            if tlio:
                tlio.register(emitter.log_file)
            return box.box_id, run_box_pipeline(
                box, state.raw_request or "", agents_config, tasks_config,
                topology=topology, emitter=emitter,
            )
        else:
            color = _BOX_COLORS[box_ids.index(box.box_id) % len(_BOX_COLORS)]
            log = _BoxLog(box.box_id, box.hostname, log_path, color)
            return box.box_id, run_box_pipeline(
                box, state.raw_request or "", agents_config, tasks_config,
                topology=topology, log=log,
            )

    failed_boxes: Set[str] = set()

    try:
        with ThreadPoolExecutor(max_workers=len(boxes)) as executor:
            futures: dict[Future, BoxDefinition] = {
                executor.submit(_worker, box): box for box in boxes
            }
            done, _ = wait(futures, return_when=ALL_COMPLETED)
    finally:
        # Restore stdout/stderr before any terminal output
        if tlio:
            sys.stdout = saved_stdout
            sys.stderr = saved_stderr

        # Signal renderer to stop and wait for it to drain
        if renderer:
            event_queue.put(AllBoxesDone())
            renderer.wait()

    # Collect results (single-threaded from here)
    for future in done:
        box = futures[future]
        try:
            box_id, box_state = future.result()
            state.box_states[box_id] = box_state
            if box_state.final_script:
                state.deploy_scripts[box_id] = box_state.final_script
            if not _box_has_valid_output(box_state):
                failed_boxes.add(box_id)
        except Exception as exc:
            if ui:
                ui.info(f"  [bold red]{box.box_id}: pipeline raised exception: {exc}[/bold red]")
            else:
                rich.print(f"[bold red]{box.box_id!r}: pipeline raised exception: {exc}[/bold red]")
            failed_boxes.add(box.box_id)

    # Box summary
    if ui:
        ui.info("")
        ui.info("  Box Summary:")
        for box in topology.boxes:
            color = ["cyan", "magenta", "yellow", "green", "blue", "red"][
                box_ids.index(box.box_id) % 6
            ]
            if box.box_id in failed_boxes:
                ui.box_done(box.box_id, box.hostname, False, color=color)
            elif box.box_id in state.deploy_scripts:
                ui.box_done(box.box_id, box.hostname, True,
                            script_chars=len(state.deploy_scripts[box.box_id]), color=color)
            else:
                ui.info(f"    [yellow]?[/yellow] {box.box_id} ({box.hostname}) — no script")
    else:
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
        if ui:
            ui.info("")
            ui.info("[bold yellow]Credential Warnings:[/bold yellow]")
            for w in cred_warnings:
                ui.info(f"  [bold yellow]⚠[/bold yellow]  {w}")
        else:
            rich.print(f"\n[bold yellow]=== CREDENTIAL WARNINGS ===[/bold yellow]")
            for w in cred_warnings:
                rich.print(f"  [bold yellow]⚠[/bold yellow]  {w}")
