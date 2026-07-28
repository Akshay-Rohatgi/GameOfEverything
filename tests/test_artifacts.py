"""Unit tests for workflow artifact capture and persistence.

All tests are unmarked (no docker, no llm) — they must pass under:
    pytest -m "not docker and not llm"
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_session():
    """Ensure no metrics session is active between tests."""
    from goe.metrics import end_session, get_session
    if get_session():
        end_session()
    yield
    if get_session():
        end_session()


@pytest.fixture
def sample_artifact():
    """Minimal BuildArtifact for testing."""
    from goe.models.artifacts import BuildArtifact, DBSetup
    return BuildArtifact(
        source_files={
            "app.py": "print('hello')",
            "utils/helpers.py": "# helpers",
        },
        primary_source="app.py",
        port=5000,
        db_setup=DBSetup(
            db_type="mysql",
            schema_sql="CREATE TABLE users (id INT);",
            seed_sql="INSERT INTO users VALUES (1);",
        ),
    )


@pytest.fixture
def sample_artifact_no_db():
    """BuildArtifact without DB setup."""
    from goe.models.artifacts import BuildArtifact
    return BuildArtifact(
        source_files={"app.py": "print('no db')"},
        primary_source="app.py",
        port=5000,
    )


@pytest.fixture
def sample_procedure():
    """Minimal Procedure for testing."""
    from goe.models.procedure import Procedure
    return Procedure.model_validate({
        "sessions": [],
        "procedure": [],
    })


@pytest.fixture
def sample_engineer_plan():
    """Minimal EngineerPlan for testing."""
    from goe.construction_crew.engineer import EngineerPlan
    return EngineerPlan(
        summary="Test plan",
        runtime="flask",
        vulnerability_placement="in /login",
        attack_entry_point="/login",
        success_indicator="200 OK",
    )


@pytest.fixture
def sample_crew(sample_artifact, sample_procedure, sample_engineer_plan):
    """Minimal CrewResult for testing."""
    from goe.construction_crew.orchestrator import CrewResult
    return CrewResult(
        artifact=sample_artifact,
        procedure=sample_procedure,
        outgoing_values={},
        plan=sample_engineer_plan,
    )


@pytest.fixture
def sample_crew_no_db(sample_artifact_no_db, sample_procedure, sample_engineer_plan):
    """CrewResult without DB setup."""
    from goe.construction_crew.orchestrator import CrewResult
    return CrewResult(
        artifact=sample_artifact_no_db,
        procedure=sample_procedure,
        outgoing_values={},
        plan=sample_engineer_plan,
    )


# ---------------------------------------------------------------------------
# MetricsSession — artifact capture flags
# ---------------------------------------------------------------------------

def test_start_session_default_no_capture():
    """start_session() with no args leaves transcripts=None (zero overhead)."""
    from goe.metrics import start_session, end_session
    session = start_session()
    assert session.transcripts is None
    assert session.artifact_run_dir is None
    end_session()


def test_start_session_with_capture(tmp_path):
    """start_session(capture_artifacts=True, run_dir=...) allocates transcript buffer."""
    from goe.metrics import start_session, end_session
    session = start_session(capture_artifacts=True, run_dir=tmp_path)
    assert session.transcripts is not None
    assert session.transcripts == []
    assert session.artifact_run_dir == tmp_path
    end_session()


# ---------------------------------------------------------------------------
# Transcript de-duplication logic
# ---------------------------------------------------------------------------

def test_record_transcript_single_turn():
    """A single-turn call stores its message as new_messages."""
    from goe.metrics import start_session, end_session
    session = start_session(capture_artifacts=True)

    messages = [{"role": "user", "content": "hello"}]
    session.record_transcript(
        call_id="a1",
        timestamp=time.time(),
        caller="engineer",
        model_id="test-model",
        system="sys",
        messages=messages,
        response="hi",
        input_tokens=10,
        output_tokens=5,
    )

    assert len(session.transcripts) == 1
    rec = session.transcripts[0]
    assert rec.new_messages == [{"role": "user", "content": "hello"}]
    assert rec.response == "hi"
    assert rec.caller == "engineer"
    end_session()


def test_record_transcript_cumulative_dedup():
    """developer→self_review pattern: each call stores only the delta messages."""
    from goe.metrics import start_session, end_session
    session = start_session(capture_artifacts=True)

    # Call 1: developer (generate) — 2 messages
    msgs_after_gen = [
        {"role": "user", "content": "build it"},
        {"role": "assistant", "content": "here is the app"},
    ]
    session.record_transcript(
        call_id="d1", timestamp=time.time(), caller="developer",
        model_id="m", system="sys", messages=msgs_after_gen,
        response="here is the app", input_tokens=100, output_tokens=50,
    )

    # Call 2: developer.self_review — same list grown by 2 more messages
    msgs_after_review = msgs_after_gen + [
        {"role": "user", "content": "review it"},
        {"role": "assistant", "content": "looks good"},
    ]
    session.record_transcript(
        call_id="d2", timestamp=time.time(), caller="developer.self_review",
        model_id="m", system="sys", messages=msgs_after_review,
        response="looks good", input_tokens=120, output_tokens=30,
    )

    assert len(session.transcripts) == 2
    r1 = session.transcripts[0]
    r2 = session.transcripts[1]

    # First call gets all initial messages
    assert r1.new_messages == msgs_after_gen
    # Second call gets only the delta (the two new messages)
    assert r2.new_messages == [
        {"role": "user", "content": "review it"},
        {"role": "assistant", "content": "looks good"},
    ]

    # Concatenating new_messages reconstructs the full conversation
    full = r1.new_messages + r2.new_messages
    assert full == msgs_after_review
    end_session()


def test_record_transcript_retry_accumulation():
    """developer→self_review→retry: three calls produce three non-overlapping deltas."""
    from goe.metrics import start_session, end_session
    session = start_session(capture_artifacts=True)

    m1 = [{"role": "user", "content": "turn1"}, {"role": "assistant", "content": "r1"}]
    m2 = m1 + [{"role": "user", "content": "turn2"}, {"role": "assistant", "content": "r2"}]
    m3 = m2 + [{"role": "user", "content": "turn3"}, {"role": "assistant", "content": "r3"}]

    for i, (caller, msgs) in enumerate([
        ("developer", m1),
        ("developer.self_review", m2),
        ("developer.retry", m3),
    ]):
        session.record_transcript(
            call_id=f"c{i}", timestamp=time.time(), caller=caller,
            model_id="m", system="sys", messages=msgs,
            response=msgs[-1]["content"], input_tokens=10, output_tokens=5,
        )

    assert len(session.transcripts) == 3
    assert session.transcripts[0].new_messages == m1
    assert session.transcripts[1].new_messages == m2[len(m1):]
    assert session.transcripts[2].new_messages == m3[len(m2):]

    # Full reconstruction
    full = []
    for rec in session.transcripts:
        full.extend(rec.new_messages)
    assert full == m3
    end_session()


def test_record_transcript_fresh_list_resets_counter():
    """A shorter messages list (new conversation) resets the per-root counter.

    Simulates attacker.fix_procedure, which builds a fresh single-turn list
    after the attacker's multi-turn generate+self_review conversation.
    """
    from goe.metrics import start_session, end_session
    session = start_session(capture_artifacts=True)

    # Attacker initial call with 2 messages
    long_msgs = [
        {"role": "user", "content": "attack"},
        {"role": "assistant", "content": "procedure v1"},
    ]
    session.record_transcript(
        call_id="a1", timestamp=time.time(), caller="attacker",
        model_id="m", system="sys", messages=long_msgs,
        response="procedure v1", input_tokens=10, output_tokens=5,
    )

    # fix_procedure builds a *new* single-turn message list (shorter than 2)
    short_msgs = [{"role": "user", "content": "fix it"}]
    session.record_transcript(
        call_id="a2", timestamp=time.time(), caller="attacker.fix_procedure",
        model_id="m", system="sys", messages=short_msgs,
        response="fixed", input_tokens=5, output_tokens=3,
    )

    assert len(session.transcripts) == 2
    # fix_procedure should get its full (reset) list, not an empty delta
    assert session.transcripts[1].new_messages == short_msgs
    end_session()


def test_record_transcript_noop_when_not_capturing():
    """record_transcript is a no-op when transcripts is None (metrics-only session)."""
    from goe.metrics import start_session, end_session
    session = start_session()  # no capture_artifacts

    session.record_transcript(
        call_id="x", timestamp=time.time(), caller="developer",
        model_id="m", system="sys", messages=[{"role": "user", "content": "hi"}],
        response="ok", input_tokens=5, output_tokens=2,
    )

    assert session.transcripts is None  # still None — not converted to list
    end_session()


# ---------------------------------------------------------------------------
# save_crew_artifacts
# ---------------------------------------------------------------------------

def test_save_crew_artifacts_writes_all_files(tmp_path, sample_crew):
    """save_crew_artifacts writes app/, db/, procedure.yaml, artifact.json, plan."""
    from goe.artifacts.writer import save_crew_artifacts

    save_crew_artifacts("test_entity", sample_crew, tmp_path)

    entity_dir = tmp_path / "entities" / "test_entity"
    assert (entity_dir / "app" / "app.py").exists()
    assert (entity_dir / "app" / "utils" / "helpers.py").exists()
    assert (entity_dir / "db" / "schema.sql").exists()
    assert (entity_dir / "db" / "seed.sql").exists()
    assert (entity_dir / "artifact.json").exists()
    assert (entity_dir / "procedure.yaml").exists()
    assert (entity_dir / "engineer_plan.json").exists()


def test_save_crew_artifacts_file_contents(tmp_path, sample_crew):
    """Verify file contents are written correctly."""
    from goe.artifacts.writer import save_crew_artifacts

    save_crew_artifacts("ent1", sample_crew, tmp_path)

    entity_dir = tmp_path / "entities" / "ent1"
    assert (entity_dir / "app" / "app.py").read_text() == "print('hello')"
    assert "CREATE TABLE users" in (entity_dir / "db" / "schema.sql").read_text()
    assert "INSERT INTO users" in (entity_dir / "db" / "seed.sql").read_text()

    art = json.loads((entity_dir / "artifact.json").read_text())
    # source_files should be stripped from artifact.json (written as individual files)
    assert "source_files" not in art
    assert art["port"] == 5000

    plan = json.loads((entity_dir / "engineer_plan.json").read_text())
    assert plan["runtime"] == "flask"


def test_save_crew_artifacts_no_db_skips_db_dir(tmp_path, sample_crew_no_db):
    """When db_setup is None, the db/ directory should not be created."""
    from goe.artifacts.writer import save_crew_artifacts

    save_crew_artifacts("no_db", sample_crew_no_db, tmp_path)

    entity_dir = tmp_path / "entities" / "no_db"
    assert not (entity_dir / "db").exists()
    assert (entity_dir / "app" / "app.py").exists()


# ---------------------------------------------------------------------------
# Path traversal protection
# ---------------------------------------------------------------------------

def test_sanitize_path_normal():
    from goe.artifacts.writer import _sanitize_path
    assert _sanitize_path("app.py") == "app.py"
    assert _sanitize_path("utils/helpers.py") == "utils/helpers.py"
    assert _sanitize_path("a/b/c.js") == "a/b/c.js"


def test_sanitize_path_rejects_traversal():
    from goe.artifacts.writer import _sanitize_path
    with pytest.raises(ValueError):
        _sanitize_path("../../etc/passwd")


def test_sanitize_path_rejects_absolute():
    from goe.artifacts.writer import _sanitize_path
    with pytest.raises(ValueError):
        _sanitize_path("/etc/passwd")


def test_save_crew_artifacts_traversal_blocked(tmp_path, sample_engineer_plan, sample_procedure):
    """An LLM-supplied ../escape filename must not write outside entities/<id>/app/."""
    from goe.models.artifacts import BuildArtifact
    from goe.construction_crew.orchestrator import CrewResult
    from goe.artifacts.writer import save_crew_artifacts

    evil_artifact = BuildArtifact(
        source_files={"../../evil.py": "malicious"},
        primary_source="../../evil.py",
        port=5000,
    )
    crew = CrewResult(
        artifact=evil_artifact,
        procedure=sample_procedure,
        outgoing_values={},
        plan=sample_engineer_plan,
    )

    with pytest.raises(ValueError):
        save_crew_artifacts("safe", crew, tmp_path)

    # Confirm the file was NOT written outside the run dir
    assert not (tmp_path / "evil.py").exists()
    assert not (tmp_path.parent / "evil.py").exists()


# ---------------------------------------------------------------------------
# save_attempt_artifacts + write_attempt_diff
# ---------------------------------------------------------------------------

def _make_crew_with_content(source_content: str, proc_content: str,
                             sample_engineer_plan, sample_procedure_class):
    """Helper: build a CrewResult with given source file content."""
    from goe.models.artifacts import BuildArtifact
    from goe.construction_crew.orchestrator import CrewResult
    art = BuildArtifact(
        source_files={"app.py": source_content},
        primary_source="app.py",
        port=5000,
    )
    proc = sample_procedure_class.model_validate({"sessions": [], "procedure": []})
    return CrewResult(artifact=art, procedure=proc, outgoing_values={}, plan=sample_engineer_plan)


def test_save_attempt_artifacts(tmp_path, sample_crew, sample_engineer_plan):
    """save_attempt_artifacts writes under attempts/attempt_N/."""
    from goe.artifacts.writer import save_crew_artifacts, save_attempt_artifacts

    # Save the initial crew (attempt 0 / entity dir)
    save_crew_artifacts("ent", sample_crew, tmp_path)

    # Fake diagnosis object
    class FakeDiag:
        def __init__(self):
            self.category = "procedure_bug"
            self.description = "wrong URL"

    save_attempt_artifacts("ent", 1, sample_crew, FakeDiag(), tmp_path)

    attempt_dir = tmp_path / "entities" / "ent" / "attempts" / "attempt_1"
    assert (attempt_dir / "app" / "app.py").exists()
    assert (attempt_dir / "procedure.yaml").exists()
    assert (attempt_dir / "diagnosis.json").exists()
    diag = json.loads((attempt_dir / "diagnosis.json").read_text())
    assert diag["category"] == "procedure_bug"


def test_write_attempt_diff_produces_diff(tmp_path, sample_engineer_plan):
    """write_attempt_diff writes a unified diff between initial and attempt 1."""
    from goe.models.artifacts import BuildArtifact
    from goe.models.procedure import Procedure
    from goe.construction_crew.orchestrator import CrewResult
    from goe.artifacts.writer import save_crew_artifacts, save_attempt_artifacts, write_attempt_diff

    def make_crew(content):
        art = BuildArtifact(
            source_files={"app.py": content},
            primary_source="app.py",
            port=5000,
        )
        proc = Procedure.model_validate({"sessions": [], "procedure": []})
        from goe.construction_crew.engineer import EngineerPlan
        plan = EngineerPlan(
            summary="T", runtime="flask",
            vulnerability_placement="in /login",
            attack_entry_point="/login",
            success_indicator="200",
        )
        return CrewResult(artifact=art, procedure=proc, outgoing_values={}, plan=plan)

    initial_crew = make_crew("v1 = True")
    attempt1_crew = make_crew("v2 = False")

    class FakeDiag:
        category = "procedure_bug"
        description = "wrong"

    save_crew_artifacts("ent", initial_crew, tmp_path)
    save_attempt_artifacts("ent", 1, attempt1_crew, FakeDiag(), tmp_path)
    write_attempt_diff("ent", 1, tmp_path)

    diff_file = tmp_path / "entities" / "ent" / "attempts" / "attempt_initial_to_1.diff"
    assert diff_file.exists()
    diff_content = diff_file.read_text()
    assert "v1 = True" in diff_content or "-v1" in diff_content
    assert "v2 = False" in diff_content or "+v2" in diff_content


def test_write_attempt_diff_noop_if_no_dirs(tmp_path):
    """write_attempt_diff is a no-op when attempt directories don't exist."""
    from goe.artifacts.writer import write_attempt_diff
    # Should not raise
    write_attempt_diff("nonexistent", 1, tmp_path)


