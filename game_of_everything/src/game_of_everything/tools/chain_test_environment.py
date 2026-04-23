"""ChainTestEnvironment — Docker lifecycle manager for Layer 3 chain testing.

Sets up the full multi-box topology in Docker (all boxes + attacker on a single
flat network) to validate end-to-end attack chains via ChainProbe execution.

Design decisions:
  - Each box runs ubuntu:22.04 with the generated deploy.sh applied at runtime.
  - Attacker container uses the same goe-attacker:latest image as snippet testing.
  - All containers share a flat Docker bridge network (goe_net).
  - Container names: goe_{box_id}  /  goe_attacker.
  - Each box hostname is set to box.hostname (matches ChainProbe target fields).
"""

import base64
import logging
import time
from typing import Dict, Optional, Tuple

import docker
from docker.errors import NotFound, APIError

from game_of_everything.models import NetworkTopology, BoxDefinition
from game_of_everything.tools.test_environment import (
    TARGET_IMAGE,
    ATTACKER_IMAGE_TAG,
    ATTACKER_DOCKERFILE_DIR,
    wait_for_docker,
)
from game_of_everything.tools.naming import (
    box_container_name as _box_container_name,
    attacker_container_name,
    network_name,
)

logger = logging.getLogger(__name__)

CHAIN_NETWORK_NAME = network_name()
_ATTACKER_CONTAINER_NAME = attacker_container_name()


class ChainTestEnvironment:
    """Manages a full multi-box Docker topology for chain-probe execution."""

    def __init__(self, topology: NetworkTopology, deploy_scripts: Dict[str, str]):
        self._topology = topology
        self._deploy_scripts = deploy_scripts
        self._client: Optional[docker.DockerClient] = None
        self._network = None
        # Maps "attacker" → attacker container, box_id → box container
        self._containers: Dict[str, object] = {}

    # ------------------------------------------------------------------
    # Lazy Docker client
    # ------------------------------------------------------------------

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Create the chain network, start all box containers and the attacker."""
        wait_for_docker("set up chain test environment")
        logger.info("ChainTestEnvironment: setting up...")
        self._force_cleanup()

        self._network = self.client.networks.create(CHAIN_NETWORK_NAME, driver="bridge")
        logger.info(f"Created chain network: {CHAIN_NETWORK_NAME}")

        for box in self._topology.boxes:
            cname = _box_container_name(box.box_id)
            logger.info(f"Starting box container: {cname} (hostname={box.hostname})")
            # Add the box hostname as a network alias so Docker DNS resolves it
            # from the attacker (Docker DNS only resolves container names and
            # aliases, not the hostname= setting).
            _net_cfg = self.client.api.create_networking_config({
                CHAIN_NETWORK_NAME: self.client.api.create_endpoint_config(
                    aliases=[box.hostname]
                )
            })
            container = self.client.containers.run(
                TARGET_IMAGE,
                command="sleep infinity",
                name=cname,
                hostname=box.hostname,
                networking_config=_net_cfg,
                detach=True,
                remove=False,
            )
            self._bootstrap_box(container, box)
            self._containers[box.box_id] = container

        logger.info(f"Building attacker image from {ATTACKER_DOCKERFILE_DIR}...")
        self.client.images.build(
            path=ATTACKER_DOCKERFILE_DIR,
            tag=ATTACKER_IMAGE_TAG,
            rm=True,
        )
        attacker = self.client.containers.run(
            ATTACKER_IMAGE_TAG,
            command="sleep infinity",
            name=_ATTACKER_CONTAINER_NAME,
            network=CHAIN_NETWORK_NAME,
            hostname="attacker",
            detach=True,
            remove=False,
        )
        self._containers["attacker"] = attacker
        logger.info(f"Started attacker container: {_ATTACKER_CONTAINER_NAME}")

    def teardown(self) -> None:
        """Remove all chain containers and the network. Safe to call multiple times."""
        logger.info("ChainTestEnvironment: tearing down...")
        self._force_cleanup()
        logger.info("ChainTestEnvironment: teardown complete.")

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def exec_on(self, container_key: str, command: str) -> Tuple[int, str, str]:
        """Execute a bash command in the named container.

        Args:
            container_key: ``"attacker"`` or a ``box_id`` string.
            command: Shell command to run.

        Returns:
            ``(exit_code, stdout, stderr)``
        """
        container = self._containers.get(container_key)
        if container is None:
            raise KeyError(
                f"ChainTestEnvironment: no container for key {container_key!r}. "
                f"Known keys: {sorted(self._containers)}"
            )
        exit_code, output = container.exec_run(
            cmd=["bash", "-c", command],
            demux=True,
        )
        stdout = (output[0] or b"").decode("utf-8", errors="replace")
        stderr = (output[1] or b"").decode("utf-8", errors="replace")
        return exit_code, stdout, stderr

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bootstrap_box(self, container, box: BoxDefinition) -> None:
        """Run apt bootstrap then the box's deploy.sh inside ``container``."""
        bootstrap = (
            "apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
            "curl wget ca-certificates gnupg lsb-release"
        )
        ec, out_tuple = container.exec_run(["bash", "-c", bootstrap], demux=True)
        ec = ec or 0
        if ec != 0:
            stderr = (out_tuple[1] or b"").decode("utf-8", errors="replace") if out_tuple else ""
            logger.warning(
                f"Bootstrap warning for {box.box_id} (exit {ec}): {stderr[:200]}"
            )

        deploy_script = self._deploy_scripts.get(box.box_id, "")
        if not deploy_script:
            logger.warning(
                f"No deploy script for box {box.box_id!r} — container will be un-configured."
            )
            return

        # Transfer via base64 to handle arbitrary content safely
        b64 = base64.b64encode(deploy_script.encode()).decode("ascii")
        upload_cmd = f"echo '{b64}' | base64 -d > /deploy.sh && chmod +x /deploy.sh"
        container.exec_run(["bash", "-c", upload_cmd], demux=True)

        logger.info(f"Running deploy script for {box.box_id}...")
        ec, out_tuple = container.exec_run(["bash", "-c", "bash /deploy.sh"], demux=True)
        ec = ec or 0
        stderr = (out_tuple[1] or b"").decode("utf-8", errors="replace") if out_tuple else ""
        if ec != 0:
            logger.warning(
                f"Deploy script for {box.box_id} exited {ec}: {stderr[:500]}"
            )
        else:
            logger.info(f"Deploy script for {box.box_id} completed.")

    def _force_cleanup(self) -> None:
        """Remove all chain containers and the network by well-known names."""
        for box in self._topology.boxes:
            cname = _box_container_name(box.box_id)
            try:
                c = self.client.containers.get(cname)
                c.stop(timeout=5)
                c.remove(force=True)
                logger.info(f"Removed stale container: {cname}")
            except NotFound:
                pass
            except APIError as e:
                logger.warning(f"Error cleaning up {cname}: {e}")

        try:
            c = self.client.containers.get(_ATTACKER_CONTAINER_NAME)
            c.stop(timeout=5)
            c.remove(force=True)
            logger.info(f"Removed stale container: {_ATTACKER_CONTAINER_NAME}")
        except NotFound:
            pass
        except APIError as e:
            logger.warning(f"Error cleaning up attacker container: {e}")

        try:
            net = self.client.networks.get(CHAIN_NETWORK_NAME)
            net.remove()
            logger.info(f"Removed stale network: {CHAIN_NETWORK_NAME}")
        except NotFound:
            pass
        except APIError as e:
            logger.warning(f"Error cleaning up chain network: {e}")
