"""Unit tests for AWS deployment; no Terraform binary or AWS account required."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from goe.config import GoEConfig
from goe.deploy.aws import AwsDeploymentClient, CommandResult
from goe.deploy.lifecycle import (
    DeploymentError,
    deploy,
    destroy,
    retry_provisioning,
    status,
)
from goe.deploy.models import DeploymentState, load_manifest
from goe.deploy.spec import AwsDeploymentSpec, AwsSystemSpec, SPEC_FILENAME
from goe.deploy.terraform import TerraformRunner


def _package(tmp_path: Path) -> Path:
    out = tmp_path / "run-123"
    out.mkdir()
    (out / "deploy.sh").write_text("#!/bin/bash\necho ready\n", encoding="utf-8")
    spec = AwsDeploymentSpec(
        run_id="run-123",
        entry_system_ids=["web"],
        systems=[
            AwsSystemSpec(
                id="web",
                hostname="webserver",
                os="ubuntu_22_04",
                script="deploy.sh",
                public=True,
                exposed_ports=[80],
                internal_ports=[3306],
            )
        ],
    )
    (out / SPEC_FILENAME).write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return out


class FakeTerraform:
    instances: list["FakeTerraform"] = []
    apply_error: Exception | None = None

    def __init__(self, out_dir, *, environment):
        self.out_dir = Path(out_dir)
        self.environment = environment
        self.destroyed = False
        self.prepared = None
        type(self).instances.append(self)

    def ensure_available(self):
        pass

    def prepare(self, spec, **kwargs):
        self.prepared = (spec, kwargs)

    def init(self):
        pass

    def plan(self):
        return "Plan: 12 to add, 0 to change, 0 to destroy."

    def apply(self):
        if self.apply_error is not None:
            raise self.apply_error
        return {
            "vpc_id": "vpc-123",
            "artifact_bucket": "goe-artifacts",
            "systems": {
                "web": {
                    "instance_id": "i-123",
                    "public_ip": "198.51.100.10",
                    "private_ip": "10.0.1.10",
                    "security_group_id": "sg-123",
                    "artifact_key": "scripts/web.sh",
                }
            },
        }

    def destroy(self):
        self.destroyed = True


class FakeAws:
    provision_result = CommandResult(status="success", exit_code=0)

    def account_id(self):
        return "123456789012"

    def wait_ready(self, instances):
        return set(instances)

    def provision(self, instance_id, bucket, object_key, system_id, host_map):
        assert (instance_id, bucket, object_key, system_id, host_map) == (
            "i-123", "goe-artifacts", "scripts/web.sh", "web", {"webserver": "10.0.1.10"}
        )
        return self.provision_result

    def check_listening_ports(self, instance_id, ports, system_id):
        assert ports == [80, 3306]
        return CommandResult(status="success", exit_code=0)

    def live_status(self, instance_ids):
        return {
            "i-123": {
                "instance_state": "running",
                "public_ip": "198.51.100.10",
                "private_ip": "10.0.1.10",
                "ssm_ready": True,
            }
        }


def _aws_factory(_config, _region, _profile):
    return FakeAws()


@pytest.fixture(autouse=True)
def _clear_fakes():
    FakeTerraform.instances.clear()
    FakeTerraform.apply_error = None
    FakeAws.provision_result = CommandResult(status="success", exit_code=0)


def test_terraform_prepare_copies_scripts_and_writes_safe_tfvars(tmp_path):
    out = _package(tmp_path)
    spec = AwsDeploymentSpec.model_validate_json((out / SPEC_FILENAME).read_text())
    runner = TerraformRunner(out)

    path = runner.prepare(
        spec,
        region="us-west-2",
        instance_type="t3.small",
        attacker_cidr="203.0.113.5/32",
    )

    variables = json.loads(path.read_text())
    assert variables["systems"]["web"]["public"] is True
    assert variables["systems"]["web"]["exposed_ports"] == [80]
    staged_script = runner.work_dir / variables["systems"]["web"]["script_path"]
    assert staged_script.is_file()
    assert b"\r" not in staged_script.read_bytes()
    assert "access_key" not in path.read_text().lower()


def test_explicit_profile_clears_ambient_static_credentials(tmp_path, monkeypatch):
    from goe.deploy.lifecycle import _terraform_environment

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-secret")
    config = GoEConfig(config_path=tmp_path / "missing.toml")
    environment = _terraform_environment(config, "us-west-2", "sandbox")
    runner = TerraformRunner(tmp_path, environment=environment)

    assert runner.environment["AWS_PROFILE"] == "sandbox"
    assert "AWS_ACCESS_KEY_ID" not in runner.environment
    assert "AWS_SECRET_ACCESS_KEY" not in runner.environment


def test_ssm_provisioning_configures_graph_hostnames_before_script(monkeypatch):
    client = object.__new__(AwsDeploymentClient)
    captured = {}

    def fake_run(instance_id, commands, *, comment, timeout=900):
        captured["commands"] = commands
        return CommandResult(status="success", exit_code=0)

    monkeypatch.setattr(client, "run_command", fake_run)
    result = client.provision(
        "i-123",
        "goe-artifacts",
        "scripts/web.sh",
        "web",
        {"webserver": "10.0.1.10", "dbserver": "10.0.2.10"},
    )

    combined = "\n".join(captured["commands"])
    assert result.status == "success"
    assert "set -euo pipefail" not in combined
    assert combined.startswith("set -eu\n")
    assert "10.0.1.10 webserver" in combined
    assert "10.0.2.10 dbserver" in combined
    assert "cloud-init status --wait" in combined
    assert combined.index("cloud-init status --wait") < combined.index("aws s3 cp")
    assert combined.index("# BEGIN GOE HOSTS") < combined.index("aws s3 cp")
    assert "sed -i 's/\\r$//'" in combined
    assert combined.index("aws s3 cp") < combined.index("sed -i 's/\\r$//'")


def test_ssm_port_check_is_posix_shell_compatible(monkeypatch):
    client = object.__new__(AwsDeploymentClient)
    captured = {}

    def fake_run(instance_id, commands, *, comment, timeout=900):
        captured["script"] = commands[0]
        return CommandResult(status="success", exit_code=0)

    monkeypatch.setattr(client, "run_command", fake_run)
    result = client.check_listening_ports("i-123", [80, 3306], "web")

    assert result.status == "success"
    assert captured["script"].startswith("set -eu\n")
    assert "pipefail" not in captured["script"]


def test_deploy_success_writes_ready_inventory(tmp_path, monkeypatch):
    out = _package(tmp_path)
    monkeypatch.setattr("goe.deploy.lifecycle._wait_public_port", lambda *_: True)

    result = deploy(
        out,
        region="us-west-2",
        instance_type="t3.small",
        attacker_cidr="203.0.113.5/32",
        confirm_plan=lambda plan: "12 to add" in plan,
        config=GoEConfig(config_path=tmp_path / "missing.toml"),
        terraform_factory=FakeTerraform,
        aws_factory=_aws_factory,
    )

    assert result.state == DeploymentState.READY
    assert result.systems["web"].provision_status.value == "success"
    assert result.systems["web"].readiness == {
        "listening:80": True,
        "listening:3306": True,
        "public:80": True,
    }
    assert (out / "aws_inventory.json").is_file()
    assert load_manifest(out).state == DeploymentState.READY


def test_failure_preserves_infrastructure_by_default(tmp_path, monkeypatch):
    out = _package(tmp_path)
    FakeAws.provision_result = CommandResult(status="failed", exit_code=1, stderr="boom")
    monkeypatch.setattr("goe.deploy.lifecycle._wait_public_port", lambda *_: True)

    with pytest.raises(DeploymentError, match="provisioning failed"):
        deploy(
            out,
            region="us-west-2",
            instance_type="t3.small",
            attacker_cidr="203.0.113.5/32",
            confirm_plan=lambda _: True,
            config=GoEConfig(config_path=tmp_path / "missing.toml"),
            terraform_factory=FakeTerraform,
            aws_factory=_aws_factory,
        )

    assert FakeTerraform.instances[-1].destroyed is False
    assert load_manifest(out).state == DeploymentState.FAILED
    assert load_manifest(out).vpc_id == "vpc-123"


def test_failed_provisioning_can_retry_without_terraform_apply(tmp_path, monkeypatch):
    out = _package(tmp_path)
    config = GoEConfig(config_path=tmp_path / "missing.toml")
    FakeAws.provision_result = CommandResult(status="failed", exit_code=1, stderr="dash error")
    monkeypatch.setattr("goe.deploy.lifecycle._wait_public_port", lambda *_: True)

    with pytest.raises(DeploymentError):
        deploy(
            out,
            region="us-west-2",
            instance_type="t3.small",
            attacker_cidr="203.0.113.5/32",
            confirm_plan=lambda _: True,
            config=config,
            terraform_factory=FakeTerraform,
            aws_factory=_aws_factory,
        )
    original_terraform_count = len(FakeTerraform.instances)

    FakeAws.provision_result = CommandResult(status="success", exit_code=0)
    result = retry_provisioning(
        out,
        config=config,
        terraform_factory=FakeTerraform,
        aws_factory=_aws_factory,
    )

    assert result.state == DeploymentState.READY
    assert result.systems["web"].instance_id == "i-123"
    assert len(FakeTerraform.instances) == original_terraform_count


def test_rollback_on_failure_destroys_infrastructure(tmp_path):
    out = _package(tmp_path)
    FakeAws.provision_result = CommandResult(status="failed", exit_code=1, stderr="boom")

    with pytest.raises(DeploymentError):
        deploy(
            out,
            region="us-west-2",
            instance_type="t3.small",
            attacker_cidr="203.0.113.5/32",
            rollback_on_failure=True,
            confirm_plan=lambda _: True,
            config=GoEConfig(config_path=tmp_path / "missing.toml"),
            terraform_factory=FakeTerraform,
            aws_factory=_aws_factory,
        )

    assert FakeTerraform.instances[-1].destroyed is True
    assert load_manifest(out).state == DeploymentState.DESTROYED


def test_rollback_attempts_destroy_after_partial_terraform_apply(tmp_path):
    out = _package(tmp_path)
    FakeTerraform.apply_error = RuntimeError("partial apply")

    with pytest.raises(DeploymentError, match="partial apply"):
        deploy(
            out,
            region="us-west-2",
            instance_type="t3.small",
            attacker_cidr="203.0.113.5/32",
            rollback_on_failure=True,
            confirm_plan=lambda _: True,
            config=GoEConfig(config_path=tmp_path / "missing.toml"),
            terraform_factory=FakeTerraform,
            aws_factory=_aws_factory,
        )

    assert FakeTerraform.instances[-1].destroyed is True
    assert load_manifest(out).state == DeploymentState.DESTROYED


def test_status_and_destroy_use_local_manifest(tmp_path, monkeypatch):
    out = _package(tmp_path)
    monkeypatch.setattr("goe.deploy.lifecycle._wait_public_port", lambda *_: True)
    config = GoEConfig(config_path=tmp_path / "missing.toml")
    deploy(
        out,
        region="us-west-2",
        instance_type="t3.small",
        attacker_cidr="203.0.113.5/32",
        confirm_plan=lambda _: True,
        config=config,
        terraform_factory=FakeTerraform,
        aws_factory=_aws_factory,
    )

    manifest, live = status(out, config=config, aws_factory=_aws_factory)
    assert manifest.state == DeploymentState.READY
    assert live["i-123"]["instance_state"] == "running"

    result = destroy(out, config=config, terraform_factory=FakeTerraform)
    assert result.state == DeploymentState.DESTROYED
    assert FakeTerraform.instances[-1].destroyed is True


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "not-a-cidr", "2001:db8::1/128"])
def test_unsafe_or_unsupported_attacker_cidr_is_rejected(tmp_path, cidr):
    out = _package(tmp_path)
    with pytest.raises(DeploymentError):
        deploy(
            out,
            region="us-west-2",
            instance_type="t3.small",
            attacker_cidr=cidr,
            config=GoEConfig(config_path=tmp_path / "missing.toml"),
            terraform_factory=FakeTerraform,
            aws_factory=_aws_factory,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("id", "../../escape"), ("hostname", "bad;hostname"), ("hostname", "two.labels")],
)
def test_aws_system_identifiers_are_safe(field, value):
    values = {
        "id": "web",
        "hostname": "webserver",
        "os": "ubuntu_22_04",
        "script": "deploy.sh",
        "public": True,
        "exposed_ports": [80],
        "internal_ports": [],
    }
    values[field] = value
    with pytest.raises(ValueError):
        AwsSystemSpec(**values)
