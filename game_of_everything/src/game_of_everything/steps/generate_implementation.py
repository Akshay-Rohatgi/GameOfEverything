"""Step 2: Generate code_snippet, testing_snippet, and attack_snippet for each sequenced atom.

Iterates atom-by-atom so each LLM call focuses on a single atom — avoids
output truncation when the sequenced list is long.
"""

import json
from typing import List, Optional, TYPE_CHECKING

from crewai import Agent, Task, Crew, Process

from game_of_everything.state import GoEState
from game_of_everything.models import GeneratedSnippet
from game_of_everything.tools.read_atom_tool import ReadAtomTool
from game_of_everything.tools.search_atoms_tool import SearchAtomsTool
from game_of_everything.llm_factory import make_llm

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole


def _si(s: str) -> str:
    """Sanitize a string for use as a crewAI crew.kickoff() input value."""
    return s.replace("{{", "{ {").replace("}}", "} }")


def run_generate_implementation(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
    box_id: str = "",
    target_hostname: str = "target",
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Generate implementation snippets for each sequenced atom, one at a time.

    Args:
        state: Flow state to mutate in-place.
        agents_config: Loaded agents.yaml dict.
        tasks_config: Loaded tasks.yaml dict.
        box_id: Optional box identifier for scoped logging.
        target_hostname: Hostname of the target container on the Docker bridge
            network.  Used by attack_snippets so they address the correct host.
            Defaults to "target" (the single-box default).
        ui: Optional GoEConsole for structured output.
    """
    if not state.sequenced_request:
        if ui:
            ui.log("No sequenced atoms to generate snippets for. Skipping.")
        return

    atoms = state.sequenced_request
    total = len(atoms)
    snippets: List[GeneratedSnippet] = []

    _tag = f"[{box_id}][SNIPPET-GEN]" if box_id else "[SNIPPET-GEN]"

    for idx, atom in enumerate(atoms):
        atom_json = json.dumps(atom.model_dump(), indent=2)

        # Build a brief summary of previously generated atoms for context
        if snippets:
            prior_lines = [
                f"  {i+1}. {s.atom_name}" for i, s in enumerate(snippets)
            ]
            prior_atoms_summary = "\n".join(prior_lines)
        else:
            prior_atoms_summary = "(none — this is the first atom)"

        if ui:
            ui.log(f"\n--- Generating snippet {idx+1}/{total}: {atom.name} ---")

        llm = make_llm("snippet_generation_agent")

        snippet_generator = Agent(
            config=agents_config["snippet_generation_agent"],
            llm=llm,
            tools=[ReadAtomTool(), SearchAtomsTool()],
            verbose=False,
            **({"step_callback": lambda step: print(f"{_tag} {step}")} if not ui and box_id else {}),
        )  # type: ignore

        generate_task = Task(
            config=tasks_config["generate_snippets_task"],  # type: ignore
            agent=snippet_generator,
            output_pydantic=GeneratedSnippet,
        )

        crew_name = f"{box_id}/generate/{atom.name}" if box_id else f"generate/{atom.name}"
        generation_crew = Crew(
            name=crew_name,
            agents=[snippet_generator],
            tasks=[generate_task],
            process=Process.sequential,
            verbose=False,
            function_calling_llm=llm,
        )

        kickoff_inputs = {
            "atom_json": _si(atom_json),
            "atom_index": str(idx + 1),
            "total_atoms": str(total),
            "prior_atoms_summary": _si(prior_atoms_summary),
            "target_hostname": target_hostname,
        }

        if ui:
            with ui.capture():
                generation_crew.kickoff(inputs=kickoff_inputs)
        else:
            generation_crew.kickoff(inputs=kickoff_inputs)

        if generate_task.output.pydantic:  # type: ignore
            snippets.append(generate_task.output.pydantic)  # type: ignore
        else:
            if ui:
                ui.log(f"  WARNING: Failed to parse snippet for {atom.name}")

    state.generated_snippets = snippets

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
