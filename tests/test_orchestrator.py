"""Unit tests for goe/flow orchestrator — plan + build_entity mocked (no Docker/LLM)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from goe.flow import orchestrator
from goe.flow.chain_test import ChainTestOutcome
from goe.flow.checkpoint import load_state
from goe.graph.models import EntityGraph
from goe.models.procedure import ExecAttackerAction, Procedure, Step
from goe.models.report import BuildOutcome, ChainTestResult, ChainTestStatus, EntityResult, EntityStatus
from goe.planner.pipeline import PlanResult
from goe.planner.resolve import resolve

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"

# Concrete creds the sqli_entity build "leaks" — every declared param of the consumed
# creds_for edge, so the edge-completeness guard is satisfied (host comes from resolve()).
_SQLI_CREDS = {"sqli_to_ssh": {"user": "admin", "secret": "hunter2", "cred_type": "password"}}


def _graph() -> EntityGraph:
    # Mirror the real pipeline: plan() returns an already-resolved graph (host/port filled).
    return resolve(EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml"))


def _passed_outcome(entity_id: str, outgoing: dict) -> BuildOutcome:
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


def _mock_progressive_env_patch():
    """Patch ProgressiveEnvironment so multi-entity graphs don't touch real Docker."""
    mock_penv = MagicMock()
    mock_penv.current_snapshot = None
    return patch("goe.container.progressive.ProgressiveEnvironment", return_value=mock_penv)


def test_propagation_and_packaging(output_root):
    graph = _graph()
    seen_incoming: dict[str, dict] = {}

    def fake_build(entity, incoming_edges=None, scope="", verbose=False, env=None, edge_schemas=None, system_context=None, provided_values=None, console=None):
        seen_incoming[entity.id] = dict(incoming_edges or {})
        if entity.id == "sqli_entity":
            return _passed_outcome("sqli_entity", dict(_SQLI_CREDS))
        return _passed_outcome("ssh_entity", {})

    chain_outcome = ChainTestOutcome(
        result=ChainTestResult(status=ChainTestStatus.PASSED),
    )

    with patch("goe.planner.pipeline.plan",
               return_value=PlanResult(graph=graph, success=True, attempts=1)), \
         patch("goe.build.build_entity", side_effect=fake_build), \
         patch("goe.flow.chain_test.run_chain_test", return_value=chain_outcome), \
         _mock_progressive_env_patch():
        result = orchestrator.run("sqli to ssh")

    assert result.success
    # Full credential dict (incl. username) propagated from sqli_entity to ssh_entity, plus the
    # resolved host read straight off the graph edge (consumers now receive resolved params too).
    expected = {"sqli_to_ssh": {**_SQLI_CREDS["sqli_to_ssh"], "host": "target"}}
    assert seen_incoming["ssh_entity"] == expected
    # Package produced.
    assert result.output_dir is not None
    assert (result.output_dir / "deploy.sh").exists()
    assert {r.status for r in result.results} == {EntityStatus.PASSED}
    # Chain test result attached.
    assert result.chain_test is not None
    assert result.chain_test.status == ChainTestStatus.PASSED


def test_incomplete_edge_param_fails_run(output_root):
    """If the producer omits a declared param (partial info), the run must fail loudly
    rather than silently substituting the structural placeholder downstream."""
    graph = _graph()

    def fake_build(entity, incoming_edges=None, scope="", verbose=False, env=None, edge_schemas=None, system_context=None, provided_values=None, console=None):
        if entity.id == "sqli_entity":
            # Leak only the username — 'secret' and 'cred_type' never propagate.
            return _passed_outcome("sqli_entity", {"sqli_to_ssh": {"user": "admin"}})
        return _passed_outcome("ssh_entity", {})

    chain_outcome = ChainTestOutcome(result=ChainTestResult(status=ChainTestStatus.PASSED))

    with patch("goe.planner.pipeline.plan",
               return_value=PlanResult(graph=graph, success=True, attempts=1)), \
         patch("goe.build.build_entity", side_effect=fake_build), \
         patch("goe.flow.chain_test.run_chain_test", return_value=chain_outcome), \
         _mock_progressive_env_patch():
        result = orchestrator.run("sqli to ssh")

    assert not result.success
    # The unfilled params are reported (secret + cred_type on the consumed creds_for edge).
    assert "sqli_to_ssh.secret" in result.edge_gaps
    assert "sqli_to_ssh.cred_type" in result.edge_gaps


