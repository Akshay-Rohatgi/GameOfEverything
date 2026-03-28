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
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional

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

# Maps attacker-side command names to their apt package names (Kali/Debian).
# Used by ensure_attacker_tools() to detect and install missing tools at runtime.
# The Dockerfile pre-installs all of these; this mapping is the fallback safety net
# for tools the LLM may reference that aren't in the static image.
TOOL_TO_PACKAGE: Dict[str, str] = {
    "redis-cli":    "redis-tools",
    "psql":         "postgresql-client",
    "mysql":        "default-mysql-client",
    "ftp":          "ftp",
    "ncat":         "ncat",
    "nc":           "netcat-traditional",
    "smbclient":    "smbclient",
    "sshpass":      "sshpass",
    "ssh":          "openssh-client",
    "hydra":        "hydra",
    "nmap":         "nmap",
    "curl":         "curl",
    "wget":         "wget",
    "nikto":        "nikto",
    "enum4linux":   "enum4linux",
    "wpscan":       "wpscan",
    "dig":          "dnsutils",
    "nslookup":     "dnsutils",
    "msfconsole":   "metasploit-framework",
    "msfvenom":     "metasploit-framework",
}

# Tools that cannot be installed via apt (no Kali/Debian package available) and
# require a custom install command instead. These are handled separately in
# ensure_attacker_tools() after the apt-based installs.
MONGOSH_VERSION = "2.3.1"
TOOL_TO_INSTALL_CMD: Dict[str, str] = {
    "mongosh": (
        f"wget -q 'https://downloads.mongodb.com/compass/mongosh-{MONGOSH_VERSION}-linux-x64.tgz'"
        f" -O /tmp/mongosh.tgz"
        f" && tar -xzf /tmp/mongosh.tgz -C /tmp"
        f" && mv '/tmp/mongosh-{MONGOSH_VERSION}-linux-x64/bin/mongosh' /usr/local/bin/mongosh"
        f" && chmod +x /usr/local/bin/mongosh"
        f" && rm -rf /tmp/mongosh*"
    ),
}


