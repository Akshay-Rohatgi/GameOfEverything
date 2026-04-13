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

    rich.print()
    rich.print("[bold]MISCONFIG SCOPE:[/bold]")
    rich.print(f"  {scenario.misconfig_scope.strip()}")

    if scenario.custom_app_scope:
        rich.print()
        rich.print("[bold]CUSTOM APP SCOPE:[/bold]")
        rich.print(f"  {scenario.custom_app_scope.strip()}")

    rich.print()
    rich.print(f"[bold]NUMBER OF BOXES:[/bold] {scenario.num_boxes}")

    if scenario.boxes:
        rich.print()
        rich.print("[bold magenta]BOX DESCRIPTIONS:[/bold magenta]")
        for spec in scenario.boxes:
            rich.print(f"  [bold cyan]{spec.box_id}[/bold cyan] ({spec.hostname}): {spec.role}")
            scope_preview = spec.misconfig_scope[:100].replace("\n", " ")
            rich.print(f"    scope: {scope_preview}{'...' if len(spec.misconfig_scope) > 100 else ''}")
        if scenario.shared_secrets:
            rich.print()
            rich.print("[bold magenta]SHARED SECRETS:[/bold magenta]")
            for s in scenario.shared_secrets:
                rich.print(f"  [{s.key}] {s.source_box} → {s.target_box}: {s.target_user}:{s.value} via {s.access_method}")

    rich.print()


def _confirm_scenario() -> bool:
    """Ask the user to confirm the synthesized scenario before proceeding."""
    while True:
        response = input("Continue with this scenario? [y/n]: ").strip().lower()
        if response in ("y", "yes"):
            return True
        if response in ("n", "no"):
            return False
        print("Please enter 'y' or 'n'.")


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

    while True:
        rich.print(f"\n[bold]Synthesizing scenario for:[/bold] {user_input}\n")

        # Build dynamic atom list for the synthesis prompt
        available_atoms = _discover_available_atoms()

        # --- Agent ---
        synthesizer = Agent(
            config=agents_config["scenario_synthesis_agent"],
            llm=make_llm("scenario_synthesis_agent"),
            verbose=True,
            step_callback=lambda step: print(f"[SYNTHESIS] {step}"),
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
            verbose=True,
            function_calling_llm=make_llm(),
        )

        synthesis_crew.kickoff(inputs={
            "initial_prompt": user_input,
            "available_atoms": available_atoms,
        })

        scenario: SynthesizedScenario = synthesis_task.output.pydantic  # type: ignore

        # --- Show comparison and confirm ---
        _print_scenario_comparison(user_input, scenario)

        if _confirm_scenario():
            break

        rich.print("[bold yellow]Scenario rejected.[/bold yellow]")
        modified = input("Enter a modified request (or press Enter to retry same prompt): ").strip()
        if modified:
            user_input = modified
            state.raw_request = user_input

    rich.print("[bold green]Scenario confirmed. Proceeding to pipeline.[/bold green]\n")
    state.synthesized_scenario = scenario

    # Convert to NetworkTopology: single-box wraps naturally; multi-box uses scenario.boxes list
    state.topology = scenario_to_topology(scenario)
