#!/usr/bin/env python
"""GoE Flow orchestrator — thin delegation to step modules.

Each flow step is defined in its own module under game_of_everything.steps/.
This file wires them together using crewAI's @start()/@listen() decorators,
which must live on methods of a Flow[State] subclass.
"""

import yaml
from pathlib import Path

from crewai.flow import Flow, listen, start
from crewai.events.event_context import (
    _event_context_config,
    EventContextConfig,
    MismatchBehavior,
)
from dotenv import load_dotenv

from game_of_everything.state import GoEState
from game_of_everything.steps import (
    run_engineer_requirements,
    run_generate_implementation,
    run_test_snippets,
    run_finalize_script,
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

load_dotenv()


class GoEFlow(Flow[GoEState]):
    def __init__(self):
        super().__init__()
        config_dir = Path(__file__).parent / "config"
        with open(config_dir / "agents.yaml", "r") as f:
            self.agents_config = yaml.safe_load(f)
        with open(config_dir / "tasks.yaml", "r") as f:
            self.tasks_config = yaml.safe_load(f)

    @start()
    def engineer_requirements(self):
        run_engineer_requirements(self.state, self.agents_config, self.tasks_config)

    @listen(engineer_requirements)
    def generate_implementation(self):
        run_generate_implementation(self.state, self.agents_config, self.tasks_config)

    @listen(generate_implementation)
    def test_snippets(self):
        run_test_snippets(self.state, self.agents_config, self.tasks_config)

    @listen(test_snippets)
    def finalize_script(self):
        run_finalize_script(self.state, self.agents_config, self.tasks_config)


def kickoff():
    goe_flow = GoEFlow()
    goe_flow.kickoff()


def plot():
    goe_flow = GoEFlow()
    goe_flow.plot("goe_flow.png")


if __name__ == "__main__":
    kickoff()