def test_populate_edge_concrete_writes_all_declared_params(output_root):
    """_populate_edge_concrete writes every payload param onto the matching declared edge
    param, for any edge type, and never invents keys."""
    graph = _graph()  # sqli_to_ssh is creds_for {user, host, cred_type, secret}
    orchestrator._populate_edge_concrete(
        graph, "sqli_entity",
        {"sqli_to_ssh": {"user": "admin", "secret": "hunter2", "cred_type": "password"}},
    )
    edge = graph.edge_by_id("sqli_to_ssh")
    assert edge.params["user"].concrete == "admin"
    assert edge.params["secret"].concrete == "hunter2"
    assert edge.params["cred_type"].concrete == "password"
    # No phantom keys created (e.g. the old 'value' / 'password' backfill).
    assert set(edge.params) == {"user", "host", "cred_type", "secret"}


def test_chain_test_failure_gates_success(output_root):
    """A failed chain test must make RunResult.success=False even if all entities built."""
    graph = _graph()

    def fake_build(entity, incoming_edges=None, scope="", verbose=False, env=None, edge_schemas=None, system_context=None, provided_values=None, console=None):
        if entity.id == "sqli_entity":
            return _passed_outcome("sqli_entity", dict(_SQLI_CREDS))
        return _passed_outcome("ssh_entity", {})

    chain_outcome = ChainTestOutcome(
        result=ChainTestResult(status=ChainTestStatus.FAILED, reason="SSH auth rejected"),
    )

    with patch("goe.planner.pipeline.plan",
               return_value=PlanResult(graph=graph, success=True, attempts=1)), \
         patch("goe.build.build_entity", side_effect=fake_build), \
         patch("goe.flow.chain_test.run_chain_test", return_value=chain_outcome), \
         _mock_progressive_env_patch():
        result = orchestrator.run("sqli to ssh")

    # Both entities built but chain test failed → overall run fails.
    assert not result.success
    assert result.chain_test is not None
    assert result.chain_test.status == ChainTestStatus.FAILED
    # Package still written (entities passed individually).
    assert result.output_dir is not None


def test_failed_entity_skips_dependents(output_root):
    graph = _graph()

    def fake_build(entity, incoming_edges=None, scope="", verbose=False, env=None, edge_schemas=None, system_context=None, provided_values=None, console=None):
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
         patch("goe.build.build_entity", side_effect=fake_build), \
         _mock_progressive_env_patch():
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


def test_mixed_runtime_same_system_uses_progressive_env(output_root):
    """Flask + ubuntu entities on the same system should use ProgressiveEnvironment."""
    graph = resolve(EntityGraph.from_yaml(FIXTURES / "valid_flask_suid_chain.yaml"))
    _SHELL_VALS = {"cmdi_to_suid": {"user": "www-data"}}

    envs_passed = []

    def fake_build(entity, incoming_edges=None, scope="", verbose=False, env=None, edge_schemas=None, system_context=None, provided_values=None, console=None):
        envs_passed.append((entity.id, env))
        if entity.id == "flask_cmdi":
            return _passed_outcome("flask_cmdi", dict(_SHELL_VALS))
        return _passed_outcome("suid_privesc", {})

    chain_outcome = ChainTestOutcome(
        result=ChainTestResult(status=ChainTestStatus.PASSED),
    )

    with patch("goe.planner.pipeline.plan",
               return_value=PlanResult(graph=graph, success=True, attempts=1)), \
         patch("goe.build.build_entity", side_effect=fake_build), \
         patch("goe.flow.chain_test.run_chain_test", return_value=chain_outcome), \
         _mock_progressive_env_patch():
        result = orchestrator.run("flask cmdi to suid")

    assert result.success
    # Both entities should receive the same progressive env (not None)
    assert envs_passed[0][1] is not None
    assert envs_passed[0][1] is envs_passed[1][1]


def test_checkpoint_resume_skips_completed(output_root):
    graph = _graph()
    call_count = {"n": 0}

    def fake_build(entity, incoming_edges=None, scope="", verbose=False, env=None, edge_schemas=None, system_context=None, provided_values=None, console=None):
        call_count["n"] += 1
        if entity.id == "sqli_entity":
            return _passed_outcome("sqli_entity", dict(_SQLI_CREDS))
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
         patch("goe.build.build_entity", side_effect=fake_build), \
         _mock_progressive_env_patch():
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
         patch("goe.build.build_entity", side_effect=fake_build), \
         _mock_progressive_env_patch():
        resumed = orchestrator.run(resume_dir=ckpt_dir / run_id)
        mock_plan.assert_not_called()

    # No new build calls — both entities were terminal in the checkpoint.
    assert call_count["n"] == calls_before
    # sqli_entity still packaged from the restored snapshot.
    assert resumed.output_dir is not None
    assert (resumed.output_dir / "deploy.sh").read_text().count("# ---") == 1