class TestEnvironmentTool:
    """Manages Docker network + container lifecycle for snippet testing."""

    def __init__(self):
        self.client: docker.DockerClient = docker.from_env()
        self.network = None
        self.target_container = None
        self.attacker_container = None
        # Tracks whether `apt-get update` has been run in the attacker container
        # this session, so we only pay the cost once even if ensure_attacker_tools
        # is called multiple times.
        self._attacker_apt_updated: bool = False

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

        # Bootstrap target with tools that generated snippets commonly rely on.
        # ubuntu:22.04 base image omits curl, wget, and other utilities.
        logger.info("Bootstrapping target container with base tools...")
        bootstrap_cmd = (
            "apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
            "curl wget ca-certificates gnupg lsb-release"
        )
        exit_code, _, stderr = self._exec_in_container(self.target_container, bootstrap_cmd)
        if exit_code != 0:
            logger.warning(f"Target bootstrap had non-zero exit ({exit_code}): {stderr[:300]}")

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

    def ensure_attacker_tools(self, attack_snippets: List[str]) -> None:
        """Scan attack_snippets for tool references and install any that are missing.

        This is a safety net for cases where the Snippet Generation Agent uses a
        tool not pre-installed in the static Dockerfile image. The Dockerfile
        should cover 95%+ of cases; this method handles the rest without requiring
        an image rebuild.

        Runs `apt-get update` at most once per TestEnvironmentTool instance
        (tracked by `_attacker_apt_updated`) to minimise latency.

        Args:
            attack_snippets: List of attack_snippet strings to scan for tool names.
        """
        if self.attacker_container is None:
            raise RuntimeError(
                "Attacker container not started. Call setup() before ensure_attacker_tools()."
            )

        # Collect every tool name referenced in any attack snippet
        referenced: Set[str] = set()
        # Word-boundary pattern to avoid partial matches (e.g. "curl" in "curly")
        all_known_tools = set(TOOL_TO_PACKAGE) | set(TOOL_TO_INSTALL_CMD)
        for snippet in attack_snippets:
            for tool in all_known_tools:
                if re.search(rf"(?<![\w-]){re.escape(tool)}(?![\w-])", snippet):
                    referenced.add(tool)

        if not referenced:
            logger.info("ensure_attacker_tools: no known tools referenced in attack_snippets.")
            return

        logger.info(f"ensure_attacker_tools: tools referenced in snippets: {sorted(referenced)}")

        # Check which tools are actually missing via `which`
        missing_tools: Set[str] = set()
        for tool in referenced:
            exit_code, _, _ = self._exec_in_container(
                self.attacker_container, f"which {tool}"
            )
            if exit_code != 0:
                missing_tools.add(tool)
                logger.info(f"  '{tool}' not found in attacker container.")
            else:
                logger.debug(f"  '{tool}' already present.")

        if not missing_tools:
            logger.info("ensure_attacker_tools: all referenced tools already present.")
            return

        # Split missing tools: apt-installable vs custom install (e.g. mongosh via tarball)
        apt_tools = {t for t in missing_tools if t in TOOL_TO_PACKAGE}
        custom_tools = {t for t in missing_tools if t in TOOL_TO_INSTALL_CMD}

        # --- apt-based installs ---
        if apt_tools:
            packages_to_install: Set[str] = {TOOL_TO_PACKAGE[t] for t in apt_tools}
            logger.info(
                f"ensure_attacker_tools: installing via apt: {sorted(packages_to_install)}"
            )

            # Run apt-get update once per session
            if not self._attacker_apt_updated:
                logger.info("ensure_attacker_tools: running apt-get update in attacker container...")
                upd_exit, _, upd_err = self._exec_in_container(
                    self.attacker_container, "apt-get update -qq"
                )
                if upd_exit != 0:
                    logger.warning(
                        f"ensure_attacker_tools: apt-get update failed (exit {upd_exit}): {upd_err}"
                    )
                self._attacker_apt_updated = True

            pkg_list = " ".join(sorted(packages_to_install))
            install_cmd = (
                f"DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends {pkg_list}"
            )
            inst_exit, _, inst_err = self._exec_in_container(
                self.attacker_container, install_cmd
            )
            if inst_exit != 0:
                logger.warning(
                    f"ensure_attacker_tools: apt install failed (exit {inst_exit}): {inst_err[:500]}"
                )
            else:
                logger.info(
                    f"ensure_attacker_tools: successfully installed via apt: {sorted(packages_to_install)}"
                )

        # --- custom installs (direct binary downloads, etc.) ---
        for tool in custom_tools:
            logger.info(f"ensure_attacker_tools: installing '{tool}' via custom method...")
            cmd = TOOL_TO_INSTALL_CMD[tool]
            inst_exit, _, inst_err = self._exec_in_container(self.attacker_container, cmd)
            if inst_exit != 0:
                logger.warning(
                    f"ensure_attacker_tools: custom install of '{tool}' failed (exit {inst_exit}): {inst_err[:500]}"
                )
            else:
                logger.info(f"ensure_attacker_tools: successfully installed '{tool}' via custom method.")

    def copy_to_target(self, content: str, remote_path: str) -> None:
        """Write a string as a file inside the target container.

        Uses base64 encoding to safely transfer arbitrary content regardless
        of special characters. Creates parent directories automatically.

        Args:
            content: File content to write.
            remote_path: Absolute path inside the target container.
        """
        import base64
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        # base64 output only contains A-Za-z0-9+/= — safe in single-quoted shell strings
        cmd = (
            f"mkdir -p \"$(dirname '{remote_path}')\" && "
            f"echo '{b64}' | base64 -d > '{remote_path}'"
        )
        exit_code, _, stderr = self._exec_in_container(self.target_container, cmd)
        if exit_code != 0:
            raise RuntimeError(f"copy_to_target failed for {remote_path}: {stderr}")

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
