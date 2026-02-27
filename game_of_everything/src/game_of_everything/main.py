#!/usr/bin/env python
import os
import yaml
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel, SecretStr
from crewai import Agent, Task, Crew, Process, LLM
from crewai.flow import Flow, listen, start
from game_of_everything.models import (
    ParsedRequest, MappedRequest, GeneratedSnippet, MappedAtom, 
    SequencedRequest, GeneratedSnippets
)
from game_of_everything.tools.search_atoms_tool import SearchAtomsTool
# from langchain_aws import ChatBedrock
from dotenv import load_dotenv
import rich

from crewai.events.event_context import (
    _event_context_config,
    EventContextConfig,
    MismatchBehavior,
)

# Suppress CrewAI internal event-bus pairing warnings (known bug in 1.9.x).
# ToolUsageFinished is emitted without a matching ToolUsageStarted in the
# current version, causing spurious scope-stack mismatch warnings.
_event_context_config.set(
    EventContextConfig(
        mismatch_behavior=MismatchBehavior.SILENT,
        empty_pop_behavior=MismatchBehavior.SILENT,
    )
)

load_dotenv()

class GoEState(BaseModel):
    raw_request: Optional[str] = None
    parsed_request: Optional[ParsedRequest] = None
    mapped_request: Optional[MappedRequest] = None
    sequenced_request: Optional[List[MappedAtom]] = None
    generated_snippets: Optional[List[GeneratedSnippet]] = None
    final_script: Optional[str] = None

