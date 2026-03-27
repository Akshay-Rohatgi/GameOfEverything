"""Step 2: Generate code_snippet, testing_snippet, and attack_snippet for each sequenced atom."""

import json

import rich
from crewai import Agent, Task, Crew, Process

from game_of_everything.state import GoEState
from game_of_everything.models import GeneratedSnippets
from game_of_everything.tools.read_atom_tool import ReadAtomTool
from game_of_everything.tools.search_atoms_tool import SearchAtomsTool
from game_of_everything.llm_factory import make_llm


def run_generate_implementation(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
) -> None:
    """Generate implementation snippets for each sequenced atom.

    Args:
        state: Flow state to mutate in-place.
        agents_config: Loaded agents.yaml dict.
        tasks_config: Loaded tasks.yaml dict.
    """
    if not state.sequenced_request:
        print("No sequenced atoms to generate snippets for. Skipping.")
        return

    sequenced_atoms_json = json.dumps(
        [atom.model_dump() for atom in state.sequenced_request],
        indent=2,
    )

    snippet_generator = Agent(
        config=agents_config["snippet_generation_agent"],
        llm=make_llm("snippet_generation_agent"),
        tools=[ReadAtomTool(), SearchAtomsTool()],
        verbose=True,
        step_callback=lambda step: print(f"[SNIPPET-GEN] {step}"),
    )  # type: ignore

    generate_task = Task(
        config=tasks_config["generate_snippets_task"],  # type: ignore
        agent=snippet_generator,
        output_pydantic=GeneratedSnippets,
    )

    generation_crew = Crew(
        agents=[snippet_generator],
        tasks=[generate_task],
        process=Process.sequential,
        verbose=True,
        function_calling_llm=make_llm("snippet_generation_agent"),
    )

    generation_crew.kickoff(inputs={"sequenced_atoms_json": sequenced_atoms_json})

    if generate_task.output.pydantic:  # type: ignore
        state.generated_snippets = generate_task.output.pydantic.snippets  # type: ignore

    # --- Console output ---
    rich.print("\n[bold green]=== GENERATED SNIPPETS ===[/bold green]")
    if state.generated_snippets:
        for snippet in state.generated_snippets:
            rich.print(f"\n  [bold cyan]--- {snippet.atom_name} ---[/bold cyan]")
            rich.print(f"  [yellow]code_snippet:[/yellow]\n{snippet.code_snippet}")
            rich.print(f"  [blue]testing_snippet:[/blue]\n{snippet.testing_snippet}")
            if snippet.attack_snippet:
                rich.print(f"  [red]attack_snippet:[/red]\n{snippet.attack_snippet}")
            else:
                rich.print(f"  [dim]attack_snippet: null (no external attack surface)[/dim]")
    else:
        rich.print("  (no snippets generated)")
