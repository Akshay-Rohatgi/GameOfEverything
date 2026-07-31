"""Tests for chain test diagnosis improvements."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def test_extract_target_system_from_exec_attacker():
    """Test that we can extract system_id from exec_attacker command."""
    from goe.flow.chain_test import _extract_target_system

    failed_step = SimpleNamespace(step_id="ssh_to_db")
    procedure = SimpleNamespace(
        procedure=[
            SimpleNamespace(
                step_id="ssh_to_db",
                action=SimpleNamespace(
                    type="exec_attacker",
                    command="ssh user@${system.db_system.host} 'id'",
                ),
            )
        ]
    )

    system_id = _extract_target_system(failed_step, procedure)
    assert system_id == "db_system"


def test_extract_target_system_from_http_request():
    """Test that we can extract system_id from http_request URL."""
    from goe.flow.chain_test import _extract_target_system

    failed_step = SimpleNamespace(step_id="sqli_attack")
    procedure = SimpleNamespace(
        procedure=[
            SimpleNamespace(
                step_id="sqli_attack",
                action=SimpleNamespace(
                    type="http_request",
                    url="http://${system.web_system.host}:${system.web_system.port}/login",
                ),
            )
        ]
    )

    system_id = _extract_target_system(failed_step, procedure)
    assert system_id == "web_system"


def test_extract_target_system_no_match():
    """Test that we return None when no system interpolation is found."""
    from goe.flow.chain_test import _extract_target_system

    failed_step = SimpleNamespace(step_id="local_cmd")
    procedure = SimpleNamespace(
        procedure=[
            SimpleNamespace(
                step_id="local_cmd",
                action=SimpleNamespace(
                    type="exec_attacker",
                    command="echo 'test'",
                ),
            )
        ]
    )

    system_id = _extract_target_system(failed_step, procedure)
    assert system_id is None


def test_extract_target_system_missing_procedure():
    """Test that we handle None procedure gracefully."""
    from goe.flow.chain_test import _extract_target_system

    failed_step = SimpleNamespace(step_id="test")
    system_id = _extract_target_system(failed_step, None)
    assert system_id is None


def test_summarise_failure_shows_context():
    """Test that successful steps are shown for context."""
    from goe.flow.chain_test import _summarise_failure

    result = SimpleNamespace(
        error=None,
        steps=[
            SimpleNamespace(
                step_id="step1",
                passed=True,
                reason="",
                raw=SimpleNamespace(stdout="", stderr="", error="", body=""),
            ),
            SimpleNamespace(
                step_id="step2",
                passed=True,
                reason="",
                raw=SimpleNamespace(stdout="", stderr="", error="", body=""),
            ),
            SimpleNamespace(
                step_id="step3",
                passed=False,
                reason="Connection refused",
                raw=SimpleNamespace(
                    stdout="",
                    stderr="curl: (7) Failed to connect",
                    error="",
                    body="",
                ),
            ),
        ],
    )

    diagnosis = _summarise_failure(result, env=None, procedure=None)

    assert "Recent Successful Steps" in diagnosis
    assert "✓ step1" in diagnosis
    assert "✓ step2" in diagnosis
    assert "Failed Steps" in diagnosis
    assert "✗ step3" in diagnosis
    assert "Connection refused" in diagnosis
    assert "Failed to connect" in diagnosis


def test_summarise_failure_shows_all_failures():
    """Test that ALL failed steps are shown, not just first."""
    from goe.flow.chain_test import _summarise_failure

    result = SimpleNamespace(
        error=None,
        steps=[
            SimpleNamespace(
                step_id="step1",
                passed=False,
                reason="First failure",
                raw=SimpleNamespace(stdout="out1", stderr="err1", error="", body=""),
            ),
            SimpleNamespace(
                step_id="step2",
                passed=False,
                reason="Second failure",
                raw=SimpleNamespace(stdout="out2", stderr="err2", error="", body=""),
            ),
        ],
    )

    diagnosis = _summarise_failure(result, env=None, procedure=None)

    # Both failures should be present
    assert "✗ step1" in diagnosis
    assert "First failure" in diagnosis
    assert "✗ step2" in diagnosis
    assert "Second failure" in diagnosis


def test_summarise_failure_with_env_probes():
    """Test that container probes are collected when env is provided."""
    from goe.flow.chain_test import _summarise_failure

    mock_env = MagicMock()
    mock_env.exec_in.return_value = (0, "probe output", "")

    result = SimpleNamespace(
        error=None,
        steps=[
            SimpleNamespace(
                step_id="failed",
                passed=False,
                reason="Test failure",
                raw=SimpleNamespace(stdout="", stderr="", error="", body=""),
            )
        ],
    )

    diagnosis = _summarise_failure(result, env=mock_env, procedure=None)

    # Should include container evidence section
    assert "Container Evidence" in diagnosis
    assert "[Attacker Container]" in diagnosis
    assert "Processes:" in diagnosis
    assert "Connections:" in diagnosis

    # Should have probed attacker container at least twice (ps and ss)
    assert mock_env.exec_in.call_count >= 2
    calls = [call[0] for call in mock_env.exec_in.call_args_list]
    assert any("attacker" in str(c) for c in calls)


def test_summarise_failure_probes_target_system():
    """Test that target system is probed when identifiable."""
    from goe.flow.chain_test import _summarise_failure

    mock_env = MagicMock()
    mock_env.exec_in.return_value = (0, "probe output", "")

    failed_step = SimpleNamespace(
        step_id="ssh_fail",
        passed=False,
        reason="Permission denied",
        raw=SimpleNamespace(stdout="", stderr="Permission denied (publickey)", error="", body=""),
    )

    procedure = SimpleNamespace(
        procedure=[
            SimpleNamespace(
                step_id="ssh_fail",
                action=SimpleNamespace(
                    type="exec_attacker",
                    command="ssh user@${system.target_box.host} 'id'",
                ),
            )
        ]
    )

    result = SimpleNamespace(error=None, steps=[failed_step])

    diagnosis = _summarise_failure(result, env=mock_env, procedure=procedure)

    # Should include both attacker and target_box evidence
    assert "[Attacker Container]" in diagnosis
    assert "[target_box Container]" in diagnosis

    # Should have probed both containers
    calls = [call[0][0] for call in mock_env.exec_in.call_args_list]
    assert "attacker" in calls
    assert "target_box" in calls


def test_summarise_failure_truncates_output():
    """Test that stdout/stderr are truncated to prevent huge diagnostics."""
    from goe.flow.chain_test import _summarise_failure

    long_output = "x" * 2000

    result = SimpleNamespace(
        error=None,
        steps=[
            SimpleNamespace(
                step_id="verbose_fail",
                passed=False,
                reason="Too much output",
                raw=SimpleNamespace(
                    stdout=long_output,
                    stderr=long_output[:500],
                    error="",
                    body="",
                ),
            )
        ],
    )

    diagnosis = _summarise_failure(result, env=None, procedure=None)

    # stdout should be truncated to 800 chars
    assert diagnosis.count("x") < 2000
    # Verify the actual truncation happened
    assert "stdout:" in diagnosis
    # Should have at most 800 x's from stdout + 400 from stderr = 1200
    assert diagnosis.count("x") <= 1200