# ---------------------------------------------------------------------------
# Transcript writers
# ---------------------------------------------------------------------------

def test_write_transcripts_jsonl_and_md(tmp_path):
    """write_transcripts produces llm_calls.jsonl and per-agent .md files."""
    from goe.metrics import start_session, end_session
    from goe.artifacts.writer import write_transcripts

    session = start_session(capture_artifacts=True)
    session.record_transcript(
        call_id="t1", timestamp=time.time(), caller="developer",
        model_id="m", system="system prompt text",
        messages=[{"role": "user", "content": "build app"}],
        response="here is app.py", input_tokens=100, output_tokens=50,
    )
    session.record_transcript(
        call_id="t2", timestamp=time.time(), caller="developer.self_review",
        model_id="m", system="system prompt text",
        messages=[{"role": "user", "content": "review"}, {"role": "assistant", "content": "ok"}],
        response="looks fine", input_tokens=20, output_tokens=10,
    )
    end_session()

    write_transcripts(session, tmp_path)

    assert (tmp_path / "conversations" / "llm_calls.jsonl").exists()
    assert (tmp_path / "conversations" / "developer.md").exists()

    # JSONL should have 2 lines
    lines = (tmp_path / "conversations" / "llm_calls.jsonl").read_text().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["caller"] == "developer"

    # Markdown should contain system prompt and response
    md = (tmp_path / "conversations" / "developer.md").read_text()
    assert "system prompt text" in md
    assert "here is app.py" in md


