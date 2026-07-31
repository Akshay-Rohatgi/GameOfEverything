"""Top-level run flow — plan → schedule → build → package."""

from goe.flow.orchestrator import RunResult, run

__all__ = ["run", "RunResult"]