class GoEFlow(Flow[GoEState]):
    def __init__(self):
        super().__init__()
        # Load configs from the new config directory
        config_dir = Path(__file__).parent / "config"
        with open(config_dir / "agents.yaml", "r") as f:
            self.agents_config = yaml.safe_load(f)
        with open(config_dir / "tasks.yaml", "r") as f:
            self.tasks_config = yaml.safe_load(f)

    @start()
    def engineer_requirements(self):
        """Step 1: Parse the requirements."""
        user_input = input("Enter your vulnerable environment request: ")
        self.state.raw_request = user_input
        
        print(f"Engineering requirements for: {user_input}")

        # Define Agents
        # Use an inference profile ID (with us. prefix) to avoid the "on-demand throughput" error
        # LiteLLM requires "bedrock/" prefix to route to AWS Bedrock
        model_id = "anthropic.claude-sonnet-4-6"
        if not model_id.startswith("us.") and not model_id.startswith("eu."):
            model_id = f"us.{model_id}"

        llm = LLM(
            model=f"bedrock/{model_id}",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            region=os.getenv("AWS_REGION", "us-east-1"),
        )
        parser = Agent(
            config=self.agents_config["request_parser_agent"],
            llm=llm,
            step_callback=lambda step: print(f"Parser Step: {step}"),
        ) # type: ignore

        def make_step_logger(label: str):
            def _log(step):
                print(f"[{label}] {step}")
            return _log

        search_atoms_tool = SearchAtomsTool()
        mapper = Agent(
            config=self.agents_config["mapping_agent"],
            llm=llm,
            tools=[search_atoms_tool],
            verbose=True,
            step_callback=make_step_logger("MAPPER")
        ) # type: ignore
        validator = Agent(
            config=self.agents_config["mapping_validator_agent"],
            llm=llm,
            tools=[search_atoms_tool],
            verbose=True,
            step_callback=make_step_logger("VALIDATOR")
        ) # type: ignore
        dep_enumerator = Agent(
            config=self.agents_config["dependency_enumeration_agent"],
            llm=llm,
            tools=[search_atoms_tool],
            verbose=True,
            step_callback=make_step_logger("DEP-ENUM")
        ) # type: ignore
        sequencer = Agent(
            config=self.agents_config["sequencing_agent"],
            llm=llm,
            verbose=True,
            step_callback=make_step_logger("SEQUENCER")
        ) # type: ignore

        # Define Tasks
        parse_task = Task(
            config=self.tasks_config["parse_request_task"], # type: ignore
            agent=parser,
            output_pydantic=ParsedRequest
        )
        map_task = Task(
            config=self.tasks_config["map_atoms_task"], # type: ignore
            agent=mapper,
            context=[parse_task], # type: ignore
            output_pydantic=MappedRequest
        )
        validate_task = Task(
            config=self.tasks_config["validate_mapping_task"], # type: ignore
            agent=validator,
            context=[parse_task, map_task], # type: ignore
            output_pydantic=MappedRequest
        )
        dep_task = Task(
            config=self.tasks_config["enumerate_dependencies_task"], # type: ignore
            agent=dep_enumerator,
            context=[validate_task], # type: ignore
            output_pydantic=MappedRequest
        )
        sequence_task = Task(
            config=self.tasks_config["sequence_atoms_task"], # type: ignore
            agent=sequencer,
            context=[dep_task], # type: ignore
            output_pydantic=SequencedRequest
        )

        # Create and Run Engineering Crew
        engineering_crew = Crew(
            agents=[parser, mapper, validator, dep_enumerator, sequencer],
            tasks=[parse_task, map_task, validate_task, dep_task, sequence_task],
            process=Process.sequential,
            verbose=True,
            function_calling_llm=llm
        )

        result = engineering_crew.kickoff(inputs={"initial_prompt": user_input})

        # Access the raw output of the parser task using the crew's task output tracking
        self.state.parsed_request = parse_task.output.pydantic # type: ignore
        self.state.mapped_request = dep_task.output.pydantic # type: ignore
        self.state.sequenced_request = sequence_task.output.pydantic.atoms if sequence_task.output.pydantic else None # type: ignore

        rich.print("\n[bold cyan]=== PARSED REQUEST ===[/bold cyan]")
        rich.print(self.state.parsed_request)

        rich.print("\n[bold yellow]=== MAPPER OUTPUT (pre-validation) ===[/bold yellow]")
        rich.print(map_task.output.pydantic)

        rich.print("\n[bold green]=== VALIDATED MAPPING ===[/bold green]")
        rich.print(validate_task.output.pydantic)

        rich.print("\n[bold blue]=== MAPPING + DEPENDENCIES ===[/bold blue]")
        rich.print(self.state.mapped_request)

        rich.print("\n[bold magenta]=== SEQUENCED ATOMS ===[/bold magenta]")
        if self.state.sequenced_request:
            for i, atom in enumerate(self.state.sequenced_request, 1):
                rich.print(f"  {i}. [bold]{atom.name}[/bold] — {atom.context}")
        else:
            rich.print("  (no sequenced atoms)")

    @listen(engineer_requirements)
    def generate_implementation(self):
        """Step 2: Generate and validate implementation snippets."""
        print("Skipping generation and validation for testing...")
        # # Define Agents
        # generator = Agent(config=self.agents_config["snippet_generation_agent"]) # type: ignore
        # tester = Agent(config=self.agents_config["testing_agent"]) # type: ignore

        # # Define Tasks
        # generate_task = Task(
        #     config=self.tasks_config["generate_snippets_task"], # type: ignore
        #     agent=generator,
        # )
        # validate_task = Task(
        #     config=self.tasks_config["validate_snippets_task"], # type: ignore
        #     agent=tester,
        #     context=[generate_task],
        #     output_pydantic=GeneratedSnippets
        # )

        # implementation_crew = Crew(
        #     agents=[generator, tester],
        #     tasks=[generate_task, validate_task],
        #     process=Process.sequential,
        #     verbose=False
        # )

        # # Pass inputs to the implementation crew
        # implementation_crew.kickoff(inputs={
        #     "sequenced_atoms": str([atom.model_dump() for atom in (self.state.sequenced_request or [])]),
        #     "parsed_request": str(self.state.parsed_request.model_dump() if self.state.parsed_request else "{}"),
        #     "mapped_atoms": str(self.state.mapped_request.model_dump() if self.state.mapped_request else "{}")
        # })

        print("Implementation generation complete (skipped).")

    @listen(generate_implementation)
    def finalize_script(self):
        """Step 3: Synthesize the final script."""
        print("Skipping final synthesis for testing...")
        
        # final_script = "#!/bin/bash\n"
        # final_script += f"# Generated for request: {self.state.raw_request}\n\n"
        
        # if self.state.generated_snippets:
        #     for snippet in self.state.generated_snippets:
        #         if snippet.validated:
        #             final_script += f"# Atom: {snippet.atom_name}\n"
        #             final_script += snippet.code_snippet + "\n\n"
        # else:
        #     final_script += "echo 'No snippets were generated or validated.'\n"

        # self.state.final_script = final_script
        
        # # Output the final result
        # print("\n--- FINAL DEPLOYMENT SCRIPT ---")
        # print(self.state.final_script)
        # print("--- END OF SCRIPT ---")

def kickoff():
    goe_flow = GoEFlow()
    goe_flow.kickoff()

def plot():
    goe_flow = GoEFlow()
    goe_flow.plot("goe_flow.png")

if __name__ == "__main__":
    kickoff()
