"""CustomAppFlow — generates and validates a single vulnerable web application.

Takes a CustomVector and produces a ResolvedCustomApp containing a validated
deploy_snippet ready for sequencing into the main GoEFlow deploy script.

Steps:
    1. load_context    — fetch vuln atom from ChromaDB + load attack goal + runtime YAMLs
    2. generate_app    — Opus-class agent writes app code + all supporting files
    3. validate_end_to_end — Docker L1/L2 testing with diagnostic retry loop (max 2)
    4. emit_result     — package into ResolvedCustomApp
"""

import os
import yaml
import boto3


def _si(s: str) -> str:
    """Sanitize a string for crewAI crew.kickoff() inputs — see test_snippets._si."""
    return s.replace("{{", "{ {").replace("}}", "} }")
import chromadb
import rich
from pathlib import Path
from typing import Optional

from crewai import Agent, Task, Crew, Process
from crewai.flow import Flow, listen, start
from dotenv import load_dotenv
from chromadb.utils.embedding_functions import AmazonBedrockEmbeddingFunction

from game_of_everything.models import (
    CustomVector, CustomAppState, GeneratedApp, ResolvedCustomApp, TestVerdict,
)
from game_of_everything.tools.test_environment import TestEnvironmentTool
from game_of_everything.llm_factory import make_llm

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SRC_DIR = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _SRC_DIR / "config"
_CUSTOM_APPS_DIR = _SRC_DIR / "custom_apps"
_CHROMA_DB_PATH = _SRC_DIR / "chroma_db"

# Staging directory convention: app files are written here before deploy_snippet runs
_STAGE_DIR = "/tmp/goe_app"

MAX_GENERATE_RETRIES = 2


class AppGenerationError(Exception):
    """Raised when CustomAppFlow exhausts all retries without a passing validation."""
    pass


# ---------------------------------------------------------------------------
# Helper: load configs
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_attack_goal(goal_id: str) -> dict:
    path = _CUSTOM_APPS_DIR / "attack_goals" / f"{goal_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Attack goal not found: {path}")
    return _load_yaml(path)


def _load_web_runtime(runtime_id: str) -> dict:
    path = _CUSTOM_APPS_DIR / "web_runtimes" / f"{runtime_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Web runtime not found: {path}")
    return _load_yaml(path)


def _fetch_vuln_atom(vuln_atom_id: str) -> str:
    """Query web_vuln_atoms ChromaDB collection and return the atom's markdown."""
    aws_session = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )
    bedrock_ef = AmazonBedrockEmbeddingFunction(
        session=aws_session,
        model_name="amazon.titan-embed-text-v2:0",
    )
    client = chromadb.PersistentClient(path=str(_CHROMA_DB_PATH))
    collection = client.get_collection(name="web_vuln_atoms", embedding_function=bedrock_ef)  # type: ignore
    results = collection.get(ids=[vuln_atom_id], include=["documents"])
    if not results["documents"]:
        raise ValueError(f"Vuln atom '{vuln_atom_id}' not found in web_vuln_atoms collection. Run rag_gen.py first.")
    return results["documents"][0]


# ---------------------------------------------------------------------------
# Helper: generation crew
# ---------------------------------------------------------------------------

def _run_app_generation_crew(
    state: CustomAppState,
    agents_config: dict,
    tasks_config: dict,
    failure_context: str = "",
) -> GeneratedApp:
    """Run the app_generation_agent crew and return a GeneratedApp."""
    assert state.vuln_atom_content and state.attack_goal and state.web_runtime and state.vector

    llm = make_llm("app_generation_agent")
    generator = Agent(
        config=agents_config["app_generation_agent"],
        llm=llm,
        verbose=True,
        step_callback=lambda step: print(f"[APP-GEN] {step}"),
    )  # type: ignore

    gen_task = Task(
        config=tasks_config["generate_app_task"],  # type: ignore
        agent=generator,
        output_pydantic=GeneratedApp,
    )

    crew = Crew(
        agents=[generator],
        tasks=[gen_task],
        process=Process.sequential,
        verbose=True,
        function_calling_llm=llm,
    )

    failure_section = (
        f"FAILURE CONTEXT (retry — fix the issues below):\n{failure_context}"
        if failure_context
        else ""
    )

    crew.kickoff(inputs={
        "vuln_atom": state.vuln_atom_content,
        "attack_goal": yaml.dump(state.attack_goal),
        "web_runtime": yaml.dump(state.web_runtime),
        "synthesis_context": state.vector.synthesis_context,
        "failure_context": failure_section,
        "port": str(state.vector.port),
    })

    generated: GeneratedApp = gen_task.output.pydantic  # type: ignore
    return generated


