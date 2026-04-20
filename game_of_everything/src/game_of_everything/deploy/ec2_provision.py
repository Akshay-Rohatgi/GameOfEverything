"""EC2ProvisionTool — upload and execute deploy scripts on EC2 via SSM RunCommand.

No SSH keys needed. Scripts are sent directly via SSM SendCommand, which
handles authentication through the instance's IAM role.
"""

import logging
import time
from typing import Optional

import boto3
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# SSM RunCommand has a 24-hour max timeout; we use 15 minutes for deploy scripts.
_COMMAND_TIMEOUT = 900
_POLL_INTERVAL = 10


class ProvisionResult(BaseModel):
    instance_id: str
    box_id: str
    status: str  # "success" | "failed" | "timeout"
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""


class EC2ProvisionTool:
    """Upload and execute GoE deploy scripts on EC2 instances via SSM."""

    def __init__(self, region: str = "us-east-1"):
        self.ssm = boto3.client("ssm", region_name=region)

    def _wait_ssm_ready(self, instance_id: str, timeout: int = 300) -> bool:
        """Wait until the SSM agent on the instance is online."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = self.ssm.describe_instance_information(
                    Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
                )
                instances = resp.get("InstanceInformationList", [])
                if instances and instances[0].get("PingStatus") == "Online":
                    return True
            except Exception:
                pass
            time.sleep(_POLL_INTERVAL)
        return False

    def upload_and_run(
        self,
        instance_id: str,
        box_id: str,
        script_content: str,
    ) -> ProvisionResult:
        """Send a deploy script to an EC2 instance and execute it via SSM RunCommand.

        The script is sent inline (up to 24KB via SSM). For larger scripts,
        we chunk into a heredoc-based wrapper.
        """
        if not self._wait_ssm_ready(instance_id):
            return ProvisionResult(
                instance_id=instance_id,
                box_id=box_id,
                status="failed",
                stderr="SSM agent did not become ready within timeout",
            )

        # Wrap the deploy script in a self-extracting runner
        wrapped_script = (
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            f"cat > /opt/goe_deploy.sh << 'GOE_DEPLOY_EOF'\n"
            f"{script_content}\n"
            "GOE_DEPLOY_EOF\n"
            "chmod +x /opt/goe_deploy.sh\n"
            "bash /opt/goe_deploy.sh 2>&1\n"
        )

        try:
            resp = self.ssm.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={
                    "commands": [wrapped_script],
                    "executionTimeout": [str(_COMMAND_TIMEOUT)],
                },
                Comment=f"GoE deploy: {box_id}",
                TimeoutSeconds=_COMMAND_TIMEOUT + 60,
            )
        except Exception as e:
            return ProvisionResult(
                instance_id=instance_id,
                box_id=box_id,
                status="failed",
                stderr=f"SSM SendCommand failed: {e}",
            )

        command_id = resp["Command"]["CommandId"]
        return self._wait_command(instance_id, box_id, command_id)

    def _wait_command(
        self,
        instance_id: str,
        box_id: str,
        command_id: str,
    ) -> ProvisionResult:
        """Poll SSM command until completion."""
        deadline = time.time() + _COMMAND_TIMEOUT + 120

        while time.time() < deadline:
            try:
                resp = self.ssm.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id,
                )
                status = resp["Status"]

                if status in ("Success",):
                    return ProvisionResult(
                        instance_id=instance_id,
                        box_id=box_id,
                        status="success",
                        exit_code=resp.get("ResponseCode", 0),
                        stdout=resp.get("StandardOutputContent", ""),
                        stderr=resp.get("StandardErrorContent", ""),
                    )
                elif status in ("Failed", "Cancelled", "TimedOut"):
                    return ProvisionResult(
                        instance_id=instance_id,
                        box_id=box_id,
                        status="failed",
                        exit_code=resp.get("ResponseCode"),
                        stdout=resp.get("StandardOutputContent", ""),
                        stderr=resp.get("StandardErrorContent", ""),
                    )
                # InProgress, Pending, Delayed — keep waiting
            except self.ssm.exceptions.InvocationDoesNotExist:
                pass  # Command not registered yet

            time.sleep(_POLL_INTERVAL)

        return ProvisionResult(
            instance_id=instance_id,
            box_id=box_id,
            status="timeout",
            stderr=f"Command {command_id} did not complete within timeout",
        )
