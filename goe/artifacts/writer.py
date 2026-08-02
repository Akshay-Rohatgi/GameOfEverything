"""Artifact file writers for GoE v2 pipeline runs."""

from __future__ import annotations

import difflib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.construction_crew.orchestrator import CrewResult
    from goe.metrics.collector import MetricsSession


# ---------------------------------------------------------------------------
# Path sanitization
# ---------------------------------------------------------------------------

def _sanitize_path(fname: str) -> str:
    """Return a safe relative path for an LLM-supplied source filename.

    Guards against path traversal (../../etc/passwd) and absolute paths.
    Raises ValueError if the name is empty, absolute, or would escape the destination.
    """
    # Reject absolute paths before normalization (os.path.normpath would preserve /)
    if os.path.isabs(fname):
        raise ValueError(f"Unsafe source filename rejected (absolute path): {fname!r}")

    normalized = os.path.normpath(fname)
    if not normalized or normalized.startswith(".."):
        raise ValueError(f"Unsafe source filename rejected: {fname!r}")
    # Reject any .. component anywhere in the path
    parts = Path(normalized).parts
    if ".." in parts:
        raise ValueError(f"Unsafe source filename rejected: {fname!r}")
    return normalized


# ---------------------------------------------------------------------------
# Crew artifact writers
# ---------------------------------------------------------------------------

def _write_entity_files(entity_dir: Path, crew: "CrewResult") -> list[str]:
    """Write all files from a CrewResult into entity_dir.

    Returns a list of relative paths (relative to entity_dir) that were written.
    """
    import yaml

    written: list[str] = []

    # App source files
    for fname, content in crew.artifact.source_files.items():
        safe = _sanitize_path(fname)
        dest = entity_dir / "app" / safe
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written.append(str(Path("app") / safe))

    # DB setup SQL (often missing from the tempfile dump — this is the gap we fix)
    if crew.artifact.db_setup:
        db_dir = entity_dir / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "schema.sql").write_text(crew.artifact.db_setup.schema_sql, encoding="utf-8")
        (db_dir / "seed.sql").write_text(crew.artifact.db_setup.seed_sql, encoding="utf-8")
        written.extend(["db/schema.sql", "db/seed.sql"])

    # BuildArtifact metadata (port, app_dir, deps, etc. — not the source files)
    artifact_dict = crew.artifact.model_dump()
    artifact_dict.pop("source_files", None)  # already written as individual files
    (entity_dir / "artifact.json").write_text(
        json.dumps(artifact_dict, indent=2), encoding="utf-8"
    )
    written.append("artifact.json")

    # Attack procedure
    import yaml as _yaml
    proc_yaml = _yaml.dump(crew.procedure.model_dump(), default_flow_style=False)
    (entity_dir / "procedure.yaml").write_text(proc_yaml, encoding="utf-8")
    written.append("procedure.yaml")

    # Architect plan
    (entity_dir / "architect_plan.json").write_text(
        json.dumps(crew.plan.model_dump(), indent=2), encoding="utf-8"
    )
    written.append("architect_plan.json")

    return written


def save_crew_artifacts(
    entity_id: str,
    crew: "CrewResult",
    run_dir: Path,
) -> None:
    """Persist the initial crew result for an entity into run_dir.

    Writes to: run_dir/entities/<entity_id>/{app/, db/, artifact.json,
    procedure.yaml, architect_plan.json}
    """
    entity_dir = run_dir / "entities" / entity_id
    entity_dir.mkdir(parents=True, exist_ok=True)
    _write_entity_files(entity_dir, crew)


def save_attempt_artifacts(
    entity_id: str,
    attempt_n: int,
    crew: "CrewResult",
    diagnosis,
    run_dir: Path,
) -> None:
    """Persist a retry attempt's crew result + diagnosis.

    Writes to: run_dir/entities/<entity_id>/attempts/attempt_<n>/
    """
    attempt_dir = run_dir / "entities" / entity_id / "attempts" / f"attempt_{attempt_n}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    _write_entity_files(attempt_dir, crew)

    # Diagnosis
    diag_dict: dict = {}
    if hasattr(diagnosis, "model_dump"):
        diag_dict = diagnosis.model_dump()
    elif hasattr(diagnosis, "__dict__"):
        diag_dict = {k: str(v) for k, v in diagnosis.__dict__.items()}
    (attempt_dir / "diagnosis.json").write_text(
        json.dumps(diag_dict, indent=2, default=str), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Attempt diff
# ---------------------------------------------------------------------------

def write_attempt_diff(entity_id: str, attempt_n: int, run_dir: Path) -> None:
    """Write a unified diff between attempt_<n-1> and attempt_<n>.

    Diffs procedure.yaml and all app/ source files.
    Output: run_dir/entities/<entity_id>/attempts/attempt_<n-1>_to_<n>.diff

    No-op if either attempt directory does not exist.
    """
    base_dir = run_dir / "entities" / entity_id / "attempts"
    prev_dir = base_dir / f"attempt_{attempt_n - 1}" if attempt_n > 1 else (
        run_dir / "entities" / entity_id
    )
    curr_dir = base_dir / f"attempt_{attempt_n}"

    if not prev_dir.exists() or not curr_dir.exists():
        return

    prev_label = "initial" if attempt_n == 1 else f"attempt_{attempt_n - 1}"
    diff_lines: list[str] = []

    # Collect all files to diff (procedure.yaml + all app/ files)
    for rel in _collect_diffable_files(prev_dir, curr_dir):
        prev_file = prev_dir / rel
        curr_file = curr_dir / rel
        prev_text = prev_file.read_text(encoding="utf-8", errors="replace") if prev_file.exists() else ""
        curr_text = curr_file.read_text(encoding="utf-8", errors="replace") if curr_file.exists() else ""
        if prev_text == curr_text:
            continue
        diff = difflib.unified_diff(
            prev_text.splitlines(keepends=True),
            curr_text.splitlines(keepends=True),
            fromfile=f"{prev_label}/{rel}",
            tofile=f"attempt_{attempt_n}/{rel}",
        )
        diff_lines.extend(diff)

    if not diff_lines:
        diff_lines = [f"# No differences between {prev_label} and attempt_{attempt_n}\n"]

    diff_name = f"attempt_{prev_label}_to_{attempt_n}.diff"
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / diff_name).write_text("".join(diff_lines), encoding="utf-8")