# ---------------------------------------------------------------------------
# Helper: verdict crew (reuses testing_agent from main flow)
# ---------------------------------------------------------------------------

def _run_verdict_crew(
    agents_config: dict,
    tasks_config: dict,
    atom_name: str,
    atom_context: str,
    layer: str,
    snippet_executed: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> TestVerdict:
    llm = make_llm("testing_agent")
    tester = Agent(
        config=agents_config["testing_agent"],
        llm=llm,
        verbose=True,
        step_callback=lambda step: print(f"[APP-TESTER] {step}"),
    )  # type: ignore

    verdict_task = Task(
        config=tasks_config["validate_snippets_task"],  # type: ignore
        agent=tester,
        output_pydantic=TestVerdict,
    )

    Crew(
        agents=[tester],
        tasks=[verdict_task],
        process=Process.sequential,
        verbose=True,
        function_calling_llm=llm,
    ).kickoff(inputs={
        "atom_name": atom_name,
        "atom_context": _si(atom_context),
        "layer": layer,
        "snippet_executed": _si(snippet_executed),
        "exit_code": str(exit_code),
        "stdout": _si(stdout or "(empty)"),
        "stderr": _si(stderr or "(empty)"),
    })

    if verdict_task.output.pydantic:  # type: ignore
        return verdict_task.output.pydantic  # type: ignore
    return TestVerdict(passed=False, reasoning="Failed to parse LLM verdict output.")


# ---------------------------------------------------------------------------
# Helper: stage app files into the target container
# ---------------------------------------------------------------------------

def _stage_app_files(env: TestEnvironmentTool, generated_app: GeneratedApp) -> None:
    """Copy all generated app files to /tmp/goe_app/ in the target container."""
    env.exec_in_target(f"mkdir -p {_STAGE_DIR}")
    env.copy_to_target(generated_app.app_source, f"{_STAGE_DIR}/{generated_app.app_filename}")
    if generated_app.setup_db_sh:
        env.copy_to_target(generated_app.setup_db_sh, f"{_STAGE_DIR}/setup_db.sh")
        env.exec_in_target(f"chmod +x {_STAGE_DIR}/setup_db.sh")
    if generated_app.schema_sql:
        env.copy_to_target(generated_app.schema_sql, f"{_STAGE_DIR}/schema.sql")
    if generated_app.seed_sql:
        env.copy_to_target(generated_app.seed_sql, f"{_STAGE_DIR}/seed.sql")


# ---------------------------------------------------------------------------
# CustomAppFlow
# ---------------------------------------------------------------------------

def _package_deploy_snippet(generated_app: GeneratedApp) -> str:
    """Prepend heredoc file-staging preamble to deploy_snippet.

    The deploy_snippet references /tmp/goe_app/ paths (e.g. cp /tmp/goe_app/app.py ...).
    During Docker testing those files are staged via copy_to_target(), but the final
    deploy script runs on a bare machine with no staging directory.  This function
    embeds all app files as quoted heredocs so the script is fully self-contained.

    DB-related files (schema.sql, seed.sql, setup_db.sh) are only included when
    the generated app actually uses a database.
    """
    # App source is always required; DB files are conditional
    files = [(generated_app.app_filename, generated_app.app_source)]
    if generated_app.schema_sql:
        files.append(("schema.sql", generated_app.schema_sql))
    if generated_app.seed_sql:
        files.append(("seed.sql", generated_app.seed_sql))
    if generated_app.setup_db_sh:
        files.append(("setup_db.sh", generated_app.setup_db_sh))

    lines = [f"mkdir -p {_STAGE_DIR}"]
    for filename, content in files:
        # Use a per-file delimiter to avoid collisions with file content
        safe_name = filename.replace(".", "_").upper()
        delimiter = f"GOE_{safe_name}_EOF"
        lines.append(f"cat > {_STAGE_DIR}/{filename} << '{delimiter}'")
        lines.append(content)
        lines.append(delimiter)

    if generated_app.setup_db_sh:
        lines.append(f"chmod +x {_STAGE_DIR}/setup_db.sh")
    lines.append("")  # blank line before deploy commands

    preamble = "\n".join(lines)
    return f"{preamble}\n{generated_app.deploy_snippet}\n\n# Cleanup staging directory\nrm -rf {_STAGE_DIR}"


class CustomAppFlow(Flow[CustomAppState]):

    def __init__(self, vector: CustomVector):
        super().__init__()
        self.state.vector = vector
        self.agents_config = _load_yaml(_CONFIG_DIR / "agents.yaml")
        self.tasks_config = _load_yaml(_CONFIG_DIR / "tasks.yaml")

    @start()
    def load_context(self) -> None:
        """Fetch vuln atom from ChromaDB and load attack goal + web runtime YAMLs."""
        v = self.state.vector
        assert v is not None

        rich.print(f"\n[bold cyan]=== CustomAppFlow: load_context ===[/bold cyan]")
        rich.print(f"  vuln_atom_id    : {v.vuln_atom_id}")
        rich.print(f"  attack_goal     : {v.attack_chain_goal}")
        rich.print(f"  runtime         : {v.runtime_id}")

        self.state.vuln_atom_content = _fetch_vuln_atom(v.vuln_atom_id)
        self.state.attack_goal = _load_attack_goal(v.attack_chain_goal)
        self.state.web_runtime = _load_web_runtime(v.runtime_id)

        rich.print(f"  [green]Context loaded.[/green]")

    @listen(load_context)
    def generate_app(self) -> None:
        """Run the Opus-class generation crew to produce app code and snippets."""
        rich.print(f"\n[bold cyan]=== CustomAppFlow: generate_app (attempt 1) ===[/bold cyan]")
        self.state.generated_app = _run_app_generation_crew(
            self.state, self.agents_config, self.tasks_config
        )
        self.state.generate_attempts = 1
        rich.print(f"  [green]App generated: {self.state.generated_app.app_filename}[/green]")

    @listen(generate_app)
    def validate_end_to_end(self) -> None:
        """Deploy the app in Docker containers and run L1 + L2 tests with retry loop."""
        assert self.state.generated_app and self.state.vector

        rich.print(f"\n[bold cyan]=== CustomAppFlow: validate_end_to_end ===[/bold cyan]")

        env = TestEnvironmentTool()
        failure_context = ""

        try:
            env.setup()
            rich.print("[green]Test environment ready.[/green]")

            for attempt in range(1 + MAX_GENERATE_RETRIES):
                is_retry = attempt > 0
                generated_app = self.state.generated_app
                assert generated_app

                if is_retry:
                    rich.print(f"\n[bold yellow]--- Regenerating app (attempt {attempt + 1}/{1 + MAX_GENERATE_RETRIES}) ---[/bold yellow]")
                    self.state.generated_app = _run_app_generation_crew(
                        self.state, self.agents_config, self.tasks_config,
                        failure_context=failure_context,
                    )
                    generated_app = self.state.generated_app
                    self.state.generate_attempts += 1
                    # Tear down and reset target container for a clean retry
                    env.teardown()
                    env.setup()

                # Stage files and run deploy_snippet
                rich.print(f"  [yellow]Staging app files...[/yellow]")
                _stage_app_files(env, generated_app)

                rich.print(f"  [yellow]Running deploy_snippet...[/yellow]")
                deploy_exit, deploy_stdout, deploy_stderr = env.exec_in_target(
                    generated_app.deploy_snippet
                )
                rich.print(f"  Deploy exit code: {deploy_exit}")
                if deploy_stderr:
                    rich.print(f"  [dim]Deploy stderr: {deploy_stderr[:500]}[/dim]")

                # --- Layer 1 ---
                rich.print(f"  [blue]Running Layer 1 (internal state check)...[/blue]")
                l1_exit, l1_stdout, l1_stderr = env.exec_in_target(generated_app.testing_snippet)
                rich.print(f"  L1 exit code: {l1_exit}")

                l1_verdict = _run_verdict_crew(
                    agents_config=self.agents_config,
                    tasks_config=self.tasks_config,
                    atom_name=self.state.vector.vuln_atom_id,  # type: ignore
                    atom_context=self.state.vector.synthesis_context,  # type: ignore
                    layer="internal state check",
                    snippet_executed=generated_app.testing_snippet,
                    exit_code=l1_exit,
                    stdout=l1_stdout,
                    stderr=l1_stderr,
                )
                status = "[green]PASS[/green]" if l1_verdict.passed else "[red]FAIL[/red]"
                rich.print(f"  Layer 1: {status} — {l1_verdict.reasoning}")

                if not l1_verdict.passed:
                    failure_context = (
                        f"Layer 1 (internal state check) FAILED.\n"
                        f"Deploy stderr:\n{deploy_stderr[:800]}\n"
                        f"Testing snippet:\n{generated_app.testing_snippet}\n"
                        f"L1 exit code: {l1_exit}\n"
                        f"L1 stdout:\n{l1_stdout[:800]}\n"
                        f"L1 stderr:\n{l1_stderr[:800]}\n"
                        f"Verdict reasoning: {l1_verdict.reasoning}"
                    )
                    if attempt < MAX_GENERATE_RETRIES:
                        continue
                    else:
                        self.state.layer1_verdict = l1_verdict
                        rich.print(f"[bold red]Layer 1 failed after {1 + MAX_GENERATE_RETRIES} attempts.[/bold red]")
                        raise AppGenerationError(
                            f"CustomAppFlow: Layer 1 failed after {1 + MAX_GENERATE_RETRIES} attempts. "
                            f"Last reason: {l1_verdict.reasoning}"
                        )

                # --- Layer 2 ---
                rich.print(f"  [red]Running Layer 2 (external attack probe)...[/red]")
                env.ensure_attacker_tools([generated_app.attack_snippet])
                l2_exit, l2_stdout, l2_stderr = env.exec_in_attacker(generated_app.attack_snippet)
                rich.print(f"  L2 exit code: {l2_exit}")

                l2_verdict = _run_verdict_crew(
                    agents_config=self.agents_config,
                    tasks_config=self.tasks_config,
                    atom_name=self.state.vector.vuln_atom_id,  # type: ignore
                    atom_context=self.state.vector.synthesis_context,  # type: ignore
                    layer="external attack probe",
                    snippet_executed=generated_app.attack_snippet,
                    exit_code=l2_exit,
                    stdout=l2_stdout,
                    stderr=l2_stderr,
                )
                status = "[green]PASS[/green]" if l2_verdict.passed else "[red]FAIL[/red]"
                rich.print(f"  Layer 2: {status} — {l2_verdict.reasoning}")

                if not l2_verdict.passed:
                    failure_context = (
                        f"Layer 1 PASSED but Layer 2 (external attack probe) FAILED.\n"
                        f"Attack snippet:\n{generated_app.attack_snippet}\n"
                        f"L2 exit code: {l2_exit}\n"
                        f"L2 stdout:\n{l2_stdout[:800]}\n"
                        f"L2 stderr:\n{l2_stderr[:800]}\n"
                        f"Verdict reasoning: {l2_verdict.reasoning}"
                    )
                    if attempt < MAX_GENERATE_RETRIES:
                        continue
                    else:
                        self.state.layer1_verdict = l1_verdict
                        self.state.layer2_verdict = l2_verdict
                        rich.print(f"[bold red]Layer 2 failed after {1 + MAX_GENERATE_RETRIES} attempts.[/bold red]")
                        raise AppGenerationError(
                            f"CustomAppFlow: Layer 2 failed after {1 + MAX_GENERATE_RETRIES} attempts. "
                            f"Last reason: {l2_verdict.reasoning}"
                        )

                # Both layers passed
                self.state.layer1_verdict = l1_verdict
                self.state.layer2_verdict = l2_verdict
                rich.print(f"[bold green]Both layers passed on attempt {attempt + 1}.[/bold green]")
                return

        finally:
            env.teardown()
            rich.print("[green]Test environment cleaned up.[/green]")

    @listen(validate_end_to_end)
    def emit_result(self) -> None:
        """Package the validated app into a ResolvedCustomApp."""
        assert self.state.generated_app and self.state.vector

        l2_passed = self.state.layer2_verdict.passed if self.state.layer2_verdict else False
        l1_passed = self.state.layer1_verdict.passed if self.state.layer1_verdict else False

        packaged_snippet = _package_deploy_snippet(self.state.generated_app)

        self.state.resolved = ResolvedCustomApp(
            vector=self.state.vector,
            deploy_snippet=packaged_snippet,
            testing_snippet=self.state.generated_app.testing_snippet,
            attack_snippet=self.state.generated_app.attack_snippet,
            validation_passed=l1_passed and l2_passed,
        )

        rich.print(f"\n[bold green]=== CustomAppFlow complete ===[/bold green]")
        rich.print(f"  App        : {self.state.generated_app.app_filename}")
        rich.print(f"  Attempts   : {self.state.generate_attempts}")
        rich.print(f"  Validated  : {self.state.resolved.validation_passed}")
