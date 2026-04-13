#!/usr/bin/env python
"""GoE Flow orchestrator — thin delegation to step modules.

Each flow step is defined in its own module under game_of_everything.steps/.
This file wires them together using crewAI's @start()/@listen() decorators,
which must live on methods of a Flow[State] subclass.
"""

import os
import time
import yaml
import logging
from pathlib import Path


from crewai.flow import Flow, listen, start
from crewai.events.event_context import (
    _event_context_config,
    EventContextConfig,
    MismatchBehavior,
)
from dotenv import load_dotenv

import game_of_everything.patches  # noqa: F401 — monkey-patches crewAI JSON converter

from game_of_everything.state import GoEState
from game_of_everything.ui import GoEConsole
from game_of_everything.steps import (
    run_synthesize_scenario,
    run_resolve_custom_apps,
    run_engineer_requirements,
    run_generate_implementation,
    run_test_snippets,
    run_finalize_script,
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

logging.getLogger('crewai').setLevel(logging.WARNING)

os.environ["GOE_VERSION"] = "0.1.0"
os.environ["OTEL_SDK_DISABLED"] = "true"  # Disable OpenTelemetry to avoid unrelated warnings
os.environ["LOG_LEVEL"] = "ERROR" # Suppress lower-level logs from CrewAI and dependencies to reduce noise
os.environ["CREWAI_TRACING_ENABLED"] = "false"  # Disable CrewAI's internal tracing to reduce noise

load_dotenv()


class GoEFlow(Flow[GoEState]):
    def __init__(self):
        super().__init__()
        config_dir = Path(__file__).parent / "config"
        with open(config_dir / "agents.yaml", "r") as f:
            self.agents_config = yaml.safe_load(f)
        with open(config_dir / "tasks.yaml", "r") as f:
            self.tasks_config = yaml.safe_load(f)
        self.ui = GoEConsole()
        if hasattr(self, 'console'):
            self.console_quiet = True  # Suppress CrewAI's default console output since we're using our own

    @start()
    def synthesize_scenario(self):
        self.ui.status("Synthesizing scenario")
        t0 = time.monotonic()
        run_synthesize_scenario(self.state, self.agents_config, self.tasks_config, ui=self.ui)
        self.ui.step_done("Synthesizing scenario", time.monotonic() - t0)

    @listen(synthesize_scenario)
    def resolve_custom_apps(self):
        vectors = (
            self.state.synthesized_scenario.custom_vectors
            if self.state.synthesized_scenario
            else []
        )
        label = f"Resolving custom apps ({len(vectors)})" if vectors else "Resolving custom apps"
        if not vectors:
            return
        self.ui.status(label)
        t0 = time.monotonic()
        run_resolve_custom_apps(self.state, ui=self.ui)
        self.ui.step_done(label, time.monotonic() - t0)

    @listen(resolve_custom_apps)
    def engineer_requirements(self):
        self.ui.status("Engineering requirements")
        t0 = time.monotonic()
        run_engineer_requirements(self.state, self.agents_config, self.tasks_config, ui=self.ui)
        self.ui.step_done("Engineering requirements", time.monotonic() - t0)

    @listen(engineer_requirements)
    def generate_implementation(self):
        n = len(self.state.sequenced_request) if self.state.sequenced_request else 0
        label = f"Generating snippets ({n} atoms)"
        self.ui.status(label)
        t0 = time.monotonic()
        run_generate_implementation(self.state, self.agents_config, self.tasks_config, ui=self.ui)
        self.ui.step_done(label, time.monotonic() - t0)

    @listen(generate_implementation)
    def test_snippets(self):
        run_test_snippets(self.state, self.agents_config, self.tasks_config, ui=self.ui)

    @listen(test_snippets)
    def finalize_script(self):
        self.ui.status("Finalizing script")
        t0 = time.monotonic()
        run_finalize_script(self.state, self.agents_config, self.tasks_config, ui=self.ui)
        self.ui.step_done("Finalizing script", time.monotonic() - t0)

        # Print summary
        all_snippets = self.state.generated_snippets or []
        validated = sum(1 for s in all_snippets if s.validated)
        skipped = len(all_snippets) - validated
        if self.state.output_path:
            self.ui.summary(validated, len(all_snippets), skipped, Path(self.state.output_path))

    @listen(finalize_script)
    def deploy(self):
        run_deploy(self.state, ui=self.ui)

    def _cleanup(self):
        if hasattr(self, "ui"):
            self.ui.close()


def kickoff():
    goe_flow = GoEFlow()
    try:
        goe_flow.kickoff()
    finally:
        goe_flow._cleanup()


def plot():
    goe_flow = GoEFlow()
    goe_flow.plot("goe_flow.png")


if __name__ == "__main__":
    kickoff()
