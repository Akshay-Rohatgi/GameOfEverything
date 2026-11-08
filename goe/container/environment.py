"""TestEnvironment — thin adapter over v1 TestEnvironmentTool.

Provides a clean interface for the v2 executor without duplicating the
Docker lifecycle code that already works in v1.
"""

from __future__ import annotations

from goe.distros import get_profile
from goe.runtimes.registry import get_registry

# Base images for runtimes that have no web-runtime template and no distro
# profile (not built from a BuildArtifact). Web-runtime images live in
# goe/runtimes/templates/*.yaml as `target_image` (looked up via
# RuntimeRegistry.image_for()); the OS-level `system` runtime resolves its image
# from the selected distro profile instead (see _image_for).
_BASE_IMAGES: dict[str, str] = {
    "preset": "goe-preset-target:latest",
}


def _image_for(runtime: str, os: str = "ubuntu") -> str:
    """Resolve the Docker image for a runtime.

    Order: web-runtime template → OS-level distro profile (for the `system`
    runtime, keyed by `os`) → remaining base images (e.g. preset).

    The runtime string is normalized first, so a legacy "ubuntu" runtime (from an
    old checkpoint or a direct caller) still resolves to the OS-level `system` path.
    """
    from goe.models.entity import normalize_runtime

    runtime = normalize_runtime(runtime)
    registry = get_registry()
    if registry.has_runtime(runtime):
        return registry.image_for(runtime)
    if runtime == "system":
        return get_profile(os).image
    if runtime in _BASE_IMAGES:
        return _BASE_IMAGES[runtime]
    raise ValueError(
        f"Unknown runtime {runtime!r}: no runtime template, distro profile, or base image. "
        f"Templates: {registry.available_runtimes()}; base: {list(_BASE_IMAGES)}"
    )


class TestEnvironment:
    """Wraps v1 TestEnvironmentTool with a clean interface for the v2 executor."""

    def __init__(self, runtime: str = "system", scope: str = "", enable_browser: bool = True, expose_ports: dict[int, int] | None = None, os: str = "ubuntu"):
        image = _image_for(runtime, os)
        from goe.container.test_environment_tool import TestEnvironmentTool
        self._tool = TestEnvironmentTool(
            scope=scope,
            target_image=image,
            enable_browser=enable_browser,
            expose_ports=expose_ports,
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

    def reset_attacker(self) -> None:
        """Replace the attacker container with a fresh one — clears all background processes."""
        self._tool.reset_attacker()

    def reset_target(self) -> None:
        """Replace the target container with a fresh one — clears app state, processes, DB files."""
        self._tool.reset_target()

    def exec_in_bg(self, container: str, command: str) -> None:
        """Fire-and-forget exec — process survives after the exec shell exits."""
        if container == "attacker":
            self._tool.exec_in_attacker_bg(command)
        else:
            raise ValueError(f"Background exec only supported for attacker container, got: {container!r}")

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
