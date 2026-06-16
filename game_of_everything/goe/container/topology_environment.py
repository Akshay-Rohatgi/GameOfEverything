"""TopologyEnvironment — multi-system Docker environment for L3 chain testing.

Sets up a shared flat bridge network with one ubuntu:22.04 target container per
system and one shared Kali attacker container. Each system container is given a
Docker network alias equal to its hostname so Docker DNS resolves it from the
attacker and other containers.

Design mirrors v1's ChainTestEnvironment but is parametrized on an EntityGraph
rather than a v1 NetworkTopology, and uses the self-installing deploy script
approach so no pre-built runtime image is required.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph

logger = logging.getLogger(__name__)

# Reuse the attacker image and wait_for_docker from v1
_V1_TOOLS = "game_of_everything.tools.test_environment"

_BASE_TARGET_IMAGE = "ubuntu:22.04"
_CHAIN_NETWORK_NAME = "goe_chain_net"
_ATTACKER_CONTAINER_PREFIX = "goe_chain_attacker"

# Bootstrap installed into each target before the deploy script runs.
# Matches the subset that v1's chain test installed.
_BOOTSTRAP_CMD = (
    "apt-get update -qq && "
    "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
    "curl wget ca-certificates gnupg lsb-release"
)


class TopologyEnvironment:
    """One shared bridge network + one target container per system + one attacker.

    Usage::

        with TopologyEnvironment(graph) as env:
            for system_id, script in per_system_scripts.items():
                env.deploy_system(system_id, script)
            result = run(chain_procedure, env, ctx)
    """

    def __init__(self, graph: "EntityGraph", scope: str = "") -> None:
        self._graph = graph
        self._scope = scope or "chain"
        self._client = None
        self._network = None
        # Maps "attacker" → container object, system_id → container object
        self._containers: dict[str, object] = {}

    # ------------------------------------------------------------------
    # Lazy Docker client
    # ------------------------------------------------------------------

    @property
    def _docker(self):
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Create the chain network, bootstrap system containers, start attacker."""
        from game_of_everything.tools.test_environment import (
            wait_for_docker,
            ATTACKER_IMAGE_TAG,
            ATTACKER_DOCKERFILE_DIR,
        )
        wait_for_docker("set up chain topology")
        self._force_cleanup()

        net_name = f"{_CHAIN_NETWORK_NAME}_{self._scope}"
        self._network = self._docker.networks.create(net_name, driver="bridge")
        self._net_name = net_name
        logger.info(f"TopologyEnvironment: created network {net_name}")

        for system in self._graph.systems:
            cname = f"goe_chain_{self._scope}_{system.id}"
            hostname = system.network.hostname
            logger.info(f"Starting system container: {cname} (hostname={hostname})")
            # Start container disconnected, then connect with network alias.
            # Create container directly on the chain network instead of "none" then
            # connect, as Docker doesn't allow connecting containers from private
            # (none) mode networks. Set hostname and create with alias for DNS.
            container = self._docker.containers.run(
                _BASE_TARGET_IMAGE,
                command="sleep infinity",
                name=cname,
                hostname=hostname,
                network=net_name,
                detach=True,
                remove=False,
            )
            # Add hostname as a network alias for DNS resolution across containers
            self._network.disconnect(container)
            self._network.connect(container, aliases=[hostname])
            # Bootstrap apt tools so the self-installing deploy scripts work
            self._bootstrap(container, system.id)
            self._containers[system.id] = container

        # Build attacker image (cached after first build) then start it
        logger.info("TopologyEnvironment: building attacker image…")
        self._docker.images.build(
            path=ATTACKER_DOCKERFILE_DIR,
            tag=ATTACKER_IMAGE_TAG,
            rm=True,
        )
        attacker_name = f"{_ATTACKER_CONTAINER_PREFIX}_{self._scope}"
        attacker = self._docker.containers.run(
            ATTACKER_IMAGE_TAG,
            command="sleep infinity",
            name=attacker_name,
            network=net_name,
            hostname="attacker",
            detach=True,
            remove=False,
        )
        self._containers["attacker"] = attacker
        logger.info(f"TopologyEnvironment: attacker up ({attacker_name})")

    def teardown(self) -> None:
        """Remove all containers and the network. Safe to call multiple times."""
        logger.info("TopologyEnvironment: tearing down…")
        self._force_cleanup()

    def __enter__(self) -> "TopologyEnvironment":
        self.setup()
        return self

    def __exit__(self, *_) -> None:
        self.teardown()

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------

    def deploy_system(self, system_id: str, deploy_script: str) -> tuple[int, str, str]:
        """Run deploy_script inside the named system's container.

        The script is transferred via base64 to handle arbitrary content.
        Returns (exit_code, stdout, stderr).
        """
        container = self._containers.get(system_id)
        if container is None:
            raise KeyError(f"TopologyEnvironment: no container for system {system_id!r}")

        b64 = base64.b64encode(deploy_script.encode()).decode("ascii")
        upload = f"echo '{b64}' | base64 -d > /deploy.sh && chmod +x /deploy.sh"
        container.exec_run(["bash", "-c", upload], demux=True)

        logger.info(f"TopologyEnvironment: deploying system {system_id}…")
        ec, out_tuple = container.exec_run(
            ["bash", "-c", "bash /deploy.sh"],
            demux=True,
        )
        ec = ec or 0
        stdout = (out_tuple[0] or b"").decode("utf-8", errors="replace") if out_tuple else ""
        stderr = (out_tuple[1] or b"").decode("utf-8", errors="replace") if out_tuple else ""
        if ec != 0:
            logger.warning(
                f"TopologyEnvironment: deploy for {system_id} exited {ec}: {stderr[:500]}"
            )
        else:
            logger.info(f"TopologyEnvironment: deploy for {system_id} OK")
        return ec, stdout, stderr

    # ------------------------------------------------------------------
    # Execution interface (mirrors TestEnvironment for executor compatibility)
    # ------------------------------------------------------------------

    def exec_in(self, container_key: str, command: str, privileged: bool = False) -> tuple[int, str, str]:
        """Run a bash command in the named container.

        Args:
            container_key: ``"attacker"`` or a ``system_id``.
            command: Shell command to run.
        """
        container = self._containers.get(container_key)
        if container is None:
            raise KeyError(
                f"TopologyEnvironment: no container for key {container_key!r}. "
                f"Known: {sorted(self._containers)}"
            )
        ec, out_tuple = container.exec_run(
            cmd=["bash", "-c", command],
            demux=True,
            privileged=privileged,
        )
        ec = ec or 0
        stdout = (out_tuple[0] or b"").decode("utf-8", errors="replace") if out_tuple else ""
        stderr = (out_tuple[1] or b"").decode("utf-8", errors="replace") if out_tuple else ""
        return ec, stdout, stderr

    def exec_in_bg(self, container_key: str, command: str) -> None:
        """Fire-and-forget exec in the named container (attacker only)."""
        if container_key != "attacker":
            raise ValueError(
                f"TopologyEnvironment: exec_in_bg only supported for 'attacker', got {container_key!r}"
            )
        container = self._containers["attacker"]
        container.exec_run(
            ["bash", "-c", f"nohup bash -c {repr(command)} &>/dev/null &"],
            detach=True,
        )

    def get_attacker_host(self) -> str:
        return "attacker"

    def get_target_host(self) -> str:
        # Not meaningful for multi-system; chain procedures use ${system.<id>.host}
        if len(self._graph.systems) == 1:
            return self._graph.systems[0].network.hostname
        return "target"

    def get_cdp_url(self) -> str:
        # Browser sessions not supported in chain test context
        return ""

    @property
    def target_name(self) -> str:
        # Best-effort — used only for display
        if len(self._graph.systems) == 1:
            return f"goe_chain_{self._scope}_{self._graph.systems[0].id}"
        return f"goe_chain_{self._scope}_<multi>"

    @property
    def attacker_name(self) -> str:
        return f"{_ATTACKER_CONTAINER_PREFIX}_{self._scope}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bootstrap(self, container, system_id: str) -> None:
        ec, out_tuple = container.exec_run(["bash", "-c", _BOOTSTRAP_CMD], demux=True)
        ec = ec or 0
        if ec != 0:
            stderr = (out_tuple[1] or b"").decode("utf-8", errors="replace") if out_tuple else ""
            logger.warning(f"TopologyEnvironment: bootstrap warning for {system_id} (exit {ec}): {stderr[:200]}")

    def _force_cleanup(self) -> None:
        """Remove all chain containers and networks by well-known names."""
        from docker.errors import NotFound, APIError

        # Containers: system containers + attacker
        system_names = [f"goe_chain_{self._scope}_{s.id}" for s in self._graph.systems]
        attacker_name = f"{_ATTACKER_CONTAINER_PREFIX}_{self._scope}"
        for cname in system_names + [attacker_name]:
            try:
                c = self._docker.containers.get(cname)
                c.stop(timeout=5)
                c.remove(force=True)
                logger.info(f"TopologyEnvironment: removed container {cname}")
            except NotFound:
                pass
            except APIError as e:
                logger.warning(f"TopologyEnvironment: cleanup error for {cname}: {e}")

        net_name = getattr(self, "_net_name", f"{_CHAIN_NETWORK_NAME}_{self._scope}")
        try:
            net = self._docker.networks.get(net_name)
            net.remove()
            logger.info(f"TopologyEnvironment: removed network {net_name}")
        except NotFound:
            pass
        except APIError as e:
            logger.warning(f"TopologyEnvironment: cleanup error for network {net_name}: {e}")
