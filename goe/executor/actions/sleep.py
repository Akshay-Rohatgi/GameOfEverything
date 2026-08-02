"""Sleep action handler."""

import time
from goe.executor.actions import ActionResult


def sleep_action(seconds: int) -> ActionResult:
    time.sleep(seconds)
    return ActionResult(exit_code=0)
