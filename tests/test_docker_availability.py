"""Unit tests for the interactive Docker availability gate."""

from unittest.mock import MagicMock, patch

import pytest
from docker.errors import DockerException

from goe.container.progressive import ProgressiveEnvironment
from goe.container.test_environment_tool import wait_for_docker


def test_wait_for_docker_prompts_before_retrying():
    unavailable = MagicMock()
    unavailable.ping.side_effect = DockerException("daemon is not running")
    available = MagicMock()

    with (
        patch(
            "goe.container.test_environment_tool.docker.from_env",
            side_effect=[unavailable, available],
        ) as from_env,
        patch("builtins.input", return_value="") as prompt,
        patch("goe.container.test_environment_tool.time.sleep") as sleep,
    ):
        wait_for_docker("build the environment")

    assert from_env.call_count == 2
    prompt.assert_called_once_with()
    sleep.assert_called_once_with(2)
    available.ping.assert_called_once_with()
    available.close.assert_called_once_with()


def test_progressive_setup_waits_for_docker():
    env = ProgressiveEnvironment(system_id="web")

    with (
        patch(
            "goe.container.test_environment_tool.wait_for_docker",
            side_effect=RuntimeError("stop after availability check"),
        ) as wait,
        pytest.raises(RuntimeError, match="stop after availability check"),
    ):
        env.setup()

    wait.assert_called_once_with(
        "set up progressive environment for system 'web'"
    )
