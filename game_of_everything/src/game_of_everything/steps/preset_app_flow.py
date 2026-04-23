"""PresetAppFlow — deploys and validates a pre-built web application.

Takes a PresetVector and produces a ResolvedPresetApp containing a validated
deploy_snippet ready for sequencing into the main GoEFlow deploy script.

Unlike CustomAppFlow (which generates app code via LLM), PresetAppFlow uses
template-driven deploy snippets assembled from YAML definitions. The LLM is
only involved in verdict judgments during Docker L1/L2 testing.

Steps:
    1. load_and_render   — load YAML definitions, render deploy/test/attack snippets
    2. validate_end_to_end — Docker L1/L2 testing with retry loop (max 2)
    3. emit_result        — package into ResolvedPresetApp
"""

import re
import yaml
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from crewai import Agent, Task, Crew, Process
from crewai.flow import Flow, listen, start

from game_of_everything.models import (
    PresetVector, ResolvedPresetApp, TestVerdict,
)
from game_of_everything.tools.test_environment import (
    TestEnvironmentTool, PRESET_TARGET_IMAGE_TAG,
)
from game_of_everything.llm_factory import make_llm

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SRC_DIR = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _SRC_DIR / "config"
_PRESET_APPS_DIR = _SRC_DIR / "preset_apps"

MAX_VALIDATE_RETRIES = 2


class PresetDeployError(Exception):
    """Raised when PresetAppFlow exhausts all retries without a passing validation."""
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_stack(stack_id: str) -> dict:
    path = _PRESET_APPS_DIR / "stacks" / f"{stack_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Stack not found: {path}")
    return _load_yaml(path)


def _load_preset(preset_id: str) -> dict:
    path = _PRESET_APPS_DIR / "presets" / f"{preset_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Preset not found: {path}")
    return _load_yaml(path)


def _load_vuln_profile(profile_id: str) -> dict:
    path = _PRESET_APPS_DIR / "vuln_profiles" / f"{profile_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Vuln profile not found: {path}")
    return _load_yaml(path)


def _load_harness(harness_id: str) -> str:
    path = _PRESET_APPS_DIR / "harnesses" / f"{harness_id}.sh"
    if not path.exists():
        raise FileNotFoundError(f"Harness not found: {path}")
    return path.read_text(encoding="utf-8")


def _render_template(template: str, variables: dict) -> str:
    """Replace {{var}} placeholders with values from the variables dict."""
    result = template
    for key, value in variables.items():
        result = result.replace("{{" + key + "}}", str(value))
    return result


def _si(s: str) -> str:
    """Sanitize a string for crewAI crew.kickoff() inputs."""
    return s.replace("{{", "{ {").replace("}}", "} }")


# ---------------------------------------------------------------------------
# Verdict crew (reuses testing_agent from main flow config)
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


# ---------------------------------------------------------------------------
# State model for the flow
# ---------------------------------------------------------------------------

from pydantic import BaseModel


class PresetAppState(BaseModel):
    vector: Optional[PresetVector] = None
    preset_def: Optional[dict] = None
    stack_def: Optional[dict] = None
    vuln_profiles: list = []
    harness_content: Optional[str] = None
    # Rendered snippets
    deploy_snippet: Optional[str] = None
    testing_snippet: Optional[str] = None
    attack_snippet: Optional[str] = None
    # Verdicts
    layer1_verdict: Optional[TestVerdict] = None
    layer2_verdict: Optional[TestVerdict] = None
    validate_attempts: int = 0
    resolved: Optional[ResolvedPresetApp] = None


# ---------------------------------------------------------------------------
# PresetAppFlow
# ---------------------------------------------------------------------------

