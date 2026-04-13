#!/usr/bin/env python
"""GoE Flow orchestrator — thin delegation to step modules.

Each flow step is defined in its own module under game_of_everything.steps/.
This file wires them together using crewAI's @start()/@listen() decorators,
which must live on methods of a Flow[State] subclass.
"""

import argparse
import os
from datetime import datetime
from pathlib import Path

import yaml

from crewai.flow import Flow, listen, start
from crewai.events.event_context import (
    _event_context_config,
    EventContextConfig,
    MismatchBehavior,
)
from dotenv import load_dotenv

from game_of_everything.checkpoint import (
    checkpoint_dir,
    completed_steps,
    find_latest_checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from game_of_everything.state import GoEState
from game_of_everything.ui import GoEConsole
from game_of_everything.steps import (
    run_synthesize_topology,
    run_box_pipelines,
    run_chain_test,
    run_finalize_topology,
)

# Suppress CrewAI internal event-bus pairing warnings (known bug in 1.9.x).
# ToolUsageFinished is emitted without a matching ToolUsageStarted in the
# current version, causing spurious scope-stack mismatch warnings.
_event_context_config.set(
    EventContextConfig(
        mismatch_behavior=MismatchBehavior.SILENT,
        empty_pop_behavior=MismatchBehavior.SILENT,
    )
)

logging.getLogger('crewai').setLevel(logging.WARNING)

os.environ["GOE_VERSION"] = "0.1.0"
os.environ["OTEL_SDK_DISABLED"] = "true"  # Disable OpenTelemetry to avoid unrelated warnings
os.environ["LOG_LEVEL"] = "ERROR"  # Suppress lower-level logs from CrewAI and dependencies to reduce noise
os.environ["CREWAI_TRACING_ENABLED"] = "false"  # Disable CrewAI's internal tracing to reduce noise

load_dotenv()


class GoEFlow(Flow[GoEState]):
    def __init__(self, resume_dir: Path | None = None):
        super().__init__()
        config_dir = Path(__file__).parent / "config"
        with open(config_dir / "agents.yaml", "r") as f:
            self.agents_config = yaml.safe_load(f)
        with open(config_dir / "tasks.yaml", "r") as f:
            self.tasks_config = yaml.safe_load(f)
        self.ui = GoEConsole()
        if hasattr(self, 'console'):
            self.console_quiet = True  # Suppress CrewAI's default console output since we're using our own

        if resume_dir is not None:
            latest = find_latest_checkpoint(resume_dir)
            if latest is None:
                raise ValueError(f"No checkpoint files found in {resume_dir}")
            loaded = load_checkpoint(latest)
            for field_name in GoEState.model_fields:
                setattr(self.state, field_name, getattr(loaded, field_name))
            self.state.box_states = loaded.box_states
            self._resume_dir: Path | None = resume_dir
            print(f"[checkpoint] Resuming run {self.state.run_id} from {latest.name}")
        else:
            self.state.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._resume_dir = None

    def _should_skip(self, step_name: str) -> bool:
        """Return True when resuming and this step already has a checkpoint."""
        if self._resume_dir is None:
            return False
        done = completed_steps(self._resume_dir)
        if step_name in done:
            print(f"[checkpoint] Skipping {step_name} (already completed)")
            return True
        return False

        if resume_dir is not None:
            latest = find_latest_checkpoint(resume_dir)
            if latest is None:
                raise ValueError(f"No checkpoint files found in {resume_dir}")
            loaded = load_checkpoint(latest)
            for field_name in GoEState.model_fields:
                setattr(self.state, field_name, getattr(loaded, field_name))
            self.state.box_states = loaded.box_states
            self._resume_dir: Path | None = resume_dir
            print(f"[checkpoint] Resuming run {self.state.run_id} from {latest.name}")
        else:
            self.state.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._resume_dir = None

    def _should_skip(self, step_name: str) -> bool:
        """Return True when resuming and this step already has a checkpoint."""
        if self._resume_dir is None:
            return False
        done = completed_steps(self._resume_dir)
        if step_name in done:
            print(f"[checkpoint] Skipping {step_name} (already completed)")
            return True
        return False

    @start()
    def synthesize_scenario(self):
        if self._should_skip("synthesize_scenario"):
            return
        run_synthesize_topology(self.state, self.agents_config, self.tasks_config)
        save_checkpoint(self.state, "synthesize_scenario")

    @listen(synthesize_scenario)
    def box_pipelines(self):
        """Run the full per-box pipeline for every box in the topology (parallel)."""
        if self._should_skip("box_pipelines"):
            return
        run_box_pipelines(self.state, self.agents_config, self.tasks_config)
        save_checkpoint(self.state, "box_pipelines")

    @listen(box_pipelines)
    def chain_test(self):
        """Multi-box: validate end-to-end attack chain. Single-box: no-op."""
        if self._should_skip("chain_test"):
            return
        run_chain_test(self.state, self.agents_config, self.tasks_config)
        save_checkpoint(self.state, "chain_test")

    @listen(chain_test)
    def finalize_topology(self):
        """Multi-box: write output package. Single-box: no-op."""
        if self._should_skip("finalize_topology"):
            return
        run_finalize_topology(self.state, self.agents_config, self.tasks_config)
        save_checkpoint(self.state, "finalize_topology")


def kickoff():
    parser = argparse.ArgumentParser(description="Game of Everything")
    parser.add_argument(
        "--resume",
        metavar="CHECKPOINT_DIR",
        default=os.environ.get("GOE_RESUME_DIR"),
        help="Resume from a checkpoint directory (e.g. output/.checkpoints/<run_id>)",
    )
    args, _ = parser.parse_known_args()
    resume_dir = Path(args.resume) if args.resume else None
    goe_flow = GoEFlow(resume_dir=resume_dir)
    goe_flow.kickoff()


def plot():
    goe_flow = GoEFlow()
    goe_flow.plot("goe_flow.png")


if __name__ == "__main__":
    kickoff()
