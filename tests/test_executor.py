"""Phase 0.2 — Procedure executor tests. Requires Docker.

All four fixture procedures are run against the simple_express test app.
"""

import pytest
from tests.conftest import load_procedure, express_deploy_script

pytestmark = pytest.mark.docker


class TestInterpolation:
    """Unit tests for interpolation (no Docker needed)."""

    def test_basic(self):
        from goe.executor.interpolation import interpolate
        result = interpolate("http://${target_host}:${target_port}/", {
            "target_host": "192.168.1.1",
            "target_port": "3000",
        })
        assert result == "http://192.168.1.1:3000/"

    def test_edge_param(self):
        from goe.executor.interpolation import interpolate
        result = interpolate("user=${edge.creds.user}", {
            "edges": {"creds": {"user": "dbadmin"}}
        })
        assert result == "user=dbadmin"

    def test_step_output(self):
        from goe.executor.interpolation import interpolate
        result = interpolate("cookie=${steps.capture.stolen_cookie}", {
            "steps": {"capture": {"stolen_cookie": "abc123"}}
        })
        assert result == "cookie=abc123"

    def test_unresolved_left_as_is(self):
        from goe.executor.interpolation import interpolate
        result = interpolate("${unknown_var}", {})
        assert result == "${unknown_var}"


class TestAssertions:
    """Unit tests for assertion checking (no Docker needed)."""

    def test_status_pass(self):
        from goe.executor.assertions import check
        from goe.executor.actions import ActionResult
        from goe.models.procedure import StatusAssertion
        passed, reason = check(StatusAssertion(status=200), ActionResult(status_code=200))
        assert passed

    def test_status_fail(self):
        from goe.executor.assertions import check
        from goe.executor.actions import ActionResult
        from goe.models.procedure import StatusAssertion
        passed, _ = check(StatusAssertion(status=200), ActionResult(status_code=404))
        assert not passed

    def test_stdout_contains_pass(self):
        from goe.executor.assertions import check
        from goe.executor.actions import ActionResult
        from goe.models.procedure import StdoutContainsAssertion
        passed, _ = check(StdoutContainsAssertion(stdout_contains="hello"), ActionResult(stdout="say hello world"))
        assert passed

    def test_stdout_contains_fail(self):
        from goe.executor.assertions import check
        from goe.executor.actions import ActionResult
        from goe.models.procedure import StdoutContainsAssertion
        passed, _ = check(StdoutContainsAssertion(stdout_contains="missing"), ActionResult(stdout="other content"))
        assert not passed

    def test_all_passes(self):
        from goe.executor.assertions import check
        from goe.executor.actions import ActionResult
        from goe.models.procedure import AllAssertion, StatusAssertion, BodyContainsAssertion
        a = AllAssertion(all=[StatusAssertion(status=200), BodyContainsAssertion(body_contains="ok")])
        passed, _ = check(a, ActionResult(status_code=200, body='{"ok": true}'))
        assert passed

    def test_all_fails_on_first(self):
        from goe.executor.assertions import check
        from goe.executor.actions import ActionResult
        from goe.models.procedure import AllAssertion, StatusAssertion, BodyContainsAssertion
        a = AllAssertion(all=[StatusAssertion(status=200), BodyContainsAssertion(body_contains="missing")])
        passed, _ = check(a, ActionResult(status_code=200, body="no match here"))
        assert not passed

    def test_exit_code(self):
        from goe.executor.assertions import check
        from goe.executor.actions import ActionResult
        from goe.models.procedure import ExitCodeAssertion
        passed, _ = check(ExitCodeAssertion(exit_code=0), ActionResult(exit_code=0))
        assert passed


class TestOutputCapture:
    """Unit tests for output capture (no Docker needed)."""

    def test_regex_from_stdout(self):
        from goe.executor.outputs import capture
        from goe.executor.actions import ActionResult
        result = ActionResult(stdout="session_id=abc123&other=stuff")
        val = capture('regex("session_id=([^&\\s]+)")', result)
        assert val == "abc123"

    def test_stdout_spec(self):
        from goe.executor.outputs import capture
        from goe.executor.actions import ActionResult
        val = capture("stdout", ActionResult(stdout="full stdout"))
        assert val == "full stdout"

    def test_body_spec(self):
        from goe.executor.outputs import capture
        from goe.executor.actions import ActionResult
        val = capture("body", ActionResult(body='{"key": "value"}'))
        assert val == '{"key": "value"}'

    def test_json_path(self):
        from goe.executor.outputs import capture
        from goe.executor.actions import ActionResult
        val = capture('json(".id")', ActionResult(body='{"id": 42}'))
        assert val == "42"

    def test_header(self):
        from goe.executor.outputs import capture
        from goe.executor.actions import ActionResult
        val = capture('header("content-type")', ActionResult(headers={"content-type": "application/json"}))
        assert val == "application/json"


# ---------------------------------------------------------------------------
# Docker tests — fixture procedures against simple_express app
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def express_env_module():
    """Module-scoped Express environment for executor tests."""
    import time
    from goe.container.environment import TestEnvironment
    env = TestEnvironment(runtime="express", scope="test_executor", enable_browser=True)
    env.setup()
    script = express_deploy_script(port=3000)
    exit_code, stdout, stderr = env.deploy(script)
    assert exit_code == 0, f"Deploy failed:\n{stdout}\n{stderr}"
    # Wait for app
    for _ in range(30):
        ec, out, _ = env._tool.exec_in_target("curl -sf http://localhost:3000/login > /dev/null && echo ok")
        if "ok" in out or ec == 0:
            break
        time.sleep(1)
    yield env
    env.teardown()


