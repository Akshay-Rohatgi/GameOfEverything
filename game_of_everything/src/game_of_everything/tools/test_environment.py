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
import time
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional

import docker
from docker.errors import DockerException, NotFound, APIError

logger = logging.getLogger(__name__)


def wait_for_docker(action: str = "setup Docker environment") -> None:
    """Block until Docker is reachable, prompting the user on each failure.

    Call this before any operation that requires a live Docker daemon so that
    a "Docker Desktop not started" situation produces a friendly pause instead
    of a hard crash.

    Args:
        action: Human-readable description of what is about to happen, shown
                in the prompt so the user knows which step is waiting.
    """
    while True:
        try:
            client = docker.from_env()
            client.ping()
            client.close()
            return
        except DockerException:
            print(
                f"\n[Docker not available] Cannot {action}.\n"
                "Please start Docker Desktop (or the Docker daemon) and press Enter to retry,"
                " or type 'skip' to skip this step: ",
                end="",
                flush=True,
            )
            response = input().strip().lower()
            if response == "skip":
                raise RuntimeError(
                    "Docker unavailable — step skipped by user."
                ) from None
            # Brief pause before re-checking so the daemon has time to start
            time.sleep(2)

# Default names (single-box / no scope). Multi-box runs use a scope prefix
# so that each box gets its own isolated set of containers + network.
_DEFAULT_NETWORK = "goe_test_net"
_DEFAULT_TARGET = "goe_target"
_DEFAULT_ATTACKER = "goe_attacker"

# Legacy module-level constants kept for backwards-compat (test scripts, etc.)
NETWORK_NAME = _DEFAULT_NETWORK
TARGET_NAME = _DEFAULT_TARGET
ATTACKER_NAME = _DEFAULT_ATTACKER
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

