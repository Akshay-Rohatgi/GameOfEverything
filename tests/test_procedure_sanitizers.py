"""Regression tests for the deterministic procedure-side command sanitizers.

Two Week-7 live breakers, both fixed in code (not by coaxing the attacker LLM):

* P2 lateral SSH — ``WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!``. A host key pinned
  in ``known_hosts`` that later differs (a container regenerated between retries) is refused
  with ``HOST KEY VERIFICATION FAILED`` regardless of ``StrictHostKeyChecking=no``. Forcing
  ``UserKnownHostsFile=/dev/null`` gives every connection a throwaway store, so there is no
  stale pin to collide with.
* P4 ``psql --protocol=TCP`` — the attacker invents a MySQL-only flag on a Postgres command;
  ``psql`` errors out. ``psql`` already speaks TCP when given ``-h <host>``, so the flag is
  stripped loss-free.

The composite ``_sanitize_procedure_commands`` runs these plus the env-source/printenv fix in
one pass without mutating the stored procedure.
"""

from goe.build import (
    _rewrite_psql_protocol,
    _rewrite_ssh_known_hosts,
    _sanitize_procedure_commands,
)
from goe.models.procedure import ExecAttackerAction, Procedure, Step


def _exec_step(step_id: str, command: str) -> Step:
    return Step(step_id=step_id, action=ExecAttackerAction(type="exec_attacker", command=command))


def _proc(*steps: Step) -> Procedure:
    return Procedure(procedure=list(steps))


def _cmd(proc: Procedure, idx: int = 0) -> str:
    return proc.procedure[idx].action.command


# --------------------------------------------------------------------------- #
# SSH known-hosts hardening (P2)
# --------------------------------------------------------------------------- #
def test_ssh_gets_user_known_hosts_file():
    c = _rewrite_ssh_known_hosts("ssh -i /root/.ssh/chain_x -o StrictHostKeyChecking=no u@h 'id'")
    assert "-o UserKnownHostsFile=/dev/null" in c
    # StrictHostKeyChecking already present → not duplicated
    assert c.count("StrictHostKeyChecking=no") == 1


def test_ssh_bare_gets_both_options():
    c = _rewrite_ssh_known_hosts("ssh u@h 'id'")
    assert "-o UserKnownHostsFile=/dev/null" in c
    assert "-o StrictHostKeyChecking=no" in c


def test_ssh_is_idempotent():
    once = _rewrite_ssh_known_hosts("ssh u@h 'id'")
    twice = _rewrite_ssh_known_hosts(once)
    assert once == twice
    assert once.count("UserKnownHostsFile") == 1


def test_sshpass_ssh_is_rewritten_not_the_sshpass_token():
    c = _rewrite_ssh_known_hosts("sshpass -p 'pw' ssh -o StrictHostKeyChecking=no u@h 'cat /etc/passwd'")
    assert c.startswith("sshpass -p 'pw' ssh -o UserKnownHostsFile=/dev/null")


def test_scp_is_rewritten():
    c = _rewrite_ssh_known_hosts("scp -o StrictHostKeyChecking=no f u@h:/tmp/")
    assert "-o UserKnownHostsFile=/dev/null" in c


def test_ssh_keygen_and_dotssh_path_untouched():
    for cmd in ("ssh-keygen -y -f /root/.ssh/chain_x", "cat /root/.ssh/id_rsa", "echo no ssh here"):
        assert _rewrite_ssh_known_hosts(cmd) == cmd


# --------------------------------------------------------------------------- #
# psql --protocol strip (P4)
# --------------------------------------------------------------------------- #
def test_psql_protocol_equals_stripped():
    c = _rewrite_psql_protocol("psql -h 127.0.0.1 --protocol=TCP -U u -d db -c 'select 1'")
    assert "--protocol" not in c
    assert "-h 127.0.0.1" in c  # TCP preserved via -h


def test_psql_protocol_space_form_stripped():
    assert "--protocol" not in _rewrite_psql_protocol("psql -h 127.0.0.1 --protocol TCP -U u -c x")


def test_psql_without_protocol_untouched():
    cmd = "psql -h 127.0.0.1 -U u -c x"
    assert _rewrite_psql_protocol(cmd) == cmd


def test_mysql_protocol_is_preserved():
    # --protocol=TCP is valid and required for MySQL @'%' accounts; only psql is stripped.
    cmd = "mysql -h 127.0.0.1 --protocol=TCP -u root -c x"
    assert _rewrite_psql_protocol(cmd) == cmd


# --------------------------------------------------------------------------- #
# composite pass
# --------------------------------------------------------------------------- #
def test_composite_applies_all_rewriters():
    proc = _proc(
        _exec_step("hop", "ssh -o StrictHostKeyChecking=no u@h 'id'"),
        _exec_step("pg", "psql -h 127.0.0.1 --protocol=TCP -U u -c x"),
        _exec_step("leak", "source /etc/environment && printenv DB_PASSWORD"),
    )
    out = _sanitize_procedure_commands(proc)
    assert "-o UserKnownHostsFile=/dev/null" in _cmd(out, 0)
    assert "--protocol" not in _cmd(out, 1)
    assert "set -a; . /etc/environment; set +a" in _cmd(out, 2)


def test_composite_no_op_returns_same_object():
    proc = _proc(_exec_step("x", "echo hi"))
    assert _sanitize_procedure_commands(proc) is proc


def test_composite_handles_none():
    assert _sanitize_procedure_commands(None) is None
