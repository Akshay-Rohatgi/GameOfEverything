"""EC2StatusTool — poll EC2 instance and SSM agent readiness."""

import logging
import time
from typing import Optional

import boto3
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 10


class BoxStatus(BaseModel):
    box_id: str
    instance_id: str
    instance_state: str  # "pending" | "running" | "stopped" | etc.
    ssm_ready: bool
    public_ip: Optional[str] = None


class StatusResult(BaseModel):
    all_ready: bool
    boxes: list[BoxStatus]


class EC2StatusTool:
    """Check readiness of all EC2 instances in a GoE deployment."""

    def __init__(self, region: str = "us-east-1"):
        self.ec2 = boto3.client("ec2", region_name=region)
        self.ssm = boto3.client("ssm", region_name=region)

    def check(self, instance_map: dict[str, str]) -> StatusResult:
        """One-shot status check.

        Args:
            instance_map: {box_id: instance_id}

        Returns:
            StatusResult with per-box status.
        """
        instance_ids = list(instance_map.values())
        if not instance_ids:
            return StatusResult(all_ready=True, boxes=[])

        # EC2 instance state
        ec2_resp = self.ec2.describe_instances(InstanceIds=instance_ids)
        ec2_states = {}
        ec2_ips = {}
        for reservation in ec2_resp["Reservations"]:
            for inst in reservation["Instances"]:
                ec2_states[inst["InstanceId"]] = inst["State"]["Name"]
                ec2_ips[inst["InstanceId"]] = inst.get("PublicIpAddress")

        # SSM agent status
        ssm_resp = self.ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": instance_ids}]
        )
        ssm_online = {
            info["InstanceId"]
            for info in ssm_resp.get("InstanceInformationList", [])
            if info.get("PingStatus") == "Online"
        }

        boxes = []
        for box_id, iid in instance_map.items():
            boxes.append(BoxStatus(
                box_id=box_id,
                instance_id=iid,
                instance_state=ec2_states.get(iid, "unknown"),
                ssm_ready=iid in ssm_online,
                public_ip=ec2_ips.get(iid),
            ))

        all_ready = all(b.instance_state == "running" and b.ssm_ready for b in boxes)
        return StatusResult(all_ready=all_ready, boxes=boxes)

    def wait_ready(
        self,
        instance_map: dict[str, str],
        timeout: int = 600,
    ) -> StatusResult:
        """Poll until all instances are running and SSM agents are online.

        Args:
            instance_map: {box_id: instance_id}
            timeout: Maximum seconds to wait.
        """
        deadline = time.time() + timeout

        while time.time() < deadline:
            result = self.check(instance_map)
            if result.all_ready:
                logger.info("All %d boxes ready", len(result.boxes))
                return result

            not_ready = [b for b in result.boxes if not (b.instance_state == "running" and b.ssm_ready)]
            logger.info(
                "Waiting for %d/%d boxes: %s",
                len(not_ready),
                len(result.boxes),
                ", ".join(f"{b.box_id}({b.instance_state})" for b in not_ready),
            )
            time.sleep(_POLL_INTERVAL)

        # Final check
        return self.check(instance_map)
