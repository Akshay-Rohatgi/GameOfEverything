"""Step 2: Generate code_snippet, testing_snippet, and attack_snippet for each sequenced atom."""

import json
from typing import Optional, TYPE_CHECKING

from crewai import Agent, Task, Crew, Process

from game_of_everything.state import GoEState
from game_of_everything.models import GeneratedSnippets
from game_of_everything.tools.read_atom_tool import ReadAtomTool
from game_of_everything.tools.search_atoms_tool import SearchAtomsTool
from game_of_everything.llm_factory import make_llm

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole


def run_generate_implementation(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Generate implementation snippets for each sequenced atom."""
    if not state.sequenced_request:
        if ui:
            ui.log("No sequenced atoms to generate snippets for. Skipping.")
        return

    sequenced_atoms_json = json.dumps(
        [atom.model_dump() for atom in state.sequenced_request],
        indent=2,
    )

    snippet_generator = Agent(
        config=agents_config["snippet_generation_agent"],
        llm=make_llm("snippet_generation_agent"),
        tools=[ReadAtomTool(), SearchAtomsTool()],
        verbose=False,
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
        verbose=False,
        function_calling_llm=make_llm("snippet_generation_agent"),
    )

    if ui:
        with ui.capture():
            generation_crew.kickoff(inputs={"sequenced_atoms_json": sequenced_atoms_json})
    else:
        generation_crew.kickoff(inputs={"sequenced_atoms_json": sequenced_atoms_json})

    if generate_task.output.pydantic:  # type: ignore
        state.generated_snippets = generate_task.output.pydantic.snippets  # type: ignore

    # Log details
    if ui:
        ui.log("\n=== GENERATED SNIPPETS ===")
        if state.generated_snippets:
            for snippet in state.generated_snippets:
                ui.log(f"\n--- {snippet.atom_name} ---")
                ui.log(f"code_snippet:\n{snippet.code_snippet}")
                ui.log(f"testing_snippet:\n{snippet.testing_snippet}")
                if snippet.attack_snippet:
                    ui.log(f"attack_snippet:\n{snippet.attack_snippet}")
                else:
                    ui.log("attack_snippet: null (no external attack surface)")
        else:
            ui.log("  (no snippets generated)")
