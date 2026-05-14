"""
Attack Orchestrator — validates custom apps end-to-end (L1 + L2).

This is a manual tool execution implementation (Option B) that bypasses the
Bedrock/CrewAI tool use incompatibility. Instead of letting an agent autonomously
choose and execute tools, we:
1. Parse attack_objective to extract steps
2. Execute each step manually (route to appropriate tool)
3. Use LLM only for judgment (L1/L2 pass/fail)
4. Return structured AttackOrchestratorResult

This approach avoids the issue where Bedrock returns tool use blocks instead of
final text responses, which causes CrewAI's TaskOutput validation to fail.
"""

import logging
import re
import time
from typing import TYPE_CHECKING, Optional, List, Tuple

from game_of_everything.llm_factory import make_llm
from game_of_everything.models import AttackOrchestratorResult, GeneratedApp
from game_of_everything.tools.bound_exec_tools import (
    BoundExecInAttackerTool,
    BoundExecInTargetTool,
)
from game_of_everything.tools.bound_browser_tool import BoundBrowserTool

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole

logger = logging.getLogger(__name__)


def _si(s: str) -> str:
    """Sanitize input strings to prevent crewAI Jinja2 SSTI."""
    return s.replace("{{", "{ {").replace("}}", "} }")


def _parse_attack_objective(attack_objective: str) -> Tuple[List[dict], str]:
    """Parse attack_objective into structured steps and success criterion.

    Returns:
        (steps, success_criterion)
        steps: List of {"type": "attacker"|"browser"|"target", "action": str}
        success_criterion: The success criterion string
    """
    steps = []
    success_criterion = ""

    lines = attack_objective.strip().split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Parse "Step N: Run in attacker: <command>"
        if re.match(r'Step \d+:\s*Run in attacker:', line, re.IGNORECASE):
            action = re.sub(r'Step \d+:\s*Run in attacker:\s*', '', line, flags=re.IGNORECASE)
            steps.append({"type": "attacker", "action": action})

        # Parse "Step N: In browser: <action>"
        elif re.match(r'Step \d+:\s*In browser:', line, re.IGNORECASE):
            action = re.sub(r'Step \d+:\s*In browser:\s*', '', line, flags=re.IGNORECASE)
            steps.append({"type": "browser", "action": action})

        # Parse "Step N: Run in target: <command>"
        elif re.match(r'Step \d+:\s*Run in target:', line, re.IGNORECASE):
            action = re.sub(r'Step \d+:\s*Run in target:\s*', '', line, flags=re.IGNORECASE)
            steps.append({"type": "target", "action": action})

        # Parse "Success criterion: <criterion>"
        elif re.match(r'Success criterion:', line, re.IGNORECASE):
            success_criterion = re.sub(r'Success criterion:\s*', '', line, flags=re.IGNORECASE)

    return steps, success_criterion


