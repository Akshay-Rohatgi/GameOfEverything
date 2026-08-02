"""Listen action handler — ncat background listener in attacker container."""

from __future__ import annotations
import time
from typing import TYPE_CHECKING
from goe.executor.actions import ActionResult

if TYPE_CHECKING:
    from goe.container.environment import TestEnvironment

# Per-port temp file in attacker container
_LISTEN_FILE = "/tmp/goe_listen_{port}.txt"


def listen(env: "TestEnvironment", port: int, duration: int) -> ActionResult:
    """Open a listener, wait duration seconds, return received content."""
    listen_file = _LISTEN_FILE.format(port=port)

    # Kill any existing listener on this port, start fresh
    env.exec_in("attacker", f"pkill -f 'ncat.*{port}' 2>/dev/null; rm -f {listen_file}; true")
    env.exec_in("attacker", f"ncat -lk {port} > {listen_file} 2>/dev/null &")

    time.sleep(duration)

    exit_code, stdout, stderr = env.exec_in("attacker", f"cat {listen_file} 2>/dev/null || echo ''")
    return ActionResult(exit_code=exit_code, stdout=stdout, stderr=stderr)
