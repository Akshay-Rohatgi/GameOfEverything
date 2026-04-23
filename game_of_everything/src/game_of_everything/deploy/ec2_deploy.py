"""EC2DeployTool — deterministic translation of GoE topology to Terraform vars.

No LLM decisions happen here. The tool:
  1. Renders terraform.tfvars from a NetworkTopology model
  2. Runs `terraform init` + `terraform plan`
  3. Runs `terraform apply -auto-approve`
  4. Parses outputs into a structured DeployResult
  5. Supports `destroy` for full cleanup

All AWS-specific logic is in the Terraform modules. This tool is a thin
subprocess wrapper that ensures the agent cannot hallucinate infrastructure.
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from game_of_everything.models import NetworkTopology

logger = logging.getLogger(__name__)

_TERRAFORM_DIR = Path(__file__).parent.parent.parent.parent / "terraform"
_STATE_BASE = Path(__file__).parent.parent.parent.parent / "output" / ".tfstate"


class BoxDeployInfo(BaseModel):
    box_id: str
    instance_id: str
    public_ip: Optional[str] = None
    private_ip: str
    security_group_id: str


class DeployResult(BaseModel):
    vpc_id: str
    boxes: list[BoxDeployInfo]
    state_dir: str


class EC2DeployTool:
    """Deterministic infrastructure provisioner — no LLM involvement."""

    def __init__(
        self,
        region: str = "us-east-1",
        instance_type: str = "t3.small",
        attacker_cidr: Optional[str] = None,
        ttl_hours: int = 4,
    ):
        self.region = region
        self.instance_type = instance_type
        self.attacker_cidr = attacker_cidr or os.environ.get("GOE_ATTACKER_CIDR")
        self.ttl_hours = ttl_hours

        if not self.attacker_cidr:
            raise ValueError(
                "attacker_cidr is required. Set GOE_ATTACKER_CIDR or pass explicitly. "
                "Use your public IP with /32 suffix (e.g. '203.0.113.5/32')."
            )

    def _state_dir(self, run_id: str) -> Path:
        d = _STATE_BASE / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _run_tf(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
        """Run a terraform command, raising on failure."""
        cmd = ["terraform"] + args
        logger.info("Running: %s (cwd=%s)", " ".join(cmd), cwd)
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            logger.error("Terraform failed:\nSTDOUT: %s\nSTDERR: %s", result.stdout, result.stderr)
            raise RuntimeError(f"Terraform command failed: {' '.join(cmd)}\n{result.stderr}")
        return result

    def _render_tfvars(self, topology: NetworkTopology, run_id: str, state_dir: Path) -> Path:
        """Render terraform.tfvars from the topology model."""
        boxes = []
        for box in topology.boxes:
            # Determine if box should be public (entry points are public)
            is_public = box.box_id in topology.entry_point
            boxes.append({
                "box_id": box.box_id,
                "hostname": box.hostname,
                "role": box.role,
                "services": box.services,
                "public": is_public,
            })

        tfvars = {
            "aws_region": self.region,
            "run_id": run_id,
            "scenario_name": topology.scenario_name,
            "attacker_cidr": self.attacker_cidr,
            "instance_type": self.instance_type,
            "vpc_cidr": "10.0.0.0/16",
            "ttl_hours": self.ttl_hours,
            "boxes": boxes,
        }

        tfvars_path = state_dir / "terraform.tfvars.json"
        tfvars_path.write_text(json.dumps(tfvars, indent=2))
        return tfvars_path

    def plan(self, topology: NetworkTopology, run_id: str) -> dict:
        """Render tfvars and run terraform plan. Returns plan summary."""
        state_dir = self._state_dir(run_id)
        self._render_tfvars(topology, run_id, state_dir)

        # Copy terraform modules to state dir for isolation
        work_dir = state_dir / "terraform"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        shutil.copytree(_TERRAFORM_DIR, work_dir)

        # Copy tfvars into the working directory
        shutil.copy2(state_dir / "terraform.tfvars.json", work_dir / "terraform.tfvars.json")

        self._run_tf(["init", "-input=false"], cwd=work_dir)
        result = self._run_tf(
            ["plan", "-var-file=terraform.tfvars.json", "-out=tfplan", "-input=false"],
            cwd=work_dir,
        )

        return {
            "state_dir": str(state_dir),
            "work_dir": str(work_dir),
            "plan_output": result.stdout,
            "box_count": len(topology.boxes),
        }

    def apply(self, run_id: str) -> DeployResult:
        """Apply the planned infrastructure. Must call plan() first."""
        state_dir = self._state_dir(run_id)
        work_dir = state_dir / "terraform"

        if not (work_dir / "tfplan").exists():
            raise RuntimeError("No plan found. Call plan() first.")

        self._run_tf(["apply", "-auto-approve", "tfplan"], cwd=work_dir)

        # Parse outputs
        result = self._run_tf(["output", "-json"], cwd=work_dir)
        outputs = json.loads(result.stdout)

        vpc_id = outputs["vpc_id"]["value"]
        boxes_raw = outputs["boxes"]["value"]

        boxes = []
        for box_id, info in boxes_raw.items():
            boxes.append(BoxDeployInfo(
                box_id=box_id,
                instance_id=info["instance_id"],
                public_ip=info.get("public_ip"),
                private_ip=info["private_ip"],
                security_group_id=info["sg_id"],
            ))

        return DeployResult(
            vpc_id=vpc_id,
            boxes=boxes,
            state_dir=str(state_dir),
        )

    def destroy(self, run_id: str) -> None:
        """Destroy all infrastructure for a given run."""
        state_dir = self._state_dir(run_id)
        work_dir = state_dir / "terraform"

        if not work_dir.exists():
            raise RuntimeError(f"No terraform state found for run {run_id}")

        tfvars_path = work_dir / "terraform.tfvars.json"
        var_file_args = ["-var-file=terraform.tfvars.json"] if tfvars_path.exists() else []

        self._run_tf(
            ["destroy", "-auto-approve"] + var_file_args,
            cwd=work_dir,
        )
        logger.info("Destroyed infrastructure for run %s", run_id)
