"""Unit tests for persistent local Docker deployment (no daemon required)."""

from __future__ import annotations

import json

import pytest

from goe.deploy.docker import (
    DockerDeploymentError,
    DockerDeploymentState,
    default_project_name,
    deploy,
    destroy,
    load_manifest,
    status,
)


class FakeComposeRunner:
    instances = []
    fail_up = False
    rows = [
        {
            "Service": "web",
            "Name": "project-web-1",
            "State": "running",
            "Health": "healthy",
        }
    ]

    def __init__(self, compose_file, project_name):
        self.compose_file = compose_file
        self.project_name = project_name
        self.checked = False
        self.started = False
        self.removed = False
        type(self).instances.append(self)

    def check_available(self):
        self.checked = True

    def up(self, timeout):
        self.started = True
        self.timeout = timeout
        if type(self).fail_up:
            raise DockerDeploymentError("provisioning failed")

    def status(self):
        return list(type(self).rows)

    def down(self):
        self.removed = True


@pytest.fixture(autouse=True)
def _reset_runner():
    FakeComposeRunner.instances = []
    FakeComposeRunner.fail_up = False
    FakeComposeRunner.rows = [
        {
            "Service": "web",
            "Name": "project-web-1",
            "State": "running",
            "Health": "healthy",
        }
    ]


def _package(tmp_path):
    out = tmp_path / "Run With Spaces"
    out.mkdir()
    (out / "docker-compose.yml").write_text(
        "services:\n  web:\n    image: ubuntu:22.04\n", encoding="utf-8"
    )
    return out


def test_deploy_waits_for_compose_and_persists_ready_manifest(tmp_path):
    out = _package(tmp_path)

    manifest = deploy(out, timeout=42, runner_factory=FakeComposeRunner)

    runner = FakeComposeRunner.instances[-1]
    assert runner.checked is True
    assert runner.started is True
    assert runner.timeout == 42
    assert manifest.state == DockerDeploymentState.READY
    assert manifest.project_name == default_project_name(out.name)
    assert manifest.services["web"]["Health"] == "healthy"
    assert load_manifest(out).state == DockerDeploymentState.READY


def test_deploy_rejects_an_active_manifest(tmp_path):
    out = _package(tmp_path)
    deploy(out, runner_factory=FakeComposeRunner)

    with pytest.raises(DockerDeploymentError, match="already has a Docker deployment"):
        deploy(out, runner_factory=FakeComposeRunner)


def test_failed_deploy_records_partial_state_for_cleanup(tmp_path):
    out = _package(tmp_path)
    FakeComposeRunner.fail_up = True

    with pytest.raises(DockerDeploymentError, match="provisioning failed"):
        deploy(out, runner_factory=FakeComposeRunner)

    manifest = load_manifest(out)
    assert manifest.state == DockerDeploymentState.FAILED
    assert manifest.error == "provisioning failed"
    assert manifest.services["web"]["State"] == "running"


def test_status_and_destroy_use_saved_compose_project(tmp_path):
    out = _package(tmp_path)
    deployed = deploy(
        out, project_name="goe-local-test", runner_factory=FakeComposeRunner
    )

    manifest, rows = status(out, runner_factory=FakeComposeRunner)
    assert manifest.project_name == deployed.project_name
    assert rows[0]["Service"] == "web"

    result = destroy(out, runner_factory=FakeComposeRunner)
    assert FakeComposeRunner.instances[-1].removed is True
    assert result.state == DockerDeploymentState.DESTROYED


@pytest.mark.parametrize("name", ["Uppercase", "-starts-with-dash", "bad.name"])
def test_invalid_project_names_are_rejected(tmp_path, name):
    out = _package(tmp_path)
    with pytest.raises(DockerDeploymentError, match="project name"):
        deploy(out, project_name=name, runner_factory=FakeComposeRunner)


def test_manifest_is_valid_json(tmp_path):
    out = _package(tmp_path)
    deploy(out, runner_factory=FakeComposeRunner)
    payload = json.loads((out / ".docker" / "manifest.json").read_text())
    assert payload["provider"] == "docker"