def test_write_transcripts_noop_when_empty(tmp_path):
    """write_transcripts does nothing when session has no transcripts."""
    from goe.metrics import start_session, end_session
    from goe.artifacts.writer import write_transcripts

    session = start_session(capture_artifacts=True)
    end_session()

    write_transcripts(session, tmp_path)
    assert not (tmp_path / "conversations").exists()


# ---------------------------------------------------------------------------
# write_manifest
# ---------------------------------------------------------------------------

def test_write_manifest(tmp_path):
    """write_manifest produces valid JSON with expected top-level keys."""
    from goe.metrics import start_session, end_session
    from goe.artifacts.writer import write_manifest

    session = start_session(capture_artifacts=True)
    session.record_transcript(
        call_id="m1", timestamp=time.time(), caller="planner",
        model_id="m", system="sys",
        messages=[{"role": "user", "content": "plan"}],
        response="graph", input_tokens=10, output_tokens=5,
    )
    end_session()

    entities = [{"id": "ent1", "status": "PASSED", "attempts": 1}]
    write_manifest(
        run_dir=tmp_path,
        session=session,
        entry_point="build",
        command="python -m goe.build --spec foo.yaml",
        entities=entities,
        started_at="2026-06-12T14:00:00",
        ended_at="2026-06-12T14:05:00",
    )

    manifest_path = tmp_path / "manifest.json"
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text())
    assert manifest["entry_point"] == "build"
    assert manifest["command"] == "python -m goe.build --spec foo.yaml"
    assert manifest["started_at"] == "2026-06-12T14:00:00"
    assert manifest["ended_at"] == "2026-06-12T14:05:00"
    assert "llm" in manifest
    assert "conversations" in manifest
    assert "entities" in manifest
    assert manifest["entities"] == entities
    assert "files" in manifest


