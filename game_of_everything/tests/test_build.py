"""Phase 1 E2E tests — construction crew + deploy + L2 executor.

Requires: Docker daemon + AWS credentials in goe.toml.
Run with: pytest tests/test_build.py -v -m "llm and docker"
"""

import pytest
import yaml

pytestmark = [pytest.mark.llm, pytest.mark.docker]


def load_entity(name: str):
    from pathlib import Path
    from goe.models.entity import Entity
    path = Path(__file__).parent / "fixtures" / "entities" / f"{name}.yaml"
    return Entity.model_validate(yaml.safe_load(path.read_text()))


class TestSingleEntityBuild:
    def test_sqli_express(self):
        """Engineer → Developer → Attacker generates a working SQLi exploit."""
        from goe.build import build_entity
        from goe.models.report import EntityStatus

        entity = load_entity("sqli_express")
        result = build_entity(entity, scope="test_sqli", verbose=True)

        assert result.status == EntityStatus.PASSED, (
            f"Build failed after {result.attempts} attempts: {result.failure_reason}"
        )

    def test_cmdi_flask(self):
        """Full crew generates a working command injection exploit for Flask."""
        from goe.build import build_entity
        from goe.models.report import EntityStatus

        entity = load_entity("cmdi_flask")
        result = build_entity(entity, scope="test_cmdi", verbose=True)

        assert result.status == EntityStatus.PASSED, (
            f"Build failed after {result.attempts} attempts: {result.failure_reason}"
        )


class TestConstructionCrewUnit:
    """Smoke tests that verify crew components parse their inputs correctly.
    These still call the LLM but don't require Docker.
    """

    @pytest.mark.llm
    def test_engineer_returns_plan(self):
        from goe.construction_crew.engineer import plan
        from goe.models.entity import Entity, AppSpec

        entity = Entity(
            id="test",
            description="Test SQLi app",
            system_id="sys",
            requires=[],
            provides=[],
            app_spec=AppSpec(runtime="express", vulnerabilities=["sqli_union"], goal="extract creds"),
            atoms=["sqli_union"],
        )
        eng_plan = plan(entity, incoming_edges={})
        assert eng_plan.runtime in ("express", "flask", "apache_php")
        assert eng_plan.attack_entry_point
        assert eng_plan.success_indicator

    @pytest.mark.llm
    def test_attacker_returns_procedure(self):
        from goe.construction_crew.engineer import plan, EngineerPlan
        from goe.construction_crew.attacker import attack
        from goe.models.entity import Entity, AppSpec
        from goe.models.artifacts import BuildArtifact

        entity = Entity(
            id="test",
            description="SQLi app",
            system_id="sys",
            requires=[],
            provides=[],
            app_spec=AppSpec(runtime="express", vulnerabilities=["sqli_union"], goal="extract creds"),
            atoms=["sqli_union"],
        )
        eng_plan = plan(entity, incoming_edges={})

        # Minimal artifact — just enough for the attacker to work with
        artifact = BuildArtifact(
            source_files={"app.js": "// stub"},
            primary_source="app.js",
            port=3000,
        )
        procedure = attack(entity, eng_plan, artifact, outgoing_values={})
        assert procedure.procedure
        assert len(procedure.procedure) >= 1
