"""Checkpoint/resume for the single-system run flow.

A flat adaptation of the v1 checkpoint idea: serialize a ``RunState`` to
``output/.checkpoints/<run_id>/state.json`` after planning and after each
entity reaches a terminal state. On resume we skip planning + already-built
entities (the expensive Opus/Sonnet calls) and continue the build loop.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from goe.graph.models import EntityGraph


class BuildOutcomeSnapshot(BaseModel):
    """Serializable form of a successful BuildOutcome (no live Procedure object)."""

    model_config = ConfigDict(strict=True)

    deploy_script: str
    procedure: dict | None = None  # Procedure.model_dump(mode="json")
    outgoing_values: dict[str, dict[str, str]] = {}  # edge_id → {param: value}
    attempts: int = 1


class FailureSnapshot(BaseModel):
    """Serializable record of a failed entity build."""

    model_config = ConfigDict(strict=True)

    reason: str
    category: str | None = None  # DiagnosisCategory value, None for older checkpoints


class RunState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    request: str
    graph: EntityGraph
    completed: dict[str, BuildOutcomeSnapshot] = {}
    # entity_id → failure record. Accepts a bare reason string from older
    # checkpoints (migrated to a FailureSnapshot with category=None on load).
    failed: dict[str, FailureSnapshot] = {}
    # Chain test result — None until the chain test has run for this run.
    # Serialized as {"status": "PASSED"|"FAILED", "broken_edge": ..., "reason": ...}
    chain_test: dict | None = None

    @field_validator("failed", mode="before")
    @classmethod
    def _migrate_failed(cls, value):
        """Migrate legacy ``{eid: reason_string}`` checkpoints to FailureSnapshot."""
        if isinstance(value, dict):
            return {
                eid: ({"reason": v} if isinstance(v, str) else v)
                for eid, v in value.items()
            }
        return value


def checkpoint_dir(out_root: Path, run_id: str) -> Path:
    d = Path(out_root) / ".checkpoints" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_state(state: RunState, out_root: Path) -> Path:
    """Write state.json for this run. Returns the path written."""
    dest = checkpoint_dir(out_root, state.run_id) / "state.json"
    dest.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return dest


def load_state(checkpoint_path: Path) -> RunState:
    """Load a RunState from a checkpoint directory or its state.json file."""
    path = Path(checkpoint_path)
    if path.is_dir():
        path = path / "state.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    # strict=False so the nested EntityGraph's str-enum fields (EdgeType, Runtime)
    # round-trip from JSON, mirroring EntityGraph.from_yaml.
    return RunState.model_validate(data, strict=False)