def _collect_diffable_files(dir_a: Path, dir_b: Path) -> list[str]:
    """Return the union of relative paths for procedure.yaml and app/** in two dirs."""
    rel_paths: set[str] = set()
    for d in (dir_a, dir_b):
        if (d / "procedure.yaml").exists():
            rel_paths.add("procedure.yaml")
        app_dir = d / "app"
        if app_dir.exists():
            for f in app_dir.rglob("*"):
                if f.is_file():
                    rel_paths.add(str(f.relative_to(d)))
    return sorted(rel_paths)


# ---------------------------------------------------------------------------
# Transcript writers
# ---------------------------------------------------------------------------

def write_transcripts(session: "MetricsSession", run_dir: Path) -> None:
    """Write LLM conversation history to run_dir/conversations/.

    Produces:
    - conversations/llm_calls.jsonl  — one LLMTranscriptRecord per line (machine)
    - conversations/<root>.md        — human-readable per-agent transcript
    """
    if not session.transcripts:
        return

    conv_dir = run_dir / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)

    # Machine-readable JSONL
    session.transcripts_to_jsonl(conv_dir / "llm_calls.jsonl")

    # Group by agent root for the human-readable Markdown files
    from collections import defaultdict
    by_root: dict[str, list] = defaultdict(list)
    for rec in session.transcripts:
        root = rec.caller.split(".")[0]
        by_root[root].append(rec)

    for root, records in by_root.items():
        _write_agent_transcript_md(conv_dir / f"{root}.md", root, records)


def _write_agent_transcript_md(path: Path, agent_name: str, records: list) -> None:
    """Write a human-readable Markdown file for one agent's conversation."""
    lines: list[str] = [
        f"# Conversation: {agent_name}\n\n",
        f"**Model**: {records[0].model_id if records else 'unknown'}  \n",
        f"**Calls**: {len(records)}\n\n",
    ]

    # System prompt — only render it once (identical across all calls in a conversation)
    if records:
        lines.append("## System Prompt\n\n")
        lines.append("```\n")
        lines.append(records[0].system)
        lines.append("\n```\n\n")

    turn_num = 0
    for rec in records:
        for msg in rec.new_messages:
            turn_num += 1
            role = msg.get("role", "user")
            content = msg.get("content", "")
            lines.append(f"## Turn {turn_num} — `{rec.caller}` ({role})\n\n")
            lines.append(f"**{role}:**\n\n")
            lines.append("```\n")
            lines.append(str(content))
            lines.append("\n```\n\n")

        # Assistant response
        turn_num += 1
        tok_info = f"in {rec.input_tokens} tok / out {rec.output_tokens} tok"
        lines.append(f"## Turn {turn_num} — `{rec.caller}` (assistant, {tok_info})\n\n")
        lines.append("**assistant:**\n\n")
        lines.append("```\n")
        lines.append(rec.response)
        lines.append("\n```\n\n")

    path.write_text("".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def write_manifest(
    run_dir: Path,
    session: "MetricsSession",
    entry_point: str,
    command: str,
    entities: list[dict] | None,
    started_at: str,
    ended_at: str,
) -> None:
    """Write a manifest.json index file for the run.

    The manifest contains: run_id, entry_point, command, timestamps, LLM cost
    summary, conversation list, per-entity status, and the top-level file list.
    Only whitelisted fields are included — goe.toml credentials are never serialized.
    """
    # Discover conversations written
    conv_dir = run_dir / "conversations"
    conversations = []
    if conv_dir.exists():
        conversations = sorted(
            p.stem for p in conv_dir.glob("*.md")
        )

    # Discover top-level files
    top_files = sorted(
        str(p.relative_to(run_dir))
        for p in run_dir.rglob("*")
        if p.is_file()
    )

    manifest = {
        "run_id": run_dir.name,
        "entry_point": entry_point,
        "command": command,
        "started_at": started_at,
        "ended_at": ended_at,
        "llm": session.summary(),
        "conversations": conversations,
        "entities": entities or [],
        "files": top_files,
    }

    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
