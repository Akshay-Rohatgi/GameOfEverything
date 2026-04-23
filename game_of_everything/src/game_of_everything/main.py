#!/usr/bin/env python
"""GoE Flow orchestrator — thin delegation to step modules.

Each flow step is defined in its own module under game_of_everything.steps/.
This file wires them together using crewAI's @start()/@listen() decorators,
which must live on methods of a Flow[State] subclass.
"""

import argparse
import logging
import os
import time
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

import game_of_everything.patches  # noqa: F401 — monkey-patches crewAI JSON converter

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
    run_deploy,
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

logging.getLogger('crewai.flow.flow').setLevel(logging.WARNING)

os.environ["GOE_VERSION"] = "0.1.0"
os.environ["OTEL_SDK_DISABLED"] = "true"  # Disable OpenTelemetry to avoid unrelated warnings
os.environ["LOG_LEVEL"] = "ERROR"  # Suppress lower-level logs from CrewAI and dependencies to reduce noise
os.environ["CREWAI_TRACING_ENABLED"] = "false"  # Disable CrewAI's internal tracing to reduce noise
os.environ["CREWAI_VERBOSE"] = "false"  # Disable CrewAI's verbose logging to reduce noise

load_dotenv()


class GoEFlow(Flow[GoEState]):
    def __init__(self, resume_dir: Path | None = None):
        super().__init__(tracing=False)
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

    @start()
    def synthesize_scenario(self):
        if self._should_skip("synthesize_scenario"):
            return
        self.ui.status("Synthesizing scenario")
        t0 = time.monotonic()
        run_synthesize_topology(self.state, self.agents_config, self.tasks_config, ui=self.ui)
        self.ui.step_done("Synthesizing scenario", time.monotonic() - t0)
        save_checkpoint(self.state, "synthesize_scenario")

    @listen(synthesize_scenario)
    def box_pipelines(self):
        """Run the full per-box pipeline for every box in the topology (parallel)."""
        if self._should_skip("box_pipelines"):
            return
        self.ui.status("Running box pipelines")
        t0 = time.monotonic()
        run_box_pipelines(self.state, self.agents_config, self.tasks_config, ui=self.ui)
        self.ui.step_done("Running box pipelines", time.monotonic() - t0)
        save_checkpoint(self.state, "box_pipelines")

    @listen(box_pipelines)
    def chain_test(self):
        """Multi-box: validate end-to-end attack chain. Single-box: no-op."""
        if self._should_skip("chain_test"):
            return
        self.ui.status("Chain testing")
        t0 = time.monotonic()
        run_chain_test(self.state, self.agents_config, self.tasks_config, ui=self.ui)
        self.ui.step_done("Chain testing", time.monotonic() - t0)
        save_checkpoint(self.state, "chain_test")

    @listen(chain_test)
    def finalize_topology(self):
        """Multi-box: write output package. Single-box: no-op."""
        if self._should_skip("finalize_topology"):
            return
        self.ui.status("Finalizing output")
        t0 = time.monotonic()
        run_finalize_topology(self.state, self.agents_config, self.tasks_config, ui=self.ui)
        self.ui.step_done("Finalizing output", time.monotonic() - t0)
        save_checkpoint(self.state, "finalize_topology")

        # Print summary — count both misconfig snippets and custom/preset apps
        all_snippets = self.state.generated_snippets or []
        validated_snippets = sum(1 for s in all_snippets if s.validated)

        # Aggregate across all box states for custom/preset apps
        all_box_states = list(self.state.box_states.values()) or [self.state]
        custom_apps = [a for bs in all_box_states for a in (bs.resolved_custom_apps or [])]
        preset_apps = [a for bs in all_box_states for a in (bs.resolved_preset_apps or [])]
        validated_apps = sum(1 for a in custom_apps + preset_apps if a.validation_passed)
        total_apps = len(custom_apps) + len(preset_apps)

        validated = validated_snippets + validated_apps
        total = len(all_snippets) + total_apps
        skipped = total - validated
        if self.state.output_path:
            self.ui.summary(validated, total, skipped, Path(self.state.output_path))

    @listen(finalize_topology)
    def deploy(self):
        run_deploy(self.state, ui=self.ui)

    def _cleanup(self):
        if hasattr(self, "ui"):
            self.ui.close()


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
    try:
        goe_flow.kickoff()
    finally:
        goe_flow._cleanup()


def plot():
    goe_flow = GoEFlow()
    goe_flow.plot("goe_flow.png")


if __name__ == "__main__":
    kickoff()