# Maps target-side command names to their apt package names (Ubuntu 22.04).
# ubuntu:22.04 base image ships without many common utilities that testing
# snippets need (ss, ps, netstat, ping, etc.).  ensure_target_tools() uses
# this to detect and install anything the LLM references at runtime.
TARGET_TOOL_TO_PACKAGE: Dict[str, str] = {
    "ss":       "iproute2",
    "ip":       "iproute2",
    "netstat":  "net-tools",
    "ifconfig": "net-tools",
    "ps":       "procps",
    "free":     "procps",
    "top":      "procps",
    "ping":     "iputils-ping",
    "ping6":    "iputils-ping",
    "sudo":     "sudo",
    "openssl":  "openssl",
    "sqlite3":  "sqlite3",
    "strace":   "strace",
    "nmap":     "nmap",
    "php":      "php-cli",
    "python3":  "python3",
    "pip3":     "python3-pip",
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

    def __init__(self, scope: str = "", hostname: str = ""):
        """Args:
            scope: Optional prefix for container and network names.
                   Empty string → default names (goe_target, goe_attacker, goe_test_net).
                   Non-empty    → scoped names (goe_{scope}_target, etc.) so that
                                  multiple boxes can run independently without collisions.
            hostname: Hostname to assign to the target container.
                      Defaults to "target" when empty so single-box / legacy callers
                      are unaffected. Pass box.hostname for per-box multi-box runs so
                      that tested scripts see the same hostname as the final deployment.
        """
        self._scope = scope
        self._hostname = hostname or "target"
        self._client: Optional[docker.DockerClient] = None
        self.network = None
        self.target_container = None
        self.attacker_container = None
        # Tracks whether `apt-get update` has been run in the attacker container
        # this session, so we only pay the cost once even if ensure_attacker_tools
        # is called multiple times.
        self._attacker_apt_updated: bool = False
        # Same flag for the target container (used by ensure_target_tools).
        self._target_apt_updated: bool = False

    # ------------------------------------------------------------------
    # Lazy Docker client
    # ------------------------------------------------------------------

    @property
    def client(self) -> docker.DockerClient:
        """Docker client — initialised on first access so instantiation never
        fails when Docker is not running (e.g. during import-time tests)."""
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    # ------------------------------------------------------------------
    # Scoped name properties
    # ------------------------------------------------------------------

    @property
    def _prefix(self) -> str:
        return f"goe_{self._scope}_" if self._scope else "goe_"

    @property
    def network_name(self) -> str:
        return f"{self._prefix}test_net"

    @property
    def target_name(self) -> str:
        return f"{self._prefix}target"

    @property
    def attacker_name(self) -> str:
        return f"{self._prefix}attacker"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        """Create bridge network, start target (Ubuntu) and attacker (Kali) containers."""
        wait_for_docker(f"set up test environment for scope '{self._scope or 'default'}'")
        logger.info("Setting up test environment...")

        # Clean up any stale resources from a previous failed run
        self._force_cleanup()

        # Create bridge network
        self.network = self.client.networks.create(self.network_name, driver="bridge")
        logger.info(f"Created network: {self.network_name}")

        # Start target container
        self.target_container = self.client.containers.run(
            TARGET_IMAGE,
            command="sleep infinity",
            name=self.target_name,
            network=self.network_name,
            hostname=self._hostname,
            detach=True,
            remove=False,
        )
        logger.info(f"Started target container: {self.target_name} ({TARGET_IMAGE})")

        # Bootstrap target with tools that generated snippets commonly rely on.
        # ubuntu:22.04 base image omits curl, wget, and many standard utilities.
        # This list covers the most common testing-snippet patterns; tools not
        # listed here are caught at runtime by ensure_target_tools().
        logger.info("Bootstrapping target container with base tools...")
        bootstrap_cmd = (
            "apt-get update -qq && "
            "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
            "curl wget ca-certificates gnupg lsb-release "
            "iproute2 net-tools procps iputils-ping sudo sshpass"
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
            name=self.attacker_name,
            network=self.network_name,
            hostname="attacker",
            detach=True,
            remove=False,
        )
        logger.info(f"Started attacker container: {self.attacker_name} ({ATTACKER_IMAGE_TAG})")

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

    def ensure_target_tools(self, code_snippets: List[str], testing_snippets: List[str]) -> None:
        """Scan code and testing snippets for tool references and install any that
        are missing from the target container.

        Mirrors ensure_attacker_tools() for the target side.  The bootstrap in
        setup() covers the most common utilities; this method is the safety net
        for anything the LLM generates that wasn't anticipated.

        Runs `apt-get update` at most once per TestEnvironmentTool instance
        (tracked by `_target_apt_updated`) to minimise latency.

        Args:
            code_snippets:    List of code_snippet strings to scan.
            testing_snippets: List of testing_snippet strings to scan.
        """
        if self.target_container is None:
            raise RuntimeError(
                "Target container not started. Call setup() before ensure_target_tools()."
            )

        all_snippets = code_snippets + testing_snippets
        referenced: Set[str] = set()
        for snippet in all_snippets:
            for tool in TARGET_TOOL_TO_PACKAGE:
                if re.search(rf"(?<![\w-]){re.escape(tool)}(?![\w-])", snippet):
                    referenced.add(tool)

        if not referenced:
            logger.info("ensure_target_tools: no known tools referenced in snippets.")
            return

        logger.info(f"ensure_target_tools: tools referenced in snippets: {sorted(referenced)}")

        missing_tools: Set[str] = set()
        for tool in referenced:
            exit_code, _, _ = self._exec_in_container(
                self.target_container, f"which {tool}"
            )
            if exit_code != 0:
                missing_tools.add(tool)
                logger.info(f"  '{tool}' not found in target container.")
            else:
                logger.debug(f"  '{tool}' already present.")

        if not missing_tools:
            logger.info("ensure_target_tools: all referenced tools already present.")
            return

        packages_to_install: Set[str] = {TARGET_TOOL_TO_PACKAGE[t] for t in missing_tools}
        logger.info(f"ensure_target_tools: installing via apt: {sorted(packages_to_install)}")

        if not self._target_apt_updated:
            logger.info("ensure_target_tools: running apt-get update in target container...")
            upd_exit, _, upd_err = self._exec_in_container(
                self.target_container, "apt-get update -qq"
            )
            if upd_exit != 0:
                logger.warning(
                    f"ensure_target_tools: apt-get update failed (exit {upd_exit}): {upd_err}"
                )
            self._target_apt_updated = True

        pkg_list = " ".join(sorted(packages_to_install))
        install_cmd = (
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends {pkg_list}"
        )
        inst_exit, _, inst_err = self._exec_in_container(self.target_container, install_cmd)
        if inst_exit != 0:
            logger.warning(
                f"ensure_target_tools: apt install failed (exit {inst_exit}): {inst_err[:500]}"
            )
        else:
            logger.info(
                f"ensure_target_tools: successfully installed via apt: {sorted(packages_to_install)}"
            )

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
        for name in (self.target_name, self.attacker_name):
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
            net = self.client.networks.get(self.network_name)
            net.remove()
            logger.info(f"Removed stale network: {self.network_name}")
        except NotFound:
            pass
        except APIError as e:
            logger.warning(f"Error cleaning up network {self.network_name}: {e}")
