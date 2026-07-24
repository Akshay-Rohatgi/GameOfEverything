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
# Bootstrap command imported from central registry
from goe.container.bootstrap import get_bootstrap_command
_BOOTSTRAP_CMD = get_bootstrap_command("ubuntu")


class TopologyEnvironment:
    """One shared bridge network + one target container per system + one attacker.

    Usage::

        with TopologyEnvironment(graph) as env:
            for system_id, script in per_system_scripts.items():
                env.deploy_system(system_id, script)
            result = run(chain_procedure, env, ctx)
    """

    def __init__(self, graph: "EntityGraph", scope: str = "", expose_ports: bool | dict[int, int] = False) -> None:
        self._graph = graph
        self._scope = scope or "chain"
        self._expose_ports = expose_ports
        self._client = None
        self._network = None
        # Maps "attacker" → container object, system_id → container object
        self._containers: dict[str, object] = {}
        # system_id → {container_port: host_port} (populated during setup when expose_ports=True)
        self.port_map: dict[str, dict[int, int]] = {}

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
        from goe.container.test_environment_tool import (
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

        # Track host ports already claimed to avoid collisions across systems
        claimed_host_ports: set[int] = set()

        for system in self._graph.systems:
            cname = f"goe_chain_{self._scope}_{system.id}"
            hostname = system.network.hostname
            logger.info(f"Starting system container: {cname} (hostname={hostname})")
            # Start container disconnected, then connect with network alias.
            # Create container directly on the chain network instead of "none" then
            # connect, as Docker doesn't allow connecting containers from private
            # (none) mode networks. Set hostname and create with alias for DNS.
            port_bindings = None
            if self._expose_ports:
                if isinstance(self._expose_ports, dict):
                    # Explicit mapping provided — use as-is for each system,
                    # but offset host ports to avoid collisions across systems.
                    port_bindings = {}
                    system_port_map: dict[int, int] = {}
                    for cp, hp in self._expose_ports.items():
                        while hp in claimed_host_ports:
                            hp += 1
                        port_bindings[f"{cp}/tcp"] = hp
                        system_port_map[cp] = hp
                        claimed_host_ports.add(hp)
                    self.port_map[system.id] = system_port_map
                elif system.network.exposed_ports:
                    port_bindings = {}
                    system_port_map: dict[int, int] = {}
                    for p in system.network.exposed_ports:
                        host_port = p
                        while host_port in claimed_host_ports:
                            host_port += 1
                        port_bindings[f"{p}/tcp"] = host_port
                        system_port_map[p] = host_port
                        claimed_host_ports.add(host_port)
                    self.port_map[system.id] = system_port_map
                if port_bindings:
                    logger.info(f"  port mappings: {port_bindings}")
            container = self._docker.containers.run(
                _BASE_TARGET_IMAGE,
                command="sleep infinity",
                name=cname,
                hostname=hostname,
                network=net_name,
                ports=port_bindings,
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
    # Reset (for retry loops)
    # ------------------------------------------------------------------

    def reset_all_systems(self, per_system_scripts: dict[str, str]) -> dict[str, tuple[int, str, str]]:
        """Reset all system containers to fresh state and re-deploy.

        Used by the chain test retry loop to clear stale state from failed attempts.
        Kills all containers, recreates them from the base ubuntu image, re-bootstraps,
        and re-runs all deploy scripts.

        Args:
            per_system_scripts: {system_id: deploy_script} dict

        Returns:
            {system_id: (exit_code, stdout, stderr)} for each re-deploy
        """
        logger.info("TopologyEnvironment: resetting all systems for retry…")

        # Kill and recreate all system containers
        for system in self._graph.systems:
            cname = f"goe_chain_{self._scope}_{system.id}"
            hostname = system.network.hostname

            # Remove old container
            old_container = self._containers.get(system.id)
            if old_container:
                try:
                    old_container.remove(force=True)
                except Exception as e:
                    logger.warning(f"TopologyEnvironment: error removing {cname}: {e}")

            # Recreate container (reuse port bindings from setup)
            port_bindings = None
            if system.id in self.port_map:
                port_bindings = {f"{cp}/tcp": hp for cp, hp in self.port_map[system.id].items()}

            container = self._docker.containers.run(
                _BASE_TARGET_IMAGE,
                command="sleep infinity",
                name=cname,
                hostname=hostname,
                network=self._net_name,
                ports=port_bindings,
                detach=True,
                remove=False,
            )
            # Re-add hostname alias
            self._network.disconnect(container)
            self._network.connect(container, aliases=[hostname])
            # Re-bootstrap
            self._bootstrap(container, system.id)
            self._containers[system.id] = container

        # Re-deploy all system scripts
        results: dict[str, tuple[int, str, str]] = {}
        for system_id, script in per_system_scripts.items():
            results[system_id] = self.deploy_system(system_id, script)

        return results

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------

    def deploy_system(self, system_id: str, deploy_script: str) -> tuple[int, str, str]:
        """Run deploy_script inside the named system's container.

        The script is transferred via base64 to handle arbitrary content.
        After deployment succeeds, restarts any services that need it (SSH, Samba,
        etc.) so they pick up user/config changes made by the deploy script.

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
            # Restart services so they pick up user/config changes (SSH, Samba, etc.)
            self._restart_services(container, system_id)
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

    def _restart_services(self, container, system_id: str) -> None:
        """Restart services on the system after deploy so they pick up config changes.

        Restarts SSH, Samba, MySQL, etc. — services that don't auto-reload when /etc/passwd
        or config files change. Uses ServiceRegistry.restart_all() if the system declares
        services; otherwise uses a heuristic restart script for common daemons.
        """
        system = self._graph.system_by_id(system_id)
        if system is None:
            logger.warning(f"TopologyEnvironment: no system found for {system_id}, skipping service restart")
            return

        # If system declares services, use ServiceRegistry to restart them
        if system.services:
            from goe.services import get_registry
            registry = get_registry()
            specs = [s for s in system.services if registry.has_recipe(s.id)]
            if specs:
                restart_script = registry.restart_all(specs)
                logger.info(f"TopologyEnvironment: restarting {len(specs)} service(s) for system {system_id}…")
                ec, _stdout, stderr = self.exec_in(system_id, restart_script)
                if ec != 0:
                    logger.warning(f"TopologyEnvironment: service restart errors for {system_id}: {stderr[:300]}")
                return

        # Fallback: heuristic restart for common daemons (when system has no declared services)
        # Kill and restart SSH, Samba, MySQL background processes. Use `|| true` to ignore
        # failures (service might not be installed). These containers don't have systemd —
        # services run as background processes started with `nohup cmd &`.
        heuristic_restart = """
# SSH
if pgrep -f '/usr/sbin/sshd' >/dev/null 2>&1; then
    pkill -f '/usr/sbin/sshd' || true
    sleep 1
    /usr/sbin/sshd -D -e &>/dev/null &
fi

# Samba
if pgrep -f 'smbd' >/dev/null 2>&1; then
    pkill -f 'smbd|nmbd' || true
    sleep 1
    smbd -D &>/dev/null &
    nmbd -D &>/dev/null &
fi

# MySQL/MariaDB
if pgrep -f 'mysqld' >/dev/null 2>&1; then
    pkill -f 'mysqld' || true
    sleep 2
    mysqld &>/var/log/mysql.log &
fi
"""
        logger.info(f"TopologyEnvironment: heuristic service restart for system {system_id}…")
        ec, _stdout, stderr = self.exec_in(system_id, heuristic_restart)
        if ec != 0:
            logger.debug(f"TopologyEnvironment: heuristic restart had warnings for {system_id}: {stderr[:200]}")

    def _force_cleanup(self) -> None:
        """Remove all chain containers and networks by well-known names."""
        from docker.errors import NotFound, APIError

        # Containers: system containers + attacker
        system_names = [f"goe_chain_{self._scope}_{s.id}" for s in self._graph.systems]
        attacker_name = f"{_ATTACKER_CONTAINER_PREFIX}_{self._scope}"
        for cname in system_names + [attacker_name]:
            try:
                c = self._docker.containers.get(cname)
                # force=True SIGKILLs + removes in one call. Do NOT call stop()
                # first: these containers run `sleep infinity` as PID 1, which
                # ignores SIGTERM, so a graceful stop just blocks for the full
                # timeout (up to client_timeout + t seconds) per container.
                c.remove(force=True, v=True)
                logger.info(f"TopologyEnvironment: removed container {cname}")
            except NotFound:
                pass
            except APIError as e:
                logger.warning(f"TopologyEnvironment: cleanup error for {cname}: {e}")

        net_name = getattr(self, "_net_name", f"{_CHAIN_NETWORK_NAME}_{self._scope}")
        try:
            net = self._docker.networks.get(net_name)
            # Force-disconnect any lingering endpoints first; otherwise net.remove()
            # fails (or stalls on some daemons) with "network has active endpoints".
            net.reload()
            for endpoint in net.containers:
                try:
                    net.disconnect(endpoint, force=True)
                except APIError:
                    pass
            net.remove()
            logger.info(f"TopologyEnvironment: removed network {net_name}")
        except NotFound:
            pass
        except APIError as e:
            logger.warning(f"TopologyEnvironment: cleanup error for network {net_name}: {e}")
