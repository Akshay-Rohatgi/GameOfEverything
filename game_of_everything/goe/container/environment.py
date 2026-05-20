"""TestEnvironment — thin adapter over v1 TestEnvironmentTool.

Provides a clean interface for the v2 executor without duplicating the
Docker lifecycle code that already works in v1.
"""

from __future__ import annotations

# Runtime image identifiers (mirrors v1 RUNTIME_TARGET_IMAGES)
_RUNTIME_IMAGES: dict[str, str] = {
    "ubuntu": "ubuntu:22.04",
    "express": "goe-target-express:latest",
    "flask": "goe-target-flask:latest",
    "apache_php": "goe-target-php:latest",
    "preset": "goe-preset-target:latest",
}


class TestEnvironment:
    """Wraps v1 TestEnvironmentTool with a clean interface for the v2 executor."""

    def __init__(self, runtime: str = "ubuntu", scope: str = "", enable_browser: bool = True):
        image = _RUNTIME_IMAGES.get(runtime, runtime)
        from game_of_everything.tools.test_environment import TestEnvironmentTool
        self._tool = TestEnvironmentTool(
            scope=scope,
            target_image=image,
            enable_browser=enable_browser,
        )
        self._setup_done = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self) -> None:
        self._tool.setup()
        self._setup_done = True

    def teardown(self) -> None:
        self._tool.teardown()
        self._setup_done = False

    def __enter__(self) -> "TestEnvironment":
        self.setup()
        return self

    def __exit__(self, *_) -> None:
        self.teardown()

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------

    def deploy(self, deploy_script: str) -> tuple[int, str, str]:
        """Execute a bash deploy script in the target container."""
        return self._tool.exec_in_target(deploy_script)

    def copy_file(self, container: str, content: str, path: str) -> None:
        """Write a file into the target or attacker container."""
        if container == "target":
            self._tool.copy_to_target(content, path)
        elif container == "attacker":
            self._tool.copy_to_attacker(content, path)
        else:
            raise ValueError(f"Unknown container: {container!r}")

    def healthcheck(self, port: int, host: str | None = None) -> bool:
        """Return True if the given port is responding to HTTP on the target."""
        h = host or "localhost"
        exit_code, _, _ = self._tool.exec_in_target(
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
            return self._tool.exec_in_target(command)
        elif container == "attacker":
            return self._tool.exec_in_attacker(command)
        else:
            raise ValueError(f"Unknown container: {container!r}")

    # ------------------------------------------------------------------
    # Network addresses
    # ------------------------------------------------------------------

    def get_target_host(self) -> str:
        """Hostname of the target container as seen from the attacker container."""
        return self._tool._hostname or "target"

    def get_attacker_host(self) -> str:
        """Hostname of the attacker container as seen from the target container."""
        # The attacker container is started with hostname="attacker" on the bridge network
        return "attacker"

    def get_cdp_url(self) -> str:
        """WebSocket CDP URL for the browser sidecar (empty if browser not enabled)."""
        return self._tool.browser_cdp_url or ""

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def target_name(self) -> str:
        return self._tool.target_name

    @property
    def attacker_name(self) -> str:
        return self._tool.attacker_name
