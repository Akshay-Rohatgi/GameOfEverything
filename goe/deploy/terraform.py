"""Terraform subprocess boundary for AWS scenario infrastructure."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from goe.deploy.spec import AwsDeploymentSpec


TERRAFORM_TEMPLATE_DIR = Path(__file__).with_name("terraform")


class TerraformError(RuntimeError):
    """A Terraform command failed or returned unusable output."""


class TerraformRunner:
    def __init__(
        self,
        out_dir: Path,
        *,
        environment: dict[str, str | None] | None = None,
        executable: str = "terraform",
        timeout: int = 900,
    ) -> None:
        self.out_dir = Path(out_dir).resolve()
        self.work_dir = self.out_dir / ".aws" / "terraform"
        self.executable = executable
        self.timeout = timeout
        self.environment = os.environ.copy()
        if environment:
            for key, value in environment.items():
                if value:
                    self.environment[key] = value
                else:
                    self.environment.pop(key, None)

    def ensure_available(self) -> None:
        if shutil.which(self.executable) is None:
            raise TerraformError(
                "Terraform executable not found. Install Terraform 1.5 or later and ensure "
                "'terraform' is on PATH."
            )

    def prepare(
        self,
        spec: AwsDeploymentSpec,
        *,
        region: str,
        instance_type: str,
        attacker_cidr: str,
    ) -> Path:
        if not TERRAFORM_TEMPLATE_DIR.is_dir():
            raise TerraformError(f"packaged Terraform templates not found: {TERRAFORM_TEMPLATE_DIR}")

        self.work_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(TERRAFORM_TEMPLATE_DIR, self.work_dir, dirs_exist_ok=True)

        scripts_dir = self.work_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        systems: dict[str, dict[str, Any]] = {}
        for system in spec.systems:
            source = self.out_dir / system.script
            if not source.is_file():
                raise TerraformError(f"deployment script missing for system {system.id}: {source}")
            destination = scripts_dir / f"{system.id}.sh"
            script = source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            destination.write_bytes(script)
            systems[system.id] = {
                "hostname": system.hostname,
                "public": system.public,
                "exposed_ports": system.exposed_ports,
                "internal_ports": system.internal_ports,
                # Keep state portable when the whole output package is moved.
                "script_path": f"scripts/{system.id}.sh",
            }

        tfvars = {
            "aws_region": region,
            "run_id": spec.run_id,
            "instance_type": instance_type,
            "attacker_cidr": attacker_cidr,
            "vpc_cidr": "10.0.0.0/16",
            "systems": systems,
        }
        path = self.work_dir / "terraform.tfvars.json"
        path.write_text(json.dumps(tfvars, indent=2) + "\n", encoding="utf-8")
        return path

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        command = [self.executable, *args]
        try:
            result = subprocess.run(
                command,
                cwd=self.work_dir,
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TerraformError(f"Terraform timed out: {' '.join(command)}") from exc
        except OSError as exc:
            raise TerraformError(f"Unable to run Terraform: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise TerraformError(f"Terraform command failed ({' '.join(command)}):\n{detail}")
        return result

    def init(self) -> None:
        self._run(["init", "-input=false"])

    def plan(self) -> str:
        result = self._run([
            "plan",
            "-input=false",
            "-var-file=terraform.tfvars.json",
            "-out=tfplan",
        ])
        return result.stdout

    def apply(self) -> dict[str, Any]:
        self._run(["apply", "-input=false", "-auto-approve", "tfplan"])
        return self.outputs()

    def outputs(self) -> dict[str, Any]:
        result = self._run(["output", "-json"])
        try:
            raw = json.loads(result.stdout)
            return {key: value["value"] for key, value in raw.items()}
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise TerraformError("Terraform returned invalid JSON outputs") from exc

    def destroy(self) -> None:
        args = ["destroy", "-input=false", "-auto-approve"]
        if (self.work_dir / "terraform.tfvars.json").is_file():
            args.append("-var-file=terraform.tfvars.json")
        self._run(args)
