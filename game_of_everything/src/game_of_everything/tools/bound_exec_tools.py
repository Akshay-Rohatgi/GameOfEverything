"""Bound container exec tools for the Attack Agent.

Unlike ExecInContainerTool (which requires the LLM to supply a container_id),
these tools are pre-bound to a specific container at construction time. The LLM
only supplies the bash snippet to execute.
"""

from typing import Type

import docker
from docker.errors import NotFound, APIError
from pydantic import BaseModel, Field
from crewai.tools import BaseTool


class _SnippetInput(BaseModel):
    snippet: str = Field(
        ...,
        description="Bash command(s) to execute inside the container.",
    )


def _exec(container_name: str, snippet: str, privileged: bool = False) -> str:
    try:
        client = docker.from_env()
        container = client.containers.get(container_name)
        exit_code, output = container.exec_run(
            cmd=["bash", "-c", snippet],
            demux=True,
            privileged=privileged,
        )
        stdout = (output[0] or b"").decode("utf-8", errors="replace")
        stderr = (output[1] or b"").decode("utf-8", errors="replace")
        return (
            f"EXIT CODE: {exit_code}\n"
            f"--- STDOUT ---\n{stdout}\n"
            f"--- STDERR ---\n{stderr}"
        )
    except NotFound:
        return f"Error: Container '{container_name}' not found. Is the test environment running?"
    except APIError as e:
        return f"Error: Docker API error — {e}"
    except Exception as e:
        return f"Error: {type(e).__name__} — {e}"


class BoundExecInAttackerTool(BaseTool):
    name: str = "exec_in_attacker"
    description: str = (
        "Execute bash commands inside the attacker (Kali) container. "
        "The target is reachable via hostname 'target' on the Docker bridge network. "
        "Returns exit code, stdout, and stderr."
    )
    args_schema: Type[BaseModel] = _SnippetInput
    _container_name: str = "goe_attacker"

    def __init__(self, container_name: str = "goe_attacker", **kwargs):
        super().__init__(**kwargs)
        self._container_name = container_name

    def _run(self, snippet: str) -> str:
        return _exec(self._container_name, snippet)


class BoundExecInTargetTool(BaseTool):
    name: str = "exec_in_target"
    description: str = (
        "Execute bash commands inside the target container to inspect logs, "
        "check service status, read configuration files, or query databases. "
        "Returns exit code, stdout, and stderr."
    )
    args_schema: Type[BaseModel] = _SnippetInput
    _container_name: str = "goe_target"

    def __init__(self, container_name: str = "goe_target", **kwargs):
        super().__init__(**kwargs)
        self._container_name = container_name

    def _run(self, snippet: str) -> str:
        return _exec(self._container_name, snippet, privileged=True)
