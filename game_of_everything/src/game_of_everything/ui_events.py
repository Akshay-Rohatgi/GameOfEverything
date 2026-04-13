"""Event-driven UI for multi-box parallel pipelines.

Box threads emit typed events onto a shared queue. A single PipelineRenderer
thread consumes them and writes to GoEConsole — the sole terminal writer.
This makes multi-box output thread-safe and cleanly serialized.

Single-box pipelines skip this entirely and use GoEConsole directly.
"""

import io
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

@dataclass
class BoxStarted:
    box_id: str
    hostname: str


@dataclass
class PhaseStarted:
    box_id: str
    phase: str


@dataclass
class PhaseDone:
    box_id: str
    phase: str
    elapsed: float


@dataclass
class PhaseFailed:
    box_id: str
    phase: str
    error: str


@dataclass
class AtomDisplay:
    """Emitted after engineer_requirements — one per sequenced atom."""
    box_id: str
    atom_name: str
    parameters: dict
    context: str


@dataclass
class AtomSectionHeader:
    """Emitted before atom list to print 'Sequenced Atoms:' header."""
    box_id: str


@dataclass
class TestResultEvent:
    """Emitted per atom during test_snippets."""
    box_id: str
    atom_name: str
    l1_pass: bool
    l2_pass: Optional[bool] = None
    retries: int = 0
    is_app: bool = False
    testing_snippet: Optional[str] = None
    attack_snippet: Optional[str] = None


@dataclass
class TestSkippedEvent:
    box_id: str
    atom_name: str
    reason: str = "upstream failed"


@dataclass
class BoxDone:
    box_id: str
    hostname: str
    success: bool
    script_chars: int = 0


@dataclass
class AllBoxesDone:
    """Sentinel to stop the renderer thread."""
    pass


# ---------------------------------------------------------------------------
# Color palette (matches _BoxLog)
# ---------------------------------------------------------------------------

BOX_COLORS = ["cyan", "magenta", "yellow", "green", "blue", "red"]


# ---------------------------------------------------------------------------
# BoxEventEmitter — replaces _BoxLog for terminal output when ui is present
# ---------------------------------------------------------------------------

class BoxEventEmitter:
    """Color-coded emitter for one box pipeline.

    Same interface as _BoxLog (header, phase, phase_done, info, error, close)
    but puts typed events onto a shared queue instead of calling rich.print().
    Still writes to per-box log file directly (thread-local, no contention).
    """

    def __init__(
        self,
        box_id: str,
        hostname: str,
        log_path: Path,
        event_queue: queue.Queue,
    ) -> None:
        self._id = box_id
        self._hostname = hostname
        self._queue = event_queue
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = log_path.open("w", encoding="utf-8", buffering=1)
        self._phase_starts: Dict[str, float] = {}

    # ------------------------------------------------------------------ public

    def header(self, title: str) -> None:
        self._queue.put(BoxStarted(box_id=self._id, hostname=self._hostname))
        self._write(f"\n{'═' * 68}\n  {self._id} | {title}\n{'═' * 68}")

    def phase(self, name: str) -> None:
        self._phase_starts[name] = time.monotonic()
        self._queue.put(PhaseStarted(box_id=self._id, phase=name))
        self._write(f"\n── {self._id} ► {name}")

    def phase_done(self, name: str) -> None:
        t0 = self._phase_starts.pop(name, time.monotonic())
        elapsed = time.monotonic() - t0
        self._queue.put(PhaseDone(box_id=self._id, phase=name, elapsed=elapsed))
        self._write(f"── {self._id} ✓ {name}  ({elapsed:.1f}s)")

    def info(self, msg: str) -> None:
        # Info messages go to log file only — terminal gets phase events
        self._write(f"[{self._id}] {msg}")

    def error(self, msg: str) -> None:
        self._queue.put(PhaseFailed(box_id=self._id, phase="", error=msg))
        self._write(f"[{self._id}] ERROR: {msg}")

    def emit_atoms_header(self) -> None:
        self._queue.put(AtomSectionHeader(box_id=self._id))

    def emit_atom(self, atom_name: str, parameters: dict, context: str) -> None:
        self._queue.put(AtomDisplay(
            box_id=self._id,
            atom_name=atom_name,
            parameters=parameters,
            context=context,
        ))

    def emit_test_result(
        self,
        atom_name: str,
        l1_pass: bool,
        l2_pass: Optional[bool] = None,
        retries: int = 0,
        is_app: bool = False,
        testing_snippet: Optional[str] = None,
        attack_snippet: Optional[str] = None,
    ) -> None:
        self._queue.put(TestResultEvent(
            box_id=self._id,
            atom_name=atom_name,
            l1_pass=l1_pass,
            l2_pass=l2_pass,
            retries=retries,
            is_app=is_app,
            testing_snippet=testing_snippet,
            attack_snippet=attack_snippet,
        ))

    def emit_test_skipped(self, atom_name: str, reason: str = "upstream failed") -> None:
        self._queue.put(TestSkippedEvent(
            box_id=self._id,
            atom_name=atom_name,
            reason=reason,
        ))

    def close(self) -> None:
        self._fh.flush()
        self._fh.close()

    @property
    def log_file(self) -> io.TextIOBase:
        """Return the per-box log file handle for ThreadLocalIO registration."""
        return self._fh

    # ----------------------------------------------------------------- private

    def _write(self, msg: str) -> None:
        self._fh.write(msg + "\n")
        self._fh.flush()


