"""Workflow artifact persistence for GoE v2 pipeline runs.

Provides opt-in saving of:
- LLM conversation history (full system + turns + responses)
- Generated app source files (BuildArtifact.source_files + DB schema/seed)
- Attack procedures (Procedure YAML)
- Architect plans (ArchitectPlan JSON)
- Per-attempt retry artifacts with diffs between attempts
- Per-run manifest.json summarising what ran

Enable via goe.toml [artifacts] section or GOE_SAVE_ARTIFACTS=1 env var.
See artifacts/README.md for the full on-disk layout.
"""

from .writer import save_crew_artifacts, save_attempt_artifacts, write_attempt_diff
from .run import artifact_run

__all__ = [
    "artifact_run",
    "save_crew_artifacts",
    "save_attempt_artifacts",
    "write_attempt_diff",
]
