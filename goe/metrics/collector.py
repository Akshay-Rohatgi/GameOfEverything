"""Metrics collection for LLM calls."""

from contextvars import ContextVar
from dataclasses import dataclass, asdict, field
from pathlib import Path
import json
import time
import uuid


@dataclass
class LLMCallRecord:
    """Record of a single LLM API call."""
    call_id: str
    timestamp: float
    caller: str
    model_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


@dataclass
class LLMTranscriptRecord:
    """Full record of one LLM call including the conversation content.

    Only captured when a MetricsSession is started with capture_artifacts=True.
    new_messages contains only the turns added since the last call from the same
    agent root (caller.split(".")[0]), so consecutive calls from the same agent
    conversation (e.g. "developer" → "developer.self_review") produce non-overlapping
    deltas that reconstruct the full conversation when concatenated in order.
    """
    call_id: str       # same uuid as the corresponding LLMCallRecord
    timestamp: float
    caller: str        # e.g. "developer", "developer.self_review", "attacker.retry"
    model_id: str
    system: str        # full system prompt
    new_messages: list  # only the turns added since the previous call from this agent root
    response: str      # assistant text returned
    input_tokens: int
    output_tokens: int


class MetricsSession:
    """Collects LLM call metrics during a pipeline run."""

    def __init__(self, capture_artifacts: bool = False, run_dir: Path | None = None):
        self.records: list[LLMCallRecord] = []
        self.start_time = time.time()
        # Artifact capture — only allocated when explicitly requested
        self.transcripts: list[LLMTranscriptRecord] | None = [] if capture_artifacts else None
        self.artifact_run_dir: Path | None = run_dir
        self._last_msg_count: dict[str, int] = {}

    def record(self, rec: LLMCallRecord) -> None:
        """Add an LLM call record."""
        self.records.append(rec)

    def record_transcript(
        self,
        call_id: str,
        timestamp: float,
        caller: str,
        model_id: str,
        system: str,
        messages: list,
        response: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Capture the full conversation content for one LLM call.

        Only active when this session was started with capture_artifacts=True.
        Handles the de-duplication of cumulative message lists: developer.py and
        attacker.py grow one 'messages' list across multiple calls (generate →
        self_review → retry). This method stores only the delta (new turns) since
        the last call from the same agent root so conversations don't repeat.
        """
        if self.transcripts is None:
            return

        # Key on the caller root (e.g. "developer" for "developer.self_review")
        root = caller.split(".")[0]
        prev = self._last_msg_count.get(root, 0)

        # Reset if the new messages list is shorter — this agent started a fresh
        # conversation (e.g. attacker.fix_procedure builds a new single-turn list).
        if len(messages) <= prev:
            prev = 0

        new_messages = list(messages[prev:])
        self._last_msg_count[root] = len(messages)

        self.transcripts.append(LLMTranscriptRecord(
            call_id=call_id,
            timestamp=timestamp,
            caller=caller,
            model_id=model_id,
            system=system,
            new_messages=new_messages,
            response=response,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ))

    def summary(self) -> dict:
        """Return aggregated statistics."""
        if not self.records:
            return {
                "total_calls": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "total_latency_ms": 0.0,
                "avg_latency_ms": 0.0,
                "calls_by_caller": {},
            }

        total_input = sum(r.input_tokens for r in self.records)
        total_output = sum(r.output_tokens for r in self.records)
        total_latency = sum(r.latency_ms for r in self.records)

        # Group by caller
        by_caller = {}
        for rec in self.records:
            if rec.caller not in by_caller:
                by_caller[rec.caller] = {
                    "count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "latency_ms": 0.0,
                }
            by_caller[rec.caller]["count"] += 1
            by_caller[rec.caller]["input_tokens"] += rec.input_tokens
            by_caller[rec.caller]["output_tokens"] += rec.output_tokens
            by_caller[rec.caller]["latency_ms"] += rec.latency_ms

        return {
            "total_calls": len(self.records),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "total_latency_ms": total_latency,
            "avg_latency_ms": total_latency / len(self.records),
            "calls_by_caller": by_caller,
        }

    def to_jsonl(self, path: Path) -> None:
        """Write all LLM call metric records as JSON Lines."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for rec in self.records:
                f.write(json.dumps(asdict(rec)) + "\n")

    def transcripts_to_jsonl(self, path: Path) -> None:
        """Write all transcript records as JSON Lines (no-op when not capturing)."""
        if not self.transcripts:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for rec in self.transcripts:
                f.write(json.dumps(asdict(rec)) + "\n")


# Module-level context var for opt-in metrics collection
_active_session: ContextVar[MetricsSession | None] = ContextVar(
    "metrics_session", default=None
)


def get_session() -> MetricsSession | None:
    """Get the currently active metrics session, if any."""
    return _active_session.get()


def start_session(
    capture_artifacts: bool = False,
    run_dir: Path | None = None,
) -> MetricsSession:
    """Start a new metrics session and make it active.

    Args:
        capture_artifacts: If True, capture full conversation history in addition
            to token/latency metrics. Also stores run_dir on the session so that
            build_entity can look it up to persist generated files.
        run_dir: The per-run output directory (required when capture_artifacts=True;
            stored as session.artifact_run_dir for downstream consumers).
    """
    session = MetricsSession(capture_artifacts=capture_artifacts, run_dir=run_dir)
    _active_session.set(session)
    return session


def end_session() -> MetricsSession:
    """End the current session and return it."""
    session = _active_session.get()
    _active_session.set(None)
    return session
