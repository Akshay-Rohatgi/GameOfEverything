"""Regression tests for the deterministic producer-side SSH key write.

`_fixup_served_ssh_keys` corrects the fragile LLM behaviour of serving a private
key: mangled PEM bytes, wrong permissions, or — the case these tests pin — writing
the key from an environment variable that nothing injects, leaving a correctly
permissioned but EMPTY key file. The fixup appends a deterministic re-write of the
exact materialized key bytes to the served path, running last so it wins.
"""

from goe.build import _fixup_served_ssh_keys

B64 = "QkVHSU5LRVk="  # stand-in base64 key material


def _appended(out: str) -> str:
    return out.split("[harness]", 1)[1] if "[harness]" in out else ""


def test_env_var_write_is_backfilled():
    """The P2 live failure: key written from an uninjected env var -> empty file.

    The material is NOT inline in the script, so the fixup must still detect the
    served path (from the base64-decode redirect) and append the real bytes.
    """
    script = (
        "#!/bin/bash\n"
        "set -e\n"
        "install -d -m700 -o root -g root /root/.ssh\n"
        'printf \'%s\' "${INTERNALUSER_KEY_B64}" | base64 -d > /root/.ssh/id_rsa_internaluser\n'
        "chmod 600 /root/.ssh/id_rsa_internaluser\n"
        "chown root:root /root/.ssh/id_rsa_internaluser"
    )
    pv = {"ssh_edge": {"cred_type": "ssh_key", "secret": B64, "user": "internaluser"}}
    out = _fixup_served_ssh_keys(script, pv)
    app = _appended(out)
    assert "/root/.ssh/id_rsa_internaluser" in app
    assert f"printf '%s' '{B64}' | base64 -d > /root/.ssh/id_rsa_internaluser" in app
    assert "chmod 600 /root/.ssh/id_rsa_internaluser" in app


def test_inline_write_still_handled_and_preserves_perms():
    script = (
        "#!/bin/bash\n"
        f"echo '{B64}' | base64 -d > /opt/key\n"
        "chmod 644 /opt/key"
    )
    pv = {"e": {"cred_type": "ssh_key", "secret": B64}}
    out = _fixup_served_ssh_keys(script, pv)
    app = _appended(out)
    assert "/opt/key" in app and "chmod 644 /opt/key" in app


def test_public_key_path_is_never_overwritten():
    script = (
        'printf \'%s\' "${K}" | base64 -d > /root/.ssh/id_rsa_svc\n'
        "cp /root/.ssh/id_rsa_svc.pub /tmp/x"
    )
    pv = {"e": {"cred_type": "ssh_key", "secret": B64}}
    app = _appended(_fixup_served_ssh_keys(script, pv))
    assert "/root/.ssh/id_rsa_svc" in app
    assert "id_rsa_svc.pub" not in app


def test_multi_key_without_inline_material_is_noop():
    """Two keys, neither inline: we cannot know which path gets which key, so leave
    the script untouched rather than risk cross-assigning the wrong private key."""
    script = 'printf "%s" "${X}" | base64 -d > /root/.ssh/id_rsa_a'
    pv = {
        "e1": {"cred_type": "ssh_key", "secret": "AAAA"},
        "e2": {"cred_type": "ssh_key", "secret": "BBBB"},
    }
    assert _fixup_served_ssh_keys(script, pv) == script


def test_no_ssh_key_edge_is_noop():
    assert _fixup_served_ssh_keys("echo hi", {"e": {"cred_type": "password", "secret": "x"}}) == "echo hi"


def test_empty_inputs_are_noop():
    assert _fixup_served_ssh_keys("", {"e": {"cred_type": "ssh_key", "secret": B64}}) == ""
    assert _fixup_served_ssh_keys("echo hi", None) == "echo hi"


# --- _sanitize_ssh_dir_perms: force a group/world-writable ~/.ssh back to 0700 ----
# sshd StrictModes rejects a world-writable .ssh dir, so a Redis-BGSAVE key injection
# that `chmod 777 /root/.ssh` fails the subsequent SSH login. The sanitizer appends a
# `chmod 700` of every .ssh dir, running last to override the loosen.

from goe.build import _sanitize_ssh_dir_perms


def test_sanitize_appends_chmod_700_for_world_writable_ssh_dir():
    script = "mkdir -p /root/.ssh\nchmod 700 /root/.ssh\nchmod 777 /root/.ssh\n"
    out = _sanitize_ssh_dir_perms(script)
    assert out.rstrip().endswith('[ -d "/root/.ssh" ] && chmod 700 "/root/.ssh"')
    # original content is preserved; the corrective runs AFTER the chmod 777
    assert out.index("chmod 777 /root/.ssh") < out.index('chmod 700 "/root/.ssh"')


def test_sanitize_handles_multiple_distinct_ssh_dirs_once_each():
    script = "chmod 777 /root/.ssh\nchmod 0777 /home/svc/.ssh\nchmod 777 /root/.ssh\n"
    out = _sanitize_ssh_dir_perms(script)
    assert out.count('chmod 700 "/root/.ssh"') == 1
    assert out.count('chmod 700 "/home/svc/.ssh"') == 1


def test_sanitize_noop_without_ssh_dir():
    assert _sanitize_ssh_dir_perms("echo hi\nchmod 777 /tmp/data\n") == "echo hi\nchmod 777 /tmp/data\n"
    assert _sanitize_ssh_dir_perms("") == ""


# --- _l2_verify_port: verify against the port the app actually bound ---------------
from goe.build import _l2_verify_port


class _Art:
    def __init__(self, port):
        self.port = port


class _Reg:
    def port_for(self, runtime):
        return 5000  # template default (e.g. flask)


def test_l2_verify_port_prefers_artifact_port():
    # App bound :80 though the template default is 5000 -> verify must curl :80.
    assert _l2_verify_port("flask", _Art(80), _Reg()) == 80


def test_l2_verify_port_falls_back_to_template():
    # Entity reports no port of its own -> use the runtime template default.
    assert _l2_verify_port("flask", _Art(None), _Reg()) == 5000


def test_l2_verify_port_none_for_system():
    assert _l2_verify_port("system", _Art(6379), _Reg()) is None
