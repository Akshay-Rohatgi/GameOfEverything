"""
TestEnvironmentTool — Python helper (not a crewAI tool) that owns Docker lifecycle
for the two-layer snippet testing architecture.

Responsibilities:
  - Create/teardown a Docker bridge network with target + attacker containers.
  - Execute commands inside either container and return raw output.
  - Orchestrate the incremental cumulative test loop (Layer 1 + Layer 2).

The LLM (Testing Agent) is called externally by the flow step to judge outputs;
this class only runs commands and returns raw results.
"""

import logging
from pathlib import Path
from typing import List, Tuple, Optional

import docker
from docker.errors import NotFound, APIError

logger = logging.getLogger(__name__)

NETWORK_NAME = "goe_test_net"
TARGET_NAME = "goe_target"
ATTACKER_NAME = "goe_attacker"
TARGET_IMAGE = "ubuntu:22.04"
ATTACKER_DOCKERFILE_DIR = str(
    Path(__file__).parent.parent.parent.parent / "docker" / "attacker"
)
ATTACKER_IMAGE_TAG = "goe-attacker:latest"


class TestEnvironmentTool:
    """Manages Docker network + container lifecycle for snippet testing."""

    def __init__(self):
        self.client: docker.DockerClient = docker.from_env()
        self.network = None
        self.target_container = None
        self.attacker_container = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Create bridge network, start target (Ubuntu) and attacker (Kali) containers."""
        logger.info("Setting up test environment...")

        # Clean up any stale resources from a previous failed run
        self._force_cleanup()

        # Create bridge network
        self.network = self.client.networks.create(NETWORK_NAME, driver="bridge")
        logger.info(f"Created network: {NETWORK_NAME}")

        # Start target container
        self.target_container = self.client.containers.run(
            TARGET_IMAGE,
            command="sleep infinity",
            name=TARGET_NAME,
            network=NETWORK_NAME,
            hostname="target",
            detach=True,
            remove=False,
        )
        logger.info(f"Started target container: {TARGET_NAME} ({TARGET_IMAGE})")

        # Build the attacker image from the Kali Dockerfile
        logger.info(f"Building attacker image from {ATTACKER_DOCKERFILE_DIR}...")
        self.client.images.build(
            path=ATTACKER_DOCKERFILE_DIR,
            tag=ATTACKER_IMAGE_TAG,
            rm=True,
        )
        logger.info(f"Built attacker image: {ATTACKER_IMAGE_TAG}")

        # Start attacker container on the same network
        self.attacker_container = self.client.containers.run(
            ATTACKER_IMAGE_TAG,
            command="sleep infinity",
            name=ATTACKER_NAME,
            network=NETWORK_NAME,
            hostname="attacker",
            detach=True,
            remove=False,
        )
        logger.info(f"Started attacker container: {ATTACKER_NAME} ({ATTACKER_IMAGE_TAG})")

    def teardown(self) -> None:
        """Stop and remove both containers and the network. Safe to call multiple times."""
        logger.info("Tearing down test environment...")
        self._force_cleanup()
        logger.info("Test environment teardown complete.")

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def exec_in_target(self, snippet: str) -> Tuple[int, str, str]:
        """Run a bash snippet inside the target container.

        Returns:
            (exit_code, stdout, stderr)
        """
        return self._exec_in_container(self.target_container, snippet)

    def exec_in_attacker(self, snippet: str) -> Tuple[int, str, str]:
        """Run a bash snippet inside the attacker (Kali) container.

        Returns:
            (exit_code, stdout, stderr)
        """
        return self._exec_in_container(self.attacker_container, snippet)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _exec_in_container(
        self, container, snippet: str
    ) -> Tuple[int, str, str]:
        """Execute a bash -c command in the given container.

        Returns:
            (exit_code, stdout, stderr)
        """
        if container is None:
            raise RuntimeError(
                "Container not started. Call setup() before executing commands."
            )
        # docker exec returns (exit_code, (stdout_bytes, stderr_bytes)) with demux=True
        exit_code, output = container.exec_run(
            cmd=["bash", "-c", snippet],
            demux=True,
        )
        stdout = (output[0] or b"").decode("utf-8", errors="replace")
        stderr = (output[1] or b"").decode("utf-8", errors="replace")
        return exit_code, stdout, stderr

    def _force_cleanup(self) -> None:
        """Remove any existing containers and network with our well-known names."""
        for name in (TARGET_NAME, ATTACKER_NAME):
            try:
                c = self.client.containers.get(name)
                c.stop(timeout=5)
                c.remove(force=True)
                logger.info(f"Removed stale container: {name}")
            except NotFound:
                pass
            except APIError as e:
                logger.warning(f"Error cleaning up container {name}: {e}")

        try:
            net = self.client.networks.get(NETWORK_NAME)
            net.remove()
            logger.info(f"Removed stale network: {NETWORK_NAME}")
        except NotFound:
            pass
        except APIError as e:
            logger.warning(f"Error cleaning up network {NETWORK_NAME}: {e}")
