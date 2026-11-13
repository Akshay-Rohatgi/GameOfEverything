"""Unit tests for the diagnosis→regen link helpers (build.py directions 1-3).

These guard the three fixes that make the retry loop verify its own cause→action link
deterministically, instead of trusting that a re-run addressed the diagnosis:

  1. Enforce the link at step granularity — a procedure fix that left the *cited* step
     unchanged is caught (_cited_steps_left_unchanged) so the loop can re-prompt.
  2. Generalise the no-op guard beyond procedure_bug — any regen that left the artifact
     its category owns byte-identical is a no-op (_regenerated_artifact_unchanged).
  3. Tighten category→artifact routing — a verify-step defect mislabelled
     implementation_bug is re-routed to fix_procedure (_should_reroute_to_procedure).

All helpers are pure (no Docker/Bedrock), so these run offline.
"""

from types import SimpleNamespace

from goe.build import (
    _artifact_unchanged,
    _changed_step_ids,
    _cited_step_ids,
    _cited_steps_left_unchanged,
    _regenerated_artifact_unchanged,
    _should_reroute_to_procedure,
)
from goe.models.procedure import (
    ExecAttackerAction,
    HttpRequestAction,
    Procedure,
    StatusAssertion,
    Step,
)
from goe.retry.diagnostician import Diagnosis, DiagnosisCategory


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def _http_step(step_id: str, url: str, status: int | None = 200) -> Step:
    return Step(
        step_id=step_id,
        action=HttpRequestAction(type="http_request", method="GET", url=url),
        expect=StatusAssertion(type="status", status=status) if status else None,
    )


def _exec_step(step_id: str, command: str) -> Step:
    return Step(
        step_id=step_id,
        action=ExecAttackerAction(type="exec_attacker", command=command),
    )


def _proc(*steps: Step) -> Procedure:
    return Procedure(procedure=list(steps))


def _crew(procedure=None, artifact=None):
    """A stand-in for CrewResult — the helpers only touch .procedure / .artifact."""
    return SimpleNamespace(procedure=procedure, artifact=artifact)


class _Artifact:
    """Minimal artifact exposing model_dump, like a pydantic BuildArtifact."""

    def __init__(self, payload: dict):
        self._payload = payload

    def model_dump(self):
        return self._payload


def _result(failed_step=None):
    return SimpleNamespace(failed_step=failed_step)


def _diag(category, description="", evidence=""):
    return Diagnosis(category=category, description=description, evidence=evidence)


# --------------------------------------------------------------------------- #
# _cited_step_ids
# --------------------------------------------------------------------------- #
def test_cited_step_ids_whole_word_case_insensitive():
    proc = _proc(_http_step("login", "/a"), _http_step("exploit", "/b"))
    assert _cited_step_ids("the EXPLOIT step asserts the wrong status", proc) == ["exploit"]


def test_cited_step_ids_ignores_non_steps_and_substrings():
    proc = _proc(_http_step("login", "/a"))
    # "login" as a substring of "logindex" must not count (whole-word match).
    assert _cited_step_ids("open the logindex page", proc) == []


def test_cited_step_ids_empty_when_no_text_or_no_steps():
    proc = _proc(_http_step("login", "/a"))
    assert _cited_step_ids("", proc) == []
    assert _cited_step_ids("login", _proc()) == []


# --------------------------------------------------------------------------- #
# _changed_step_ids
# --------------------------------------------------------------------------- #
def test_changed_step_ids_detects_modified_added_removed():
    old = _proc(_http_step("a", "/x"), _http_step("b", "/y"))
    # 'a' modified (url), 'b' removed, 'c' added.
    new = _proc(_http_step("a", "/changed"), _http_step("c", "/z"))
    assert _changed_step_ids(old, new) == {"a", "b", "c"}


def test_changed_step_ids_empty_when_identical():
    old = _proc(_http_step("a", "/x"))
    new = _proc(_http_step("a", "/x"))
    assert _changed_step_ids(old, new) == set()


# --------------------------------------------------------------------------- #
# Direction 1 — _cited_steps_left_unchanged
# --------------------------------------------------------------------------- #
def test_cited_step_left_unchanged_is_flagged():
    """Diagnosis blames 'exploit'; the fix rewrote 'login' but not 'exploit'."""
    old = _proc(_http_step("login", "/a"), _http_step("exploit", "/b", status=200))
    new = _proc(_http_step("login", "/CHANGED"), _http_step("exploit", "/b", status=200))
    diag = _diag(DiagnosisCategory.procedure_bug, "the exploit step asserts the wrong status")
    assert _cited_steps_left_unchanged(diag, _result(), old, new) == ["exploit"]


