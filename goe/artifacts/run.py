"""Artifact run lifecycle — context manager for opt-in artifact capture."""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Generator

if TYPE_CHECKING:
    from goe.metrics.collector import MetricsSession


def _make_timestamp() -> str:
    """ISO-8601 timestamp safe for use as a directory name."""
    return datetime.now().isoformat(timespec="seconds").replace(":", "-")


def flush_run(
    run_dir: Path,
    session: "MetricsSession",
    entry_point: str,
    command: str,
    entities: list[dict] | None,
    started_at: str,
    ended_at: str,
) -> None:
    """Write all end-of-run artifact files: conversations, metrics, manifest."""
    from goe.artifacts.writer import write_transcripts, write_manifest

    # Conversation transcripts (JSONL + per-agent .md)
    write_transcripts(session, run_dir)

    # Metrics JSONL (token/latency records — separate from the full transcripts)
    session.to_jsonl(run_dir / "metrics" / "llm_calls.jsonl")

    # Run manifest (written last so it can index everything)
    write_manifest(
        run_dir=run_dir,
        session=session,
        entry_point=entry_point,
        command=command,
        entities=entities,
        started_at=started_at,
        ended_at=ended_at,
    )


@contextmanager
def artifact_run(
    entry_point: str,
    command: str,
    capture: bool | None = None,
) -> Generator[tuple[Path | None, "MetricsSession"], None, None]:
    """Context manager that starts a metrics/artifact session for a run.

    Yields:
        (run_dir, session) — run_dir is None when artifact capture is disabled.

    Args:
        entry_point: Human label for the entry point, e.g. "build", "planner", "eval".
        command: The full command string for the manifest (e.g. sys.argv joined).
        capture: Override for whether to capture artifacts. If None (default), reads
            from GoEConfig.save_artifacts (goe.toml [artifacts].enabled /
            GOE_SAVE_ARTIFACTS env var).

    Usage::

        with artifact_run("build", " ".join(sys.argv)) as (run_dir, session):
            result = build_entity(entity)
        # Artifacts written to run_dir (or nothing if capture is off)
    """
    from goe.config import GoEConfig
    from goe.metrics import start_session, end_session

    cfg = GoEConfig.get()
    should_capture = capture if capture is not None else cfg.save_artifacts
    started_at = datetime.now().isoformat(timespec="seconds")

    if not should_capture:
        # Metrics-only — current behaviour, no file output from this context manager
        session = start_session()
        try:
            yield None, session
        finally:
            end_session()
        return

    # Artifact capture — create a per-run timestamped directory
    run_dir = Path(cfg.artifacts_dir) / _make_timestamp()
    run_dir.mkdir(parents=True, exist_ok=True)

    session = start_session(capture_artifacts=True, run_dir=run_dir)
    try:
        yield run_dir, session
    finally:
        ended_at = datetime.now().isoformat(timespec="seconds")
        ended_session = end_session()
        flush_run(
            run_dir=run_dir,
            session=ended_session,
            entry_point=entry_point,
            command=command,
            entities=None,  # populated by the caller after the yield if needed
            started_at=started_at,
            ended_at=ended_at,
        )
        _print_summary(run_dir, ended_session)


def _print_summary(run_dir: Path, session: "MetricsSession") -> None:
    """Print the 'Artifacts written to ...' line after a run."""
    summary = session.summary()
    total_calls = summary.get("total_calls", 0)
    total_tokens = summary.get("total_tokens", 0)
    transcript_count = len(session.transcripts) if session.transcripts else 0
    print(
        f"\nArtifacts written to: {run_dir}\n"
        f"  LLM calls: {total_calls}  |  total tokens: {total_tokens:,}  |"
        f"  transcripts captured: {transcript_count}"
    )
