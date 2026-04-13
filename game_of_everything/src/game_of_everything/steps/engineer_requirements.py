"""Step 1: Parse synthesized scenario → map to atoms → validate → enumerate deps → sequence."""

from typing import Optional, TYPE_CHECKING

from crewai import Agent, Task, Crew, Process

from game_of_everything.state import GoEState
from game_of_everything.models import (
    ParsedRequest, MappedRequest, SequencedRequest,
)
from game_of_everything.tools.search_atoms_tool import SearchAtomsTool
from game_of_everything.llm_factory import make_llm

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole


def run_engineer_requirements(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Run the full engineering crew: parse → map → validate → dep-enumerate → sequence."""
    if state.synthesized_scenario:
        parser_prompt = state.synthesized_scenario.misconfig_scope
    else:
        parser_prompt = state.raw_request or ""

    if ui:
        ui.log(f"Engineering requirements for: {parser_prompt}")

    # --- Agents ---
    search_atoms_tool = SearchAtomsTool()

    parser = Agent(
        config=agents_config["request_parser_agent"],
        llm=make_llm("request_parser_agent"),
        verbose=False,
    )  # type: ignore

    mapper = Agent(
        config=agents_config["mapping_agent"],
        llm=make_llm("mapping_agent"),
        tools=[search_atoms_tool],
        verbose=False,
    )  # type: ignore

    validator = Agent(
        config=agents_config["mapping_validator_agent"],
        llm=make_llm("mapping_validator_agent"),
        tools=[search_atoms_tool],
        verbose=False,
    )  # type: ignore

    dep_enumerator = Agent(
        config=agents_config["dependency_enumeration_agent"],
        llm=make_llm("dependency_enumeration_agent"),
        tools=[search_atoms_tool],
        verbose=False,
    )  # type: ignore

    sequencer = Agent(
        config=agents_config["sequencing_agent"],
        llm=make_llm("sequencing_agent"),
        verbose=False,
    )  # type: ignore

    # --- Tasks ---
    parse_task = Task(
        config=tasks_config["parse_request_task"],  # type: ignore
        agent=parser,
        output_pydantic=ParsedRequest,
    )
    map_task = Task(
        config=tasks_config["map_atoms_task"],  # type: ignore
        agent=mapper,
        context=[parse_task],  # type: ignore
        output_pydantic=MappedRequest,
    )
    validate_task = Task(
        config=tasks_config["validate_mapping_task"],  # type: ignore
        agent=validator,
        context=[parse_task, map_task],  # type: ignore
        output_pydantic=MappedRequest,
    )
    dep_task = Task(
        config=tasks_config["enumerate_dependencies_task"],  # type: ignore
        agent=dep_enumerator,
        context=[validate_task],  # type: ignore
        output_pydantic=MappedRequest,
    )
    sequence_task = Task(
        config=tasks_config["sequence_atoms_task"],  # type: ignore
        agent=sequencer,
        context=[dep_task],  # type: ignore
        output_pydantic=SequencedRequest,
    )

    # --- Crew ---
    engineering_crew = Crew(
        agents=[parser, mapper, validator, dep_enumerator, sequencer],
        tasks=[parse_task, map_task, validate_task, dep_task, sequence_task],
        process=Process.sequential,
        verbose=False,
        function_calling_llm=make_llm(),
    )

    if ui:
        with ui.capture():
            engineering_crew.kickoff(inputs={"initial_prompt": parser_prompt})
    else:
        engineering_crew.kickoff(inputs={"initial_prompt": parser_prompt})

    # --- Populate state ---
    state.parsed_request = parse_task.output.pydantic  # type: ignore
    state.mapped_request = dep_task.output.pydantic  # type: ignore
    state.sequenced_request = (  # type: ignore
        sequence_task.output.pydantic.atoms
        if sequence_task.output.pydantic
        else None
    )

    # --- Log details ---
    if ui:
        ui.log("\n=== PARSED REQUEST ===")
        ui.log(str(state.parsed_request))
        ui.log("\n=== VALIDATED MAPPING ===")
        ui.log(str(validate_task.output.pydantic))
        ui.log("\n=== MAPPING + DEPENDENCIES ===")
        ui.log(str(state.mapped_request))
        ui.log("\n=== SEQUENCED ATOMS ===")
        if state.sequenced_request:
            ui.status("Sequenced Atoms:")
            for i, atom in enumerate(state.sequenced_request, 1):
                ui.display_atom(atom, verbose=True)
                ui.log(f"  {i}. {atom.name}({atom.parameters}) — {atom.context}")
            # for i, atom in enumerate(state.sequenced_request, 1):
            #     ui.log(f"  {i}. {atom.name} — {atom.context}")
        else:
            ui.log("  (no sequenced atoms)")
