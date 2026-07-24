"""Unit tests for the L1 diagnostician's JSON handling. No docker, no LLM."""

import json
from types import SimpleNamespace

import goe.planner._utils as utils
from goe.retry.diagnostician import Diagnosis, DiagnosisCategory, diagnose


class _FakeEnv:
    def exec_in(self, container, command):  # noqa: D401 - matches TestEnvironment
        return (0, "(log line)", "")


def _args():
    # Mock entity with all fields needed by ProbeContext
    from goe.models.entity import Runtime
    entity = SimpleNamespace(
        id="e1",
        runtime=SimpleNamespace(value="express"),
        atoms=[],
    )
    # Mock artifact with all fields needed by ProbeContext
    artifact = SimpleNamespace(
        source_files={"app.py": "print('x')"},
        db_setup=None,
        app_dir="/opt/webapp",
        system_deps=[],
    )
    result = SimpleNamespace(error="assertion failure", failed_step="exploit", steps=[])
    return entity, artifact, result, _FakeEnv()


def test_diagnose_parses_valid_json(monkeypatch):
    monkeypatch.setattr(utils, "call_json", lambda *a, **k: {
        "category": "procedure_bug", "description": "wrong payload", "evidence": "x",
    })
    di = diagnose(*_args())
    assert di.category == DiagnosisCategory.procedure_bug
    assert di.description == "wrong payload"


def test_diagnose_falls_back_on_unparseable_response(monkeypatch):
    def _boom(*a, **k):
        raise json.JSONDecodeError("Expecting value", "", 0)

    monkeypatch.setattr(utils, "call_json", _boom)
    di = diagnose(*_args())
    # Malformed LLM output must not crash the run — conservative fallback instead.
    assert di.category == DiagnosisCategory.design_flaw
    assert "could not be parsed" in di.description


def test_diagnose_falls_back_on_wrong_shape(monkeypatch):
    # Valid JSON but not a Diagnosis (bad category) → still degrade gracefully.
    monkeypatch.setattr(utils, "call_json", lambda *a, **k: {"category": "nonsense"})
    di = diagnose(*_args())
    assert di.category == DiagnosisCategory.design_flaw
