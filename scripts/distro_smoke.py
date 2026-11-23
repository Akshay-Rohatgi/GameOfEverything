#!/usr/bin/env python3
"""Service-recipe smoke test across distros (pure Docker, zero Bedrock credits).

Two modes, both exercising only the deterministic distro + service layers (no LLM),
so they are the cheap way to find distro-portability breakage before a live build.

SINGLE-HOST (default) — one bare container per service for one distro:

    python scripts/distro_smoke.py [distro] [service ...]

    distro    one of goe.distros.available_distros() (default: rocky)
    service   optional list to restrict the run (default: all recipes)

    Each service: profile.bootstrap_command() + ServiceRegistry.deploy_all()
    (install -> configure -> start w/ gate) + the recipe healthcheck.

MULTI-HOST — a fleet of containers on one bridge network (mixed distros allowed),
reproducing TopologyEnvironment: per-host image from its profile, hostname + DNS
alias, per-host bootstrap + deploy_all, then cross-host reachability over Docker DNS:

    python scripts/distro_smoke.py multi <host-spec> [<host-spec> ...]

    host-spec = name:distro:svc1,svc2   (distro defaults to rocky; services optional)

    Examples:
      python scripts/distro_smoke.py multi web:rocky:nginx db:debian:postgres
      python scripts/distro_smoke.py multi a:rocky:redis b:rocky:mysql c:ubuntu:nginx

    Reports per-host deploy/health AND, for every ordered host pair, whether each
    host can resolve + reach the other host's declared service ports (the
    distro-sensitive multi-host surface: DNS alias + deploy-per-profile + binding).

Exits non-zero if anything fails, so CI/scripts can gate on it.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

# Make the package importable no matter the cwd (script lives in scripts/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from goe.distros import get_profile  # noqa: E402
from goe.services.registry import get_registry  # noqa: E402
from goe.models.system import ServiceSpec  # noqa: E402

TIMEOUT = 420  # seconds per service container / per deploy exec


# ----------------------------------------------------------------------------
# shared helpers
# ----------------------------------------------------------------------------

def _deploy_script(profile, reg, service_id: str) -> str:
    spec = ServiceSpec(id=service_id)
    return reg.deploy_all([spec], profile=profile)


def _phased_script(profile, reg, service_id: str) -> str:
    """bootstrap + deploy + healthcheck, with phase markers (single-host mode)."""
    recipe = reg.get_recipe(service_id)
    healthcheck = (recipe.get("healthcheck") or "true").strip()
    return "\n".join([
        "set -o pipefail",
        "echo HARNESS_MARK:bootstrap",
        profile.bootstrap_command(),
        'rc=$?; [ $rc -ne 0 ] && { echo "HARNESS_FAIL:bootstrap:$rc"; exit $rc; }',
        "echo HARNESS_MARK:deploy",
        _deploy_script(profile, reg, service_id),
        'rc=$?; [ $rc -ne 0 ] && { echo "HARNESS_FAIL:deploy:$rc"; exit $rc; }',
        "echo HARNESS_MARK:healthcheck",
        healthcheck,
        'rc=$?; [ $rc -ne 0 ] && { echo "HARNESS_FAIL:healthcheck:$rc"; exit $rc; }',
        "echo HARNESS_OK",
    ])


def _docker(args: list[str], timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


# ----------------------------------------------------------------------------
# single-host mode
# ----------------------------------------------------------------------------

def run_service(profile, reg, service_id: str) -> tuple[str, str, str]:
    script = _phased_script(profile, reg, service_id)
    try:
        p = _docker(["run", "--rm", "-i", profile.image, "bash", "-c", script])
        out = p.stdout + p.stderr
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or "") + (e.stderr or "")) if isinstance(e.stdout, str) else ""
        return "TIMEOUT", "deploy", out[-1500:]

    reached = "bootstrap"
    for ph in ("deploy", "healthcheck"):
        if f"HARNESS_MARK:{ph}" in out:
            reached = ph
    if "HARNESS_OK" in out and rc == 0:
        return "PASS", reached, ""
    return "FAIL", reached, out[-1500:]


def run_single(distro: str, only: set[str]) -> int:
    profile = get_profile(distro)
    reg = get_registry()
    services = reg.available_services()
    if only:
        services = [s for s in services if s in only]

    print(f"# distro smoke (single-host) — distro={distro} image={profile.image} "
          f"pm={profile.package_manager}")
    print(f"# services: {services}\n")

    results: dict[str, tuple[str, str]] = {}
    for sid in services:
        print(f"==== {sid} ====", flush=True)
        status, phase, tail = run_service(profile, reg, sid)
        results[sid] = (status, phase)
        print(f"  -> {status}  (reached: {phase})")
        if status != "PASS":
            print(textwrap.indent(tail.strip(), "    | "))
        print(flush=True)

    print("\n================ MATRIX ================")
    for sid, (status, phase) in results.items():
        mark = "PASS" if status == "PASS" else f"{status}@{phase}"
        print(f"  {sid:12s} {mark}")
    npass = sum(1 for s, _ in results.values() if s == "PASS")
    print(f"\n{npass}/{len(results)} passed on {distro}")
    return 0 if npass == len(results) else 1


# ----------------------------------------------------------------------------
# multi-host mode
# ----------------------------------------------------------------------------

def _parse_hosts(specs: list[str]) -> list[tuple[str, str, list[str]]]:
    hosts = []
    for s in specs:
        parts = s.split(":")
        name = parts[0]
        host_os = parts[1] if len(parts) > 1 and parts[1] else "rocky"
        svcs = [x for x in parts[2].split(",") if x] if len(parts) > 2 else []
        hosts.append((name, host_os, svcs))
    return hosts


def _listening_ports(container: str) -> set[int]:
    """TCP ports the container is actually LISTENing on (via ss)."""
    r = _docker(["exec", container, "bash", "-c",
                 "ss -tlnH 2>/dev/null | awk '{print $4}' | sed 's/.*://' | sort -u"],
                timeout=30)
    ports: set[int] = set()
    for tok in (r.stdout or "").split():
        try:
            ports.add(int(tok))
        except ValueError:
            pass
    return ports


def _host_ports(reg, services: list[str]) -> list[int]:
    ports: list[int] = []
    for sid in services:
        for p in (reg.get_recipe(sid).get("ports") or []):
            if p not in ports:
                ports.append(int(p))
    return ports


def run_multi(specs: list[str]) -> int:
    if not specs:
        print("multi mode needs at least one host-spec, e.g. web:rocky:nginx")
        return 2
    reg = get_registry()
    hosts = _parse_hosts(specs)
    scope = f"{os.getpid()}_{int(time.time())}"
    net = f"goe_smoke_{scope}"
    cname = {name: f"goe_smoke_{scope}_{name}" for name, _, _ in hosts}

    print(f"# distro smoke (multi-host) — network={net}")
    for name, host_os, svcs in hosts:
        print(f"#   {name}: os={host_os} image={get_profile(host_os).image} services={svcs or '(none)'}")
    print(flush=True)

    deploy_res: dict[str, tuple[str, str]] = {}   # name -> (status, detail)
    reach_res: dict[str, str] = {}                # "A->B:port" -> PASS/FAIL
    created: list[str] = []

    try:
        _docker(["network", "create", net])
        # 1. start + bootstrap + deploy each host
        for name, host_os, svcs in hosts:
            profile = get_profile(host_os)
            c = cname[name]
            r = _docker([
                "run", "-d", "--name", c, "--hostname", name,
                "--network", net, "--network-alias", name,
                profile.image, "sleep", "infinity",
            ])
            if r.returncode != 0:
                deploy_res[name] = ("FAIL", f"container start: {r.stderr.strip()[:200]}")
                continue
            created.append(c)

            boot = _docker(["exec", c, "bash", "-c", profile.bootstrap_command()])
            if boot.returncode != 0:
                deploy_res[name] = ("FAIL", f"bootstrap: {(boot.stdout + boot.stderr)[-300:]}")
                continue

            if not svcs:
                deploy_res[name] = ("PASS", "(no services — base host up)")
                continue

            script = reg.deploy_all([ServiceSpec(id=s) for s in svcs], profile=profile)
            dep = _docker(["exec", c, "bash", "-c", "set -o pipefail\n" + script])
            if dep.returncode != 0:
                deploy_res[name] = ("FAIL", f"deploy: {(dep.stdout + dep.stderr)[-400:]}")
                continue

            # per-host healthcheck for each service
            hc_fail = []
            for sid in svcs:
                hc = (reg.get_recipe(sid).get("healthcheck") or "true").strip()
                h = _docker(["exec", c, "bash", "-c", hc])
                if h.returncode != 0:
                    hc_fail.append(sid)
            deploy_res[name] = ("PASS", "deployed + healthy") if not hc_fail \
                else ("FAIL", f"healthcheck failed: {hc_fail}")

        # 2. cross-host reachability (only between hosts that deployed OK).
        # Probe only ports the service actually BINDS: a recipe may declare a port
        # it doesn't listen on by default (e.g. nginx declares 443 but binds only 80
        # without TLS), which is a declaration nuance, not a reachability failure.
        up = [h for h in hosts if deploy_res.get(h[0], ("FAIL",))[0] == "PASS"]
        listening = {name: _listening_ports(cname[name]) for name, _, _ in up}
        for a_name, _, _ in up:
            for b_name, _, b_svcs in up:
                if a_name == b_name:
                    continue
                for port in _host_ports(reg, b_svcs):
                    if port not in listening.get(b_name, set()):
                        reach_res[f"{a_name}->{b_name}:{port}"] = "SKIP (not bound)"
                        continue
                    probe = (
                        f"getent hosts {b_name} >/dev/null 2>&1 "
                        f"&& timeout 4 bash -c 'exec 3<>/dev/tcp/{b_name}/{port}' 2>/dev/null "
                        f"&& echo REACH_OK"
                    )
                    pr = _docker(["exec", cname[a_name], "bash", "-c", probe], timeout=30)
                    ok = "REACH_OK" in (pr.stdout + pr.stderr)
                    reach_res[f"{a_name}->{b_name}:{port}"] = "PASS" if ok else "FAIL"
    finally:
        for c in created:
            _docker(["rm", "-f", c], timeout=60)
        _docker(["network", "rm", net], timeout=60)

    # report
    print("\n================ HOST DEPLOY ================")
    for name, host_os, _ in hosts:
        status, detail = deploy_res.get(name, ("FAIL", "not reached"))
        print(f"  {name:10s} ({host_os:7s}) {status:4s}  {detail}")
    if reach_res:
        print("\n============ CROSS-HOST REACHABILITY ============")
        for edge, status in reach_res.items():
            print(f"  {edge:28s} {status}")

    hosts_ok = sum(1 for n, _, _ in hosts if deploy_res.get(n, ("FAIL",))[0] == "PASS")
    checked = {e: v for e, v in reach_res.items() if v in ("PASS", "FAIL")}
    reach_ok = sum(1 for v in checked.values() if v == "PASS")
    skipped = len(reach_res) - len(checked)
    print(f"\nhosts: {hosts_ok}/{len(hosts)} deployed · "
          f"reachability: {reach_ok}/{len(checked)} edges"
          + (f" ({skipped} skipped: declared but not bound)" if skipped else "")
          + ("" if checked else " (none checked)"))
    all_ok = hosts_ok == len(hosts) and reach_ok == len(checked)
    return 0 if all_ok else 1


# ----------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    if len(argv) > 1 and argv[1] == "multi":
        return run_multi(argv[2:])
    distro = argv[1] if len(argv) > 1 else "rocky"
    only = set(argv[2:])
    return run_single(distro, only)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
