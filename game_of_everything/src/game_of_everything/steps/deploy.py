"""Step 5: Optional one-click EC2 deployment of the validated script."""

from pathlib import Path
from typing import Optional, TYPE_CHECKING

from game_of_everything.state import GoEState

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole


def run_deploy(
    state: GoEState,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Prompt the user to deploy the script to an EC2 instance."""
    # Multi-box topologies use docker-compose, not single-instance EC2 deploy
    if state.topology and len(state.topology.boxes) > 1:
        if ui:
            ui.info("[dim]  EC2 deploy skipped (multi-box topology — use docker-compose)[/dim]")
        return

    if not state.output_path or not state.final_script:
        return

    # Check if deploy config is available
    from game_of_everything.config import GoEConfig
    cfg = GoEConfig.get()

    if not cfg.deploy_key_pair_name:
        if ui:
            ui.info("[dim]  EC2 deploy skipped (no key_pair_name in goe.toml)[/dim]")
        return

    # Prompt user
    if ui:
        response = ui.prompt("  Deploy to EC2? [y/n]: ")
    else:
        response = input("Deploy to EC2? [y/n]: ")

    if response.strip().lower() not in ("y", "yes"):
        return

    import time
    from game_of_everything.ec2_deploy import deploy_to_ec2

    if ui:
        ui.deploy_status("Deploying to EC2")
    t0 = time.monotonic()

    try:
        result = deploy_to_ec2(Path(state.output_path), cfg, ui=ui)
        if ui:
            ui.deploy_done(f"Deployed to EC2: {result}", time.monotonic() - t0)
            ui.info(f"  [bold]SSH:[/bold] ssh -i <key>.pem ubuntu@{result}")
        else:
            print(f"Deployed to EC2: {result}")
    except Exception as e:
        if ui:
            ui.step_fail("EC2 deploy", str(e))
        else:
            print(f"EC2 deploy failed: {e}")