def test_failed_step_counts_as_cited():
    """Even without naming it in text, the run's failed_step is treated as cited."""
    old = _proc(_http_step("login", "/a"), _http_step("exploit", "/b"))
    new = _proc(_http_step("login", "/CHANGED"), _http_step("exploit", "/b"))
    diag = _diag(DiagnosisCategory.procedure_bug, "something is wrong")
    assert _cited_steps_left_unchanged(diag, _result(failed_step="exploit"), old, new) == ["exploit"]


def test_cited_step_that_changed_is_not_flagged():
    old = _proc(_http_step("exploit", "/b", status=200))
    new = _proc(_http_step("exploit", "/b", status=500))  # assertion changed
    diag = _diag(DiagnosisCategory.procedure_bug, "fix the exploit step")
    assert _cited_steps_left_unchanged(diag, _result(failed_step="exploit"), old, new) == []


def test_cited_step_removed_is_not_flagged():
    """A cited step that no longer exists in the new procedure isn't 'unchanged'."""
    old = _proc(_http_step("exploit", "/b"))
    new = _proc(_http_step("other", "/c"))
    diag = _diag(DiagnosisCategory.procedure_bug, "fix the exploit step")
    assert _cited_steps_left_unchanged(diag, _result(), old, new) == []


# --------------------------------------------------------------------------- #
# Direction 2 — _artifact_unchanged / _regenerated_artifact_unchanged
# --------------------------------------------------------------------------- #
def test_artifact_unchanged_true_and_false():
    a = _Artifact({"files": {"app.py": "x"}})
    same = _Artifact({"files": {"app.py": "x"}})
    diff = _Artifact({"files": {"app.py": "y"}})
    assert _artifact_unchanged(a, same) is True
    assert _artifact_unchanged(a, diff) is False
    assert _artifact_unchanged(None, same) is False


def test_regen_noop_procedure_bug_compares_procedure():
    old = _crew(procedure=_proc(_http_step("a", "/x")), artifact=_Artifact({"app": 1}))
    # Same procedure, DIFFERENT artifact — for procedure_bug this is still a no-op.
    new = _crew(procedure=_proc(_http_step("a", "/x")), artifact=_Artifact({"app": 2}))
    assert _regenerated_artifact_unchanged(DiagnosisCategory.procedure_bug, old, new) is True


def test_regen_noop_implementation_bug_compares_artifact():
    old = _crew(procedure=_proc(_http_step("a", "/x")), artifact=_Artifact({"app": 1}))
    # Same artifact, DIFFERENT procedure — for implementation_bug this is still a no-op
    # (the app is what must change). The old procedure-only guard would have missed this.
    new = _crew(procedure=_proc(_http_step("a", "/CHANGED")), artifact=_Artifact({"app": 1}))
    assert _regenerated_artifact_unchanged(DiagnosisCategory.implementation_bug, old, new) is True


def test_regen_noop_false_when_owned_artifact_changed():
    old = _crew(procedure=_proc(_http_step("a", "/x")), artifact=_Artifact({"app": 1}))
    new = _crew(procedure=_proc(_http_step("a", "/x")), artifact=_Artifact({"app": 2}))
    assert _regenerated_artifact_unchanged(DiagnosisCategory.implementation_bug, old, new) is False


# --------------------------------------------------------------------------- #
# Direction 3 — _should_reroute_to_procedure
# --------------------------------------------------------------------------- #
def test_reroute_when_impl_bug_describes_assertion_fix_and_names_step():
    proc = _proc(_http_step("exploit", "/b"))
    diag = _diag(
        DiagnosisCategory.implementation_bug,
        "The exploit step uses the wrong assertion — it expects status 200 but should "
        "check the body.",
    )
    assert _should_reroute_to_procedure(diag, _result(failed_step="exploit"), proc) is True


def test_no_reroute_when_not_implementation_bug():
    proc = _proc(_http_step("exploit", "/b"))
    diag = _diag(DiagnosisCategory.procedure_bug, "wrong assertion on the exploit step")
    assert _should_reroute_to_procedure(diag, _result(failed_step="exploit"), proc) is False


def test_no_reroute_without_verify_signal():
    """A genuine app defect (500 from a missing column) must stay implementation_bug."""
    proc = _proc(_http_step("exploit", "/b"))
    diag = _diag(
        DiagnosisCategory.implementation_bug,
        "The app returns 500 because the users table has no 'passwd' column.",
    )
    assert _should_reroute_to_procedure(diag, _result(failed_step="exploit"), proc) is False


def test_no_reroute_when_no_step_named_or_failed():
    proc = _proc(_http_step("exploit", "/b"))
    diag = _diag(
        DiagnosisCategory.implementation_bug,
        "some assertion is wrong",  # verify signal but names no concrete step
    )
    assert _should_reroute_to_procedure(diag, _result(failed_step=None), proc) is False
