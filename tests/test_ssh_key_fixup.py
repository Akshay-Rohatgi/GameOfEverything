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