class PresetAppFlow(Flow[PresetAppState]):

    def __init__(self, vector: PresetVector, ui: Optional["GoEConsole"] = None):
        super().__init__()
        self.state.vector = vector
        self.agents_config = _load_yaml(_CONFIG_DIR / "agents.yaml")
        self.tasks_config = _load_yaml(_CONFIG_DIR / "tasks.yaml")
        self.ui = ui

    def _log(self, msg: str) -> None:
        if self.ui:
            self.ui.log(msg)
        else:
            print(msg)

    # -------------------------------------------------------------------
    # Step 1: Load YAML definitions and render all snippets
    # -------------------------------------------------------------------

    @start()
    def load_and_render(self) -> None:
        """Load preset, stack, and vuln profile YAMLs. Render deploy/test/attack snippets."""
        v = self.state.vector
        assert v is not None

        self._log(f"\n=== PresetAppFlow: load_and_render ===")
        self._log(f"  preset_id       : {v.preset_id}")
        self._log(f"  vuln_profiles   : {v.vuln_profile_ids}")
        self._log(f"  port            : {v.port}")

        # Load definitions
        preset = _load_preset(v.preset_id)
        stack = _load_stack(preset["stack_id"])
        profiles = [_load_vuln_profile(pid) for pid in v.vuln_profile_ids]
        harness_content = _load_harness(preset["harness_id"])

        self.state.preset_def = preset
        self.state.stack_def = stack
        self.state.vuln_profiles = profiles
        self.state.harness_content = harness_content

        # Build template variables: defaults → vector overrides → extra_vars
        template_vars: dict = {}
        template_vars.update(preset.get("defaults", {}))
        for field in ("admin_user", "admin_password", "db_name", "db_user", "db_password"):
            val = getattr(v, field, None)
            if val is not None:
                template_vars[field] = val
        template_vars["port"] = str(v.port)
        template_vars.update(v.extra_vars)

        # Apply defaults for well-known optional vars that profiles may reference.
        # The synthesis agent can override any of these via extra_vars.
        template_vars.setdefault("post_title", "Internal Notes")
        template_vars.setdefault("post_status", "private")
        if "post_content" not in template_vars:
            # Build a realistic post body from seed credentials if available
            seed_user = template_vars.get("seed_username", "")
            seed_pass = template_vars.get("seed_password", "")
            if seed_user and seed_pass:
                template_vars["post_content"] = (
                    f"<p>Credentials for the service account:</p>"
                    f"<p>Username: {seed_user}<br>Password: {seed_pass}</p>"
                )
            else:
                template_vars["post_content"] = ""

        # Validate required vars
        for var in preset.get("required_vars", []):
            if var not in template_vars:
                raise ValueError(f"Missing required variable '{var}' for preset '{v.preset_id}'")

        # --- Render deploy snippet ---
        deploy_parts = []
        # Stack install
        deploy_parts.append(f"# --- Stack: {stack['id']} ---")
        deploy_parts.append(stack["install_snippet"].strip())
        # DB setup
        if "db_setup_template" in stack and "db_name" in template_vars:
            deploy_parts.append(f"\n# --- DB setup ---")
            deploy_parts.append(_render_template(stack["db_setup_template"], template_vars).strip())
        # App install
        deploy_parts.append(f"\n# --- Preset: {preset['id']} ---")
        deploy_parts.append(_render_template(preset["install_template"], template_vars).strip())
        # Healthcheck
        if "healthcheck" in preset:
            deploy_parts.append(f"\n# --- Healthcheck ---")
            deploy_parts.append(_render_template(preset["healthcheck"], template_vars).strip())
        # Vuln profile config snippets
        for profile in profiles:
            config_snippet = profile.get("vuln_config_snippet", "").strip()
            if config_snippet:
                deploy_parts.append(f"\n# --- Vuln: {profile['id']} ---")
                deploy_parts.append(_render_template(config_snippet, template_vars).strip())

        self.state.deploy_snippet = "\n".join(deploy_parts)

        # --- Render testing snippet (L1) ---
        test_parts = []
        for profile in profiles:
            tmpl = profile.get("testing_snippet_template", "").strip()
            if tmpl:
                test_parts.append(f"# --- L1: {profile['id']} ---")
                test_parts.append(_render_template(tmpl, template_vars).strip())
        self.state.testing_snippet = "\n".join(test_parts)

        # --- Render attack snippet (L2) ---
        # Deduplicate lines across profiles (e.g. multiple profiles may source
        # the same harness and call wp_login — emit each unique line once).
        seen_lines: set = set()
        attack_lines: list = []
        for profile in profiles:
            tmpl = profile.get("attack_snippet_template", "").strip()
            if tmpl:
                rendered = _render_template(tmpl, template_vars).strip()
                for line in rendered.splitlines():
                    if line not in seen_lines:
                        seen_lines.add(line)
                        attack_lines.append(line)
        self.state.attack_snippet = "\n".join(attack_lines)

        self._log(f"  Deploy snippet : {len(self.state.deploy_snippet)} chars")
        self._log(f"  Test snippet   : {len(self.state.testing_snippet)} chars")
        self._log(f"  Attack snippet : {len(self.state.attack_snippet)} chars")
        self._log("  Rendering complete.")

    # -------------------------------------------------------------------
    # Step 2: Docker L1/L2 validation with retry loop
    # -------------------------------------------------------------------

    @listen(load_and_render)
    def validate_end_to_end(self) -> None:
        """Deploy the preset app in Docker and run L1 + L2 tests."""
        assert self.state.deploy_snippet and self.state.vector

        self._log("\n=== PresetAppFlow: validate_end_to_end ===")

        env = TestEnvironmentTool(target_image=PRESET_TARGET_IMAGE_TAG)

        try:
            for attempt in range(1 + MAX_VALIDATE_RETRIES):
                is_retry = attempt > 0

                if is_retry:
                    self._log(f"\n--- Retry {attempt + 1}/{1 + MAX_VALIDATE_RETRIES} ---")

                # Fresh environment each attempt
                env.setup()
                self.state.validate_attempts += 1

                try:
                    # Stage harness onto attacker container
                    self._log("  Staging harness on attacker...")
                    env.copy_to_attacker(
                        self.state.harness_content,
                        f"/tmp/harnesses/{self.state.preset_def['harness_id']}.sh",
                    )

                    # Run deploy snippet in target
                    self._log("  Running deploy snippet...")
                    deploy_exit, deploy_stdout, deploy_stderr = env.exec_in_target(
                        self.state.deploy_snippet
                    )
                    self._log(f"  Deploy exit code: {deploy_exit}")
                    if deploy_stderr:
                        self._log(f"  Deploy stderr (last 500): {deploy_stderr[-500:]}")

                    if deploy_exit != 0:
                        self._log(f"  Deploy snippet failed with exit code {deploy_exit}")
                        if attempt < MAX_VALIDATE_RETRIES:
                            env.teardown()
                            continue
                        raise PresetDeployError(
                            f"Deploy snippet failed after {1 + MAX_VALIDATE_RETRIES} attempts. "
                            f"Last stderr: {deploy_stderr[-500:]}"
                        )

                    # --- Layer 1 ---
                    self._log("  Running Layer 1 (internal state check)...")
                    l1_exit, l1_stdout, l1_stderr = env.exec_in_target(self.state.testing_snippet)
                    self._log(f"  L1 exit code: {l1_exit}")

                    l1_verdict = _run_verdict_crew(
                        agents_config=self.agents_config,
                        tasks_config=self.tasks_config,
                        atom_name=f"preset/{self.state.vector.preset_id}",
                        atom_context=self.state.vector.synthesis_context or f"{self.state.vector.preset_id} preset app",
                        layer="internal state check",
                        snippet_executed=self.state.testing_snippet,
                        exit_code=l1_exit,
                        stdout=l1_stdout,
                        stderr=l1_stderr,
                        ui=self.ui,
                    )
                    self._log(f"  Layer 1: {'PASS' if l1_verdict.passed else 'FAIL'} — {l1_verdict.reasoning}")

                    if not l1_verdict.passed:
                        if attempt < MAX_VALIDATE_RETRIES:
                            env.teardown()
                            continue
                        self.state.layer1_verdict = l1_verdict
                        raise PresetDeployError(
                            f"Layer 1 failed after {1 + MAX_VALIDATE_RETRIES} attempts. "
                            f"Last reason: {l1_verdict.reasoning}"
                        )

                    # --- Layer 2 ---
                    self._log("  Running Layer 2 (external attack probe)...")
                    env.ensure_attacker_tools([self.state.attack_snippet])
                    l2_exit, l2_stdout, l2_stderr = env.exec_in_attacker(self.state.attack_snippet)
                    self._log(f"  L2 exit code: {l2_exit}")

                    l2_verdict = _run_verdict_crew(
                        agents_config=self.agents_config,
                        tasks_config=self.tasks_config,
                        atom_name=f"preset/{self.state.vector.preset_id}",
                        atom_context=self.state.vector.synthesis_context or f"{self.state.vector.preset_id} preset app",
                        layer="external attack probe",
                        snippet_executed=self.state.attack_snippet,
                        exit_code=l2_exit,
                        stdout=l2_stdout,
                        stderr=l2_stderr,
                        ui=self.ui,
                    )
                    self._log(f"  Layer 2: {'PASS' if l2_verdict.passed else 'FAIL'} — {l2_verdict.reasoning}")

                    if not l2_verdict.passed:
                        if attempt < MAX_VALIDATE_RETRIES:
                            env.teardown()
                            continue
                        self.state.layer1_verdict = l1_verdict
                        self.state.layer2_verdict = l2_verdict
                        raise PresetDeployError(
                            f"Layer 2 failed after {1 + MAX_VALIDATE_RETRIES} attempts. "
                            f"Last reason: {l2_verdict.reasoning}"
                        )

                    # Both layers passed
                    self.state.layer1_verdict = l1_verdict
                    self.state.layer2_verdict = l2_verdict
                    self._log(f"  Both layers passed on attempt {attempt + 1}.")
                    return

                finally:
                    env.teardown()
                    self._log("  Test environment cleaned up.")

        except PresetDeployError:
            self._log(f"  PresetAppFlow validation failed after all attempts.")
            raise

    # -------------------------------------------------------------------
    # Step 3: Emit result
    # -------------------------------------------------------------------

    @listen(validate_end_to_end)
    def emit_result(self) -> None:
        """Package the validated preset app into a ResolvedPresetApp."""
        assert self.state.vector and self.state.deploy_snippet

        l1_passed = self.state.layer1_verdict.passed if self.state.layer1_verdict else False
        l2_passed = self.state.layer2_verdict.passed if self.state.layer2_verdict else False

        self.state.resolved = ResolvedPresetApp(
            vector=self.state.vector,
            stack_id=self.state.stack_def["id"],
            deploy_snippet=self.state.deploy_snippet,
            testing_snippet=self.state.testing_snippet or "",
            attack_snippet=self.state.attack_snippet or "",
            validation_passed=l1_passed and l2_passed,
        )

        self._log(f"\n=== PresetAppFlow complete ===")
        self._log(f"  Preset     : {self.state.vector.preset_id}")
        self._log(f"  Profiles   : {self.state.vector.vuln_profile_ids}")
        self._log(f"  Attempts   : {self.state.validate_attempts}")
        self._log(f"  Validated  : {self.state.resolved.validation_passed}")
