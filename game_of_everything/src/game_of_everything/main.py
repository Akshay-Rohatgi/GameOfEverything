#!/usr/bin/env python
"""GoE Flow orchestrator — thin delegation to step modules.

Each flow step is defined in its own module under game_of_everything.steps/.
This file wires them together using crewAI's @start()/@listen() decorators,
which must live on methods of a Flow[State] subclass.
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import yaml

from crewai.flow import Flow, listen, start
from crewai.events.event_context import (
    _event_context_config,
    EventContextConfig,
    MismatchBehavior,
)
from dotenv import load_dotenv

from game_of_everything.checkpoint import (
    checkpoint_dir,
    completed_steps,
    find_latest_checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from game_of_everything.state import GoEState
from game_of_everything.ui import GoEConsole
from game_of_everything.steps import (
    run_synthesize_topology,
    run_box_pipelines,
    run_chain_test,
    run_finalize_topology,
    run_review_and_fix,
    run_deploy_ec2,
)

# Suppress CrewAI internal event-bus pairing warnings (known bug in 1.9.x).
# ToolUsageFinished is emitted without a matching ToolUsageStarted in the
# current version, causing spurious scope-stack mismatch warnings.
_event_context_config.set(
    EventContextConfig(
        mismatch_behavior=MismatchBehavior.SILENT,
        empty_pop_behavior=MismatchBehavior.SILENT,
    )
)

logging.getLogger('crewai').setLevel(logging.WARNING)

os.environ["GOE_VERSION"] = "0.1.0"
os.environ["OTEL_SDK_DISABLED"] = "true"  # Disable OpenTelemetry to avoid unrelated warnings
os.environ["LOG_LEVEL"] = "ERROR"  # Suppress lower-level logs from CrewAI and dependencies to reduce noise
os.environ["CREWAI_TRACING_ENABLED"] = "false"  # Disable CrewAI's internal tracing to reduce noise

load_dotenv()


class GoEFlow(Flow[GoEState]):
    def __init__(
        self,
        resume_dir: Path | None = None,
        deploy_target: str | None = None,
        review: bool = False,
        ec2_region: str = "us-east-1",
        ec2_instance_type: str = "t3.small",
        ec2_attacker_cidr: str | None = None,
        ec2_ttl_hours: int = 4,
    ):
        super().__init__()
        self._deploy_target = deploy_target
        self._review = review
        self._ec2_region = ec2_region
        self._ec2_instance_type = ec2_instance_type
        self._ec2_attacker_cidr = ec2_attacker_cidr
        self._ec2_ttl_hours = ec2_ttl_hours

        config_dir = Path(__file__).parent / "config"
        with open(config_dir / "agents.yaml", "r") as f:
            self.agents_config = yaml.safe_load(f)
        with open(config_dir / "tasks.yaml", "r") as f:
            self.tasks_config = yaml.safe_load(f)
        self.ui = GoEConsole()
        if hasattr(self, 'console'):
            self.console_quiet = True  # Suppress CrewAI's default console output since we're using our own

        if resume_dir is not None:
            latest = find_latest_checkpoint(resume_dir)
            if latest is None:
                raise ValueError(f"No checkpoint files found in {resume_dir}")
            loaded = load_checkpoint(latest)
            for field_name in GoEState.model_fields:
                setattr(self.state, field_name, getattr(loaded, field_name))
            self.state.box_states = loaded.box_states
            self._resume_dir: Path | None = resume_dir
            print(f"[checkpoint] Resuming run {self.state.run_id} from {latest.name}")
        else:
            self.state.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._resume_dir = None

    def _should_skip(self, step_name: str) -> bool:
        """Return True when resuming and this step already has a checkpoint."""
        if self._resume_dir is None:
            return False
        done = completed_steps(self._resume_dir)
        if step_name in done:
            print(f"[checkpoint] Skipping {step_name} (already completed)")
            return True
        return False

        if resume_dir is not None:
            latest = find_latest_checkpoint(resume_dir)
            if latest is None:
                raise ValueError(f"No checkpoint files found in {resume_dir}")
            loaded = load_checkpoint(latest)
            for field_name in GoEState.model_fields:
                setattr(self.state, field_name, getattr(loaded, field_name))
            self.state.box_states = loaded.box_states
            self._resume_dir: Path | None = resume_dir
            print(f"[checkpoint] Resuming run {self.state.run_id} from {latest.name}")
        else:
            self.state.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._resume_dir = None

    def _should_skip(self, step_name: str) -> bool:
        """Return True when resuming and this step already has a checkpoint."""
        if self._resume_dir is None:
            return False
        done = completed_steps(self._resume_dir)
        if step_name in done:
            print(f"[checkpoint] Skipping {step_name} (already completed)")
            return True
        return False

    @start()
    def synthesize_scenario(self):
        if self._should_skip("synthesize_scenario"):
            return
        run_synthesize_topology(self.state, self.agents_config, self.tasks_config)
        save_checkpoint(self.state, "synthesize_scenario")

    @listen(synthesize_scenario)
    def box_pipelines(self):
        """Run the full per-box pipeline for every box in the topology (parallel)."""
        if self._should_skip("box_pipelines"):
            return
        run_box_pipelines(self.state, self.agents_config, self.tasks_config)
        save_checkpoint(self.state, "box_pipelines")

    @listen(box_pipelines)
    def chain_test(self):
        """Multi-box: validate end-to-end attack chain. Single-box: no-op."""
        if self._should_skip("chain_test"):
            return
        run_chain_test(self.state, self.agents_config, self.tasks_config)
        save_checkpoint(self.state, "chain_test")

    @listen(chain_test)
    def finalize_topology(self):
        """Multi-box: write output package. Single-box: no-op."""
        if self._should_skip("finalize_topology"):
            return
        run_finalize_topology(self.state, self.agents_config, self.tasks_config)
        save_checkpoint(self.state, "finalize_topology")

    @listen(finalize_topology)
    def review_and_fix(self):
        """Optional: interactive per-box review for test failures. Activated via --review."""
        if not self._review:
            return
        if self._should_skip("review_and_fix"):
            return
        run_review_and_fix(self.state, self.agents_config, self.tasks_config)
        save_checkpoint(self.state, "review_and_fix")

    @listen(review_and_fix)
    def deploy_ec2(self):
        """Optional: deploy to AWS EC2. Activated via --deploy ec2."""
        if self._deploy_target != "ec2":
            return
        if self._should_skip("deploy_ec2"):
            return
        run_deploy_ec2(
            self.state,
            region=self._ec2_region,
            instance_type=self._ec2_instance_type,
            attacker_cidr=self._ec2_attacker_cidr,
            ttl_hours=self._ec2_ttl_hours,
        )
        save_checkpoint(self.state, "deploy_ec2")


def load_state_from_output(output_dir: Path) -> GoEState:
    """Reconstruct a minimal GoEState from an existing output directory.

    Reads playbook.json for topology metadata and docker-compose.yml for box
    and service definitions. Reads *_deploy.sh files for deploy scripts.
    Suitable for feeding into run_deploy_ec2 without re-running the pipeline.
    """
    from game_of_everything.models import BoxDefinition, NetworkTopology, SharedSecret

    playbook_path = output_dir / "playbook.json"
    compose_path = output_dir / "docker-compose.yml"
    if not playbook_path.exists():
        raise FileNotFoundError(f"No playbook.json found in {output_dir}")
    if not compose_path.exists():
        raise FileNotFoundError(f"No docker-compose.yml found in {output_dir}")

    playbook = json.loads(playbook_path.read_text(encoding="utf-8"))
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))

    _PORT_SERVICE: dict[int, str] = {
        21: "ftp", 22: "ssh", 25: "smtp", 53: "dns",
        80: "http", 110: "pop3", 143: "imap",
        443: "https", 445: "smb", 873: "rsync",
        3306: "mysql", 5432: "postgres", 5900: "vnc",
        6379: "redis", 8080: "http", 8443: "https",
        9200: "elasticsearch", 27017: "mongodb",
    }

    boxes: list[BoxDefinition] = []
    for svc_name, svc_cfg in (compose.get("services") or {}).items():
        hostname = svc_cfg.get("hostname", svc_name)
        services: list[str] = []
        for p in svc_cfg.get("ports") or []:
            container_port = int(str(p).split(":")[-1])
            label = _PORT_SERVICE.get(container_port, str(container_port))
            svc_entry = f"{label}:{container_port}"
            if svc_entry not in services:
                services.append(svc_entry)
        boxes.append(BoxDefinition(
            box_id=svc_name,
            hostname=hostname,
            role=svc_name,
            misconfig_scope="",
            services=services,
        ))

    shared_secrets: list[SharedSecret] = []
    for ss in playbook.get("shared_secrets") or []:
        shared_secrets.append(SharedSecret(
            key=ss["key"],
            value=ss["value"],
            description=ss.get("description", ""),
            source_box=ss["source_box"],
            target_box=ss["target_box"],
            target_user=ss.get("user", ""),
            access_method=ss.get("access_method", "ssh"),
        ))

    topology = NetworkTopology(
        scenario_name=playbook["scenario_name"],
        narrative=playbook.get("attack_narrative", ""),
        attack_narrative=playbook["attack_narrative"],
        entry_point=playbook["entry_point"],
        boxes=boxes,
        pivots=[],
        shared_secrets=shared_secrets,
        chain_probes=[],
        shared_resources=[],
        explicit_decisions=[],
    )

    deploy_scripts: dict[str, str] = {}
    for script_path in sorted(output_dir.glob("*_deploy.sh")):
        box_id = script_path.stem[: -len("_deploy")]
        deploy_scripts[box_id] = script_path.read_text(encoding="utf-8")

    # Extract run_id: "20260418_200538_<slug>" → "20260418_200538"
    dir_parts = output_dir.name.split("_")
    run_id = "_".join(dir_parts[:2]) if len(dir_parts) >= 2 else output_dir.name

    return GoEState(
        run_id=run_id,
        topology=topology,
        deploy_scripts=deploy_scripts,
    )


def deploy_from_output():
    """CLI entry point: deploy an existing output directory to AWS EC2."""
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Deploy an existing GoE output directory to AWS EC2"
    )
    parser.add_argument(
        "output_dir",
        help="Path to the output directory (e.g. output/20260418_200538_...)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("GOE_EC2_REGION", "us-east-1"),
    )
    parser.add_argument(
        "--instance-type",
        default=os.environ.get("GOE_EC2_INSTANCE_TYPE", "t3.small"),
    )
    parser.add_argument(
        "--attacker-cidr",
        default=os.environ.get("GOE_ATTACKER_CIDR"),
        help="CIDR for SSH/admin access (e.g. '203.0.113.5/32'). Required.",
    )
    parser.add_argument(
        "--ttl-hours",
        type=int,
        default=int(os.environ.get("GOE_EC2_TTL_HOURS", "4")),
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    state = load_state_from_output(output_dir)
    run_deploy_ec2(
        state,
        region=args.region,
        instance_type=args.instance_type,
        attacker_cidr=args.attacker_cidr,
        ttl_hours=args.ttl_hours,
    )


def kickoff():
    parser = argparse.ArgumentParser(description="Game of Everything")
    parser.add_argument(
        "--resume",
        metavar="CHECKPOINT_DIR",
        default=os.environ.get("GOE_RESUME_DIR"),
        help="Resume from a checkpoint directory (e.g. output/.checkpoints/<run_id>)",
    )
    parser.add_argument(
        "--deploy",
        choices=["ec2"],
        default=None,
        help="Deploy finalized scenario to a target platform",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        default=False,
        help="After generating scripts, pause to review test failures and provide extra context per box before deploying",
    )
    parser.add_argument(
        "--ec2-region",
        default=os.environ.get("GOE_EC2_REGION", "us-east-1"),
        help="AWS region for EC2 deployment (default: us-east-1)",
    )
    parser.add_argument(
        "--ec2-instance-type",
        default=os.environ.get("GOE_EC2_INSTANCE_TYPE", "t3.small"),
        help="EC2 instance type (default: t3.small)",
    )
    parser.add_argument(
        "--ec2-attacker-cidr",
        default=os.environ.get("GOE_ATTACKER_CIDR"),
        help="CIDR for SSH/admin access (e.g. '203.0.113.5/32'). Required for --deploy ec2.",
    )
    parser.add_argument(
        "--ec2-ttl-hours",
        type=int,
        default=int(os.environ.get("GOE_EC2_TTL_HOURS", "4")),
        help="Auto-destroy TTL in hours (default: 4, 0=disabled)",
    )
    args, _ = parser.parse_known_args()
    resume_dir = Path(args.resume) if args.resume else None
    goe_flow = GoEFlow(
        resume_dir=resume_dir,
        deploy_target=args.deploy,
        review=args.review,
        ec2_region=args.ec2_region,
        ec2_instance_type=args.ec2_instance_type,
        ec2_attacker_cidr=args.ec2_attacker_cidr,
        ec2_ttl_hours=args.ec2_ttl_hours,
    )
    goe_flow.kickoff()


def plot():
    goe_flow = GoEFlow()
    goe_flow.plot("goe_flow.png")


def destroy():
    """CLI entry point: destroy EC2 infrastructure for a given run_id."""
    parser = argparse.ArgumentParser(description="Destroy GoE EC2 deployment")
    parser.add_argument("run_id", help="Run ID to destroy (e.g. 20260413_021510)")
    parser.add_argument(
        "--region",
        default=os.environ.get("GOE_EC2_REGION", "us-east-1"),
    )
    args = parser.parse_args()

    from game_of_everything.deploy.ec2_deploy import EC2DeployTool
    deployer = EC2DeployTool(
        region=args.region,
        attacker_cidr="0.0.0.0/0",  # Not used for destroy
    )
    deployer.destroy(args.run_id)
    print(f"Destroyed infrastructure for run {args.run_id}")


if __name__ == "__main__":
    kickoff()
