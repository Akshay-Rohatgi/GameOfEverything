"""Step 1: Parse synthesized scenario → map to atoms → validate → enumerate deps → sequence."""

import rich
from crewai import Agent, Task, Crew, Process

from game_of_everything.state import GoEState
from game_of_everything.models import (
    ParsedRequest, MappedRequest, SequencedRequest,
)
from game_of_everything.tools.search_atoms_tool import SearchAtomsTool
from game_of_everything.llm_factory import make_llm


def _make_step_logger(label: str):
    def _log(step):
        print(f"[{label}] {step}")
    return _log


def run_engineer_requirements(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
) -> None:
    """Run the full engineering crew: parse → map → validate → dep-enumerate → sequence.

    Reads from state.raw_request and state.synthesized_scenario (set by
    synthesize_scenario step). The parser extracts structured fields from
    the scenario rather than reasoning about the raw prompt.

    Args:
        state: Flow state to mutate in-place.
        agents_config: Loaded agents.yaml dict.
        tasks_config: Loaded tasks.yaml dict.
    """
    # Build the prompt the parser will work from.  When a synthesized scenario
    # exists, the parser focuses on misconfig_scope (the part of the scenario
    # relevant to the existing atom pipeline).  The raw prompt is still passed
    # as initial_prompt for record-keeping.
    if state.synthesized_scenario:
        parser_prompt = state.synthesized_scenario.misconfig_scope
    else:
        parser_prompt = state.raw_request or ""

    print(f"Engineering requirements for: {parser_prompt}")

    # --- Agents ---
    search_atoms_tool = SearchAtomsTool()

    parser = Agent(
        config=agents_config["request_parser_agent"],
        llm=make_llm("request_parser_agent"),
        step_callback=lambda step: print(f"Parser Step: {step}"),
    )  # type: ignore

    mapper = Agent(
        config=agents_config["mapping_agent"],
        llm=make_llm("mapping_agent"),
        tools=[search_atoms_tool],
        verbose=True,
        step_callback=_make_step_logger("MAPPER"),
    )  # type: ignore

    validator = Agent(
        config=agents_config["mapping_validator_agent"],
        llm=make_llm("mapping_validator_agent"),
        tools=[search_atoms_tool],
        verbose=True,
        step_callback=_make_step_logger("VALIDATOR"),
    )  # type: ignore

    dep_enumerator = Agent(
        config=agents_config["dependency_enumeration_agent"],
        llm=make_llm("dependency_enumeration_agent"),
        tools=[search_atoms_tool],
        verbose=True,
        step_callback=_make_step_logger("DEP-ENUM"),
    )  # type: ignore

    sequencer = Agent(
        config=agents_config["sequencing_agent"],
        llm=make_llm("sequencing_agent"),
        verbose=True,
        step_callback=_make_step_logger("SEQUENCER"),
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
        verbose=True,
        function_calling_llm=make_llm(),
    )

    engineering_crew.kickoff(inputs={"initial_prompt": parser_prompt})

    # --- Populate state ---
    state.parsed_request = parse_task.output.pydantic  # type: ignore
    state.mapped_request = dep_task.output.pydantic  # type: ignore
    state.sequenced_request = (  # type: ignore
        sequence_task.output.pydantic.atoms
        if sequence_task.output.pydantic
        else None
    )

    # --- Console output ---
    rich.print("\n[bold cyan]=== PARSED REQUEST ===[/bold cyan]")
    rich.print(state.parsed_request)

    rich.print("\n[bold yellow]=== MAPPER OUTPUT (pre-validation) ===[/bold yellow]")
    rich.print(map_task.output.pydantic)

    rich.print("\n[bold green]=== VALIDATED MAPPING ===[/bold green]")
    rich.print(validate_task.output.pydantic)

    rich.print("\n[bold blue]=== MAPPING + DEPENDENCIES ===[/bold blue]")
    rich.print(state.mapped_request)

    rich.print("\n[bold magenta]=== SEQUENCED ATOMS ===[/bold magenta]")
    if state.sequenced_request:
        for i, atom in enumerate(state.sequenced_request, 1):
            rich.print(f"  {i}. [bold]{atom.name}[/bold] — {atom.context}")
    else:
        rich.print("  (no sequenced atoms)")
