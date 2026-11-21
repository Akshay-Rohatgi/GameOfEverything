"""Unit tests for the chain-procedure lint + privesc grounding (Week-7 options 1b/2/3).

Covers the exact P2 run-2 chain failure: the chain attacker wrote
`echo '<pubkey>' > /root/.ssh/authorized_keys` THROUGH the www-data cmd-injection curl
(impossible pre-privesc), asserting only on the curl's exit code, then `ssh root@webhost`.

All pure — no Docker/LLM.
"""

from types import SimpleNamespace

from goe.flow.chain_lint import (
    _assertion_is_transport_only,
    _extract_privesc_commands,
    _is_web_injection,
    _writes_root_path,
    lint_chain,
    privesc_grounding,
)


# --------------------------------------------------------------------------- #
# lightweight stand-ins (the lint only reads .action.command / .expect / .step_id)
# --------------------------------------------------------------------------- #
def _step(step_id, command, expect=None):
    return SimpleNamespace(
        step_id=step_id,
        action=SimpleNamespace(command=command),
        expect=expect,
    )


def _proc(*steps):
    return SimpleNamespace(procedure=list(steps))


class _Assert:
    """Mimics a pydantic Assertion with model_dump(exclude_none=True)."""

    def __init__(self, **kv):
        self._kv = kv

    def model_dump(self, exclude_none=True):
        return {k: v for k, v in self._kv.items() if v is not None} if exclude_none else dict(self._kv)


# The live P2 broken command (verbatim shape).
_P2_INJECT_ROOT = (
    "PUBKEY=$(cat /root/.ssh/id_rsa.pub) && CMD=\";echo '${PUBKEY}' > "
    "/root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys\" && "
    "ENCODED=$(python3 -c \"import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))\" "
    "\"${CMD}\") && curl -s \"http://${system.web_host.host}:${system.web_host.port}/ping.php?host=127.0.0.1${ENCODED}\""
)


# --------------------------------------------------------------------------- #
# low-level predicates
# --------------------------------------------------------------------------- #
def test_detects_web_injection_curl():
    assert _is_web_injection(_P2_INJECT_ROOT) is True
    assert _is_web_injection("curl -s http://example.com/") is False  # not a ${system.*.host}
    assert _is_web_injection("ssh root@${system.web_host.host} 'id'") is False


def test_detects_root_write():
    assert _writes_root_path(_P2_INJECT_ROOT) == "/root"
    assert _writes_root_path("echo x > /etc/passwd") == "/etc"
    assert _writes_root_path("echo x >> /var/www/.ssh/authorized_keys") is None
    assert _writes_root_path("cat /root/.ssh/id_rsa") is None  # a read, not a write


def test_transport_only_assertion():
    assert _assertion_is_transport_only(_step("s", "x", expect=None)) is True
    assert _assertion_is_transport_only(_step("s", "x", expect=_Assert(exit_code=0))) is True
    assert _assertion_is_transport_only(_step("s", "x", expect=_Assert(status=200))) is True
    # an effect assertion (body/stdout) is NOT transport-only
    assert _assertion_is_transport_only(_step("s", "x", expect=_Assert(stdout_regex="uid=0"))) is False
    assert _assertion_is_transport_only(_step("s", "x", expect=_Assert(body_contains="ok"))) is False


# --------------------------------------------------------------------------- #
# Option 2 — root write through the injection vector
# --------------------------------------------------------------------------- #
def test_lint_flags_root_write_via_injection():
    proc = _proc(_step("inject_pubkey_root", _P2_INJECT_ROOT, expect=_Assert(exit_code=0)))
    issues = lint_chain(proc)
    assert len(issues) == 1
    assert "inject_pubkey_root" in issues[0]
    assert "/root" in issues[0]
    assert "escalat" in issues[0].lower()


def test_lint_allows_www_writable_path():
    """Writing /var/www/.ssh via injection is fine (www-data owns it) — no issue."""
    cmd = _P2_INJECT_ROOT.replace("/root/.ssh/authorized_keys", "/var/www/.ssh/authorized_keys").replace(
        "/root/.ssh/authorized_keys", "/var/www/.ssh/authorized_keys")
    # Effect assertion present, so option 3 is satisfied; this isolates the option-2 check:
    # a /var/www write (www-data owns it) must NOT be flagged as a root-owned write.
    proc = _proc(_step("inject_www", cmd, expect=_Assert(stdout_regex="authorized")))
    assert lint_chain(proc) == []


