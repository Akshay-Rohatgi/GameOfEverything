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
    SequencedRequest, GeneratedSnippets, TestVerdict, TestResult,
    DiagnosticResult
)
from game_of_everything.tools.search_atoms_tool import SearchAtomsTool
from game_of_everything.tools.read_atom_tool import ReadAtomTool
from game_of_everything.tools.test_environment import TestEnvironmentTool
from game_of_everything.tools.exec_in_container_tool import ExecInContainerTool
from game_of_everything.script_postprocessor import apply_post_processors
# from langchain_aws import ChatBedrock
from dotenv import load_dotenv
import json
import rich
from datetime import datetime

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
    test_results: Optional[List[TestResult]] = None
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
        """Step 2: Generate implementation snippets for each sequenced atom."""
        if not self.state.sequenced_request:
            print("No sequenced atoms to generate snippets for. Skipping.")
            return

        model_id = "anthropic.claude-sonnet-4-6"
        if not model_id.startswith("us.") and not model_id.startswith("eu."):
            model_id = f"us.{model_id}"

        llm = LLM(
            model=f"bedrock/{model_id}",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            region=os.getenv("AWS_REGION", "us-east-1"),
        )

        sequenced_atoms_json = json.dumps(
            [atom.model_dump() for atom in self.state.sequenced_request],
            indent=2,
        )

        snippet_generator = Agent(
            config=self.agents_config["snippet_generation_agent"],
            llm=llm,
            tools=[ReadAtomTool(), SearchAtomsTool()],
            verbose=True,
            step_callback=lambda step: print(f"[SNIPPET-GEN] {step}"),
        ) # type: ignore

        generate_task = Task(
            config=self.tasks_config["generate_snippets_task"], # type: ignore
            agent=snippet_generator,
            output_pydantic=GeneratedSnippets,
        )

        generation_crew = Crew(
            agents=[snippet_generator],
            tasks=[generate_task],
            process=Process.sequential,
            verbose=True,
            function_calling_llm=llm,
        )

        generation_crew.kickoff(inputs={"sequenced_atoms_json": sequenced_atoms_json})

        if generate_task.output.pydantic: # type: ignore
            self.state.generated_snippets = generate_task.output.pydantic.snippets # type: ignore

        rich.print("\n[bold green]=== GENERATED SNIPPETS ===[/bold green]")
        if self.state.generated_snippets:
            for snippet in self.state.generated_snippets:
                rich.print(f"\n  [bold cyan]--- {snippet.atom_name} ---[/bold cyan]")
                rich.print(f"  [yellow]code_snippet:[/yellow]\n{snippet.code_snippet}")
                rich.print(f"  [blue]testing_snippet:[/blue]\n{snippet.testing_snippet}")
                if snippet.attack_snippet:
                    rich.print(f"  [red]attack_snippet:[/red]\n{snippet.attack_snippet}")
                else:
                    rich.print(f"  [dim]attack_snippet: null (no external attack surface)[/dim]")
        else:
            rich.print("  (no snippets generated)")

    # ------------------------------------------------------------------
    # Step 3: Test snippets in Docker containers
    # ------------------------------------------------------------------

    def _make_llm(self) -> LLM:
        """Create a Bedrock LLM instance (shared helper to avoid repetition)."""
        model_id = "anthropic.claude-sonnet-4-6"
        if not model_id.startswith("us.") and not model_id.startswith("eu."):
            model_id = f"us.{model_id}"
        return LLM(
            model=f"bedrock/{model_id}",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
            region=os.getenv("AWS_REGION", "us-east-1"),
        )

    def _run_verdict_crew(
        self,
        llm: LLM,
        atom_name: str,
        atom_context: str,
        layer: str,
        snippet_executed: str,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> TestVerdict:
        """Kick off a one-task Testing Agent crew to judge command output.

        The LLM receives the atom context + raw output and returns a TestVerdict.
        No tools needed — pure reasoning about the output text.
        """
        tester = Agent(
            config=self.agents_config["testing_agent"],
            llm=llm,
            verbose=True,
            step_callback=lambda step: print(f"[TESTER] {step}"),
        )  # type: ignore

        verdict_task = Task(
            config=self.tasks_config["validate_snippets_task"],  # type: ignore
            agent=tester,
            output_pydantic=TestVerdict,
        )

        verdict_crew = Crew(
            agents=[tester],
            tasks=[verdict_task],
            process=Process.sequential,
            verbose=True,
            function_calling_llm=llm,
        )

        verdict_crew.kickoff(
            inputs={
                "atom_name": atom_name,
                "atom_context": atom_context,
                "layer": layer,
                "snippet_executed": snippet_executed,
                "exit_code": str(exit_code),
                "stdout": stdout or "(empty)",
                "stderr": stderr or "(empty)",
            }
        )

        if verdict_task.output.pydantic:  # type: ignore
            return verdict_task.output.pydantic  # type: ignore

        # Fallback: if pydantic parsing failed, treat as failure
        return TestVerdict(
            passed=False,
            reasoning="Failed to parse LLM verdict output into TestVerdict.",
        )

    def _run_diagnostic_crew(
        self,
        llm: LLM,
        atom_name: str,
        atom_context: str,
        atom_parameters: Optional[str],
        original_code_snippet: str,
        original_testing_snippet: str,
        apply_stderr: str,
        l1_exit_code: int,
        l1_stdout: str,
        l1_stderr: str,
        verdict_reasoning: str,
        attempt_number: int,
    ) -> DiagnosticResult:
        """Kick off a one-task Diagnostic Agent crew to diagnose and fix a failing snippet.

        The agent has ReadAtomTool (to re-read atom definitions) and ExecInContainerTool
        (to run diagnostic commands in the target container). It receives all failure
        context and returns a DiagnosticResult with corrected snippets.
        """
        diagnostician = Agent(
            config=self.agents_config["diagnostic_agent"],
            llm=llm,
            tools=[ReadAtomTool(), ExecInContainerTool()],
            verbose=True,
            step_callback=lambda step: print(f"[DIAGNOSTICIAN] {step}"),
        )  # type: ignore

        diag_task = Task(
            config=self.tasks_config["diagnose_snippet_task"],  # type: ignore
            agent=diagnostician,
            output_pydantic=DiagnosticResult,
        )

        diag_crew = Crew(
            agents=[diagnostician],
            tasks=[diag_task],
            process=Process.sequential,
            verbose=True,
            function_calling_llm=llm,
        )

        diag_crew.kickoff(
            inputs={
                "atom_name": atom_name,
                "atom_context": atom_context,
                "atom_parameters": atom_parameters or "(none)",
                "original_code_snippet": original_code_snippet,
                "original_testing_snippet": original_testing_snippet,
                "apply_stderr": apply_stderr or "(empty)",
                "l1_exit_code": str(l1_exit_code),
                "l1_stdout": l1_stdout or "(empty)",
                "l1_stderr": l1_stderr or "(empty)",
                "verdict_reasoning": verdict_reasoning,
                "attempt_number": str(attempt_number),
            }
        )

        if diag_task.output.pydantic:  # type: ignore
            return diag_task.output.pydantic  # type: ignore

        # Fallback: if pydantic parsing failed, return a no-op diagnostic
        return DiagnosticResult(
            fixed_code_snippet=original_code_snippet,
            fixed_testing_snippet=original_testing_snippet,
            diagnosis="Failed to parse diagnostic agent output. Returning original snippets unchanged.",
            confidence="low",
        )

    @listen(generate_implementation)
    def test_snippets(self):
        """Step 3: Test generated snippets in Docker containers.

        Hybrid architecture:
        - Python loop controls progression (apply snippet, run checks, decide stop/continue).
        - LLM (Testing Agent) judges each command's output — no hardcoded output parsing.

        Incremental cumulative testing:
        - After applying snippet N and passing Layer 1, re-run Layer 2 probes for
          ALL snippets 0..N that have an attack_snippet. This catches dependency
          misordering and regressions at the exact snippet that caused them.
        """
        if not self.state.generated_snippets:
            print("No generated snippets to test. Skipping.")
            return

        snippets = self.state.generated_snippets
        llm = self._make_llm()
        env = TestEnvironmentTool()
        results: List[TestResult] = []

        try:
            rich.print("\n[bold yellow]=== SETTING UP TEST ENVIRONMENT ===[/bold yellow]")
            env.setup()
            rich.print("[green]Test environment ready (target + attacker containers on goe_test_net)[/green]")

            MAX_DIAGNOSTIC_RETRIES = 2

            for i, snippet in enumerate(snippets):
                rich.print(f"\n[bold cyan]=== TESTING SNIPPET {i}: {snippet.atom_name} ===[/bold cyan]")

                diagnostic_attempts: List[DiagnosticResult] = []
                l1_passed = False
                l1_verdict = None
                apply_stderr_last = ""

                # --- Apply + Layer 1 with diagnostic retry loop ---
                for attempt in range(1 + MAX_DIAGNOSTIC_RETRIES):  # attempt 0 = original, 1-2 = retries
                    is_retry = attempt > 0

                    if is_retry:
                        rich.print(f"\n  [bold yellow]--- DIAGNOSTIC RETRY {attempt}/{MAX_DIAGNOSTIC_RETRIES} for {snippet.atom_name} ---[/bold yellow]")

                    # Apply the code_snippet on the target
                    rich.print(f"  [yellow]{'Re-applying' if is_retry else 'Applying'} code_snippet...[/yellow]")
                    apply_exit, apply_stdout, apply_stderr = env.exec_in_target(snippet.code_snippet)
                    apply_stderr_last = apply_stderr
                    rich.print(f"  Apply exit code: {apply_exit}")
                    if apply_stderr:
                        rich.print(f"  [dim]Apply stderr: {apply_stderr[:500]}[/dim]")

                    # Layer 1: run testing_snippet, ask LLM to judge
                    rich.print(f"  [blue]Running Layer 1 (internal state check)...[/blue]")
                    l1_exit, l1_stdout, l1_stderr = env.exec_in_target(snippet.testing_snippet)
                    rich.print(f"  Layer 1 exit code: {l1_exit}")

                    l1_verdict = self._run_verdict_crew(
                        llm=llm,
                        atom_name=snippet.atom_name,
                        atom_context=snippet.mapped_atom.context,
                        layer="internal state check",
                        snippet_executed=snippet.testing_snippet,
                        exit_code=l1_exit,
                        stdout=l1_stdout,
                        stderr=l1_stderr,
                    )

                    rich.print(f"  Layer 1 verdict: {'[green]PASS[/green]' if l1_verdict.passed else '[red]FAIL[/red]'}")
                    rich.print(f"  Reasoning: {l1_verdict.reasoning}")

                    if l1_verdict.passed:
                        l1_passed = True
                        break

                    # Layer 1 failed — attempt diagnosis if retries remain
                    if attempt < MAX_DIAGNOSTIC_RETRIES:
                        rich.print(f"  [yellow]Layer 1 FAILED. Running Diagnostic Agent (attempt {attempt + 1}/{MAX_DIAGNOSTIC_RETRIES})...[/yellow]")

                        diag_result = self._run_diagnostic_crew(
                            llm=llm,
                            atom_name=snippet.atom_name,
                            atom_context=snippet.mapped_atom.context,
                            atom_parameters=json.dumps(snippet.mapped_atom.parameters) if snippet.mapped_atom.parameters else None,
                            original_code_snippet=snippet.code_snippet,
                            original_testing_snippet=snippet.testing_snippet,
                            apply_stderr=apply_stderr,
                            l1_exit_code=l1_exit,
                            l1_stdout=l1_stdout,
                            l1_stderr=l1_stderr,
                            verdict_reasoning=l1_verdict.reasoning,
                            attempt_number=attempt + 1,
                        )

                        diagnostic_attempts.append(diag_result)

                        rich.print(f"  [cyan]Diagnosis:[/cyan] {diag_result.diagnosis}")
                        rich.print(f"  [cyan]Confidence:[/cyan] {diag_result.confidence}")

                        # Apply the fix — replace snippet's code and testing snippets
                        snippet.code_snippet = diag_result.fixed_code_snippet
                        snippet.testing_snippet = diag_result.fixed_testing_snippet

                        rich.print(f"  [green]Snippet updated with diagnostic fix. Retrying...[/green]")
                    else:
                        rich.print(f"  [bold red]Layer 1 FAILED after {MAX_DIAGNOSTIC_RETRIES} diagnostic retries. Giving up.[/bold red]")

                if not l1_passed:
                    # All retries exhausted — stop the chain
                    snippet.set_validated(False)
                    results.append(TestResult(
                        atom_name=snippet.atom_name,
                        layer1_verdict=l1_verdict,  # type: ignore
                        layer2_verdicts=None,
                        diagnostic_results=diagnostic_attempts if diagnostic_attempts else None,
                        error=f"Layer 1 failed after {len(diagnostic_attempts)} diagnostic retries — chain stopped at snippet {i}.",
                    ))
                    # Mark all remaining snippets as not validated
                    for remaining in snippets[i + 1:]:
                        remaining.set_validated(False)
                    break

                # --- Layer 2: re-run ALL accumulated attack probes 0..i ---
                l2_verdicts: List[TestVerdict] = []
                l2_diagnostics: List[DiagnosticResult] = []

                for j in range(i + 1):
                    attack = snippets[j].attack_snippet
                    if attack:
                        rich.print(f"  [red]Running Layer 2 probe for snippet {j} ({snippets[j].atom_name})...[/red]")
                        a_exit, a_stdout, a_stderr = env.exec_in_attacker(attack)

                        verdict = self._run_verdict_crew(
                            llm=llm,
                            atom_name=snippets[j].atom_name,
                            atom_context=snippets[j].mapped_atom.context,
                            layer="external attack probe",
                            snippet_executed=attack,
                            exit_code=a_exit,
                            stdout=a_stdout,
                            stderr=a_stderr,
                        )
                        l2_verdicts.append(verdict)

                        status = '[green]PASS[/green]' if verdict.passed else '[red]FAIL[/red]'
                        rich.print(f"    Snippet {j} ({snippets[j].atom_name}) Layer 2: {status}")
                        rich.print(f"    Reasoning: {verdict.reasoning}")

                        if not verdict.passed:
                            # Distinguish regression from first-time failure
                            if j < i:
                                rich.print(f"    [bold red]⚠ REGRESSION: snippet {j} Layer 2 was passing, now fails after snippet {i}[/bold red]")

                            # Run diagnostic for L2 failure (log only, no retry)
                            rich.print(f"    [yellow]Running Diagnostic Agent for L2 failure (log only)...[/yellow]")
                            l2_diag = self._run_diagnostic_crew(
                                llm=llm,
                                atom_name=snippets[j].atom_name,
                                atom_context=snippets[j].mapped_atom.context,
                                atom_parameters=json.dumps(snippets[j].mapped_atom.parameters) if snippets[j].mapped_atom.parameters else None,
                                original_code_snippet=snippets[j].code_snippet,
                                original_testing_snippet=attack,
                                apply_stderr=a_stderr,
                                l1_exit_code=a_exit,
                                l1_stdout=a_stdout,
                                l1_stderr=a_stderr,
                                verdict_reasoning=verdict.reasoning,
                                attempt_number=1,
                            )
                            l2_diagnostics.append(l2_diag)
                            rich.print(f"    [cyan]L2 Diagnosis:[/cyan] {l2_diag.diagnosis}")

                # Set validated: both layers must pass (or Layer 2 is N/A)
                all_l2_passed = all(v.passed for v in l2_verdicts) if l2_verdicts else True
                snippet.set_validated(l1_passed and all_l2_passed)

                # Combine all diagnostics (L1 retries + L2 logs) for this snippet
                all_diagnostics = diagnostic_attempts + l2_diagnostics

                results.append(TestResult(
                    atom_name=snippet.atom_name,
                    layer1_verdict=l1_verdict,  # type: ignore
                    layer2_verdicts=l2_verdicts if l2_verdicts else None,
                    diagnostic_results=all_diagnostics if all_diagnostics else None,
                ))

            self.state.test_results = results

            # --- Summary ---
            rich.print("\n[bold magenta]=== TEST SUMMARY ===[/bold magenta]")
            for result in results:
                l1_status = '[green]PASS[/green]' if result.layer1_verdict.passed else '[red]FAIL[/red]'
                if result.layer2_verdicts:
                    l2_all = all(v.passed for v in result.layer2_verdicts)
                    l2_status = f"[green]PASS[/green] ({len(result.layer2_verdicts)} probes)" if l2_all else f"[red]FAIL[/red]"
                else:
                    l2_status = "[dim]N/A[/dim]"
                diag_count = len(result.diagnostic_results) if result.diagnostic_results else 0
                diag_label = f" [yellow]({diag_count} diagnostic runs)[/yellow]" if diag_count else ""
                rich.print(f"  {result.atom_name}: L1={l1_status} L2={l2_status}{diag_label}")
                if result.error:
                    rich.print(f"    [red]{result.error}[/red]")
                if result.diagnostic_results:
                    for idx, dr in enumerate(result.diagnostic_results, 1):
                        rich.print(f"    [cyan]Diagnostic #{idx}[/cyan] (confidence: {dr.confidence}): {dr.diagnosis}")

        finally:
            rich.print("\n[bold yellow]=== TEARING DOWN TEST ENVIRONMENT ===[/bold yellow]")
            env.teardown()
            rich.print("[green]Test environment cleaned up.[/green]")

    @listen(test_snippets)
    def finalize_script(self):
        """Step 4: Concatenate validated snippets through the post-processor pipeline and write the final script."""
        if not self.state.generated_snippets:
            print("No generated snippets to finalize. Skipping.")
            return

        # Only include validated snippets in the final script
        validated = [s for s in self.state.generated_snippets if s.validated]
        skipped = [s for s in self.state.generated_snippets if not s.validated]

        if skipped:
            rich.print("\n[bold yellow]=== SKIPPED SNIPPETS (validation failed) ===[/bold yellow]")
            for s in skipped:
                rich.print(f"  [red]✗[/red] {s.atom_name}")
                # Find the test result with failure reasoning
                if self.state.test_results:
                    for tr in self.state.test_results:
                        if tr.atom_name == s.atom_name:
                            if not tr.layer1_verdict.passed:
                                rich.print(f"    Layer 1: {tr.layer1_verdict.reasoning}")
                            if tr.layer2_verdicts:
                                for v in tr.layer2_verdicts:
                                    if not v.passed:
                                        rich.print(f"    Layer 2: {v.reasoning}")
                            if tr.error:
                                rich.print(f"    Error: {tr.error}")
                            if tr.diagnostic_results:
                                rich.print(f"    [bold cyan]Diagnostic History ({len(tr.diagnostic_results)} attempts):[/bold cyan]")
                                for idx, dr in enumerate(tr.diagnostic_results, 1):
                                    rich.print(f"      #{idx} [confidence: {dr.confidence}]")
                                    rich.print(f"         Diagnosis: {dr.diagnosis}")
                                    if dr.fixed_code_snippet != s.code_snippet:
                                        rich.print(f"         [dim]Code was modified in this attempt[/dim]")
                                    if dr.fixed_testing_snippet and dr.fixed_testing_snippet != getattr(s, 'testing_snippet', ''):
                                        rich.print(f"         [dim]Testing snippet was modified in this attempt[/dim]")

        if not validated:
            rich.print("[bold red]No snippets passed validation. No deployment script generated.[/bold red]")
            return

        # Concatenate validated snippets in order, separated by labelled section headers
        sections = []
        for snippet in validated:
            header = f"# --- {snippet.atom_name} ---"
            sections.append(f"{header}\n{snippet.code_snippet}")
        raw_script = "\n\n".join(sections)

        # Run through the extensible post-processor pipeline
        # (injects shebang, adds set -e, normalises blank lines, ...)
        final_script = apply_post_processors(raw_script)
        self.state.final_script = final_script

        # Write to output/<timestamp>_deploy.sh
        output_dir = Path(__file__).parent.parent.parent.parent / "output"
        output_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = output_dir / f"{timestamp}_deploy.sh"
        out_path.write_text(final_script, encoding="utf-8")
        out_path.chmod(0o755)

        rich.print("\n[bold magenta]=== FINAL DEPLOYMENT SCRIPT ===[/bold magenta]")
        rich.print(final_script)
        rich.print(f"\n[bold green]Written to:[/bold green] {out_path}")

def kickoff():
    goe_flow = GoEFlow()
    goe_flow.kickoff()

def plot():
    goe_flow = GoEFlow()
    goe_flow.plot("goe_flow.png")

if __name__ == "__main__":
    kickoff()
