"""One-click EC2 deployment for validated GoE deploy scripts.

Launches an Ubuntu 22.04 instance, passes the deploy script as user_data
(runs as root on first boot), and returns the public IP.
"""

import base64
import gzip
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import boto3

from game_of_everything.config import GoEConfig

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole

# user_data raw limit is 16 KB
_USER_DATA_MAX_BYTES = 16_384

# Ports to open in the auto-created security group
_CHALLENGE_PORTS = [
    (22, "SSH"),
    (21, "FTP"),
    (80, "HTTP"),
    (443, "HTTPS"),
    (445, "SMB"),
    (3306, "MySQL/MariaDB"),
    (5432, "PostgreSQL"),
    (6379, "Redis"),
    (27017, "MongoDB"),
]


def _find_ubuntu_ami(ec2_client, region: str) -> str:
    """Look up the latest Ubuntu 22.04 amd64 AMI for the given region."""
    response = ec2_client.describe_images(
        Owners=["099720109477"],  # Canonical
        Filters=[
            {"Name": "name", "Values": ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]},
            {"Name": "state", "Values": ["available"]},
            {"Name": "architecture", "Values": ["x86_64"]},
        ],
    )
    images = response.get("Images", [])
    if not images:
        raise RuntimeError(f"No Ubuntu 22.04 AMI found in {region}")
    # Sort by creation date descending, pick the newest
    images.sort(key=lambda img: img.get("CreationDate", ""), reverse=True)
    return images[0]["ImageId"]


def _create_security_group(ec2_client, vpc_id: str, timestamp: str) -> str:
    """Create a security group with common challenge ports open."""
    sg_name = f"goe-{timestamp}"
    sg = ec2_client.create_security_group(
        GroupName=sg_name,
        Description=f"Game of Everything challenge ports ({timestamp})",
        VpcId=vpc_id,
    )
    sg_id = sg["GroupId"]

    # Build ingress rules for all challenge ports
    ip_permissions = [
        {
            "IpProtocol": "tcp",
            "FromPort": port,
            "ToPort": port,
            "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": desc}],
        }
        for port, desc in _CHALLENGE_PORTS
    ]

    ec2_client.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=ip_permissions,
    )

    return sg_id


def _prepare_user_data(script_content: str) -> str:
    """Prepare user_data string from the deploy script.

    If the script exceeds the 16 KB user_data limit, compress it with gzip
    and wrap in a small bootstrap that decompresses and executes.
    """
    raw_bytes = script_content.encode("utf-8")

    if len(raw_bytes) <= _USER_DATA_MAX_BYTES:
        return script_content

    # Compress and wrap in a self-extracting bootstrap
    compressed = gzip.compress(raw_bytes, compresslevel=9)
    b64_compressed = base64.b64encode(compressed).decode("ascii")

    bootstrap = f"""#!/bin/bash
set -e
echo '{b64_compressed}' | base64 -d | gunzip > /tmp/goe_deploy.sh
chmod +x /tmp/goe_deploy.sh
/tmp/goe_deploy.sh
rm -f /tmp/goe_deploy.sh
"""

    bootstrap_bytes = bootstrap.encode("utf-8")
    if len(bootstrap_bytes) > _USER_DATA_MAX_BYTES:
        raise RuntimeError(
            f"Deploy script too large for EC2 user_data even after compression "
            f"({len(bootstrap_bytes)} bytes, limit {_USER_DATA_MAX_BYTES}). "
            f"Deploy manually with: scp {script_content!r} ubuntu@<ip>:/tmp/ && ssh ubuntu@<ip> sudo /tmp/deploy.sh"
        )

    return bootstrap


def deploy_to_ec2(
    script_path: Path,
    config: GoEConfig,
    ui: Optional["GoEConsole"] = None,
) -> str:
    """Deploy the script to a new EC2 instance. Returns the public IP address."""
    script_content = script_path.read_text(encoding="utf-8")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    def _log(msg: str) -> None:
        if ui:
            ui.log(msg)

    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
        region_name=config.aws_region,
    )

    # 1. Find AMI
    _log("Looking up latest Ubuntu 22.04 AMI...")
    ami_id = _find_ubuntu_ami(ec2, config.aws_region)
    _log(f"  AMI: {ami_id}")

    # 2. Security group
    sg_id = config.deploy_security_group_id
    if not sg_id:
        _log("Creating security group with challenge ports...")
        # Get default VPC
        vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
        if not vpcs["Vpcs"]:
            raise RuntimeError("No default VPC found. Set deploy.security_group_id in goe.toml.")
        vpc_id = vpcs["Vpcs"][0]["VpcId"]
        sg_id = _create_security_group(ec2, vpc_id, timestamp)
        _log(f"  Security group: {sg_id}")

    # 3. Prepare user_data
    _log("Preparing user_data...")
    user_data = _prepare_user_data(script_content)
    _log(f"  Script size: {len(script_content)} bytes")

    # 4. Launch instance
    _log(f"Launching {config.deploy_instance_type} instance...")
    run_kwargs = {
        "ImageId": ami_id,
        "InstanceType": config.deploy_instance_type,
        "MinCount": 1,
        "MaxCount": 1,
        "UserData": user_data,
        "SecurityGroupIds": [sg_id],
        "TagSpecifications": [
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": f"GoE-{timestamp}"},
                    {"Key": "CreatedBy", "Value": "game-of-everything"},
                ],
            }
        ],
    }

    if config.deploy_key_pair_name:
        run_kwargs["KeyName"] = config.deploy_key_pair_name

    if config.deploy_subnet_id:
        run_kwargs["SubnetId"] = config.deploy_subnet_id

    response = ec2.run_instances(**run_kwargs)
    instance_id = response["Instances"][0]["InstanceId"]
    _log(f"  Instance: {instance_id}")

    # 5. Wait for running state
    _log("Waiting for instance to reach running state...")
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])
    _log("  Instance is running.")

    # 6. Get public IP
    desc = ec2.describe_instances(InstanceIds=[instance_id])
    public_ip = desc["Reservations"][0]["Instances"][0].get("PublicIpAddress", "")

    if not public_ip:
        _log("  WARNING: No public IP assigned. Check subnet/VPC settings.")
        return instance_id

    _log(f"  Public IP: {public_ip}")
    _log(f"  The deploy script is running via user_data (cloud-init).")
    _log(f"  It may take a few minutes for all services to be ready.")

    return public_ip