# ---------------------------------------------------------------------------
# ThreadLocalIO — per-thread stdout routing
# ---------------------------------------------------------------------------

class ThreadLocalIO(io.TextIOBase):
    """Routes sys.stdout writes to per-thread log files.

    Each box thread registers its per-box log file via register().
    Unregistered threads (main, renderer) fall back to the default.
    """

    def __init__(self, default: io.TextIOBase) -> None:
        self._default = default
        self._local = threading.local()

    def register(self, file: io.TextIOBase) -> None:
        """Register a file for the current thread."""
        self._local.file = file

    def write(self, s: str) -> int:
        f = getattr(self._local, "file", self._default)
        return f.write(s)

    def flush(self) -> None:
        f = getattr(self._local, "file", self._default)
        f.flush()

    @property
    def encoding(self) -> str:
        return "utf-8"

    def writable(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# PipelineRenderer — consumer thread
# ---------------------------------------------------------------------------

class PipelineRenderer:
    """Consumes events from the queue and renders them via GoEConsole.

    Only the renderer thread touches the console — thread safety guaranteed.
    """

    def __init__(
        self,
        ui: "GoEConsole",
        event_queue: queue.Queue,
        box_ids: List[str],
    ) -> None:
        self._ui = ui
        self._queue = event_queue
        self._colors = {
            bid: BOX_COLORS[i % len(BOX_COLORS)]
            for i, bid in enumerate(box_ids)
        }
        self._thread = threading.Thread(target=self._run, name="ui-renderer", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def wait(self) -> None:
        self._thread.join()

    def _run(self) -> None:
        while True:
            event = self._queue.get()
            if isinstance(event, AllBoxesDone):
                break
            try:
                self._dispatch(event)
            except Exception:
                pass  # Never let renderer crash — worst case: a line is missed

    def _dispatch(self, event: Any) -> None:
        color = "white"
        if hasattr(event, "box_id"):
            color = self._colors.get(event.box_id, "white")

        if isinstance(event, PhaseStarted):
            self._ui.box_phase_started(event.box_id, event.phase, color)
        elif isinstance(event, PhaseDone):
            self._ui.box_phase_done(event.box_id, event.phase, event.elapsed, color)
        elif isinstance(event, PhaseFailed):
            self._ui.box_phase_fail(event.box_id, event.phase, event.error, color)
        elif isinstance(event, AtomSectionHeader):
            self._ui.info(f"      [dim]Sequenced Atoms:[/dim]")
        elif isinstance(event, AtomDisplay):
            self._ui.box_atom(event.box_id, event.atom_name, event.parameters, event.context, color)
        elif isinstance(event, TestResultEvent):
            self._ui.box_test_result(
                event.box_id, event.atom_name,
                event.l1_pass, event.l2_pass, event.retries,
                event.testing_snippet, event.attack_snippet, color,
            )
        elif isinstance(event, TestSkippedEvent):
            self._ui.box_test_skipped(event.box_id, event.atom_name, color)
        elif isinstance(event, BoxDone):
            self._ui.box_done(event.box_id, event.hostname, event.success, event.script_chars, color)
