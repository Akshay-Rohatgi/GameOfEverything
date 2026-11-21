"""Regression tests for `_sanitize_world_readable_traversal`.

A "leak" entity makes a file world-readable (``chmod 644``) so an unprivileged
attacker can read it, but the LLM reliably forgets that a world-readable file is
unreachable unless every *parent directory* is traversable (``o+x``). The P2 live
failure: ``useradd -m deploy`` leaves ``/home/deploy`` at ``0700``, so ``nobody``
cannot ``cd`` into it to read the ``0644`` ``.bash_history``. The fixup appends a
deterministic ``chmod o+x`` of each ancestor directory, with guards so it never
exposes private material or re-loosens a ``.ssh`` dir.
"""

from goe.build import _sanitize_world_readable_traversal


def _appended(out: str) -> str:
    return out.split("[harness]", 1)[1] if "[harness]" in out else ""


def test_p2_bash_history_home_made_traversable():
    """The live P2 case: 0644 file under a 0700 home dir -> add o+x to the home dir."""
    script = (
        "#!/bin/bash\n"
        "set -e\n"
        "useradd -m -s /bin/bash deploy\n"
        "touch /home/deploy/.bash_history\n"
        "chmod 644 /home/deploy/.bash_history\n"
    )
    out = _sanitize_world_readable_traversal(script)
    body = _appended(out)
    assert 'chmod o+x "/home/deploy"' in body
    assert 'chmod o+x "/home"' in body
    # The leaf file itself is never chmod-ed as a directory.
    assert '"/home/deploy/.bash_history"' not in body


def test_private_key_at_0600_is_never_exposed():
    """A 0600 key is not world-readable, so its dirs must NOT be made traversable."""
    script = (
        "install -d -m700 /root/.ssh\n"
        'printf %s "$K" | base64 -d > /root/.ssh/id_rsa\n'
        "chmod 600 /root/.ssh/id_rsa\n"
    )
    out = _sanitize_world_readable_traversal(script)
    assert out == script  # no-op: nothing world-readable


def test_ssh_ancestor_is_skipped():
    """A world-readable file under .ssh must not re-loosen the .ssh dir itself
    (that would undo the 0700 _sanitize_ssh_dir_perms forces for sshd StrictModes)."""
    script = "chmod 644 /root/.ssh/known_hosts\n"
    out = _sanitize_world_readable_traversal(script)
    body = _appended(out)
    assert '/root/.ssh' not in body          # the .ssh dir is skipped
    assert 'chmod o+x "/root"' in body        # its parent is still fine


def test_symbolic_other_read_triggers():
    """Symbolic ``o+r`` / ``a+r`` count as world-readable just like 0644."""
    script = "chmod o+r /opt/data/secret.txt\n"
    out = _sanitize_world_readable_traversal(script)
    body = _appended(out)
    assert 'chmod o+x "/opt/data"' in body
    assert 'chmod o+x "/opt"' in body


def test_group_only_read_does_not_trigger():
    """0640 / u+r / g+r grant no *other* read, so no traversal is opened."""
    for script in ("chmod 640 /home/u/.secret\n", "chmod g+r /home/u/.secret\n"):
        assert _sanitize_world_readable_traversal(script) == script


def test_root_level_file_never_chmods_slash():
    """A file directly under / has no intermediate dir -> no-op, and / is untouched."""
    script = "chmod 644 /motd\n"
    out = _sanitize_world_readable_traversal(script)
    assert out == script
    assert 'chmod o+x "/"' not in out


def test_no_world_readable_file_is_noop():
    script = "#!/bin/bash\napt-get install -y nginx\nsystemctl start nginx\n"
    assert _sanitize_world_readable_traversal(script) == script