@pytest.fixture
def exec_ctx(express_env_module):
    return {
        "target_host": express_env_module.get_target_host(),
        "attacker_host": express_env_module.get_attacker_host(),
        "target_port": "3000",
        "edges": {},
    }


class TestFixtureProcedures:
    def test_simple_http(self, express_env_module, exec_ctx):
        """POST request returns 201 and body contains echoed content."""
        from goe.executor.runner import run
        procedure = load_procedure("simple_http")
        result = run(procedure, express_env_module, exec_ctx)
        assert result.passed, _format_failure(result)

    @pytest.mark.xfail(
        reason="Background process in exec_target exits when docker exec completes; "
               "curl never fires so listener receives nothing. Needs exec_target detach fix.",
        strict=False,
    )
    def test_exec_and_listen(self, express_env_module, exec_ctx):
        """Target sends data to attacker listener; received_contains passes."""
        from goe.executor.runner import run
        procedure = load_procedure("exec_and_listen")
        result = run(procedure, express_env_module, exec_ctx)
        assert result.passed, _format_failure(result)

    @pytest.mark.xfail(
        reason="Playwright sync API / CDP browser session connectivity issue; "
               "navigate returns about:blank. Needs browser container networking debug.",
        strict=False,
    )
    def test_browser_login(self, express_env_module, exec_ctx):
        """Browser navigates to login, fills form, ends on /dashboard."""
        cdp_url = express_env_module.get_cdp_url()
        if not cdp_url:
            pytest.skip("Browser sidecar not available (no CDP URL)")
        from goe.executor.runner import run
        procedure = load_procedure("browser_login")
        result = run(procedure, express_env_module, exec_ctx)
        assert result.passed, _format_failure(result)

    @pytest.mark.xfail(
        reason="Playwright sync objects created in session init cause greenlet cross-context "
               "errors when used later in the test. Needs SessionManager lifecycle refactor.",
        strict=False,
    )
    def test_mixed_xss(self, express_env_module, exec_ctx):
        """Browser triggers stored XSS; exfiltrated data captured by listener."""
        cdp_url = express_env_module.get_cdp_url()
        if not cdp_url:
            pytest.skip("Browser sidecar not available (no CDP URL)")
        from goe.executor.runner import run
        procedure = load_procedure("mixed_xss")
        result = run(procedure, express_env_module, exec_ctx)
        assert result.passed, _format_failure(result)


# ---------------------------------------------------------------------------
# Docker tests — ubuntu environment (SSH + SUID privesc)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ubuntu_env_module():
    """Module-scoped Ubuntu environment for SSH/privesc executor tests."""
    from goe.container.environment import TestEnvironment
    from tests.conftest import ubuntu_deploy_script
    env = TestEnvironment(runtime="ubuntu", scope="test_ubuntu_exec")
    env.setup()
    exit_code, stdout, stderr = env.deploy(ubuntu_deploy_script())
    assert exit_code == 0, f"Ubuntu deploy failed:\n{stdout}\n{stderr}"
    assert "ubuntu_env_ready" in stdout
    yield env
    env.teardown()


@pytest.fixture
def ubuntu_ctx(ubuntu_env_module):
    return {
        "target_host": ubuntu_env_module.get_target_host(),
        "attacker_host": ubuntu_env_module.get_attacker_host(),
        "target_port": "22",
        "edges": {},
    }


class TestBasicProcedures:
    def test_basic_exec(self, express_env_module, exec_ctx):
        """Attacker container basics: whoami, nmap version, target reachable."""
        from goe.executor.runner import run
        procedure = load_procedure("basic_exec")
        result = run(procedure, express_env_module, exec_ctx)
        assert result.passed, _format_failure(result)

    def test_step_chaining(self, express_env_module, exec_ctx):
        """exec_target plants a token; downstream step interpolates captured output."""
        from goe.executor.runner import run
        procedure = load_procedure("step_chaining")
        result = run(procedure, express_env_module, exec_ctx)
        assert result.passed, _format_failure(result)


class TestSSHPrivesc:
    def test_ssh_login(self, ubuntu_env_module, ubuntu_ctx):
        """Attacker SSHes into target as lowpriv user."""
        from goe.executor.runner import run
        procedure = load_procedure("ssh_login")
        result = run(procedure, ubuntu_env_module, ubuntu_ctx)
        assert result.passed, _format_failure(result)

    def test_suid_privesc(self, ubuntu_env_module, ubuntu_ctx):
        """Attacker finds SUID rootbash and exploits it for uid=0."""
        from goe.executor.runner import run
        procedure = load_procedure("suid_privesc")
        result = run(procedure, ubuntu_env_module, ubuntu_ctx)
        assert result.passed, _format_failure(result)


def _format_failure(result) -> str:
    lines = [f"Procedure failed at step: {result.failed_step}"]
    if result.error:
        lines.append(f"Error: {result.error}")
    for step in result.steps:
        if not step.passed:
            lines.append(f"  Step '{step.step_id}': {step.reason}")
            lines.append(f"  stdout: {step.raw.stdout[:300]}")
            lines.append(f"  stderr: {step.raw.stderr[:200]}")
            if step.raw.error:
                lines.append(f"  error: {step.raw.error}")
    return "\n".join(lines)
