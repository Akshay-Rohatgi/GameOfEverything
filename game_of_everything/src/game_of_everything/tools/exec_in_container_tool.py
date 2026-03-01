"""ExecInContainerTool — crewAI BaseTool for Layer 1 (internal state verification).

Runs a bash snippet inside a Docker container via `docker exec` and returns
the raw stdout, stderr, and exit code. The Testing Agent uses this to execute
testing_snippet commands inside the target container.

NOTE: This tool requires a running Docker container. The container_id must be
passed at runtime (typically the target container started by TestEnvironmentTool).
"""

from typing import Type

import docker
from docker.errors import NotFound, APIError
from pydantic import BaseModel, Field
from crewai.tools import BaseTool


class ExecInContainerInput(BaseModel):
    container_id: str = Field(
        ...,
        description="The Docker container name or ID to execute the snippet in.",
    )
    snippet: str = Field(
        ...,
        description="The bash command(s) to execute inside the container.",
    )


class ExecInContainerTool(BaseTool):
    name: str = "exec_in_container"
    description: str = (
        "Execute a bash snippet inside a Docker container and return the output. "
        "Pass the container name/ID to 'container_id' and the bash commands to 'snippet'. "
        "Returns the exit code, stdout, and stderr from the execution."
    )
    args_schema: Type[BaseModel] = ExecInContainerInput

    def _run(self, container_id: str, snippet: str) -> str:
        try:
            client = docker.from_env()
            container = client.containers.get(container_id)

            exit_code, output = container.exec_run(
                cmd=["bash", "-c", snippet],
                demux=True,
                privileged=True,
            )
            stdout = (output[0] or b"").decode("utf-8", errors="replace")
            stderr = (output[1] or b"").decode("utf-8", errors="replace")

            return (
                f"EXIT CODE: {exit_code}\n"
                f"--- STDOUT ---\n{stdout}\n"
                f"--- STDERR ---\n{stderr}"
            )
        except NotFound:
            return f"Error: Container '{container_id}' not found. Is the test environment running?"
        except APIError as e:
            return f"Error: Docker API error — {e}"
        except Exception as e:
            return f"Error: {type(e).__name__} — {e}"
