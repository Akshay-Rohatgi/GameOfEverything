"""Step 0: Synthesize a fully elaborated scenario from the raw user prompt.

Takes the user's natural-language request, reasons about the whole box
(shared resources, credential bridging, escalation paths), and produces
a SynthesizedScenario that downstream steps parse rather than interpret.
"""

import sys
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from crewai import Agent, Task, Crew, Process

from game_of_everything.state import GoEState
from game_of_everything.models import SynthesizedScenario
from game_of_everything.llm_factory import make_llm

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole

# atoms/ lives at project root, two levels above src/game_of_everything/
_ATOMS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "atoms"


def _discover_available_atoms() -> str:
    """Scan both atom directories and return a formatted list by category."""
    misconfig_ids = sorted(p.stem for p in _ATOMS_DIR.glob("*.md"))
    web_vuln_ids = sorted(p.stem for p in (_ATOMS_DIR / "web_vulnerabilities").glob("*.md"))

    lines = []
    if misconfig_ids:
        lines.append("Misconfiguration atoms:")
        lines.extend(f"  - {aid}" for aid in misconfig_ids)
    if web_vuln_ids:
        if lines:
            lines.append("")
        lines.append("Web vulnerability atoms (custom app pipeline only):")
        lines.extend(f"  - {aid}" for aid in web_vuln_ids)

    return "\n".join(lines) if lines else "(no atoms found)"


def run_synthesize_scenario(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
    user_input: Optional[str] = None,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Collect user prompt, synthesize a full scenario, and confirm with the user."""
    # Collect input
    if user_input is None:
        if ui:
            user_input = ui.prompt("  Enter your vulnerable environment request: ")
        else:
            user_input = input("Enter your vulnerable environment request: ")
    state.raw_request = user_input

    if ui:
        ui.header(user_input)
        ui.log(f"Raw request: {user_input}")

    # Build dynamic atom list for the synthesis prompt
    available_atoms = _discover_available_atoms()

    # --- Agent ---
    synthesizer = Agent(
        config=agents_config["scenario_synthesis_agent"],
        llm=make_llm("scenario_synthesis_agent"),
        verbose=False,
    )  # type: ignore

    # --- Task ---
    synthesis_task = Task(
        config=tasks_config["synthesize_scenario_task"],  # type: ignore
        agent=synthesizer,
        output_pydantic=SynthesizedScenario,
    )

    # --- Crew ---
    synthesis_crew = Crew(
        agents=[synthesizer],
        tasks=[synthesis_task],
        process=Process.sequential,
        verbose=False,
        function_calling_llm=make_llm(),
    )

    if ui:
        with ui.capture():
            synthesis_crew.kickoff(inputs={
                "initial_prompt": user_input,
                "available_atoms": available_atoms,
            })
    else:
        synthesis_crew.kickoff(inputs={
            "initial_prompt": user_input,
            "available_atoms": available_atoms,
        })

    scenario: SynthesizedScenario = synthesis_task.output.pydantic  # type: ignore

    # Log full scenario details
    if ui:
        ui.log("\n=== SYNTHESIZED SCENARIO ===")
        ui.log(f"Narrative: {scenario.narrative}")
        ui.log(f"Attack narrative: {scenario.attack_narrative}")
        ui.log(f"Misconfig scope: {scenario.misconfig_scope}")
        if scenario.custom_app_scope:
            ui.log(f"Custom app scope: {scenario.custom_app_scope}")
        if scenario.custom_vectors:
            ui.log(f"Custom vectors: {len(scenario.custom_vectors)}")
            for v in scenario.custom_vectors:
                ui.log(f"  - {v.vuln_atom_id} / {v.attack_chain_goal} / {v.runtime_id}")
        if scenario.shared_resources:
            ui.log(f"Shared resources: {scenario.shared_resources}")
        if scenario.explicit_decisions:
            ui.log(f"Explicit decisions: {scenario.explicit_decisions}")
    else:
        print(f"Scenario synthesized: {scenario.narrative[:100]}...")

    # Confirm with user
    if ui:
        ui.info("")
        ui.info(f"[bold]Narrative:[/bold] {scenario.narrative}...")
        if scenario.custom_vectors:
            ui.info(f"[bold]Custom apps:[/bold] {len(scenario.custom_vectors)}")
        ui.info("")
        response = ui.prompt("  Continue with this scenario? [y/n]: ")
    else:
        response = input("Continue with this scenario? [y/n]: ")

    if response.strip().lower() not in ("y", "yes"):
        if ui:
            ui.info("[red]Scenario rejected. Exiting.[/red]")
        sys.exit(0)

    state.synthesized_scenario = scenario
