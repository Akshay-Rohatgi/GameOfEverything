"""Tests for the attacker-procedure assertion lint.

Every rule is checked in both directions — it fires on the shape it targets, and
stays quiet on a correct procedure. Severities were calibrated against the real
fixtures in tests/fixtures/procedures/, which is why several rules are warnings:
`stdout_contains: root` after `whoami` is a strong assertion, and a step that
posts a payload and asserts 201 is fine when a later step verifies it fired.
"""

from pathlib import Path

from goe.eval.procedure_lint import Finding, has_errors, lint_file, lint_procedure

FIXTURES = Path(__file__).parent / "fixtures" / "procedures"


def rules(findings: list[Finding]) -> set[str]:
    return {f.rule for f in findings}


def errors(findings: list[Finding]) -> set[str]:
    return {f.rule for f in findings if f.severity == "error"}


def _step(step_id="s", action=None, expect=None, **kw):
    step = {"step_id": step_id, "action": action or {"type": "http_request",
                                                     "method": "GET",
                                                     "url": "http://h/"}}
    if expect is not None:
        step["expect"] = expect
    step.update(kw)
    return step


# --- errors ---------------------------------------------------------------

def test_missing_expect_on_final_step_is_an_error():
    doc = {"procedure": [_step()]}
    assert "no_expect" in errors(lint_procedure(doc))


def test_missing_expect_mid_chain_is_a_warning():
    doc = {"procedure": [
        _step("a"),
        _step("b", expect={"body_contains": "${edge.flag.value}"}),
    ]}
    findings = lint_procedure(doc)
    assert "no_expect" in rules(findings)
    assert not has_errors(findings)


def test_status_only_across_whole_procedure_is_an_error():
    doc = {"procedure": [
        _step("a", expect={"status": 200}),
        _step("b", expect={"status": 201}),
    ]}
    assert "status_only_2xx" in errors(lint_procedure(doc))


def test_status_only_step_is_fine_when_another_step_proves_content():
    # Posting a payload and asserting 201, then verifying it fired, is correct.
    doc = {"procedure": [
        _step("inject", expect={"status": 201}),
        _step("verify", expect={"body_contains": "${edge.xss.marker}"}),
    ]}
    assert "status_only_2xx" not in rules(lint_procedure(doc))


def test_exec_target_in_a_procedure_is_an_error():
    doc = {"procedure": [_step("s", action={"type": "exec_target",
                                            "command": "id"},
                               expect={"stdout_contains": "uid=0"})]}
    assert "exec_target_in_procedure" in errors(lint_procedure(doc))


def test_document_without_a_procedure_list_is_an_error():
    assert "unparsed" in errors(lint_procedure({}))
    assert "unparsed" in errors(lint_procedure({"procedure": []}))


# --- warnings -------------------------------------------------------------

def test_generic_body_text_warns():
    doc = {"procedure": [_step(expect={"body_contains": "success"})]}
    assert "generic_text" in rules(lint_procedure(doc))


def test_generic_word_from_stdout_does_not_warn():
    # `whoami` returning root is exactly the right assertion.
    doc = {"procedure": [_step("s", action={"type": "exec_attacker",
                                            "command": "whoami"},
                               expect={"stdout_contains": "root"})]}
    assert "generic_text" not in rules(lint_procedure(doc))


def test_reflected_value_warns_but_does_not_fail():
    doc = {"procedure": [_step(
        "s",
        action={"type": "http_request", "method": "GET",
                "url": "http://h/search?q=phantom_ride"},
        expect={"body_contains": "phantom_ride"})]}
    findings = lint_procedure(doc)
    assert "tautological" in rules(findings)
    assert "tautological" not in errors(findings)


def test_interpolated_value_is_not_treated_as_reflection():
    doc = {"procedure": [_step(
        "s",
        action={"type": "http_request", "method": "GET",
                "url": "http://h/?q=${edge.token.value}"},
        expect={"body_contains": "${edge.token.value}"})]}
    assert "tautological" not in rules(lint_procedure(doc))


def test_missing_dynamic_reference_warns_once():
    doc = {"procedure": [_step(expect={"body_contains": "St Dismas Intranet"})]}
    findings = lint_procedure(doc)
    assert [f.rule for f in findings].count("no_dynamic_evidence") == 1


def test_unused_output_warns():
    doc = {"procedure": [
        _step("grab", expect={"body_contains": "${edge.a.v}"},
              outputs={"token": "body"}),
        _step("next", expect={"body_contains": "${edge.b.v}"}),
    ]}
    assert "unused_output" in rules(lint_procedure(doc))


def test_used_output_does_not_warn():
    doc = {"procedure": [
        _step("grab", expect={"body_contains": "${edge.a.v}"},
              outputs={"token": "body"}),
        _step("next",
              action={"type": "http_request", "method": "GET",
                      "url": "http://h/?t=${steps.grab.token}"},
              expect={"body_contains": "${edge.b.v}"}),
    ]}
    assert "unused_output" not in rules(lint_procedure(doc))


# --- nesting --------------------------------------------------------------

def test_all_assertions_are_walked():
    doc = {"procedure": [_step(expect={"all": [{"status": 200},
                                               {"body_contains": "success"}]})]}
    findings = lint_procedure(doc)
    assert "generic_text" in rules(findings)
    assert "status_only_2xx" not in rules(findings)


# --- the project's own fixtures -------------------------------------------

def test_existing_fixtures_do_not_error_except_exec_target():
    """A noisy lint is one people learn to skip, so this pins the false-positive
    rate against the procedures already in the repo."""
    offenders = {}
    for path in sorted(FIXTURES.glob("*.yaml")):
        if path.name in ("weak.yaml",):
            continue
        errs = errors(lint_file(path))
        if errs:
            offenders[path.name] = errs
    # Only the two fixtures that deliberately exercise the god-view action.
    # Their own headers say so ("Tests: exec_target (god-view)") — they are
    # executor fixtures, not generated attacker procedures.
    assert offenders == {
        "exec_and_listen.yaml": {"exec_target_in_procedure"},
        "step_chaining.yaml": {"exec_target_in_procedure"},
    }, offenders


def test_weak_fixture_fails_and_strong_fixture_is_clean():
    assert has_errors(lint_file(FIXTURES / "weak.yaml"))
    assert not has_errors(lint_file(FIXTURES / "strong.yaml"))
