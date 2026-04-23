"""Step: Deploy finalized topology to AWS EC2.

This step is opt-in (--deploy ec2). It runs after finalize_topology and:
  1. Translates the GoE NetworkTopology into Terraform variables (deterministic)
  2. Provisions VPC + EC2 instances via Terraform
  3. Uploads and executes deploy scripts via SSM RunCommand
  4. Writes ec2_inventory.json to the output directory

No LLM involvement — all AWS logic is in Terraform modules and Python tools.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import rich

from game_of_everything.state import GoEState
from game_of_everything.deploy.ec2_deploy import EC2DeployTool
from game_of_everything.deploy.ec2_provision import EC2ProvisionTool
from game_of_everything.deploy.ec2_status import EC2StatusTool
from game_of_everything.deploy.inventory import write_inventory

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def run_deploy_ec2(
    state: GoEState,
    region: str = "us-east-1",
    instance_type: str = "t3.small",
    attacker_cidr: str | None = None,
    ttl_hours: int = 4,
) -> None:
    """Deploy the scenario to AWS EC2. Called from the flow step."""
    topology = state.topology
    if not topology or not topology.boxes:
        rich.print("[yellow]No topology to deploy. Skipping EC2 deployment.[/yellow]")
        return

    run_id = state.run_id
    rich.print(f"\n[bold cyan]=== EC2 DEPLOYMENT ===[/bold cyan]")
    rich.print(f"  Run ID:    {run_id}")
    rich.print(f"  Region:    {region}")
    rich.print(f"  Boxes:     {len(topology.boxes)}")
    rich.print(f"  Instance:  {instance_type}")

    # --- Phase 1: Plan + Apply infrastructure ---
    deployer = EC2DeployTool(
        region=region,
        instance_type=instance_type,
        attacker_cidr=attacker_cidr,
        ttl_hours=ttl_hours,
    )

    rich.print("\n[bold]Phase 1: Planning infrastructure...[/bold]")
    plan = deployer.plan(topology, run_id)
    rich.print(f"  Terraform plan ready ({plan['box_count']} boxes)")

    rich.print("\n[bold]Phase 2: Applying infrastructure...[/bold]")
    deploy_result = deployer.apply(run_id)
    rich.print(f"  VPC: {deploy_result.vpc_id}")
    for box in deploy_result.boxes:
        rich.print(f"  {box.box_id}: {box.instance_id} ({box.public_ip or box.private_ip})")

    # --- Phase 2: Wait for all instances + SSM agents ---
    rich.print("\n[bold]Phase 3: Waiting for instances to become ready...[/bold]")
    status_tool = EC2StatusTool(region=region)
    instance_map = {b.box_id: b.instance_id for b in deploy_result.boxes}
    status = status_tool.wait_ready(instance_map, timeout=600)

    if not status.all_ready:
        not_ready = [b for b in status.boxes if not b.ssm_ready]
        rich.print(f"[red]WARNING: {len(not_ready)} boxes not ready after timeout[/red]")
        for b in not_ready:
            rich.print(f"  [red]{b.box_id}: {b.instance_state} (SSM: {'ready' if b.ssm_ready else 'not ready'})[/red]")

    # --- Phase 3: Upload and execute deploy scripts ---
    rich.print("\n[bold]Phase 4: Provisioning boxes...[/bold]")
    provisioner = EC2ProvisionTool(region=region)
    provision_results = []

    instance_lookup = {b.box_id: b.instance_id for b in deploy_result.boxes}

    def _provision_box(box_id: str) -> None:
        script = state.deploy_scripts.get(box_id, "")
        if not script:
            logger.warning("No deploy script for box %s", box_id)
            return
        iid = instance_lookup[box_id]
        rich.print(f"  Provisioning {box_id} ({iid})...")
        result = provisioner.upload_and_run(iid, box_id, script)
        provision_results.append(result)
        status_icon = "[green]✓[/green]" if result.status == "success" else "[red]✗[/red]"
        rich.print(f"  {status_icon} {box_id}: {result.status}")
        if result.status != "success" and result.stderr:
            rich.print(f"    [dim]{result.stderr[:500]}[/dim]")

    # Provision boxes in parallel
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_provision_box, box_id): box_id for box_id in instance_lookup}
        for future in as_completed(futures):
            exc = future.exception()
            if exc:
                box_id = futures[future]
                rich.print(f"  [red]✗ {box_id}: {exc}[/red]")

    # --- Phase 4: Write inventory ---
    output_dir = _PROJECT_ROOT / "output" / f"{run_id}_{topology.scenario_name[:40]}"
    output_dir.mkdir(parents=True, exist_ok=True)

    inv_path = write_inventory(output_dir, deploy_result, provision_results, run_id)
    rich.print(f"\n[bold green]EC2 deployment complete.[/bold green]")
    rich.print(f"  Inventory: {inv_path}")
    rich.print(f"  Teardown:  {output_dir / 'teardown.sh'}")
    rich.print(f"  Or run:    goe destroy {run_id}")
