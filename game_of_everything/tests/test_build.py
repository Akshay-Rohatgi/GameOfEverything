"""Phase 1 E2E tests — construction crew + deploy + L2 executor.

Requires: Docker daemon + AWS credentials in goe.toml.
Run with: pytest tests/test_build.py -v -m "llm and docker"

To run a single fixture:
    pytest tests/test_build.py -v -m "llm and docker" -k "sqli_express"
"""

import pytest
import yaml

pytestmark = [pytest.mark.llm, pytest.mark.docker]


def load_entity(name: str):
    from pathlib import Path
    from goe.models.entity import Entity
    path = Path(__file__).parent / "fixtures" / "entities" / f"{name}.yaml"
    return Entity.model_validate(yaml.safe_load(path.read_text()))


def _run(fixture_name: str):
    from goe.build import build_entity
    from goe.models.report import EntityStatus

    entity = load_entity(fixture_name)
    result = build_entity(entity, scope=f"test_{fixture_name[:20]}", verbose=True)
    assert result.status == EntityStatus.PASSED, (
        f"Build failed after {result.attempts} attempts: {result.failure_reason}"
    )


# ---------------------------------------------------------------------------
# SQL Injection
# ---------------------------------------------------------------------------

class TestSQLi:
    def test_sqli_express(self):
        _run("sqli_express")

    def test_sqli_flask(self):
        _run("sqli_flask")

    def test_sqli_php(self):
        _run("sqli_php")


# ---------------------------------------------------------------------------
# Command Injection
# ---------------------------------------------------------------------------

class TestCMDi:
    def test_cmdi_express(self):
        _run("cmdi_express")

    def test_cmdi_flask(self):
        _run("cmdi_flask")

    def test_cmdi_php(self):
        _run("cmdi_php")


# ---------------------------------------------------------------------------
# Stored XSS
# ---------------------------------------------------------------------------

class TestXSSStored:
    def test_xss_stored_express(self):
        _run("xss_express")

    def test_xss_stored_flask(self):
        _run("xss_stored_flask")

    def test_xss_stored_php(self):
        _run("xss_stored_php")


# ---------------------------------------------------------------------------
# Reflected XSS
# ---------------------------------------------------------------------------

class TestXSSReflected:
    def test_xss_reflected_express(self):
        _run("xss_reflected_express")

    def test_xss_reflected_flask(self):
        _run("xss_reflected_flask")


# ---------------------------------------------------------------------------
# Path Traversal
# ---------------------------------------------------------------------------

class TestPathTraversal:
    def test_path_traversal_express(self):
        _run("path_traversal_express")

    def test_path_traversal_flask(self):
        _run("path_traversal_flask")


# ---------------------------------------------------------------------------
# SSTI
# ---------------------------------------------------------------------------

class TestSSTI:
    def test_ssti_flask(self):
        _run("ssti_flask")


# ---------------------------------------------------------------------------
# File Upload
# ---------------------------------------------------------------------------

class TestFileUpload:
    def test_file_upload_php(self):
        _run("file_upload_php")


# ---------------------------------------------------------------------------
# Insecure Deserialization
# ---------------------------------------------------------------------------

class TestDeserialization:
    def test_insecure_deserialization_flask(self):
        _run("insecure_deserialization_flask")


# ---------------------------------------------------------------------------
# XSS Admin Bot (browser-based exfil)
# ---------------------------------------------------------------------------

class TestXSSAdminBot:
    def test_xss_admin_bot_express(self):
        _run("xss_admin_bot_express")


# ---------------------------------------------------------------------------
# Construction crew unit tests (LLM only, no Docker)
# ---------------------------------------------------------------------------

class TestConstructionCrewUnit:
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
        from goe.construction_crew.engineer import plan
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

        artifact = BuildArtifact(
            source_files={"app.js": "// stub"},
            primary_source="app.js",
            port=3000,
        )
        procedure = attack(entity, eng_plan, artifact, outgoing_values={})
        assert procedure.procedure
        assert len(procedure.procedure) >= 1
