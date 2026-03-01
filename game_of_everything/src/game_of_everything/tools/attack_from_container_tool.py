"""
AttackFromContainerTool — crewAI BaseTool for Layer 2 (external attack simulation).

Runs an attack_snippet inside the Kali attacker container on the goe_test_net
Docker bridge network. The snippet targets the 'target' hostname, which resolves
to the target container on the same network.

NOTE: This tool requires the attacker container to be running (started by
TestEnvironmentTool.setup()). The attacker container name defaults to
'goe_attacker' but can be overridden.
"""

from typing import Type

import docker
from docker.errors import NotFound, APIError
from pydantic import BaseModel, Field
from crewai.tools import BaseTool

DEFAULT_ATTACKER_CONTAINER = "goe_attacker"


class AttackFromContainerInput(BaseModel):
    attack_snippet: str = Field(
        ...,
        description=(
            "The bash command(s) to execute inside the attacker container. "
            "Use hostname 'target' to address the target machine on the bridge network."
        ),
    )
    attacker_container_id: str = Field(
        default=DEFAULT_ATTACKER_CONTAINER,
        description="The attacker container name or ID. Defaults to 'goe_attacker'.",
    )


class AttackFromContainerTool(BaseTool):
    name: str = "attack_from_container"
    description: str = (
        "Execute an attack probe from the Kali attacker container against the target. "
        "Pass the attack command(s) to 'attack_snippet'. The target is reachable "
        "via hostname 'target' on the Docker bridge network. "
        "Returns the exit code, stdout, and stderr from the attack probe."
    )
    args_schema: Type[BaseModel] = AttackFromContainerInput

    def _run(
        self,
        attack_snippet: str,
        attacker_container_id: str = DEFAULT_ATTACKER_CONTAINER,
    ) -> str:
        try:
            client = docker.from_env()
            container = client.containers.get(attacker_container_id)

            exit_code, output = container.exec_run(
                cmd=["bash", "-c", attack_snippet],
                demux=True,
            )
            stdout = (output[0] or b"").decode("utf-8", errors="replace")
            stderr = (output[1] or b"").decode("utf-8", errors="replace")

            return (
                f"EXIT CODE: {exit_code}\n"
                f"--- STDOUT ---\n{stdout}\n"
                f"--- STDERR ---\n{stderr}"
            )
        except NotFound:
            return (
                f"Error: Attacker container '{attacker_container_id}' not found. "
                "Is the test environment running?"
            )
        except APIError as e:
            return f"Error: Docker API error — {e}"
        except Exception as e:
            return f"Error: {type(e).__name__} — {e}"
