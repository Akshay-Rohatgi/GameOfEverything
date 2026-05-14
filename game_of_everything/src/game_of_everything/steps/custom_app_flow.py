"""CustomAppFlow — generates and validates a single vulnerable web application.

Takes a CustomVector and produces a ResolvedCustomApp containing a validated
deploy_snippet ready for sequencing into the main GoEFlow deploy script.

Steps:
    1. load_context    — fetch vuln atom from ChromaDB + load attack goal + runtime YAMLs
    2. generate_app    — Opus-class agent writes app code + all supporting files
    3. validate_end_to_end — Docker L1/L2 testing with diagnostic retry loop (max 2)
    4. emit_result     — package into ResolvedCustomApp
"""

import yaml
import boto3


def _si(s: str) -> str:
    """Sanitize a string for crewAI crew.kickoff() inputs — see test_snippets._si."""
    return s.replace("{{", "{ {").replace("}}", "} }")
import chromadb
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from crewai import Agent, Task, Crew, Process
from crewai.flow import Flow, listen, start
from dotenv import load_dotenv
from chromadb.utils.embedding_functions import AmazonBedrockEmbeddingFunction

from game_of_everything.config import GoEConfig

from game_of_everything.models import (
    CustomVector, CustomAppState, GeneratedApp, ResolvedCustomApp, TestVerdict,
    AttackDiagnosticResult,
)
from game_of_everything.tools.test_environment import TestEnvironmentTool, RUNTIME_TARGET_IMAGES
from game_of_everything.llm_factory import make_llm

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole

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
    cfg = GoEConfig.get()
    aws_session = boto3.Session(
        aws_access_key_id=cfg.aws_access_key_id,
        aws_secret_access_key=cfg.aws_secret_access_key,
        region_name=cfg.aws_region,
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
    ui: Optional["GoEConsole"] = None,
) -> GeneratedApp:
    """Run the app_generation_agent crew and return a GeneratedApp."""
    assert state.vuln_atom_contents and state.attack_goals and state.web_runtime and state.vector

    llm = make_llm("app_generation_agent")
    generator = Agent(
        config=agents_config["app_generation_agent"],
        llm=llm,
        verbose=False,
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
        verbose=False,
        function_calling_llm=llm,
    )

    failure_section = (
        f"FAILURE CONTEXT (retry — fix the issues below):\n{failure_context}"
        if failure_context
        else ""
    )

    # Join multiple vuln atoms with clear separators
    vuln_atoms_text = "\n\n---\n\n".join(
        f"VULN ATOM {i+1}/{len(state.vuln_atom_contents)} ({state.vector.vuln_atom_ids[i]}):\n{content}"
        for i, content in enumerate(state.vuln_atom_contents)
    )
    attack_goals_text = "\n\n---\n\n".join(
        f"ATTACK GOAL {i+1}/{len(state.attack_goals)} ({state.vector.attack_chain_goals[i]}):\n{yaml.dump(goal)}"
        for i, goal in enumerate(state.attack_goals)
    )

    inputs = {
        "vuln_atom": vuln_atoms_text,
        "attack_goal": attack_goals_text,
        "web_runtime": yaml.dump(state.web_runtime),
        "synthesis_context": state.vector.synthesis_context,
        "failure_context": failure_section,
        "port": str(state.vector.port),
        "num_vulns": str(len(state.vuln_atom_contents)),
    }

    if ui:
        with ui.capture():
            crew.kickoff(inputs=inputs)
    else:
        crew.kickoff(inputs=inputs)

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
    ui: Optional["GoEConsole"] = None,
) -> TestVerdict:
    llm = make_llm("testing_agent")
    tester = Agent(
        config=agents_config["testing_agent"],
        llm=llm,
        verbose=False,
    )  # type: ignore

    verdict_task = Task(
        config=tasks_config["validate_snippets_task"],  # type: ignore
        agent=tester,
        output_pydantic=TestVerdict,
    )

    verdict_crew = Crew(
        agents=[tester],
        tasks=[verdict_task],
        process=Process.sequential,
        verbose=False,
        function_calling_llm=llm,
    )

    inputs = {
        "atom_name": atom_name,
        "atom_context": _si(atom_context),
        "layer": layer,
        "snippet_executed": _si(snippet_executed),
        "exit_code": str(exit_code),
        "stdout": _si(stdout or "(empty)"),
        "stderr": _si(stderr or "(empty)"),
    }

    if ui:
        with ui.capture():
            verdict_crew.kickoff(inputs=inputs)
    else:
        verdict_crew.kickoff(inputs=inputs)

    if verdict_task.output.pydantic:  # type: ignore
        return verdict_task.output.pydantic  # type: ignore
    return TestVerdict(passed=False, reasoning="Failed to parse LLM verdict output.")


