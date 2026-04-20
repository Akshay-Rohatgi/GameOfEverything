"""Checkpoint save/load utilities for GoEFlow.

Each completed step writes a JSON snapshot of GoEState to
output/.checkpoints/<run_id>/<step_name>.json.

box_states is excluded from Pydantic serialization (exclude=True on the field
to avoid crewAI circular-ref issues), so we handle it separately here.
"""

import json
from pathlib import Path
from typing import Optional

from game_of_everything.state import GoEState

# Canonical step order — used to find the "latest" checkpoint in a dir.
STEP_ORDER = [
    "synthesize_scenario",
    "box_pipelines",
    "chain_test",
    "finalize_topology",
    "review_and_fix",
]

_STEP_INDEX = {name: i for i, name in enumerate(STEP_ORDER)}

# Resolve output root relative to this file: src/game_of_everything/ -> project root
_OUTPUT_ROOT = Path(__file__).parent.parent.parent.parent / "output"


def checkpoint_dir(state: GoEState) -> Path:
    """Return the checkpoint directory for this run, creating it if needed."""
    if not state.run_id:
        raise ValueError("GoEState.run_id is not set — cannot determine checkpoint dir")
    d = _OUTPUT_ROOT / ".checkpoints" / state.run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_checkpoint(state: GoEState, step_name: str) -> Path:
    """Serialize GoEState to output/.checkpoints/<run_id>/<step_name>.json.

    box_states (exclude=True in Pydantic) is serialized manually as a nested
    dict so that checkpoint files are self-contained.
    """
    data = state.model_dump()
    # model_dump respects exclude=True and omits box_states; add it back.
    data["box_states"] = {
        box_id: box_state.model_dump()
        for box_id, box_state in state.box_states.items()
    }
    dest = checkpoint_dir(state) / f"{step_name}.json"
    dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return dest


def load_checkpoint(path: Path) -> GoEState:
    """Deserialize a checkpoint file into a GoEState.

    box_states is reconstructed as Dict[str, GoEState] from the nested dict
    written by save_checkpoint.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_box_states = data.pop("box_states", {})
    state = GoEState.model_validate(data)
    state.box_states = {
        box_id: GoEState.model_validate(bs_data)
        for box_id, bs_data in raw_box_states.items()
    }
    return state


def completed_steps(ckpt_dir: Path) -> set:
    """Return the set of step names that have checkpoint files in ckpt_dir."""
    if not ckpt_dir.exists():
        return set()
    return {p.stem for p in ckpt_dir.glob("*.json") if p.stem in _STEP_INDEX}


def find_latest_checkpoint(ckpt_dir: Path) -> Optional[Path]:
    """Return the checkpoint file for the last completed step, or None."""
    done = completed_steps(ckpt_dir)
    if not done:
        return None
    latest_name = max(done, key=lambda n: _STEP_INDEX[n])
    return ckpt_dir / f"{latest_name}.json"
