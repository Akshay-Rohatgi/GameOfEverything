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
    run_p.add_argument(
        "--expose-ports", nargs="*", default=None,
        help="Deploy with ports exposed on host for manual testing. "
             "Specify as host:container (e.g. --expose-ports 8080:3000 2222:22) "
             "or just port for 1:1 mapping. Auto-detects if none given.",
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
    test_p.add_argument(
        "--expose-ports", nargs="*", default=None,
        help="Publish target container ports to the host for manual testing. "
             "Specify as host:container (e.g. --expose-ports 8080:3000 2222:22) "
             "or just port for 1:1 mapping. Auto-detects if none given.",
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
            # Copy all package outputs (deploy.sh, *_deploy.sh, docker-compose.yml,
            # playbook.yaml, chain_playbook.yaml, README.md)
            for src in result.output_dir.iterdir():
                if src.is_file():
                    shutil.copy2(src, run_dir / src.name)

    if not result.success:
        if result.graph is None:
            print("\nRun failed during planning.", file=sys.stderr)
        elif result.chain_test is not None and hasattr(result.chain_test, "status"):
            from goe.models.report import ChainTestStatus
            if result.chain_test.status != ChainTestStatus.PASSED:
                reason = getattr(result.chain_test, "reason", None) or ""
                print(f"\nChain test FAILED: {reason}", file=sys.stderr)
            else:
                print("\nRun completed with entity build failures.", file=sys.stderr)
        else:
            print("\nRun completed with failures.", file=sys.stderr)
        if args.expose_ports is None:
            sys.exit(1)

    if args.expose_ports is not None and result.output_dir is not None:
        _deploy_with_exposed_ports(result, args.expose_ports)


def _deploy_with_exposed_ports(result, port_specs: list[str]) -> None:
    """After a run completes, redeploy the output with ports exposed for manual testing."""
    from goe.container.environment import TestEnvironment

    out_dir = result.output_dir
    deploy_sh_path = out_dir / "deploy.sh"
    if not deploy_sh_path.exists():
        print("[expose-ports] No deploy.sh found, skipping.", file=sys.stderr)
        return

    # Detect runtime from graph
    runtime = "ubuntu"
    if result.graph:
        runtimes = {e.runtime.value for e in result.graph.entities}
        if len(runtimes) == 1:
            runtime = runtimes.pop()

    if port_specs:
        expose_ports = _parse_port_specs(port_specs)
    else:
        port: int | None = None
        if runtime != "ubuntu":
            from goe.runtimes.registry import get_registry
            try:
                port = get_registry().port_for(runtime)
            except Exception:
                pass
        expose_ports = _detect_ports(out_dir, port)

    pairs = ", ".join(f"{hp}→:{cp}" for cp, hp in expose_ports.items())
    print(f"\n[expose-ports] Deploying with ports: {pairs}")

    deploy_sh = deploy_sh_path.read_text(encoding="utf-8")
    env = TestEnvironment(runtime=runtime, scope="manual_run", expose_ports=expose_ports)
    env.setup()
    try:
        exit_code, stdout, stderr = env.deploy(deploy_sh)
        if exit_code != 0:
            print(f"[expose-ports] Deploy FAILED (exit {exit_code})")
            if stderr.strip():
                print(f"--- stderr ---\n{stderr.strip()[:500]}")
        else:
            print(f"[expose-ports] Deploy OK — target={env.target_name}")
            print(f"[expose-ports] Ports on host: {pairs}")
        input("[expose-ports] Containers running — press Enter to tear down...")
    finally:
        env.teardown()


def _test(args) -> None:
    import yaml
    from goe.executor.runner import run as run_procedure
    from goe.models.procedure import Procedure

    out_dir = Path(args.out_dir).resolve()
    chain_playbook_path = out_dir / "chain_playbook.yaml"
    playbook_path = out_dir / "playbook.yaml"

    if chain_playbook_path.exists():
        _test_chain(args, out_dir, chain_playbook_path)
    else:
        _test_single(args, out_dir, playbook_path)


def _parse_port_specs(specs: list[str]) -> dict[int, int]:
    """Parse port specs like '8080:3000' or '22' into {container_port: host_port}."""
    mapping: dict[int, int] = {}
    for spec in specs:
        if ":" in spec:
            host_s, container_s = spec.split(":", 1)
            mapping[int(container_s)] = int(host_s)
        else:
            p = int(spec)
            mapping[p] = p
    return mapping


def _collect_ports_from_playbook(playbook_path: Path) -> set[int]:
    """Extract literal port numbers from a playbook YAML (URLs and commands)."""
    import re

    if not playbook_path.exists():
        return set()

    text = playbook_path.read_text(encoding="utf-8")
    ports: set[int] = set()

    # Literal ports in URLs: http://host:PORT/
    for m in re.finditer(r"https?://[^/:]+:(\d+)", text):
        ports.add(int(m.group(1)))

    # SSH -p PORT
    for m in re.finditer(r"-p\s+(\d+)", text):
        ports.add(int(m.group(1)))

    # nc/ncat host PORT
    for m in re.finditer(r"(?:nc|ncat)\s+\S+\s+(\d+)", text):
        ports.add(int(m.group(1)))

    return ports


def _detect_ports(out_dir: Path, runtime_port: int | None) -> dict[int, int]:
    """Auto-detect ports from playbook. Returns {container_port: host_port} (1:1)."""
    ports: set[int] = set()

    for name in ("playbook.yaml", "chain_playbook.yaml"):
        ports.update(_collect_ports_from_playbook(out_dir / name))

    if runtime_port:
        ports.add(runtime_port)

    if not ports:
        ports.add(22)

    return {p: p for p in sorted(ports)}


def _test_chain(args, out_dir: Path, chain_playbook_path: Path) -> None:
    """Replay the chain playbook against a full topology environment."""
    import yaml
    from goe.container.topology_environment import TopologyEnvironment
    from goe.executor.runner import run as run_procedure
    from goe.flow.chain_test import _build_systems_ctx
    from goe.models.procedure import Procedure

    # Load graph from checkpoint
    run_id = out_dir.name
    ckpt_path = out_dir.parent / ".checkpoints" / run_id / "state.json"
    if not ckpt_path.exists():
        print(f"error: no checkpoint found at {ckpt_path}", file=sys.stderr)
        sys.exit(2)

    from goe.flow.checkpoint import load_state
    state = load_state(ckpt_path)
    graph = state.graph

    chain_proc_data = yaml.safe_load(chain_playbook_path.read_text(encoding="utf-8"))
    chain_procedure = Procedure.model_validate(chain_proc_data)

    # Build per-system scripts from output dir
    per_system_scripts: dict[str, str] = {}
    deploy_sh = out_dir / "deploy.sh"
    if deploy_sh.exists():
        # Single-system deployed to all systems
        for s in graph.systems:
            per_system_scripts[s.id] = deploy_sh.read_text(encoding="utf-8")
    else:
        for s in graph.systems:
            p = out_dir / f"{s.id}_deploy.sh"
            if p.exists():
                per_system_scripts[s.id] = p.read_text(encoding="utf-8")

    print(f"[chain-test] topology: {len(graph.systems)} system(s), dir={out_dir}")
    systems_ctx = _build_systems_ctx(graph)

    expose: bool | dict[int, int] = _parse_port_specs(args.expose_ports) if args.expose_ports else (args.expose_ports is not None)
    env = TopologyEnvironment(graph, scope="manual_chain", expose_ports=expose)
    env.setup()
    try:
        if args.expose_ports is not None and env.port_map:
            for sys_id, mapping in env.port_map.items():
                pairs = ", ".join(f"{hp}→:{cp}" for cp, hp in mapping.items())
                print(f"[chain-test] {sys_id} ports on host: {pairs}")

        for system_id, script in per_system_scripts.items():
            print(f"[chain-test] Deploying system {system_id}…")
            ec, _out, err = env.deploy_system(system_id, script)
            if ec != 0:
                print(f"[chain-test] Deploy FAILED for {system_id} (exit {ec})")
                if err.strip():
                    print(f"--- stderr ---\n{err.strip()[:500]}")
                sys.exit(1)
            print(f"[chain-test] Deploy OK: {system_id}")

        ctx: dict = {
            "target_host": env.get_target_host(),
            "attacker_host": env.get_attacker_host(),
            "target_port": "",
            "systems": systems_ctx,
            "edges": {
                edge.id: {p: (pv.concrete or pv.structural) for p, pv in edge.params.items()}
                for edge in graph.edges
            },
        }

        print("\n[chain-test] Running chain procedure…")
        result = run_procedure(chain_procedure, env, ctx)

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
        print(f"\n[chain-test] {verdict}")
        print(f"[chain-test] attacker={env.attacker_name}")
        input("[chain-test] Containers still running — press Enter to tear down...")
    finally:
        env.teardown()

    if not result.passed:
        sys.exit(1)


def _test_single(args, out_dir: Path, playbook_path: Path) -> None:
    """Replay per-entity playbook against a single TestEnvironment (original path)."""
    import yaml
    from goe.container.environment import TestEnvironment
    from goe.executor.runner import run as run_procedure
    from goe.models.procedure import Procedure

    deploy_sh_path = out_dir / "deploy.sh"

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

    expose_ports: dict[int, int] | None = None
    if args.expose_ports is not None:
        expose_ports = _parse_port_specs(args.expose_ports) if args.expose_ports else _detect_ports(out_dir, port)

    env = TestEnvironment(runtime=runtime, scope="manual_test", expose_ports=expose_ports)
    env.setup()
    try:
        if expose_ports:
            pairs = ", ".join(f"{hp}→:{cp}" for cp, hp in expose_ports.items())
            print(f"[test] Ports exposed on host: {pairs}")

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
