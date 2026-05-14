"""Step 1: Parse synthesized scenario → map to atoms → validate → enumerate deps → sequence."""

import json
import re
import rich
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

def _tolerant_mapped_request(raw: str) -> Optional[MappedRequest]:
    """Parse LLM output into MappedRequest with tolerant JSON handling.

    Handles:
    - JSON wrapped in markdown code fences
    - Unquoted object keys  ({key: val} → {"key": val})
    - Trailing non-JSON text after the closing brace
    """
    if not raw:
        return None
    s = raw.strip()
    # Strip markdown code fences
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    else:
        start = s.find('{')
        if start != -1:
            s = s[start:]

    # Pass 1: direct parse
    try:
        return MappedRequest.model_validate_json(s)
    except Exception:
        pass

    # Pass 2: quote unquoted keys then retry
    fixed = re.sub(
        r'([\[{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)',
        lambda m: f'{m.group(1)}"{m.group(2)}"{m.group(3)}',
        s,
    )
    try:
        return MappedRequest.model_validate_json(fixed)
    except Exception:
        pass

    # Pass 3: raw_decode to handle trailing non-JSON text
    try:
        obj, _ = json.JSONDecoder().raw_decode(s)
        return MappedRequest.model_validate(obj)
    except Exception:
        pass

    return None


def run_engineer_requirements(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
    box_id: str = "",
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
        # No output_pydantic — LLMs occasionally produce unquoted JSON keys;
        # we parse tolerantly below via _tolerant_mapped_request.
    )
    validate_task = Task(
        config=tasks_config["validate_mapping_task"],  # type: ignore
        agent=validator,
        context=[parse_task, map_task],  # type: ignore
    )
    dep_task = Task(
        config=tasks_config["enumerate_dependencies_task"],  # type: ignore
        agent=dep_enumerator,
        context=[validate_task],  # type: ignore
    )
    sequence_task = Task(
        config=tasks_config["sequence_atoms_task"],  # type: ignore
        agent=sequencer,
        context=[dep_task],  # type: ignore
        output_pydantic=SequencedRequest,
    )

    # --- Crew ---
    engineering_crew = Crew(
        name=f"{box_id}/engineer_requirements" if box_id else "engineer_requirements",
        agents=[parser, mapper, validator, dep_enumerator, sequencer],
        tasks=[parse_task, map_task, validate_task, dep_task, sequence_task],
        process=Process.sequential,
        verbose=False,
        function_calling_llm=make_llm(),
    )

    kickoff_inputs = {
        "initial_prompt": parser_prompt,
        "num_boxes": state.synthesized_scenario.num_boxes if state.synthesized_scenario else 1,
    }
    if ui:
        with ui.capture():
            engineering_crew.kickoff(inputs=kickoff_inputs)
    else:
        engineering_crew.kickoff(inputs=kickoff_inputs)

    # --- Populate state ---
    state.parsed_request = parse_task.output.pydantic  # type: ignore
    # Tolerate malformed JSON (unquoted keys, trailing text) from MappedRequest tasks
    state.mapped_request = (
        dep_task.output.pydantic  # type: ignore
        or _tolerant_mapped_request(dep_task.output.raw)
        or _tolerant_mapped_request(validate_task.output.raw)
        or _tolerant_mapped_request(map_task.output.raw)
    )
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
        else:
            ui.log("  (no sequenced atoms)")
