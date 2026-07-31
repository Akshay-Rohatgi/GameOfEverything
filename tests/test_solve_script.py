"""Unit tests for the Procedure → solve.sh transpiler. No docker, no LLM."""

import shutil
import subprocess
from pathlib import Path

import pytest

from goe.graph.models import EntityGraph
from goe.models.procedure import (
    BodyContainsAssertion,
    ExecAttackerAction,
    HttpRequestAction,
    NavigateAction,
    Procedure,
    StatusAssertion,
    StdoutContainsAssertion,
    Step,
)
from goe.packaging.solve_script import UnsupportedActionError, compile_solve_script

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


def _graph() -> EntityGraph:
    # Single system `target_system` (host=target, port=3000); edges include
    # `sqli_to_ssh` with a `secret` param (structural fallback = "db_user_password").
    return EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")


def _bash_ok(script: str) -> None:
    """Assert the script is syntactically valid bash (bash -n)."""
    assert shutil.which("bash"), "bash required for this test"
    proc = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    assert proc.returncode == 0, f"bash -n failed:\n{proc.stderr}\n---\n{script}"


def _compile(steps) -> str:
    return compile_solve_script(_graph(), Procedure(procedure=steps))


def test_exec_attacker_stdout_contains():
    script = _compile([
        Step(
            step_id="run_id",
            action=ExecAttackerAction(type="exec_attacker", command="id"),
            expect=StdoutContainsAssertion(stdout_contains="uid=0(root)"),
        )
    ])
    assert 'out="$(id)"; rc=$?' in script
    assert "grep -qF -- 'uid=0(root)'" in script
    assert "_fail" in script
    assert script.strip().endswith("exit 0")
    _bash_ok(script)


def test_http_request_status_and_body():
    script = _compile([
        Step(
            step_id="hit",
            action=HttpRequestAction(
                type="http_request", method="GET",
                url="http://${system.target_system.host}:${system.target_system.port}/x",
            ),
            expect=StatusAssertion(status=200),
        ),
        Step(
            step_id="leak",
            action=HttpRequestAction(
                type="http_request", method="GET",
                url="http://${system.target_system.host}/y",
            ),
            expect=BodyContainsAssertion(body_contains="secret"),
        ),
    ])
    assert 'curl' in script and '-X' in script
    assert 'status="$(printf %s "$_raw"' in script
    assert '[ "$status" = "200" ]' in script
    assert 'printf %s "$body" | grep -qF -- \'secret\'' in script
    _bash_ok(script)


def test_edge_value_substituted_literally():
    # ${edge.sqli_to_ssh.secret} → the concrete/structural literal, verbatim.
    script = _compile([
        Step(
            step_id="login",
            action=ExecAttackerAction(
                type="exec_attacker",
                command="sshpass -p '${edge.sqli_to_ssh.secret}' ssh u@h id",
            ),
            expect=StdoutContainsAssertion(stdout_contains="uid"),
        )
    ])
    assert "sshpass -p 'db_user_password'" in script
    assert "${edge." not in script
    _bash_ok(script)


def test_system_ref_becomes_env_overridable_var():
    script = _compile([
        Step(
            step_id="reach",
            action=ExecAttackerAction(
                type="exec_attacker",
                command="curl http://${system.target_system.host}:${system.target_system.port}/",
            ),
            expect=StdoutContainsAssertion(stdout_contains="ok"),
        )
    ])
    # Declared once at the top, defaulting to the docker hostname/port, overridable.
    assert 'SYSTEM_TARGET_SYSTEM_HOST="${SYSTEM_TARGET_SYSTEM_HOST:-target}"' in script
    assert 'SYSTEM_TARGET_SYSTEM_PORT="${SYSTEM_TARGET_SYSTEM_PORT:-3000}"' in script
    assert '${SYSTEM_TARGET_SYSTEM_HOST}' in script
    _bash_ok(script)


def test_step_output_chained_via_bash_var():
    script = _compile([
        Step(
            step_id="grab",
            action=ExecAttackerAction(type="exec_attacker", command="whoami"),
            expect=StdoutContainsAssertion(stdout_contains="alice"),
            outputs={"user": "stdout"},
        ),
        Step(
            step_id="use",
            action=ExecAttackerAction(
                type="exec_attacker",
                command="ssh ${steps.grab.user}@host id",
            ),
            expect=StdoutContainsAssertion(stdout_contains="uid"),
        ),
    ])
    # Producer captures into SOLVE_grab_user; consumer references it.
    assert 'SOLVE_grab_user="${out}"' in script
    assert "${SOLVE_grab_user:-}" in script
    _bash_ok(script)


def test_regex_output_capture():
    script = _compile([
        Step(
            step_id="extract",
            action=ExecAttackerAction(type="exec_attacker", command="cat /etc/passwd"),
            expect=StdoutContainsAssertion(stdout_contains="root"),
            outputs={"uid": 'regex("uid=(\\d+)")'},
        )
    ])
    assert "SOLVE_extract_uid=" in script
    assert "GOE_PAT=" in script and "perl" in script
    _bash_ok(script)


def test_browser_action_unsupported():
    with pytest.raises(UnsupportedActionError):
        _compile([
            Step(step_id="nav", action=NavigateAction(type="navigate", path="/"))
        ])
