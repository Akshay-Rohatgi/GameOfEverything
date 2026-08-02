"""Unit tests for goe/runtimes/ — no Docker, no LLM."""

import pytest
from goe.models.artifacts import BuildArtifact, DBSetup
from goe.runtimes.registry import RuntimeRegistry


@pytest.fixture
def registry():
    return RuntimeRegistry()


def minimal_artifact(primary: str = "app.js") -> BuildArtifact:
    return BuildArtifact(
        source_files={primary: "// hello"},
        primary_source=primary,
        port=3000,
    )


class TestRegistryLoading:
    def test_available_runtimes(self, registry):
        runtimes = registry.available_runtimes()
        assert "express" in runtimes
        assert "flask" in runtimes
        assert "apache_php" in runtimes

    def test_unknown_runtime_raises(self, registry):
        with pytest.raises(ValueError, match="Unknown runtime"):
            registry.get_template("nonexistent")

    def test_port_for_express(self, registry):
        assert registry.port_for("express") == 3000

    def test_port_for_flask(self, registry):
        assert registry.port_for("flask") == 5000

    def test_port_for_apache_php(self, registry):
        assert registry.port_for("apache_php") == 80


class TestExpressDeploy:
    def test_contains_nodesource(self, registry):
        script = registry.deploy("express", minimal_artifact("app.js"))
        assert "nodesource" in script

    def test_contains_npm_install(self, registry):
        script = registry.deploy("express", minimal_artifact("app.js"))
        assert "npm install" in script

    def test_contains_source_file(self, registry):
        artifact = BuildArtifact(
            source_files={"app.js": "const x = 1;"},
            primary_source="app.js",
            port=3000,
        )
        script = registry.deploy("express", artifact)
        # File is written via base64 — check the dest path
        assert "/opt/webapp/app.js" in script

    def test_contains_nohup_start(self, registry):
        script = registry.deploy("express", minimal_artifact("app.js"))
        assert "nohup" in script
        assert "node app.js" in script

    def test_extra_deps_included(self, registry):
        artifact = BuildArtifact(
            source_files={"app.js": ""},
            primary_source="app.js",
            port=3000,
            extra_deps=["mysql2", "cookie-parser"],
        )
        script = registry.deploy("express", artifact)
        assert "mysql2" in script
        assert "cookie-parser" in script

    def test_sqlite_db_setup(self, registry):
        artifact = BuildArtifact(
            source_files={"app.js": ""},
            primary_source="app.js",
            port=3000,
            db_setup=DBSetup(
                db_type="sqlite3",
                schema_sql="CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);",
                seed_sql="INSERT INTO users VALUES (1, 'admin');",
            ),
        )
        script = registry.deploy("express", artifact)
        assert "sqlite3" in script
        assert "schema.sql" in script


class TestFlaskDeploy:
    def test_contains_pip_flask(self, registry):
        artifact = BuildArtifact(
            source_files={"app.py": "from flask import Flask"},
            primary_source="app.py",
            port=5000,
        )
        script = registry.deploy("flask", artifact)
        assert "pip3 install flask" in script

    def test_contains_python_start(self, registry):
        artifact = BuildArtifact(
            source_files={"app.py": ""},
            primary_source="app.py",
            port=5000,
        )
        script = registry.deploy("flask", artifact)
        assert "python3 app.py" in script


class TestApachePhpDeploy:
    def test_contains_apache(self, registry):
        artifact = BuildArtifact(
            source_files={"app.php": "<?php echo 'hi'; ?>"},
            primary_source="app.php",
            port=80,
        )
        script = registry.deploy("apache_php", artifact)
        assert "apache2" in script

    def test_app_dir_is_www(self, registry):
        artifact = BuildArtifact(
            source_files={"app.php": "<?php echo 'hi'; ?>"},
            primary_source="app.php",
            port=80,
            app_dir="/var/www/html",
        )
        script = registry.deploy("apache_php", artifact)
        assert "/var/www/html" in script


class TestScriptStructure:
    def test_shebang_and_set_e(self, registry):
        script = registry.deploy("express", minimal_artifact())
        assert script.startswith("#!/bin/bash")
        assert "set -e" in script

    def test_deploy_ok_marker(self, registry):
        script = registry.deploy("express", minimal_artifact())
        assert "deploy_ok" in script