MAX_ATTACK_RETRIES = 2


def _run_attack_agent_crew(
    state: CustomAppState,
    agents_config: dict,
    tasks_config: dict,
    l2_exit_code: int,
    l2_stdout: str,
    l2_stderr: str,
    l1_exit_code: int,
    l1_stdout: str,
    verdict_reasoning: str,
    attempt_number: int,
    target_container_name: str,
    attacker_container_name: str,
    ui: Optional["GoEConsole"] = None,
) -> AttackDiagnosticResult:
    """Run the Attack Agent to fix a failing L2 attack snippet."""
    from game_of_everything.tools.bound_exec_tools import (
        BoundExecInAttackerTool,
        BoundExecInTargetTool,
    )

    llm = make_llm("attack_agent")

    attacker_tool = BoundExecInAttackerTool(container_name=attacker_container_name)
    target_tool = BoundExecInTargetTool(container_name=target_container_name)

    agent = Agent(
        config=agents_config["attack_agent"],
        llm=llm,
        tools=[attacker_tool, target_tool],
        verbose=False,
    )  # type: ignore

    task = Task(
        config=tasks_config["fix_attack_snippet_task"],  # type: ignore
        agent=agent,
        output_pydantic=AttackDiagnosticResult,
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
        function_calling_llm=llm,
    )

    generated_app = state.generated_app
    assert generated_app and state.vector

    attack_goals_text = "\n\n---\n\n".join(
        f"ATTACK GOAL {i+1}/{len(state.attack_goals)} ({state.vector.attack_chain_goals[i]}):\n{yaml.dump(goal)}"
        for i, goal in enumerate(state.attack_goals)
    )

    inputs = {
        "app_filename": generated_app.app_filename,
        "app_source": _si(generated_app.app_source),
        "attack_goal": _si(attack_goals_text),
        "synthesis_context": _si(state.vector.synthesis_context or ""),
        "failed_attack_snippet": _si(generated_app.attack_snippet),
        "l2_exit_code": str(l2_exit_code),
        "l2_stdout": _si(l2_stdout[:2000] or "(empty)"),
        "l2_stderr": _si(l2_stderr[:2000] or "(empty)"),
        "verdict_reasoning": _si(verdict_reasoning),
        "testing_snippet": _si(generated_app.testing_snippet),
        "l1_exit_code": str(l1_exit_code),
        "l1_stdout": _si(l1_stdout[:2000] or "(empty)"),
        "deploy_snippet": _si(generated_app.deploy_snippet),
        "port": str(state.vector.port),
        "attempt_number": str(attempt_number),
        "max_attempts": str(MAX_ATTACK_RETRIES),
    }

    if ui:
        with ui.capture():
            crew.kickoff(inputs=inputs)
    else:
        crew.kickoff(inputs=inputs)

    if task.output.pydantic:  # type: ignore
        return task.output.pydantic  # type: ignore
    return AttackDiagnosticResult(
        fixed_attack_snippet=generated_app.attack_snippet,
        diagnosis="Failed to parse attack agent output.",
        confidence="low",
    )


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

    def __init__(self, vector: CustomVector, ui: Optional["GoEConsole"] = None):
        super().__init__()
        self.state.vector = vector
        self.agents_config = _load_yaml(_CONFIG_DIR / "agents.yaml")
        self.tasks_config = _load_yaml(_CONFIG_DIR / "tasks.yaml")
        self.ui = ui

    def _log(self, msg: str) -> None:
        """Write to log file if ui is available, else print."""
        if self.ui:
            self.ui.log(msg)
        else:
            print(msg)

    def _progress(self, msg: str) -> None:
        """Write a dim sub-line to the terminal (and log file)."""
        if self.ui:
            self.ui.info(f"    [dim]{msg}[/dim]")
            self.ui.log(msg)
        else:
            print(msg)

    @start()
    def load_context(self) -> None:
        """Fetch vuln atoms from ChromaDB and load attack goal + web runtime YAMLs."""
        v = self.state.vector
        assert v is not None

        self._log(f"\n=== CustomAppFlow: load_context ===")
        self._log(f"  vuln_atom_ids   : {v.vuln_atom_ids}")
        self._log(f"  attack_goals    : {v.attack_chain_goals}")
        self._log(f"  runtime         : {v.runtime_id}")

        self.state.vuln_atom_contents = [_fetch_vuln_atom(aid) for aid in v.vuln_atom_ids]
        self.state.attack_goals = [_load_attack_goal(gid) for gid in v.attack_chain_goals]
        self.state.web_runtime = _load_web_runtime(v.runtime_id)

        self._log(f"  Context loaded ({len(v.vuln_atom_ids)} vuln atom(s), {len(v.attack_chain_goals)} goal(s)).")

    @listen(load_context)
    def generate_app(self) -> None:
        """Run the Opus-class generation crew to produce app code and snippets."""
        self._log("\n=== CustomAppFlow: generate_app (attempt 1) ===")
        self._progress("Generating app (Opus)...")
        self.state.generated_app = _run_app_generation_crew(
            self.state, self.agents_config, self.tasks_config, ui=self.ui,
        )
        self.state.generate_attempts = 1
        self._log(f"  App generated: {self.state.generated_app.app_filename}")

    @listen(generate_app)
    def validate_end_to_end(self) -> None:
        """Deploy the app in Docker containers and run L1 + L2 tests with retry loop."""
        assert self.state.generated_app and self.state.vector

        self._log("\n=== CustomAppFlow: validate_end_to_end ===")

        runtime_id = self.state.vector.runtime_id
        runtime_info = RUNTIME_TARGET_IMAGES.get(runtime_id)
        target_image = runtime_info["tag"] if runtime_info else ""
        # Enable browser for all custom app testing (Phase 2: Attack Orchestrator)
        env = TestEnvironmentTool(target_image=target_image, enable_browser=True)
        failure_context = ""

        try:
            env.setup()
            self._log("Test environment ready.")

            for attempt in range(1 + MAX_GENERATE_RETRIES):
                is_retry = attempt > 0
                generated_app = self.state.generated_app
                assert generated_app

                if is_retry:
                    self._log(f"\n--- Regenerating app (attempt {attempt + 1}/{1 + MAX_GENERATE_RETRIES}) ---")
                    self._progress(f"Regenerating app (attempt {attempt + 1}/{1 + MAX_GENERATE_RETRIES})...")
                    self.state.generated_app = _run_app_generation_crew(
                        self.state, self.agents_config, self.tasks_config,
                        failure_context=failure_context,
                        ui=self.ui,
                    )
                    generated_app = self.state.generated_app
                    self.state.generate_attempts += 1
                    env.teardown()
                    env.setup()

                # Stage files and run deploy_snippet
                self._log("  Staging app files...")
                self._progress("Deploying app in container...")
                _stage_app_files(env, generated_app)

                self._log("  Running deploy_snippet...")
                deploy_exit, deploy_stdout, deploy_stderr = env.exec_in_target(
                    generated_app.deploy_snippet
                )
                self._log(f"  Deploy exit code: {deploy_exit}")
                if deploy_stderr:
                    self._log(f"  Deploy stderr: {deploy_stderr[:500]}")

                # --- Attack Orchestrator (L1 + L2) ---
                self._log("  Running Attack Orchestrator (L1 + L2)...")
                self._progress("Attack orchestrator: validating app...")

                from game_of_everything.crews.attack_orchestrator_crew import run_attack_orchestrator_crew
                from game_of_everything.models import TestVerdict

                orch_result = run_attack_orchestrator_crew(
                    agents_config=self.agents_config,
                    tasks_config=self.tasks_config,
                    generated_app=generated_app,
                    synthesis_context=self.state.vector.synthesis_context,  # type: ignore
                    port=self.state.vector.port,  # type: ignore
                    target_container_name=env.target_name,
                    attacker_container_name=env.attacker_name,
                    cdp_url=env.browser_cdp_url,
                    attempt_number=attempt + 1,
                    max_attempts=1 + MAX_GENERATE_RETRIES,
                    failure_context=failure_context,
                    ui=self.ui,
                )

                l1_icon = "[green]✓[/green]" if orch_result.l1_passed else "[red]✗[/red]"
                l2_icon = "[green]✓[/green]" if orch_result.l2_passed else "[red]✗[/red]"
                self._log(f"  Layer 1: {'PASS' if orch_result.l1_passed else 'FAIL'}")
                self._log(f"  Layer 2: {'PASS' if orch_result.l2_passed else 'FAIL'}")
                self._log(f"  Reasoning: {orch_result.reasoning}")
                if orch_result.used_browser:
                    self._log(f"  Used browser: yes")

                if self.ui:
                    self.ui.info(f"    {l1_icon} L1")
                    if orch_result.l1_passed:
                        self.ui.info(f"    {l2_icon} L2")

                if not orch_result.l1_passed:
                    failure_context = (
                        f"Layer 1 (internal state check) FAILED.\n"
                        f"Deploy stderr:\n{deploy_stderr[:800]}\n"
                        f"L1 evidence:\n{orch_result.l1_evidence[:800]}\n"
                        f"Reasoning: {orch_result.reasoning}"
                    )
                    if attempt < MAX_GENERATE_RETRIES:
                        continue
                    else:
                        self.state.layer1_verdict = TestVerdict(
                            passed=False, reasoning=orch_result.reasoning
                        )
                        self._log(f"Layer 1 failed after {1 + MAX_GENERATE_RETRIES} attempts.")
                        raise AppGenerationError(
                            f"CustomAppFlow: Layer 1 failed after {1 + MAX_GENERATE_RETRIES} attempts. "
                            f"Last reason: {orch_result.reasoning}"
                        )

                if not orch_result.l2_passed:
                    failure_context = (
                        f"Layer 1 PASSED but Layer 2 (external attack probe) FAILED.\n"
                        f"L2 evidence:\n{orch_result.l2_evidence[:800]}\n"
                        f"Reasoning: {orch_result.reasoning}"
                    )
                    if attempt < MAX_GENERATE_RETRIES:
                        continue
                    else:
                        self.state.layer1_verdict = TestVerdict(
                            passed=True, reasoning=orch_result.l1_evidence
                        )
                        self.state.layer2_verdict = TestVerdict(
                            passed=False, reasoning=orch_result.reasoning
                        )
                        self._log(f"Layer 2 failed after {1 + MAX_GENERATE_RETRIES} attempts.")
                        raise AppGenerationError(
                            f"CustomAppFlow: Layer 2 failed after {1 + MAX_GENERATE_RETRIES} attempts. "
                            f"Last reason: {orch_result.reasoning}"
                        )

                # Both layers passed
                self.state.layer1_verdict = TestVerdict(
                    passed=True, reasoning=orch_result.l1_evidence
                )
                self.state.layer2_verdict = TestVerdict(
                    passed=True, reasoning=orch_result.l2_evidence
                )
                self._log(f"Both layers passed on attempt {attempt + 1}.")
                return

        finally:
            env.teardown()
            self._log("Test environment cleaned up.")

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
            attack_snippet="",  # Deprecated: orchestrator uses attack_objective instead
            validation_passed=l1_passed and l2_passed,
        )

        self._log(f"\n=== CustomAppFlow complete ===")
        self._log(f"  App        : {self.state.generated_app.app_filename}")
        self._log(f"  Attempts   : {self.state.generate_attempts}")
        self._log(f"  Validated  : {self.state.resolved.validation_passed}")
