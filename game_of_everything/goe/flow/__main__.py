"""CLI entry point: ``python -m goe.flow run "..."`` (and the ``goe`` console script).

Usage:
    python -m goe.flow run "web app with SQL injection that leaks credentials"
    python -m goe.flow run --verbose "..."
    python -m goe.flow run --resume output/.checkpoints/<run_id>/
    python -m goe.flow run "..." --artifacts

    python -m goe.flow test output/<run_id>/
    python -m goe.flow test output/<run_id>/ --runtime flask
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="goe", description="Game of Everything")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Plan and build a single-system environment")
    run_p.add_argument("request", nargs="?", default="", help="Natural language request")
    run_p.add_argument(
        "--resume", type=Path, default=None,
        help="Resume from a checkpoint dir (output/.checkpoints/<run_id>/)",
    )
    run_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Stream per-entity build_entity logs",
    )
    artifact_group = run_p.add_mutually_exclusive_group()
    artifact_group.add_argument(
        "--artifacts", dest="artifacts", action="store_true", default=None,
        help="Save workflow artifacts (overrides goe.toml [artifacts].enabled)",
    )
    artifact_group.add_argument(
        "--no-artifacts", dest="artifacts", action="store_false",
        help="Disable artifact saving for this run",
    )

    test_p = sub.add_parser(
        "test",
        help="Deploy and test an existing output directory (no LLM calls)",
    )
    test_p.add_argument(
        "out_dir", type=Path,
        help="Path to an existing output directory (output/<run_id>/)",
    )
    test_p.add_argument(
        "--runtime", default=None,
        help="Docker target runtime (ubuntu/flask/express/apache_php). "
             "Auto-detected from checkpoint when omitted.",
    )

    args = parser.parse_args()

    if args.command == "run":
        _run(args)
    elif args.command == "test":
        _test(args)


def _run(args) -> None:
    import shutil

    from goe.artifacts.run import artifact_run
    from goe.flow.console import RunConsole
    from goe.flow.orchestrator import run as run_flow

    if not args.request and args.resume is None:
        print("error: provide a request or --resume <checkpoint_dir>", file=sys.stderr)
        sys.exit(2)

    console = RunConsole()
    command = " ".join(sys.argv)
    with artifact_run("run", command, capture=args.artifacts) as (run_dir, _session):
        result = run_flow(
            args.request,
            resume_dir=args.resume,
            verbose=args.verbose,
            console=console,
        )
        if run_dir is not None and result.output_dir is not None:
            for fname in ("deploy.sh", "playbook.yaml", "README.md"):
                src = result.output_dir / fname
                if src.exists():
                    shutil.copy2(src, run_dir / fname)

    if not result.success:
        if result.graph is None:
            print("\nRun failed during planning.", file=sys.stderr)
        else:
            print("\nRun completed with failures.", file=sys.stderr)
        sys.exit(1)


def _test(args) -> None:
    import yaml
    from goe.container.environment import TestEnvironment
    from goe.executor.runner import run as run_procedure
    from goe.models.procedure import Procedure

    out_dir = Path(args.out_dir).resolve()
    deploy_sh_path = out_dir / "deploy.sh"
    playbook_path = out_dir / "playbook.yaml"

    if not deploy_sh_path.exists():
        print(f"error: {deploy_sh_path} not found", file=sys.stderr)
        sys.exit(2)
    if not playbook_path.exists():
        print(f"error: {playbook_path} not found", file=sys.stderr)
        sys.exit(2)

    deploy_sh = deploy_sh_path.read_text(encoding="utf-8")
    playbook: list[dict] = yaml.safe_load(playbook_path.read_text(encoding="utf-8")) or []

    # Auto-detect runtime from checkpoint when not specified
    runtime = args.runtime
    if runtime is None:
        run_id = out_dir.name
        ckpt_path = out_dir.parent / ".checkpoints" / run_id / "state.json"
        if ckpt_path.exists():
            from goe.flow.checkpoint import load_state
            state = load_state(ckpt_path)
            runtimes = {e.runtime.value for e in state.graph.entities}
            runtime = runtimes.pop() if len(runtimes) == 1 else "ubuntu"
        else:
            runtime = "ubuntu"

    port: int | None = None
    if runtime != "ubuntu":
        from goe.runtimes.registry import get_registry
        try:
            port = get_registry().port_for(runtime)
        except Exception:
            pass

    print(f"[test] runtime={runtime}  port={port or 'n/a'}  dir={out_dir}")

    env = TestEnvironment(runtime=runtime, scope="manual_test")
    env.setup()
    try:
        print("[test] Deploying...")
        exit_code, stdout, stderr = env.deploy(deploy_sh)
        if exit_code != 0:
            print(f"[test] Deploy FAILED (exit {exit_code})")
            if stdout.strip():
                print(f"--- stdout ---\n{stdout.strip()}")
            if stderr.strip():
                print(f"--- stderr ---\n{stderr.strip()}")
            sys.exit(1)
        print("[test] Deploy OK")

        ctx: dict = {
            "target_host": env.get_target_host(),
            "attacker_host": env.get_attacker_host(),
            "target_port": str(port) if port else "",
            "edges": {},
        }

        overall_passed = True
        for entry in playbook:
            entity_id = entry.get("entity_id", "unknown")
            proc_data = entry.get("procedure")
            if proc_data is None:
                print(f"[test] {entity_id}: no procedure, skipping")
                continue

            procedure = Procedure.model_validate(proc_data)
            print(f"\n[test] Running procedure: {entity_id}")
            result = run_procedure(procedure, env, ctx)

            for step in result.steps:
                status = "PASS" if step.passed else "FAIL"
                print(f"  [{status}] {step.step_id}: {step.reason}")
                if not step.passed:
                    if step.raw.stdout:
                        print(f"         stdout: {step.raw.stdout[:500]}")
                    if step.raw.stderr:
                        print(f"         stderr: {step.raw.stderr[:300]}")
                    if step.raw.error:
                        print(f"         error:  {step.raw.error}")

            if result.error:
                print(f"  [ERROR] {result.error}")

            verdict = "PASSED" if result.passed else "FAILED"
            print(f"[test] {entity_id}: {verdict}")
            if not result.passed:
                overall_passed = False

        print(f"\n[test] target={env.target_name}  attacker={env.attacker_name}")
        input("[test] Containers still running — press Enter to tear down...")
    finally:
        env.teardown()

    if not overall_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
