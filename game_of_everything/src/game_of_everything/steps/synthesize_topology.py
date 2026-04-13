"""Step 0: Topology synthesis — delegates to run_synthesize_scenario.

run_synthesize_scenario now handles both single-box and multi-box requests:
- Single-box: produces a 1-box NetworkTopology from scenario flat fields.
- Multi-box: LLM populates scenario.boxes + scenario.shared_secrets; converted
  to a NetworkTopology with one BoxDefinition per box and shared secrets as the
  per-box dependency map.

After synthesis, state.topology is always set and run_box_pipelines handles
all further processing for each box (in parallel).
"""

from typing import Optional, TYPE_CHECKING

from game_of_everything.state import GoEState

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole

# Reuse helpers from the single-box step.
from game_of_everything.steps.synthesize_scenario import run_synthesize_scenario


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_synthesize_topology(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
    user_input: Optional[str] = None,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Synthesize a NetworkTopology from the user's request.

    Delegates entirely to run_synthesize_scenario, which now handles both
    single-box and multi-box scenarios from a single LLM call:
    - Single-box: produces a 1-box NetworkTopology from flat scenario fields.
    - Multi-box: LLM populates scenario.boxes + scenario.shared_secrets, which
      are converted to a NetworkTopology with per-box dependency context.

    Args:
        state: Flow state mutated in-place.
        agents_config: Loaded agents.yaml dict.
        tasks_config: Loaded tasks.yaml dict.
        user_input: Pre-supplied request; falls back to interactive input().
        ui: Optional GoEConsole for structured output.
    """
    run_synthesize_scenario(state, agents_config, tasks_config, user_input=user_input, ui=ui)