def test_write_manifest_rounds_trips_session_summary(tmp_path):
    """manifest.json llm section matches session.summary()."""
    from goe.metrics import start_session, end_session, LLMCallRecord
    from goe.artifacts.writer import write_manifest

    session = start_session(capture_artifacts=True)
    session.record(LLMCallRecord(
        call_id="x", timestamp=time.time(), caller="engineer",
        model_id="m", input_tokens=50, output_tokens=25, latency_ms=500.0,
    ))
    end_session()

    write_manifest(tmp_path, session, "build", "cmd", None, "s", "e")

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    expected_summary = session.summary()
    assert manifest["llm"]["total_calls"] == expected_summary["total_calls"]
    assert manifest["llm"]["total_tokens"] == expected_summary["total_tokens"]


# ---------------------------------------------------------------------------
# Config toggle
# ---------------------------------------------------------------------------

def test_save_artifacts_default_false(monkeypatch):
    """save_artifacts defaults to False when no env var or toml setting."""
    from goe.config import GoEConfig
    monkeypatch.delenv("GOE_SAVE_ARTIFACTS", raising=False)
    GoEConfig.reset()
    cfg = GoEConfig.get()
    assert cfg.save_artifacts is False
    GoEConfig.reset()


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"])
def test_save_artifacts_env_truthy(monkeypatch, value):
    from goe.config import GoEConfig
    monkeypatch.setenv("GOE_SAVE_ARTIFACTS", value)
    GoEConfig.reset()
    cfg = GoEConfig.get()
    assert cfg.save_artifacts is True
    GoEConfig.reset()


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  "])
def test_save_artifacts_env_falsy(monkeypatch, value):
    from goe.config import GoEConfig
    monkeypatch.setenv("GOE_SAVE_ARTIFACTS", value)
    GoEConfig.reset()
    cfg = GoEConfig.get()
    assert cfg.save_artifacts is False
    GoEConfig.reset()


