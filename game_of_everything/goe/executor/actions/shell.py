"""Shell action handlers: exec_attacker, exec_target."""

from __future__ import annotations
from typing import TYPE_CHECKING
from goe.executor.actions import ActionResult

if TYPE_CHECKING:
    from goe.container.environment import TestEnvironment


def exec_attacker(env: "TestEnvironment", command: str) -> ActionResult:
    exit_code, stdout, stderr = env.exec_in("attacker", command)
    return ActionResult(exit_code=exit_code, stdout=stdout, stderr=stderr)


def exec_target(env: "TestEnvironment", command: str) -> ActionResult:
    exit_code, stdout, stderr = env.exec_in("target", command, privileged=True)
    return ActionResult(exit_code=exit_code, stdout=stdout, stderr=stderr)
