"""Step 0: Synthesize a fully elaborated scenario from the raw user prompt.

Takes the user's natural-language request, reasons about the whole box
(shared resources, credential bridging, escalation paths), and produces
a SynthesizedScenario that downstream steps parse rather than interpret.
"""

import sys
from pathlib import Path
from typing import Optional

import rich
from rich.panel import Panel
from crewai import Agent, Task, Crew, Process

from game_of_everything.state import GoEState
from game_of_everything.models import SynthesizedScenario
from game_of_everything.llm_factory import make_llm

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


def _print_scenario_comparison(raw_prompt: str, scenario: SynthesizedScenario) -> None:
    """Pretty-print the raw prompt alongside the synthesized scenario."""
    rich.print()
    rich.print(Panel(raw_prompt, title="[bold cyan]YOUR PROMPT[/bold cyan]", border_style="cyan"))
    rich.print()
    rich.print(Panel(
        scenario.narrative.strip(),
        title="[bold green]SYNTHESIZED NARRATIVE[/bold green]",
        border_style="green",
    ))
    rich.print()
    rich.print(Panel(
        scenario.attack_narrative.strip(),
        title="[bold yellow]ATTACK NARRATIVE[/bold yellow]",
        border_style="yellow",
    ))

    if scenario.shared_resources:
        rich.print()
        rich.print("[bold magenta]SHARED RESOURCES:[/bold magenta]")
        for res in scenario.shared_resources:
            rich.print(f"  - {res}")

    if scenario.explicit_decisions:
        rich.print()
        rich.print("[bold blue]EXPLICIT DECISIONS (not in your prompt):[/bold blue]")
        for dec in scenario.explicit_decisions:
            rich.print(f"  - {dec}")

    rich.print()
    rich.print("[bold]MISCONFIG SCOPE:[/bold]")
    rich.print(f"  {scenario.misconfig_scope.strip()}")

    if scenario.custom_app_scope:
        rich.print()
        rich.print("[bold]CUSTOM APP SCOPE:[/bold]")
        rich.print(f"  {scenario.custom_app_scope.strip()}")

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
) -> None:
    """Collect user prompt, synthesize a full scenario, and confirm with the user.

    Args:
        state: Flow state to mutate in-place.
        agents_config: Loaded agents.yaml dict.
        tasks_config: Loaded tasks.yaml dict.
        user_input: Optional pre-supplied request. Falls back to interactive input().
    """
    if user_input is None:
        user_input = input("Enter your vulnerable environment request: ")
    state.raw_request = user_input

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

    if not _confirm_scenario():
        rich.print("[bold red]Scenario rejected. Exiting.[/bold red]")
        sys.exit(0)

    rich.print("[bold green]Scenario confirmed. Proceeding to engineering.[/bold green]\n")
    state.synthesized_scenario = scenario