def test_artifacts_dir_default(monkeypatch):
    from goe.config import GoEConfig
    monkeypatch.delenv("GOE_ARTIFACTS_DIR", raising=False)
    GoEConfig.reset()
    cfg = GoEConfig.get()
    assert cfg.artifacts_dir == "artifacts"
    GoEConfig.reset()


def test_artifacts_dir_env_override(monkeypatch, tmp_path):
    from goe.config import GoEConfig
    monkeypatch.setenv("GOE_ARTIFACTS_DIR", str(tmp_path))
    GoEConfig.reset()
    cfg = GoEConfig.get()
    assert cfg.artifacts_dir == str(tmp_path)
    GoEConfig.reset()


# ---------------------------------------------------------------------------
# artifact_run context manager
# ---------------------------------------------------------------------------

def test_artifact_run_no_capture(monkeypatch):
    """artifact_run with capture=False yields (None, session) and ends session."""
    from goe.artifacts.run import artifact_run
    from goe.metrics import get_session

    with artifact_run("build", "cmd", capture=False) as (run_dir, session):
        assert run_dir is None
        assert session is not None
        assert get_session() is session

    # Session is ended after the context
    assert get_session() is None


def test_artifact_run_with_capture(tmp_path, monkeypatch):
    """artifact_run with capture=True yields (run_dir, session) and flushes."""
    from goe.config import GoEConfig
    from goe.artifacts.run import artifact_run
    from goe.metrics import get_session

    monkeypatch.setenv("GOE_ARTIFACTS_DIR", str(tmp_path))
    GoEConfig.reset()

    with artifact_run("build", "cmd", capture=True) as (run_dir, session):
        assert run_dir is not None
        assert run_dir.exists()
        assert session.transcripts is not None

    # After context, session is ended and manifest written
    assert get_session() is None
    assert (run_dir / "manifest.json").exists()
    GoEConfig.reset()


def test_artifact_run_reads_config(monkeypatch, tmp_path):
    """When capture=None, artifact_run reads from GoEConfig.save_artifacts."""
    from goe.config import GoEConfig
    from goe.artifacts.run import artifact_run

    monkeypatch.setenv("GOE_SAVE_ARTIFACTS", "1")
    monkeypatch.setenv("GOE_ARTIFACTS_DIR", str(tmp_path))
    GoEConfig.reset()

    with artifact_run("build", "cmd") as (run_dir, session):
        assert run_dir is not None

    GoEConfig.reset()
