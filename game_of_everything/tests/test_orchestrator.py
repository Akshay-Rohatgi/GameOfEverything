"""Unit tests for goe/flow orchestrator — plan + build_entity mocked (no Docker/LLM)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from goe.flow import orchestrator
from goe.flow.chain_test import ChainTestOutcome
from goe.flow.checkpoint import load_state
from goe.graph.models import EntityGraph
from goe.models.procedure import ExecAttackerAction, Procedure, Step
from goe.models.report import BuildOutcome, ChainTestResult, ChainTestStatus, EntityResult, EntityStatus
from goe.planner.pipeline import PlanResult

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


def _graph() -> EntityGraph:
    return EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")


def _passed_outcome(entity_id: str, outgoing: dict[str, str]) -> BuildOutcome:
    proc = Procedure(procedure=[
        Step(step_id=f"{entity_id}_s1",
             action=ExecAttackerAction(type="exec_attacker", command="echo hi"))
    ])
    return BuildOutcome(
        result=EntityResult(id=entity_id, status=EntityStatus.PASSED, attempts=1),
        deploy_script=f"echo {entity_id}",
        procedure=proc,
        outgoing_values=outgoing,
    )


@pytest.fixture
def output_root(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "OUTPUT_ROOT", tmp_path / "output")
    return tmp_path / "output"


def test_propagation_and_packaging(output_root):
    graph = _graph()
    seen_incoming: dict[str, dict] = {}

    def fake_build(entity, incoming_edges=None, scope="", verbose=False):
        seen_incoming[entity.id] = dict(incoming_edges or {})
        if entity.id == "sqli_entity":
            return _passed_outcome("sqli_entity", {"sqli_to_ssh": "admin:hunter2"})
        return _passed_outcome("ssh_entity", {})

    chain_outcome = ChainTestOutcome(
        result=ChainTestResult(status=ChainTestStatus.PASSED),
    )

    with patch("goe.planner.pipeline.plan",
               return_value=PlanResult(graph=graph, success=True, attempts=1)), \
         patch("goe.build.build_entity", side_effect=fake_build), \
         patch("goe.flow.chain_test.run_chain_test", return_value=chain_outcome):
        result = orchestrator.run("sqli to ssh")

    assert result.success
    # Value propagated from sqli_entity to ssh_entity's incoming edges.
    assert seen_incoming["ssh_entity"] == {"sqli_to_ssh": "admin:hunter2"}
    # Package produced.
    assert result.output_dir is not None
    assert (result.output_dir / "deploy.sh").exists()
    assert {r.status for r in result.results} == {EntityStatus.PASSED}
    # Chain test result attached.
    assert result.chain_test is not None
    assert result.chain_test.status == ChainTestStatus.PASSED


def test_chain_test_failure_gates_success(output_root):
    """A failed chain test must make RunResult.success=False even if all entities built."""
    graph = _graph()

    def fake_build(entity, incoming_edges=None, scope="", verbose=False):
        if entity.id == "sqli_entity":
            return _passed_outcome("sqli_entity", {"sqli_to_ssh": "admin:hunter2"})
        return _passed_outcome("ssh_entity", {})

    chain_outcome = ChainTestOutcome(
        result=ChainTestResult(status=ChainTestStatus.FAILED, reason="SSH auth rejected"),
    )

    with patch("goe.planner.pipeline.plan",
               return_value=PlanResult(graph=graph, success=True, attempts=1)), \
         patch("goe.build.build_entity", side_effect=fake_build), \
         patch("goe.flow.chain_test.run_chain_test", return_value=chain_outcome):
        result = orchestrator.run("sqli to ssh")

    # Both entities built but chain test failed → overall run fails.
    assert not result.success
    assert result.chain_test is not None
    assert result.chain_test.status == ChainTestStatus.FAILED
    # Package still written (entities passed individually).
    assert result.output_dir is not None


def test_failed_entity_skips_dependents(output_root):
    graph = _graph()

    def fake_build(entity, incoming_edges=None, scope="", verbose=False):
        if entity.id == "sqli_entity":
            return BuildOutcome(
                result=EntityResult(
                    id="sqli_entity", status=EntityStatus.FAILED,
                    attempts=3, failure_reason="design_flaw: nope",
                ),
            )
        pytest.fail("ssh_entity should never be built after sqli_entity fails")

    with patch("goe.planner.pipeline.plan",
               return_value=PlanResult(graph=graph, success=True, attempts=1)), \
         patch("goe.build.build_entity", side_effect=fake_build):
        result = orchestrator.run("sqli to ssh")

    assert not result.success
    statuses = {r.id: r.status for r in result.results}
    assert statuses["sqli_entity"] == EntityStatus.FAILED
    assert statuses["ssh_entity"] == EntityStatus.SKIPPED


def test_plan_failure_aborts(output_root):
    with patch("goe.planner.pipeline.plan",
               return_value=PlanResult(graph=None, success=False, attempts=2)):
        result = orchestrator.run("nonsense")

    assert not result.success
    assert result.graph is None
    assert result.output_dir is None


def test_checkpoint_resume_skips_completed(output_root):
    graph = _graph()
    call_count = {"n": 0}

    def fake_build(entity, incoming_edges=None, scope="", verbose=False):
        call_count["n"] += 1
        if entity.id == "sqli_entity":
            return _passed_outcome("sqli_entity", {"sqli_to_ssh": "admin:hunter2"})
        # ssh_entity fails on the first run so the run is resumable mid-flight.
        if call_count["n"] <= 2 and entity.id == "ssh_entity":
            return BuildOutcome(result=EntityResult(
                id="ssh_entity", status=EntityStatus.FAILED,
                attempts=1, failure_reason="procedure_bug: transient",
            ))
        return _passed_outcome("ssh_entity", {})

    # First run: sqli passes (checkpointed), ssh fails.
    with patch("goe.planner.pipeline.plan",
               return_value=PlanResult(graph=graph, success=True, attempts=1)), \
         patch("goe.build.build_entity", side_effect=fake_build):
        first = orchestrator.run("sqli to ssh")

    assert not first.success
    # Checkpoint recorded the completed sqli_entity.
    ckpt_dir = output_root / ".checkpoints"
    run_id = next(p.name for p in ckpt_dir.iterdir())
    state = load_state(ckpt_dir / run_id)
    assert "sqli_entity" in state.completed
    assert "ssh_entity" in state.failed

    # Resume: sqli_entity must NOT be rebuilt (only ssh would be retried,
    # but it is terminal/failed in the checkpoint, so nothing rebuilds).
    calls_before = call_count["n"]
    with patch("goe.planner.pipeline.plan") as mock_plan, \
         patch("goe.build.build_entity", side_effect=fake_build):
        resumed = orchestrator.run(resume_dir=ckpt_dir / run_id)
        mock_plan.assert_not_called()

    # No new build calls — both entities were terminal in the checkpoint.
    assert call_count["n"] == calls_before
    # sqli_entity still packaged from the restored snapshot.
    assert resumed.output_dir is not None
    assert (resumed.output_dir / "deploy.sh").read_text().count("# ---") == 1
