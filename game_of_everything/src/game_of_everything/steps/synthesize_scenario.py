"""Step 0: Synthesize a fully elaborated scenario from the raw user prompt.

Takes the user's natural-language request, reasons about the whole box
(shared resources, credential bridging, escalation paths), and produces
a SynthesizedScenario that downstream steps parse rather than interpret.
"""

from pathlib import Path
from typing import Optional, TYPE_CHECKING

import rich

from crewai import Agent, Task, Crew, Process

from game_of_everything.state import GoEState
from game_of_everything.models import SynthesizedScenario, scenario_to_topology
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


def _display_scenario(
    scenario: SynthesizedScenario,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Display concise tactical summary on terminal; full details to log only."""
    # --- Terminal: tactical panels via GoEConsole ---
    if ui:
        # Build target list
        if scenario.boxes:
            targets = [
                {
                    "hostname": b.hostname,
                    "attack_vector": b.attack_vector or b.role,
                    "goal": b.goal or "—",
                }
                for b in scenario.boxes
            ]
        else:
            targets = [
                {
                    "hostname": "target",
                    "attack_vector": scenario.attack_vector or scenario.narrative[:80],
                    "goal": scenario.goal or "—",
                }
            ]
        ui.scenario_intel(targets)

        # Kill chain
        if scenario.kill_chain:
            steps = [{"tag": s.tag, "action": s.action} for s in scenario.kill_chain]
            ui.scenario_kill_chain(steps)

        ui.info("")

        # --- Log file: full verbose details ---
        ui.log("\n=== SYNTHESIZED SCENARIO ===")
        ui.log(f"Narrative: {scenario.narrative}")
        ui.log(f"Attack narrative: {scenario.attack_narrative}")
        ui.log(f"Misconfig scope: {scenario.misconfig_scope}")
        if scenario.custom_app_scope:
            ui.log(f"Custom app scope: {scenario.custom_app_scope}")
        if scenario.custom_vectors:
            ui.log(f"Custom vectors: {len(scenario.custom_vectors)}")
            for v in scenario.custom_vectors:
                ui.log(f"  - {v.display_name} / {'+'.join(v.attack_chain_goals)} / {v.runtime_id}")
        if scenario.shared_resources:
            ui.log(f"Shared resources: {scenario.shared_resources}")
        if scenario.explicit_decisions:
            ui.log(f"Explicit decisions: {scenario.explicit_decisions}")
        if scenario.boxes:
            for b in scenario.boxes:
                ui.log(f"Box {b.box_id} ({b.hostname}): {b.role}")
                ui.log(f"  misconfig_scope: {b.misconfig_scope}")
                if b.custom_app_scope:
                    ui.log(f"  custom_app_scope: {b.custom_app_scope}")
        if scenario.shared_secrets:
            for s in scenario.shared_secrets:
                ui.log(f"Secret [{s.key}] {s.source_box} -> {s.target_box}: {s.target_user}:{s.value} via {s.access_method}")
        if scenario.kill_chain:
            for i, step in enumerate(scenario.kill_chain):
                ui.log(f"Kill chain {i+1}. [{step.tag}] {step.action}")
    else:
        # Headless fallback
        print(f"\nNarrative: {scenario.narrative}")
        print(f"Attack: {scenario.attack_narrative}")
        if scenario.boxes:
            for b in scenario.boxes:
                print(f"  {b.box_id} ({b.hostname}): {b.role}")
        print()


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

    while True:
        if not ui:
            rich.print(f"\n[bold]Synthesizing scenario for:[/bold] {user_input}\n")

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

        # --- Display tactical summary + log full details ---
        _display_scenario(scenario, ui)

        if ui:
            response = ui.prompt("  Continue with this scenario? [y/n]: ")
        else:
            response = input("Continue with this scenario? [y/n]: ")

        if response.strip().lower() in ("y", "yes"):
            break

        # Scenario rejected — allow modification and retry
        if ui:
            ui.info("[bold yellow]Scenario rejected.[/bold yellow]")
            modified = ui.prompt("  Enter a modified request (or press Enter to retry same prompt): ")
        else:
            rich.print("[bold yellow]Scenario rejected.[/bold yellow]")
            modified = input("Enter a modified request (or press Enter to retry same prompt): ").strip()

        if modified.strip():
            user_input = modified.strip()
            state.raw_request = user_input

    if ui:
        ui.info("[bold green]Scenario confirmed. Proceeding to pipeline.[/bold green]")
    else:
        rich.print("[bold green]Scenario confirmed. Proceeding to pipeline.[/bold green]\n")

    state.synthesized_scenario = scenario

    # Convert to NetworkTopology: single-box wraps naturally; multi-box uses scenario.boxes list
    state.topology = scenario_to_topology(scenario)