def _wait_for_port(
    exec_target_tool: "BoundExecInTargetTool",
    port: int,
    timeout: int = 60,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Poll the target container until the app port responds or timeout expires.

    Uses curl's %{http_code} format — any non-000 HTTP status means the server
    accepted the TCP connection and responded, so the app is up.
    """
    # curl writes the status code to stdout; 000 = no connection
    probe = f"curl -s -o /dev/null -w '%{{http_code}}' --connect-timeout 2 http://localhost:{port}/"
    start = time.time()
    interval = 2
    while time.time() - start < timeout:
        result = exec_target_tool._run(probe)
        # Extract status code from STDOUT section
        http_code = ""
        in_stdout = False
        for line in result.splitlines():
            if "--- STDOUT ---" in line:
                in_stdout = True
                continue
            if "--- STDERR ---" in line:
                in_stdout = False
                continue
            if in_stdout:
                http_code = line.strip()
                break
        if http_code and http_code != "000" and http_code.isdigit():
            if ui:
                ui.log(f"  App port {port} ready (HTTP {http_code})")
            return
        if ui:
            ui.log(f"  App not ready yet (code={http_code!r}), retrying in {interval}s...")
        time.sleep(interval)
    if ui:
        ui.log(f"  Warning: app port {port} not ready after {timeout}s — proceeding anyway")


def _judge_with_llm(
    verdict_type: str,
    evidence: str,
    context: str,
    success_criterion: str = "",
    ui: Optional["GoEConsole"] = None
) -> bool:
    """Use LLM to judge if a test passed based on evidence.

    Args:
        verdict_type: "L1" or "L2"
        evidence: The command output to judge
        context: App context for the judgment
        success_criterion: For L2, the success criterion from attack_objective
        ui: Optional UI for logging

    Returns:
        True if passed, False otherwise
    """
    if verdict_type == "L1":
        prompt = f"""Analyze this Layer 1 (internal state check) output and determine if the app deployed correctly and the vulnerability is present.

Context: {context}

Evidence:
{evidence}

Return only "PASS" or "FAIL" on the first line, followed by a brief explanation."""
    else:  # L2
        prompt = f"""Analyze this Layer 2 (external attack probe) output and determine if the exploit succeeded.

Context: {context}

Success Criterion: {success_criterion}

Evidence:
{evidence}

Return only "PASS" or "FAIL" on the first line, followed by a brief explanation."""

    try:
        llm = make_llm()
        response = llm.call([{"role": "user", "content": prompt}])
        response_text = str(response)

        # Scan the full response for PASS/FAIL — first-line-only is fragile with
        # Bedrock models that sometimes emit a preamble before the verdict word.
        upper = response_text.upper()
        pass_pos = upper.find("PASS")
        fail_pos = upper.find("FAIL")

        if pass_pos == -1 and fail_pos == -1:
            passed = False  # No verdict found — conservative fail
        elif pass_pos == -1:
            passed = False
        elif fail_pos == -1:
            passed = True
        else:
            passed = pass_pos < fail_pos  # First occurrence wins

        if ui:
            ui.log(f"  {verdict_type} LLM judgment: {'PASS' if passed else 'FAIL'}")
            ui.log(f"  {verdict_type} response: {response_text[:200]}")

        return passed
    except Exception as e:
        logger.error(f"LLM judgment failed: {e}")
        return False


def run_attack_orchestrator_crew(
    agents_config: dict,
    tasks_config: dict,
    generated_app: GeneratedApp,
    synthesis_context: str,
    port: int,
    target_container_name: str,
    attacker_container_name: str,
    cdp_url: str,
    attempt_number: int = 1,
    max_attempts: int = 2,
    failure_context: str = "",
    ui: Optional["GoEConsole"] = None,
) -> AttackOrchestratorResult:
    """Run the Attack Orchestrator with manual tool execution (Option B workaround).

    Args:
        agents_config: Loaded agents.yaml (unused in manual mode)
        tasks_config: Loaded tasks.yaml (unused in manual mode)
        generated_app: The GeneratedApp to validate
        synthesis_context: Scenario context string
        port: Target app port
        target_container_name: Name of target Docker container
        attacker_container_name: Name of attacker Docker container
        cdp_url: WebSocket URL for browser CDP connection
        attempt_number: Current attempt (1-indexed)
        max_attempts: Total attempts allowed
        failure_context: Feedback from previous failed attempt
        ui: Optional GoEConsole for logging

    Returns:
        AttackOrchestratorResult with L1/L2 verdicts and evidence
    """
    if ui:
        ui.log(f"Running Attack Orchestrator - Manual Execution (attempt {attempt_number}/{max_attempts})...")

    # Create bound tools
    exec_target_tool = BoundExecInTargetTool(container_name=target_container_name)
    exec_attacker_tool = BoundExecInAttackerTool(container_name=attacker_container_name)
    browser_tool = BoundBrowserTool(
        cdp_url=cdp_url,
        target_base_url=f"http://target:{port}"
    )

    try:
        # === Wait for app port to be ready ===
        if ui:
            ui.log(f"  Waiting for app on port {port}...")
        _wait_for_port(exec_target_tool, port, ui=ui)

        # === Layer 1: Internal State Check ===
        if ui:
            ui.log("  Executing L1 (internal state check)...")

        l1_evidence = exec_target_tool._run(generated_app.testing_snippet)
        l1_passed = _judge_with_llm(
            verdict_type="L1",
            evidence=l1_evidence,
            context=f"App: {generated_app.app_filename}\nContext: {synthesis_context}",
            ui=ui
        )

        if not l1_passed:
            if ui:
                ui.log("  L1 failed - skipping L2")
            return AttackOrchestratorResult(
                l1_passed=False,
                l2_passed=False,
                l1_evidence=l1_evidence[:800],
                l2_evidence="",
                reasoning="L1 (internal state check) failed. App not deployed correctly or vulnerability not present.",
                used_browser=False,
            )

        # === Layer 2: External Attack Probe ===
        if ui:
            ui.log("  L1 passed. Executing L2 (external attack probe)...")

        # Parse attack_objective
        steps, success_criterion = _parse_attack_objective(generated_app.attack_objective)

        if not steps:
            logger.warning("No steps parsed from attack_objective")
            return AttackOrchestratorResult(
                l1_passed=True,
                l2_passed=False,
                l1_evidence=l1_evidence[:800],
                l2_evidence="",
                reasoning="No executable steps found in attack_objective. Format may be incorrect.",
                used_browser=False,
            )

        # Execute L2 steps
        l2_outputs = []
        used_browser = False

        for i, step in enumerate(steps, 1):
            step_type = step["type"]
            action = step["action"]

            if ui:
                ui.log(f"    Step {i}/{len(steps)}: {step_type}")

            try:
                if step_type == "attacker":
                    output = exec_attacker_tool._run(action)
                elif step_type == "browser":
                    output = browser_tool._run(action)
                    used_browser = True
                elif step_type == "target":
                    output = exec_target_tool._run(action)
                else:
                    output = f"Unknown step type: {step_type}"

                l2_outputs.append(f"Step {i} ({step_type}):\n{output}\n")

            except Exception as e:
                logger.error(f"Step {i} failed: {e}")
                l2_outputs.append(f"Step {i} ({step_type}) ERROR: {e}\n")

        l2_evidence = "\n".join(l2_outputs)

        # Judge L2
        l2_passed = _judge_with_llm(
            verdict_type="L2",
            evidence=l2_evidence,
            context=f"App: {generated_app.app_filename}\nContext: {synthesis_context}",
            success_criterion=success_criterion,
            ui=ui
        )

        reasoning = (
            f"L1 passed. L2 {'passed' if l2_passed else 'failed'}. "
            f"Executed {len(steps)} steps. Success criterion: {success_criterion}"
        )

        return AttackOrchestratorResult(
            l1_passed=True,
            l2_passed=l2_passed,
            l1_evidence=l1_evidence[:800],
            l2_evidence=l2_evidence[:800],
            reasoning=reasoning,
            used_browser=used_browser,
        )

    except Exception as e:
        logger.error(f"Attack orchestrator failed: {e}", exc_info=True)
        return AttackOrchestratorResult(
            l1_passed=False,
            l2_passed=False,
            l1_evidence="",
            l2_evidence="",
            reasoning=f"Orchestrator execution failed: {e}",
            used_browser=False,
        )
