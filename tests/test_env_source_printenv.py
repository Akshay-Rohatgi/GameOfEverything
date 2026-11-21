"""Regression tests for `_sanitize_env_source_printenv`.

The P2 `env_vars_leak` live breaker: the attacker step
``source /etc/environment && printenv DB_PASSWORD`` prints nothing, because
``/etc/environment`` is plain ``KEY=VALUE`` (no ``export``), so ``source`` sets a *shell*
variable that ``printenv`` (which reads the *process* environment) never sees — and
``source`` is a bashism that fails under ``/bin/sh`` anyway. The sanitizer rewrites the
dot-source with ``set -a``/``set +a`` (allexport) and POSIX ``.`` so the vars are exported
and readable. Only commands that both source a file AND read it via ``printenv`` are
touched; ordinary ``source`` is left alone.
"""

from goe.build import _sanitize_env_source_printenv
from goe.models.procedure import (
    ExecAttackerAction,
    ExecTargetAction,
    HttpRequestAction,
    Procedure,
    Step,
)


def _exec_step(step_id: str, command: str, target: bool = False) -> Step:
    action = (
        ExecTargetAction(type="exec_target", command=command)
        if target
        else ExecAttackerAction(type="exec_attacker", command=command)
    )
    return Step(step_id=step_id, action=action)


def _proc(*steps: Step) -> Procedure:
    return Procedure(procedure=list(steps))


def _cmd(proc: Procedure, idx: int = 0) -> str:
    return proc.procedure[idx].action.command


# --------------------------------------------------------------------------- #
# the live P2 case
# --------------------------------------------------------------------------- #
def test_p2_source_environment_printenv_is_rewritten():
    proc = _proc(_exec_step("leak", "source /etc/environment && printenv DB_PASSWORD"))
    out = _sanitize_env_source_printenv(proc)
    cmd = _cmd(out)
    assert "set -a; . /etc/environment; set +a" in cmd
    assert "printenv DB_PASSWORD" in cmd
    assert "source /etc/environment" not in cmd  # bashism removed


def test_dot_source_variant_is_rewritten():
    proc = _proc(_exec_step("leak", ". /etc/environment && printenv SECRET"))
    out = _sanitize_env_source_printenv(proc)
    assert "set -a; . /etc/environment; set +a" in _cmd(out)


def test_semicolon_connective_is_rewritten():
    proc = _proc(_exec_step("leak", "source /etc/environment; printenv DB_PASSWORD"))
    out = _sanitize_env_source_printenv(proc)
    cmd = _cmd(out)
    assert "set -a; . /etc/environment; set +a; printenv DB_PASSWORD" in cmd


def test_rewrite_inside_bash_c_wrapper():
    proc = _proc(_exec_step(
        "leak", 'bash -c "source /etc/environment && printenv DB_PASSWORD"'))
    out = _sanitize_env_source_printenv(proc)
    assert "set -a; . /etc/environment; set +a" in _cmd(out)


def test_exec_target_action_is_also_rewritten():
    proc = _proc(_exec_step("leak", "source /etc/environment && printenv X", target=True))
    out = _sanitize_env_source_printenv(proc)
    assert "set -a; . /etc/environment; set +a" in _cmd(out)


# --------------------------------------------------------------------------- #
# scope guards — don't touch unrelated commands
# --------------------------------------------------------------------------- #
def test_source_without_printenv_is_left_alone():
    """Ordinary profile sourcing (no printenv read) must be untouched."""
    proc = _proc(_exec_step("s", "source /etc/profile && whoami"))
    out = _sanitize_env_source_printenv(proc)
    assert out is proc  # unchanged object returned
    assert _cmd(out) == "source /etc/profile && whoami"


def test_printenv_without_source_is_left_alone():
    proc = _proc(_exec_step("s", "printenv DB_PASSWORD"))
    out = _sanitize_env_source_printenv(proc)
    assert out is proc


def test_relative_dot_path_is_not_mistaken_for_source():
    """`./script` and `..` must not be rewritten as a dot-source."""
    proc = _proc(_exec_step("s", "./collect.sh && printenv DB_PASSWORD"))
    out = _sanitize_env_source_printenv(proc)
    assert out is proc
    assert _cmd(out) == "./collect.sh && printenv DB_PASSWORD"


def test_non_exec_steps_are_ignored():
    proc = _proc(Step(
        step_id="h",
        action=HttpRequestAction(type="http_request", method="GET", url="http://t/"),
    ))
    out = _sanitize_env_source_printenv(proc)
    assert out is proc


def test_none_procedure_is_safe():
    assert _sanitize_env_source_printenv(None) is None


def test_other_steps_preserved_when_one_rewritten():
    proc = _proc(
        _exec_step("a", "whoami"),
        _exec_step("leak", "source /etc/environment && printenv DB_PASSWORD"),
        _exec_step("c", "id"),
    )
    out = _sanitize_env_source_printenv(proc)
    assert _cmd(out, 0) == "whoami"
    assert "set -a; . /etc/environment; set +a" in _cmd(out, 1)
    assert _cmd(out, 2) == "id"
    # step ids preserved
    assert [s.step_id for s in out.procedure] == ["a", "leak", "c"]
