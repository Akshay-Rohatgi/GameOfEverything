"""Phase 0.3 — Container orchestration tests. Requires Docker."""

import pytest

pytestmark = pytest.mark.docker


class TestTestEnvironment:
    def test_setup_teardown(self):
        """Environment spins up and tears down cleanly."""
        from goe.container.environment import TestEnvironment
        env = TestEnvironment(runtime="ubuntu", scope="test_basic")
        env.setup()
        try:
            assert env._setup_done
        finally:
            env.teardown()
        assert not env._setup_done

    def test_context_manager(self):
        """Context manager form works."""
        from goe.container.environment import TestEnvironment
        with TestEnvironment(runtime="ubuntu", scope="test_ctx") as env:
            exit_code, stdout, _ = env.exec_in("target", "echo hello_from_target")
            assert exit_code == 0
            assert "hello_from_target" in stdout

    def test_exec_in_target(self):
        from goe.container.environment import TestEnvironment
        with TestEnvironment(runtime="ubuntu", scope="test_exec") as env:
            exit_code, stdout, _ = env.exec_in("target", "whoami")
            assert exit_code == 0
            assert stdout.strip() != ""

    def test_exec_in_attacker(self):
        from goe.container.environment import TestEnvironment
        with TestEnvironment(runtime="ubuntu", scope="test_atk") as env:
            exit_code, stdout, _ = env.exec_in("attacker", "nmap --version")
            assert exit_code == 0
            assert "Nmap" in stdout

    def test_copy_file_to_target(self):
        from goe.container.environment import TestEnvironment
        with TestEnvironment(runtime="ubuntu", scope="test_copy") as env:
            env.copy_file("target", "hello from copy", "/tmp/goe_test_copy.txt")
            _, stdout, _ = env.exec_in("target", "cat /tmp/goe_test_copy.txt")
            assert "hello from copy" in stdout

    def test_deploy_script(self):
        """Deploy a simple bash script to the target."""
        from goe.container.environment import TestEnvironment
        with TestEnvironment(runtime="ubuntu", scope="test_deploy") as env:
            script = "touch /tmp/goe_deploy_marker && echo deployed"
            exit_code, stdout, _ = env.deploy(script)
            assert exit_code == 0
            assert "deployed" in stdout
            _, check, _ = env.exec_in("target", "test -f /tmp/goe_deploy_marker && echo exists")
            assert "exists" in check

    def test_get_hosts(self):
        from goe.container.environment import TestEnvironment
        with TestEnvironment(runtime="ubuntu", scope="test_hosts") as env:
            target_host = env.get_target_host()
            attacker_host = env.get_attacker_host()
            assert target_host != ""
            assert attacker_host != ""
            # Attacker can resolve target by hostname (bridge network DNS)
            exit_code, stdout, _ = env.exec_in("attacker", f"getent hosts {target_host}")
            assert exit_code == 0, f"Could not resolve target host '{target_host}': {stdout}"

    def test_express_runtime(self):
        """Express pre-built image starts and has Node.js available."""
        from goe.container.environment import TestEnvironment
        with TestEnvironment(runtime="express", scope="test_node") as env:
            exit_code, stdout, _ = env.exec_in("target", "node --version")
            assert exit_code == 0
            assert "v" in stdout  # e.g. "v20.x.x"
