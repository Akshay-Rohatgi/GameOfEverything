"""ProgressiveEnvironment — system-scoped container that accumulates deployments with snapshots.

For multi-entity builds on the same system, keeps a single running container that accumulates
state. Snapshots (docker commit) after each successful entity for rollback on failures.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ProgressiveEnvironment:
    """System-scoped environment that accumulates deployments with filesystem snapshots.

    Unlike TestEnvironment (which creates fresh containers per entity), this keeps a single
    target + attacker container pair running for an entire system. After each entity's L2
    passes, the target container's filesystem is committed as a Docker image snapshot.

    On failure retry or fan-out, the target is restored from a prior snapshot (stop+remove
    current container, create from snapshot image, replay scripts to restart services).

    Interface is duck-typed with TestEnvironment for compatibility with build_entity() and
    the procedure executor.
    """

    def __init__(self, system_id: str, scope: str = ""):
        self._system_id = system_id
        self._scope = scope or f"prog_{system_id[:12]}"
        self._client = None  # docker.DockerClient (lazy)
        self._network = None
        self._target_container = None
        self._attacker_container = None

        # Snapshot state
        self._deploy_scripts: list[tuple[str, str]] = []  # [(entity_id, deploy_script), ...]
        self._snapshots: dict[str, int] = {}  # entity_id → index in _deploy_scripts
        self._current_snapshot: str | None = None  # last successfully snapshotted entity_id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Create network, target, and attacker containers. Bootstrap base packages."""
        import docker

        self._client = docker.from_env()

        # Clean up any existing containers/networks from previous failed runs
        self._cleanup_existing()

        net_name = f"goe_prog_{self._scope}_net"

        # Create bridge network
        self._network = self._client.networks.create(net_name, driver="bridge")
        logger.info(f"[ProgressiveEnvironment] Created network {net_name}")

        # Start target container (ubuntu:22.04)
        target_name = f"goe_prog_target_{self._scope}"
        self._target_container = self._client.containers.run(
            "ubuntu:22.04",
            command="sleep infinity",
            name=target_name,
            hostname="target",
            network=net_name,
            detach=True,
            remove=False,
        )
        logger.info(f"[ProgressiveEnvironment] Started target {target_name}")

        # Bootstrap base packages
        logger.info("[ProgressiveEnvironment] Bootstrapping target...")
        from goe.container.bootstrap import get_bootstrap_command
        self._exec_in_target(get_bootstrap_command("ubuntu"))

        # Build and start attacker container (kali-based goe-attacker:latest)
        self._ensure_attacker_image()
        attacker_name = f"goe_prog_attacker_{self._scope}"
        self._attacker_container = self._client.containers.run(
            "goe-attacker:latest",
            command="sleep infinity",
            name=attacker_name,
            hostname="attacker",
            network=net_name,
            detach=True,
            remove=False,
        )
        logger.info(f"[ProgressiveEnvironment] Started attacker {attacker_name}")

    def teardown(self) -> None:
        """Stop and remove all containers and network. Optionally clean snapshot images."""
        if self._target_container:
            try:
                self._target_container.stop(timeout=3)
                self._target_container.remove(force=True)
            except Exception as e:
                logger.warning(f"[ProgressiveEnvironment] Failed to remove target: {e}")

        if self._attacker_container:
            try:
                self._attacker_container.stop(timeout=3)
                self._attacker_container.remove(force=True)
            except Exception as e:
                logger.warning(f"[ProgressiveEnvironment] Failed to remove attacker: {e}")

        if self._network:
            try:
                self._network.remove()
            except Exception as e:
                logger.warning(f"[ProgressiveEnvironment] Failed to remove network: {e}")

        # Clean up snapshot images
        self._cleanup_snapshots()

    def __enter__(self) -> "ProgressiveEnvironment":
        self.setup()
        return self

    def __exit__(self, *_) -> None:
        self.teardown()

    # ------------------------------------------------------------------
    # Snapshot Management
    # ------------------------------------------------------------------

    def snapshot(self, entity_id: str, deploy_script: str) -> None:
        """Commit the current target container state and store the deploy script."""
        tag = f"{self._scope}_{entity_id}"
        logger.info(f"[ProgressiveEnvironment] Snapshotting target as goe_prog:{tag}")

        self._target_container.commit(repository="goe_prog", tag=tag)
        self._deploy_scripts.append((entity_id, deploy_script))
        self._snapshots[entity_id] = len(self._deploy_scripts) - 1
        self._current_snapshot = entity_id

    def restore(self, entity_id: str) -> None:
        """Restore target to a prior snapshot (stop+remove, create from image, replay scripts)."""
        if entity_id not in self._snapshots:
            raise ValueError(f"No snapshot found for entity '{entity_id}'")

        idx = self._snapshots[entity_id]
        tag = f"goe_prog:{self._scope}_{entity_id}"
        logger.info(f"[ProgressiveEnvironment] Restoring target from {tag}")

        # Stop and remove current target
        self._target_container.stop(timeout=3)
        self._target_container.remove(force=True)

        # Create new target from snapshot image
        net_name = f"goe_prog_{self._scope}_net"
        target_name = f"goe_prog_target_{self._scope}"
        self._target_container = self._client.containers.run(
            tag,
            command="sleep infinity",
            name=target_name,
            hostname="target",
            network=net_name,
            detach=True,
            remove=False,
        )

        # Replay all scripts up to and including this snapshot to restart services
        logger.info(f"[ProgressiveEnvironment] Replaying {idx + 1} deploy script(s) to restart services...")
        for i, (eid, script) in enumerate(self._deploy_scripts[: idx + 1]):
            logger.debug(f"  [{i+1}/{idx+1}] Replaying {eid}")
            self._exec_in_target(script)

        self._current_snapshot = entity_id

    @property
    def current_snapshot(self) -> str | None:
        """The entity_id of the most recent snapshot (or None if no snapshots yet)."""
        return self._current_snapshot

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------

    def deploy(self, deploy_script: str) -> tuple[int, str, str]:
        """Execute a bash deploy script in the running target container."""
        return self._exec_in_target(deploy_script)

    def copy_file(self, container: str, content: str, path: str) -> None:
        """Write a file into the target or attacker container via base64 encoding."""
        import base64

        b64_content = base64.b64encode(content.encode()).decode()
        if container == "target":
            cmd = f"echo '{b64_content}' | base64 -d > {path}"
            self._exec_in_target(cmd)
        elif container == "attacker":
            cmd = f"echo '{b64_content}' | base64 -d > {path}"
            self._exec_in_attacker(cmd)
        else:
            raise ValueError(f"Unknown container: {container!r}")

    def healthcheck(self, port: int, host: str | None = None) -> bool:
        """Return True if the given port is responding to HTTP on the target."""
        h = host or "target"
        exit_code, _, _ = self._exec_in_attacker(
            f"curl -sf --max-time 5 http://{h}:{port}/ || "
            f"curl -sf --max-time 5 http://{h}:{port}/health"
        )
        return exit_code == 0

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def exec_in(self, container: str, command: str, privileged: bool = False) -> tuple[int, str, str]:
        """Run a bash command in the named container."""
        if container == "target":
            return self._exec_in_target(command)
        elif container == "attacker":
            return self._exec_in_attacker(command)
        else:
            raise ValueError(f"Unknown container: {container!r}")

    def reset_attacker(self) -> None:
        """Replace the attacker container with a fresh one — clears all background processes."""
        logger.info("[ProgressiveEnvironment] Resetting attacker container...")
        self._attacker_container.stop(timeout=3)
        self._attacker_container.remove(force=True)

        net_name = f"goe_prog_{self._scope}_net"
        attacker_name = f"goe_prog_attacker_{self._scope}"
        self._attacker_container = self._client.containers.run(
            "goe-attacker:latest",
            command="sleep infinity",
            name=attacker_name,
            hostname="attacker",
            network=net_name,
            detach=True,
            remove=False,
        )

    def reset_target(self) -> None:
        """Restore target to the current snapshot (pre-deploy state for retry).

        If no snapshot exists yet (first entity on the system), resets to fresh ubuntu:22.04.
        """
        if self._current_snapshot:
            logger.info(f"[ProgressiveEnvironment] Resetting target to snapshot '{self._current_snapshot}'")
            self.restore(self._current_snapshot)
        else:
            logger.info("[ProgressiveEnvironment] No snapshot yet — resetting to fresh ubuntu:22.04")
            self._reset_to_base()

    def exec_in_bg(self, container: str, command: str) -> None:
        """Fire-and-forget exec — process survives after the exec shell exits."""
        if container != "attacker":
            raise ValueError(f"Background exec only supported for attacker container, got: {container!r}")
        # Run with nohup and redirect to /dev/null
        self._attacker_container.exec_run(["bash", "-c", f"nohup {command} &>/dev/null &"], detach=True)

    # ------------------------------------------------------------------
    # Network addresses
    # ------------------------------------------------------------------

    def get_target_host(self) -> str:
        """Hostname of the target container as seen from the attacker container."""
        return "target"

    def get_attacker_host(self) -> str:
        """Hostname of the attacker container as seen from the target container."""
        return "attacker"

    def get_cdp_url(self) -> str:
        """WebSocket CDP URL for the browser sidecar (not supported in ProgressiveEnvironment)."""
        return ""

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def target_name(self) -> str:
        return f"goe_prog_target_{self._scope}"

    @property
    def attacker_name(self) -> str:
        return f"goe_prog_attacker_{self._scope}"

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _exec_in_target(self, command: str) -> tuple[int, str, str]:
        """Execute bash command in target container."""
        exit_code, output = self._target_container.exec_run(["bash", "-c", command], demux=True)
        stdout = (output[0] or b"").decode("utf-8", errors="replace")
        stderr = (output[1] or b"").decode("utf-8", errors="replace")
        return exit_code, stdout, stderr

    def _exec_in_attacker(self, command: str) -> tuple[int, str, str]:
        """Execute bash command in attacker container."""
        exit_code, output = self._attacker_container.exec_run(["bash", "-c", command], demux=True)
        stdout = (output[0] or b"").decode("utf-8", errors="replace")
        stderr = (output[1] or b"").decode("utf-8", errors="replace")
        return exit_code, stdout, stderr

    def _reset_to_base(self) -> None:
        """Stop and recreate target from fresh ubuntu:22.04, bootstrap packages."""
        self._target_container.stop(timeout=3)
        self._target_container.remove(force=True)

        net_name = f"goe_prog_{self._scope}_net"
        target_name = f"goe_prog_target_{self._scope}"
        self._target_container = self._client.containers.run(
            "ubuntu:22.04",
            command="sleep infinity",
            name=target_name,
            hostname="target",
            network=net_name,
            detach=True,
            remove=False,
        )

        # Re-bootstrap
        self._exec_in_target(
            "apt-get update -y && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y "
            "curl wget ca-certificates gnupg lsb-release iproute2 net-tools procps iputils-ping sudo sshpass"
        )

    def _ensure_attacker_image(self) -> None:
        """Build goe-attacker:latest if not present."""
        try:
            self._client.images.get("goe-attacker:latest")
        except Exception:
            logger.info("[ProgressiveEnvironment] Building goe-attacker:latest...")
            from pathlib import Path

            dockerfile_dir = Path(__file__).parent.parent.parent / "docker" / "attacker"
            self._client.images.build(path=str(dockerfile_dir), tag="goe-attacker:latest", rm=True)

    def _cleanup_existing(self) -> None:
        """Remove any existing containers/networks with our names from previous runs."""
        if not self._client:
            return

        target_name = f"goe_prog_target_{self._scope}"
        attacker_name = f"goe_prog_attacker_{self._scope}"
        net_name = f"goe_prog_{self._scope}_net"

        # Remove target container if exists
        try:
            old_target = self._client.containers.get(target_name)
            old_target.stop(timeout=1)
            old_target.remove(force=True)
            logger.debug(f"[ProgressiveEnvironment] Cleaned up existing target: {target_name}")
        except Exception:
            pass

        # Remove attacker container if exists
        try:
            old_attacker = self._client.containers.get(attacker_name)
            old_attacker.stop(timeout=1)
            old_attacker.remove(force=True)
            logger.debug(f"[ProgressiveEnvironment] Cleaned up existing attacker: {attacker_name}")
        except Exception:
            pass

        # Remove network if exists
        try:
            old_net = self._client.networks.get(net_name)
            old_net.remove()
            logger.debug(f"[ProgressiveEnvironment] Cleaned up existing network: {net_name}")
        except Exception:
            pass

    def _cleanup_snapshots(self) -> None:
        """Remove all snapshot images created during this run."""
        if not self._client:
            return
        for entity_id in self._snapshots:
            tag = f"goe_prog:{self._scope}_{entity_id}"
            try:
                self._client.images.remove(tag, force=True)
            except Exception as e:
                logger.debug(f"[ProgressiveEnvironment] Could not remove snapshot {tag}: {e}")
