"""Write ec2_inventory.json and teardown.sh to the output directory."""

import json
from pathlib import Path

from game_of_everything.deploy.ec2_deploy import DeployResult
from game_of_everything.deploy.ec2_provision import ProvisionResult


def write_inventory(
    output_dir: Path,
    deploy_result: DeployResult,
    provision_results: list[ProvisionResult],
    run_id: str,
) -> Path:
    """Write ec2_inventory.json with all deployment details."""
    inventory = {
        "run_id": run_id,
        "vpc_id": deploy_result.vpc_id,
        "state_dir": deploy_result.state_dir,
        "boxes": {},
    }

    provision_map = {p.box_id: p for p in provision_results}

    for box in deploy_result.boxes:
        prov = provision_map.get(box.box_id)
        inventory["boxes"][box.box_id] = {
            "instance_id": box.instance_id,
            "public_ip": box.public_ip,
            "private_ip": box.private_ip,
            "security_group_id": box.security_group_id,
            "provision_status": prov.status if prov else "unknown",
        }

    path = output_dir / "ec2_inventory.json"
    path.write_text(json.dumps(inventory, indent=2))

    # Write teardown helper
    teardown = output_dir / "teardown.sh"
    teardown.write_text(
        "#!/bin/bash\n"
        "# Destroy all AWS resources for this scenario\n"
        f'cd "{deploy_result.state_dir}/terraform"\n'
        "terraform destroy -auto-approve -var-file=terraform.tfvars.json\n"
    )
    teardown.chmod(0o755)

    return path
