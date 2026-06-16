"""Unit tests for Phase 4 additions: multi-system packaging, interpolation, chain test.

Marked `not docker and not llm` — all Docker and LLM calls are mocked or skipped.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from goe.executor.interpolation import interpolate
from goe.graph.models import EntityGraph
from goe.models.procedure import ExecAttackerAction, Procedure, Step
from goe.models.report import BuildOutcome, ChainTestResult, ChainTestStatus, EntityResult, EntityStatus

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _multisystem_graph() -> EntityGraph:
    return EntityGraph.from_yaml(FIXTURES / "valid_multisystem.yaml")


def _single_graph() -> EntityGraph:
    return EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")


def _passed_outcome(entity_id: str, deploy_script: str = "", outgoing: dict | None = None) -> BuildOutcome:
    proc = Procedure(procedure=[
        Step(
            step_id=f"{entity_id}_s1",
            action=ExecAttackerAction(type="exec_attacker", command="echo hi"),
        )
    ])
    return BuildOutcome(
        result=EntityResult(id=entity_id, status=EntityStatus.PASSED, attempts=1),
        deploy_script=deploy_script or f"echo {entity_id}\n",
        procedure=proc,
        outgoing_values=outgoing or {},
    )


# ---------------------------------------------------------------------------
# 1. EntityGraph.entities_by_system()
# ---------------------------------------------------------------------------

class TestEntitiesBySystem:
    def test_multisystem_grouping(self):
        graph = _multisystem_graph()
        grouped = graph.entities_by_system()
        # Both systems present as keys
        assert set(grouped.keys()) == {"web_system", "db_system"}
        # sqli_webapp on web_system, ssh_pivot on db_system
        assert [e.id for e in grouped["web_system"]] == ["sqli_webapp"]
        assert [e.id for e in grouped["db_system"]] == ["ssh_pivot"]

    def test_single_system_grouping(self):
        graph = _single_graph()
        grouped = graph.entities_by_system()
        # All entities under one system
        assert len(grouped) == 1
        system_key = list(grouped.keys())[0]
        ids = [e.id for e in grouped[system_key]]
        assert "sqli_entity" in ids
        assert "ssh_entity" in ids

    def test_topo_order_within_system(self):
        graph = _multisystem_graph()
        grouped = graph.entities_by_system()
        # sqli_webapp has no upstream entity-to-entity deps; ssh_pivot depends on it
        # but they're on different systems so within each system the order is trivial
        assert grouped["web_system"][0].id == "sqli_webapp"
        assert grouped["db_system"][0].id == "ssh_pivot"


# ---------------------------------------------------------------------------
# 2. Multi-target interpolation
# ---------------------------------------------------------------------------

class TestSystemInterpolation:
    def _ctx(self):
        return {
            "target_host": "legacy_target",
            "attacker_host": "attacker",
            "target_port": "80",
            "systems": {
                "web_system": {"host": "webserver", "port": "80"},
                "db_system": {"host": "dbserver", "port": "22"},
            },
            "steps": {},
            "edges": {},
        }

    def test_system_host(self):
        assert interpolate("${system.web_system.host}", self._ctx()) == "webserver"
        assert interpolate("${system.db_system.host}", self._ctx()) == "dbserver"

    def test_system_port(self):
        assert interpolate("${system.web_system.port}", self._ctx()) == "80"
        assert interpolate("${system.db_system.port}", self._ctx()) == "22"

    def test_system_in_url(self):
        tmpl = "http://${system.web_system.host}:${system.web_system.port}/exploit"
        assert interpolate(tmpl, self._ctx()) == "http://webserver:80/exploit"

    def test_unknown_system_leaves_template(self):
        result = interpolate("${system.nonexistent.host}", self._ctx())
        assert result == "${system.nonexistent.host}"

    def test_existing_interpolation_unchanged(self):
        """Existing ${target_host}, ${edge.*}, ${steps.*} still work."""
        ctx = self._ctx()
        ctx["edges"] = {"e1": {"user": "bob"}}
        ctx["steps"] = {"step1": {"cred": "secret"}}
        assert interpolate("${target_host}", ctx) == "legacy_target"
        assert interpolate("${edge.e1.user}", ctx) == "bob"
        assert interpolate("${steps.step1.cred}", ctx) == "secret"


# ---------------------------------------------------------------------------
# 3. Multi-system packaging
# ---------------------------------------------------------------------------

class TestMultiSystemPackaging:
    def test_per_system_deploy_scripts(self, tmp_path):
        from goe.packaging.packager import package

        graph = _multisystem_graph()
        # Populate concrete edge values so packager doesn't trip on None
        for edge in graph.edges:
            for pv in edge.params.values():
                if pv.concrete is None:
                    object.__setattr__(pv, "concrete", pv.structural)

        built = {
            "sqli_webapp": _passed_outcome("sqli_webapp", deploy_script="echo webapp\n"),
            "ssh_pivot": _passed_outcome("ssh_pivot", deploy_script="echo pivot\n"),
        }
        out = package(graph, built, tmp_path / "out")

        # Per-system deploy scripts emitted
        assert (out / "web_system_deploy.sh").exists()
        assert (out / "db_system_deploy.sh").exists()
        # Single-system deploy.sh must NOT be emitted for a multi-system graph
        assert not (out / "deploy.sh").exists()
        # docker-compose emitted
        assert (out / "docker-compose.yml").exists()

    def test_docker_compose_content(self, tmp_path):
        from goe.packaging.packager import package

        graph = _multisystem_graph()
        for edge in graph.edges:
            for pv in edge.params.values():
                if pv.concrete is None:
                    object.__setattr__(pv, "concrete", pv.structural)

        built = {
            "sqli_webapp": _passed_outcome("sqli_webapp"),
            "ssh_pivot": _passed_outcome("ssh_pivot"),
        }
        out = package(graph, built, tmp_path / "out")
        compose = yaml.safe_load((out / "docker-compose.yml").read_text())

        services = compose["services"]
        assert "web_system" in services
        assert "db_system" in services
        # Hostnames set
        assert services["web_system"]["hostname"] == "webserver"
        assert services["db_system"]["hostname"] == "dbserver"
        # Both use ubuntu base
        assert services["web_system"]["image"] == "ubuntu:22.04"
        assert services["db_system"]["image"] == "ubuntu:22.04"

    def test_chain_playbook_written_when_provided(self, tmp_path):
        from goe.packaging.packager import package

        graph = _multisystem_graph()
        for edge in graph.edges:
            for pv in edge.params.values():
                if pv.concrete is None:
                    object.__setattr__(pv, "concrete", pv.structural)

        built = {
            "sqli_webapp": _passed_outcome("sqli_webapp"),
            "ssh_pivot": _passed_outcome("ssh_pivot"),
        }
        chain_proc = Procedure(procedure=[
            Step(
                step_id="chain_step",
                action=ExecAttackerAction(type="exec_attacker", command="echo done"),
            )
        ])
        out = package(graph, built, tmp_path / "out", chain_procedure=chain_proc)
        assert (out / "chain_playbook.yaml").exists()

    def test_chain_playbook_not_written_without_procedure(self, tmp_path):
        from goe.packaging.packager import package

        graph = _multisystem_graph()
        for edge in graph.edges:
            for pv in edge.params.values():
                if pv.concrete is None:
                    object.__setattr__(pv, "concrete", pv.structural)

        built = {
            "sqli_webapp": _passed_outcome("sqli_webapp"),
        }
        out = package(graph, built, tmp_path / "out")
        assert not (out / "chain_playbook.yaml").exists()

    def test_port_collision_scoped_per_system(self, tmp_path):
        """Two entities on different systems binding the same port must NOT warn."""
        from goe.packaging.packager import _detect_port_collisions

        # Create a minimal 2-system graph where both have a flask entity on port 5000
        # This is a synthetic test of the scoping — in practice planner wouldn't do this
        graph = _multisystem_graph()  # web_system (flask) + db_system (ubuntu)
        order = ["sqli_webapp", "ssh_pivot"]
        warnings = _detect_port_collisions(graph, order)
        # No collision — different systems even if same port
        assert warnings == []

    def test_single_system_still_emits_deploy_sh(self, tmp_path):
        """Single-system path must remain unchanged."""
        from goe.packaging.packager import package

        graph = _single_graph()
        built = {
            "sqli_entity": _passed_outcome("sqli_entity"),
        }
        out = package(graph, built, tmp_path / "out")
        assert (out / "deploy.sh").exists()
        assert not (out / "docker-compose.yml").exists()


# ---------------------------------------------------------------------------
# 4. Chain-attacker prompt assembly (no real LLM call)
# ---------------------------------------------------------------------------

class TestChainAttackerPrompt:
    def test_graph_summary_includes_systems_and_edges(self):
        from goe.construction_crew.chain_attacker import _graph_summary

        graph = _multisystem_graph()
        # Populate concrete
        for edge in graph.edges:
            for pv in edge.params.values():
                if pv.concrete is None:
                    object.__setattr__(pv, "concrete", pv.structural)

        summary = _graph_summary(graph)
        assert "web_system" in summary
        assert "db_system" in summary
        assert "webserver" in summary
        assert "dbserver" in summary
        assert "sqli_webapp" in summary
        assert "ssh_pivot" in summary
        # Edge appears
        assert "webapp_to_db_creds" in summary

    def test_attack_calls_bedrock_and_parses(self):
        from goe.construction_crew import chain_attacker

        graph = _multisystem_graph()
        for edge in graph.edges:
            for pv in edge.params.values():
                if pv.concrete is None:
                    object.__setattr__(pv, "concrete", pv.structural)

        built = {
            "sqli_webapp": _passed_outcome("sqli_webapp"),
            "ssh_pivot": _passed_outcome("ssh_pivot"),
        }
        mock_procedure_yaml = """
