#!/usr/bin/env python3
"""Standalone test script for CustomAppFlow.

Runs a single CustomAppFlow end-to-end (generate → stage → L1 → L2) without
the full GoEFlow pipeline. Useful for iterating on app generation and testing
without waiting for the misconfig pipeline.

Usage:
    # Generate only — inspect LLM output before spending Docker time
    python scripts/test_custom_app.py --generate-only

    # Generate and save to file (avoids re-generating on the next run)
    python scripts/test_custom_app.py --generate-only --save /tmp/app.json

    # Load a saved generation and run Docker tests only (no LLM call)
    python scripts/test_custom_app.py --from-file /tmp/app.json --no-rebuild

    # Full end-to-end (generate + Docker) with cached attacker image
    python scripts/test_custom_app.py --no-rebuild

    # Override the vulnerability/goal/runtime
    python scripts/test_custom_app.py --vuln cmd_injection --goal rce_via_cmd_injection --runtime flask --no-rebuild

Available vuln atoms:
    sqli_union, sqli_tautology, sqli_blind,
    ssti_jinja2, cmd_injection, file_upload_bypass,
    path_traversal_lfi, xss_stored, xss_reflected

Available attack goals:
    credential_theft, auth_bypass, rce_via_cmd_injection,
    rce_via_webshell, rce_via_sqli, lfi_to_rce

Available runtimes:
    apache_php (port 80), flask (port 5000), express (port 3000)
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure src/ is on the path when running from project root
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import rich
from dotenv import load_dotenv

load_dotenv()

import game_of_everything.patches  # noqa: F401 — monkey-patches crewAI for Bedrock

from game_of_everything.models import CustomVector, GeneratedApp, CustomAppState
from game_of_everything.steps.custom_app_flow import (
    CustomAppFlow, AppGenerationError,
    _load_yaml, _load_attack_goal, _load_web_runtime,
    _fetch_vuln_atom, _run_app_generation_crew, _stage_app_files,
    _run_verdict_crew, _CONFIG_DIR,
)
from game_of_everything.tools.test_environment import TestEnvironmentTool

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
for noisy in ("httpx", "httpcore", "botocore", "boto3", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Default test vector
# ---------------------------------------------------------------------------

DEFAULT_VECTOR = CustomVector(
    vuln_atom_ids=["sqli_union"],
    attack_chain_goals=["credential_theft"],
    runtime_id="apache_php",
    port=80,
    db_name="employee_directory",
    db_user="appuser",
    db_password="BlueMountain2024!",
    seed_username="jthompson",
    seed_password="TigerLily99#",
    synthesis_context=(
        "A PHP employee directory app backed by MySQL. The search endpoint "
        "builds its query with string concatenation, making it vulnerable to "
        "UNION-based SQL injection. The users table contains jthompson's "
        "credentials (password: TigerLily99#) which are also valid for SSH login."
    ),
)

RUNTIME_PORTS = {"apache_php": 80, "flask": 5000, "express": 3000}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section(title: str) -> None:
    rich.print(f"\n[bold white on blue] {title} [/bold white on blue]")


def _print_snippet(label: str, snippet: str | None) -> None:
    rich.print(f"\n[bold cyan]{label}:[/bold cyan]")
    rich.print(f"[dim]{'─' * 60}[/dim]")
    if snippet is None:
        rich.print("  [dim](null)[/dim]")
    else:
        for line in snippet.splitlines():
            rich.print(f"  {line}")
    rich.print(f"[dim]{'─' * 60}[/dim]")


def _print_generated_app(app: GeneratedApp) -> None:
    _section("GENERATED APP")
    rich.print(f"  [bold]Filename:[/bold] {app.app_filename}")
    _print_snippet("app_source", app.app_source)
    _print_snippet("schema_sql", app.schema_sql)
    _print_snippet("seed_sql", app.seed_sql)
    _print_snippet("setup_db_sh", app.setup_db_sh)
    _print_snippet("deploy_snippet", app.deploy_snippet)
    _print_snippet("testing_snippet (L1)", app.testing_snippet)
    _print_snippet("attack_objective (L2)", app.attack_objective)


def _save_app(app: GeneratedApp, vector: CustomVector, path: str) -> None:
    data = {"vector": vector.model_dump(), "app": app.model_dump()}
    Path(path).write_text(json.dumps(data, indent=2))
    rich.print(f"\n[green]Saved to {path}[/green]")


def _load_app(path: str) -> tuple[GeneratedApp, CustomVector]:
    data = json.loads(Path(path).read_text())
    return GeneratedApp(**data["app"]), CustomVector(**data["vector"])

# ---------------------------------------------------------------------------
# Generate-only mode
# ---------------------------------------------------------------------------

def run_generate_only(vector: CustomVector, save_path: str | None) -> GeneratedApp:
    _section("GENERATE-ONLY MODE (no Docker)")
    rich.print(f"  vuln_atoms   : [cyan]{vector.display_name}[/cyan]")
    rich.print(f"  attack_goals : [cyan]{'+'.join(vector.attack_chain_goals)}[/cyan]")
    rich.print(f"  runtime      : [cyan]{vector.runtime_id}[/cyan]")

    agents_config = _load_yaml(_CONFIG_DIR / "agents.yaml")
    tasks_config  = _load_yaml(_CONFIG_DIR / "tasks.yaml")

    state = CustomAppState(vector=vector)

    rich.print("\n[yellow]Fetching vuln atom(s) from ChromaDB...[/yellow]")
    state.vuln_atom_contents = [_fetch_vuln_atom(aid) for aid in vector.vuln_atom_ids]
    rich.print("[yellow]Loading attack goal(s) and runtime...[/yellow]")
    state.attack_goals = [_load_attack_goal(gid) for gid in vector.attack_chain_goals]
    state.web_runtime = _load_web_runtime(vector.runtime_id)

    rich.print("\n[yellow]Running app generation crew (Opus)...[/yellow]")
    generated = _run_app_generation_crew(state, agents_config, tasks_config)

    _print_generated_app(generated)

    if save_path:
        _save_app(generated, vector, save_path)

    _section("DONE (generate-only)")
    return generated

# ---------------------------------------------------------------------------
# Docker-only mode — stage files + L1 + L2 without re-generating
# ---------------------------------------------------------------------------

def run_docker_only(app: GeneratedApp, vector: CustomVector, no_rebuild: bool) -> None:
    _section("DOCKER-ONLY MODE (skipping generation)")
    rich.print(f"  vuln_atoms   : [cyan]{vector.display_name}[/cyan]")
    rich.print(f"  runtime      : [cyan]{vector.runtime_id}[/cyan]  port: [cyan]{vector.port}[/cyan]")

    if no_rebuild:
        _patch_skip_build()

    agents_config = _load_yaml(_CONFIG_DIR / "agents.yaml")
    tasks_config  = _load_yaml(_CONFIG_DIR / "tasks.yaml")

    from game_of_everything.tools.test_environment import RUNTIME_TARGET_IMAGES
    runtime_info = RUNTIME_TARGET_IMAGES.get(vector.runtime_id)
    target_image = runtime_info["tag"] if runtime_info else ""
    env = TestEnvironmentTool(target_image=target_image, enable_browser=True)
    rich.print("\n[yellow]Starting Docker containers...[/yellow]")
    env.setup()

    try:
        # Stage app files into target container
        rich.print("[yellow]Staging app files...[/yellow]")
        _stage_app_files(env, app)

        # Run deploy_snippet
        rich.print("\n[yellow]Running deploy_snippet...[/yellow]")
        exit_code, stdout, stderr = env.exec_in_target(app.deploy_snippet)
        rich.print(f"  exit_code: {exit_code}")
        if stdout.strip():
            rich.print(f"  stdout: {stdout[-2000:]}")
        if stderr.strip():
            rich.print(f"  [dim]stderr: {stderr[-1000:]}[/dim]")

        # Layer 1
        _section("LAYER 1 — internal state check")
        l1_exit, l1_out, l1_err = env.exec_in_target(app.testing_snippet)
        rich.print(f"  exit_code : {l1_exit}")
        rich.print(f"  stdout    :\n{l1_out}")
        if l1_err.strip():
            rich.print(f"  [dim]stderr    : {l1_err}[/dim]")

        l1_verdict = _run_verdict_crew(
            atom_name=vector.display_name,
            atom_context=vector.synthesis_context,
            layer="internal state check",
            snippet_executed=app.testing_snippet,
            exit_code=l1_exit,
            stdout=l1_out,
            stderr=l1_err,
            agents_config=agents_config,
            tasks_config=tasks_config,
        )
        icon = "[green]✓ PASS[/green]" if l1_verdict.passed else "[red]✗ FAIL[/red]"
        rich.print(f"\n  L1 verdict : {icon}")
        rich.print(f"  reasoning  : {l1_verdict.reasoning}")

        # Layer 2 — run via Attack Orchestrator (parses attack_objective)
        _section("LAYER 2 — external attack probe (orchestrator)")
        from game_of_everything.crews.attack_orchestrator_crew import run_attack_orchestrator_crew
        from game_of_everything.tools.test_environment import RUNTIME_TARGET_IMAGES

        runtime_info = RUNTIME_TARGET_IMAGES.get(vector.runtime_id)
        target_image = runtime_info["tag"] if runtime_info else ""
        orch_result = run_attack_orchestrator_crew(
            agents_config=agents_config,
            tasks_config=tasks_config,
            generated_app=app,
            synthesis_context=vector.synthesis_context,
            port=vector.port,
            target_container_name=env.target_name,
            attacker_container_name=env.attacker_name,
            cdp_url=env.browser_cdp_url,
        )
        l2_icon = "[green]✓ PASS[/green]" if orch_result.l2_passed else "[red]✗ FAIL[/red]"
        rich.print(f"  L1 passed  : {orch_result.l1_passed}")
        rich.print(f"  L2 verdict : {l2_icon}")
        rich.print(f"  reasoning  : {orch_result.reasoning}")
        rich.print(f"  L2 evidence:\n{orch_result.l2_evidence}")

    finally:
        rich.print("\n[yellow]Tearing down containers...[/yellow]")
        env.teardown()

# ---------------------------------------------------------------------------
# Full end-to-end mode
# ---------------------------------------------------------------------------

def run_full(vector: CustomVector, no_rebuild: bool, save_path: str | None) -> None:
    _section("FULL END-TO-END MODE")
    rich.print(f"  vuln_atoms   : [cyan]{vector.display_name}[/cyan]")
    rich.print(f"  attack_goals : [cyan]{'+'.join(vector.attack_chain_goals)}[/cyan]")
    rich.print(f"  runtime      : [cyan]{vector.runtime_id}[/cyan]  port: [cyan]{vector.port}[/cyan]")

    if no_rebuild:
        _patch_skip_build()

    flow = CustomAppFlow(vector=vector)
    try:
        flow.kickoff()
    except AppGenerationError as e:
        rich.print(f"\n[bold red]AppGenerationError:[/bold red] {e}")
        if flow.state.generated_app:
            _print_generated_app(flow.state.generated_app)
        sys.exit(1)

    generated = flow.state.generated_app
    if generated:
        _print_generated_app(generated)
        if save_path:
            _save_app(generated, vector, save_path)

    _section("RESULT")
    resolved = flow.state.resolved
    if resolved:
        status = "[bold green]PASS[/bold green]" if resolved.validation_passed else "[bold red]FAIL[/bold red]"
        rich.print(f"  Validation : {status}")
        rich.print(f"  Attempts   : {flow.state.generate_attempts}")
    else:
        rich.print("  [red]No resolved output.[/red]")

    for layer, verdict in [("L1", flow.state.layer1_verdict), ("L2", flow.state.layer2_verdict)]:
        if verdict:
            icon = "[green]✓[/green]" if verdict.passed else "[red]✗[/red]"
            rich.print(f"  {layer} {icon} : {verdict.reasoning}")

# ---------------------------------------------------------------------------
# Patch to skip Docker image rebuild
# ---------------------------------------------------------------------------

def _patch_skip_build() -> None:
    from game_of_everything.tools.test_environment import (
        NETWORK_NAME, TARGET_NAME, TARGET_IMAGE, ATTACKER_NAME, ATTACKER_IMAGE_TAG,
    )

    def patched_setup(self):
        logging.getLogger(__name__).info("--no-rebuild: skipping attacker image build")
        self._force_cleanup()
        self.network = self.client.networks.create(NETWORK_NAME, driver="bridge")
        self.target_container = self.client.containers.run(
            TARGET_IMAGE, command="sleep infinity", name=TARGET_NAME,
            network=NETWORK_NAME, hostname="target", detach=True, remove=False,
        )
        bootstrap_cmd = (
            "apt-get update -qq && DEBIAN_FRONTEND=noninteractive "
            "apt-get install -y --no-install-recommends curl wget ca-certificates gnupg lsb-release"
        )
        self._exec_in_container(self.target_container, bootstrap_cmd)
        self.attacker_container = self.client.containers.run(
            ATTACKER_IMAGE_TAG, command="sleep infinity", name=ATTACKER_NAME,
            network=NETWORK_NAME, hostname="attacker", detach=True, remove=False,
        )

    TestEnvironmentTool.setup = patched_setup

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Standalone CustomAppFlow test harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--vuln",      default=",".join(DEFAULT_VECTOR.vuln_atom_ids),  help="Vuln atom id(s), comma-separated")
    p.add_argument("--goal",      default=",".join(DEFAULT_VECTOR.attack_chain_goals),  help="Attack goal id(s), comma-separated")
    p.add_argument("--runtime",   default=DEFAULT_VECTOR.runtime_id,         help="Runtime id")
    p.add_argument("--context",   default=DEFAULT_VECTOR.synthesis_context,  help="Synthesis context prose")
    p.add_argument("--generate-only", action="store_true",
                   help="Run generation crew only, no Docker")
    p.add_argument("--from-file", metavar="PATH",
                   help="Load a previously saved GeneratedApp JSON and run Docker tests only")
    p.add_argument("--save",      metavar="PATH",
                   help="Save generated app JSON to this path (use with --generate-only or full run)")
    p.add_argument("--no-rebuild", action="store_true",
                   help="Skip attacker Docker image rebuild (use cached image)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    port = RUNTIME_PORTS.get(args.runtime, 80)
    vector = DEFAULT_VECTOR.model_copy(update={
        "vuln_atom_ids":      [v.strip() for v in args.vuln.split(",")],
        "attack_chain_goals": [g.strip() for g in args.goal.split(",")],
        "runtime_id":        args.runtime,
        "port":              port,
        "synthesis_context": args.context,
    })

    if args.from_file:
        app, vector = _load_app(args.from_file)
        _print_generated_app(app)
        run_docker_only(app, vector, no_rebuild=args.no_rebuild)
    elif args.generate_only:
        run_generate_only(vector, save_path=args.save)
    else:
        run_full(vector, no_rebuild=args.no_rebuild, save_path=args.save)


if __name__ == "__main__":
    main()