def test_lint_does_not_flag_root_write_in_ssh_context():
    """A root-owned write performed inside an `ssh root@...` shell is legitimate (post-privesc)."""
    cmd = "ssh -i /root/.ssh/id_rsa root@${system.web_host.host} 'echo x > /root/marker'"
    proc = _proc(_step("post_root_write", cmd, expect=_Assert(exit_code=0)))
    assert lint_chain(proc) == []  # not a web-injection vector


# --------------------------------------------------------------------------- #
# Option 3 — side-effecting injection with transport-only assertion
# --------------------------------------------------------------------------- #
def test_lint_flags_transport_only_injection_write():
    cmd = (
        "CMD=\";useradd -m sshuser\" && ENCODED=$(python3 -c \"import urllib.parse,sys;"
        " print(urllib.parse.quote(sys.argv[1]))\" \"${CMD}\") && "
        "curl -s \"http://${system.web_host.host}/ping.php?host=127.0.0.1${ENCODED}\""
    )
    proc = _proc(_step("make_user", cmd, expect=_Assert(exit_code=0)))
    issues = lint_chain(proc)
    assert len(issues) == 1
    assert "asserts only on the HTTP/exit status" in issues[0]


def test_lint_clears_injection_with_effect_assertion():
    cmd = (
        "CMD=\";useradd -m sshuser\" && curl -s \"http://${system.web_host.host}/ping.php?host=x${CMD}\""
    )
    proc = _proc(_step("make_user", cmd, expect=_Assert(stdout_regex="sshuser")))
    assert lint_chain(proc) == []  # effect assertion present


def test_lint_ignores_non_injection_steps():
    proc = _proc(
        _step("verify", "ssh root@${system.web_host.host} 'id'", expect=_Assert(stdout_regex="uid=0")),
        _step("ping", "curl -s http://example.com/health", expect=None),
    )
    assert lint_chain(proc) == []


# --------------------------------------------------------------------------- #
# Option 1b — privesc grounding from the graph
# --------------------------------------------------------------------------- #
def _graph_p2():
    """A stand-in EntityGraph mirroring the P2 topology (only the attrs the grounding reads)."""
    def edge(eid, frm, etype, user=None):
        params = {}
        if user is not None:
            params["user"] = SimpleNamespace(concrete=user, structural=user)
        return SimpleNamespace(id=eid, from_entity=frm, to_entity=None,
                               type=SimpleNamespace(value=etype), params=params)

    entities = {
        "cmd_injection_web": SimpleNamespace(id="cmd_injection_web", system_id="web_host",
                                             atoms=["cmd_injection"]),
        "sudo_privesc": SimpleNamespace(id="sudo_privesc", system_id="web_host",
                                        atoms=["sudoers_no_passwd"]),
    }
    graph = SimpleNamespace(
        edges=[
            edge("cmdinject_to_webshell", "cmd_injection_web", "shell_as", user="www-data"),
            edge("sudo_to_root_shell", "sudo_privesc", "shell_as", user="root"),
        ],
        entity_by_id=lambda eid: entities.get(eid),
    )
    return graph


def test_privesc_grounding_names_entity_user_and_forbids_improvised_paths():
    graph = _graph_p2()
    # per-entity procedure for sudo_privesc contains the real sudo escalation
    built = {
        "sudo_privesc": SimpleNamespace(
            procedure=_proc(_step("escalate", "sudo -n /bin/bash -p -c id"))
        )
    }
    text = privesc_grounding(graph, built)
    assert "sudo_privesc" in text
    assert "www-data" in text              # names the unprivileged starting user
    assert "sudoers_no_passwd" in text     # names the atom
    assert "sudo -n /bin/bash" in text     # extracted the real escalation command
    assert "/root" in text and "do NOT" in text
    assert "ssh root@" in text             # explicitly forbids the improvised path


def test_privesc_grounding_empty_when_no_root_escalation():
    def edge(eid, frm, etype, user):
        return SimpleNamespace(id=eid, from_entity=frm, to_entity=None,
                               type=SimpleNamespace(value=etype),
                               params={"user": SimpleNamespace(concrete=user, structural=user)})
    graph = SimpleNamespace(
        edges=[edge("e", "cmd_injection_web", "shell_as", "www-data")],
        entity_by_id=lambda eid: None,
    )
    assert privesc_grounding(graph, {}) == ""


def test_extract_privesc_commands_filters_to_sudo_suid():
    built = SimpleNamespace(procedure=_proc(
        _step("a", "curl http://x/"),
        _step("b", "sudo -n cat /root/.ssh/id_rsa"),
        _step("c", "chmod u+s /usr/bin/custom"),
    ))
    cmds = _extract_privesc_commands(built)
    assert any("sudo -n cat" in c for c in cmds)
    assert any("u+s" in c for c in cmds)
    assert not any("curl" in c for c in cmds)