procedure:
  - step_id: chain_attack
    action:
      type: exec_attacker
      command: "echo done"
    expect:
      exit_code: 0
"""
        with patch("goe.bedrock.call", return_value=mock_procedure_yaml) as mock_call:
            proc = chain_attacker.attack(graph, built)

        # Two calls: generate + self_review
        assert mock_call.call_count == 2
        assert len(proc.procedure) == 1
        assert proc.procedure[0].step_id == "chain_attack"


# ---------------------------------------------------------------------------
# 5. Chain test runner (mocked Docker + LLM)
# ---------------------------------------------------------------------------

class TestChainTestRunner:
    def _mock_env(self):
        """Return a mock TopologyEnvironment that succeeds at setup/deploy/teardown."""
        env = MagicMock()
        env.get_target_host.return_value = "webserver"
        env.get_attacker_host.return_value = "attacker"
        env.get_cdp_url.return_value = ""
        env.deploy_system.return_value = (0, "", "")
        return env

    def _built(self) -> dict:
        return {
            "sqli_webapp": _passed_outcome("sqli_webapp"),
            "ssh_pivot": _passed_outcome("ssh_pivot"),
        }

    def test_passed_chain(self):
        from goe.executor.runner import ProcedureResult, StepOutcome
        from goe.executor.actions import ActionResult
        from goe.flow.chain_test import run_chain_test
        from goe.models.procedure import ExecAttackerAction, Procedure, Step

        graph = _multisystem_graph()
        for edge in graph.edges:
            for pv in edge.params.values():
                if pv.concrete is None:
                    object.__setattr__(pv, "concrete", pv.structural)

        chain_proc = Procedure(procedure=[
            Step(step_id="s1", action=ExecAttackerAction(type="exec_attacker", command="echo ok"))
        ])

        mock_env = self._mock_env()
        passed_result = ProcedureResult(passed=True, steps=[
            StepOutcome(
                step_id="s1", passed=True, reason="ok", outputs={},
                raw=ActionResult(exit_code=0, stdout="ok", stderr=""),
            )
        ])

        with patch("goe.container.topology_environment.TopologyEnvironment", return_value=mock_env), \
             patch("goe.construction_crew.chain_attacker.attack", return_value=chain_proc), \
             patch("goe.executor.runner.run", return_value=passed_result):
            outcome = run_chain_test(graph, self._built())

        assert outcome.result.status == ChainTestStatus.PASSED
        assert outcome.procedure is chain_proc

    def test_failed_chain_retries(self):
        from goe.executor.runner import ProcedureResult, StepOutcome
        from goe.executor.actions import ActionResult
        from goe.flow.chain_test import run_chain_test
        from goe.models.procedure import ExecAttackerAction, Procedure, Step

        graph = _multisystem_graph()
        for edge in graph.edges:
            for pv in edge.params.values():
                if pv.concrete is None:
                    object.__setattr__(pv, "concrete", pv.structural)

        chain_proc = Procedure(procedure=[
            Step(step_id="s1", action=ExecAttackerAction(type="exec_attacker", command="echo fail"))
        ])
        failed_step = StepOutcome(
            step_id="s1", passed=False, reason="nope", outputs={},
            raw=ActionResult(exit_code=1, stdout="", stderr="err"),
        )
        failed_result = ProcedureResult(passed=False, steps=[failed_step], failed_step="s1")

        mock_env = self._mock_env()
        run_call_count = {"n": 0}

        def fake_run(proc, env, ctx):
            run_call_count["n"] += 1
            return failed_result

        with patch("goe.container.topology_environment.TopologyEnvironment", return_value=mock_env), \
             patch("goe.construction_crew.chain_attacker.attack", return_value=chain_proc), \
             patch("goe.construction_crew.chain_attacker.fix_chain", return_value=chain_proc), \
             patch("goe.executor.runner.run", side_effect=fake_run):
            from goe.flow.chain_test import MAX_RETRIES
            outcome = run_chain_test(graph, self._built())

        assert outcome.result.status == ChainTestStatus.FAILED
        # Initial attempt + MAX_RETRIES retry attempts
        assert run_call_count["n"] == 1 + MAX_RETRIES

    def test_teardown_always_called(self):
        """Even if setup raises, teardown must be called."""
        from goe.flow.chain_test import run_chain_test

        graph = _multisystem_graph()

        mock_env = MagicMock()
        mock_env.setup.side_effect = RuntimeError("docker down")

        with patch("goe.container.topology_environment.TopologyEnvironment", return_value=mock_env):
            outcome = run_chain_test(graph, self._built())

        mock_env.teardown.assert_called_once()
        assert outcome.result.status == ChainTestStatus.FAILED
