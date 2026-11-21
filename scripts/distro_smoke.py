#!/usr/bin/env python3
"""Service-recipe smoke test across distros (pure Docker, zero Bedrock credits).

For each service recipe, spin a bare container for the chosen distro and run the
exact production deploy sequence:

    profile.bootstrap_command()
      + ServiceRegistry.deploy_all()   (install -> configure -> start w/ gate)
      + the recipe healthcheck

then report a PASS/FAIL matrix and, for each failure, which phase broke and the
tail of its output. Nothing here calls an LLM — it only exercises the
deterministic distro + service layers, so it is the cheap way to find
distro-portability breakage before spending a live build.

Usage:
    python scripts/distro_smoke.py [distro] [service ...]

    distro    one of goe.distros.available_distros() (default: rocky)
    service   optional list to restrict the run (default: all recipes)

Examples:
    python scripts/distro_smoke.py                 # all recipes on rocky
    python scripts/distro_smoke.py ubuntu          # regression check on ubuntu
    python scripts/distro_smoke.py rocky apache mysql
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

# Make the package importable no matter the cwd (script lives in scripts/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from goe.distros import get_profile  # noqa: E402
from goe.services.registry import get_registry  # noqa: E402
from goe.models.system import ServiceSpec  # noqa: E402

TIMEOUT = 420  # seconds per service container


def build_script(profile, reg, service_id: str) -> str:
    spec = ServiceSpec(id=service_id)
    deploy = reg.deploy_all([spec], profile=profile)
    recipe = reg.get_recipe(service_id)
    healthcheck = (recipe.get("healthcheck") or "true").strip()
    bootstrap = profile.bootstrap_command()
    return "\n".join([
        "set -o pipefail",
        "echo HARNESS_MARK:bootstrap",
        bootstrap,
        'rc=$?; [ $rc -ne 0 ] && { echo "HARNESS_FAIL:bootstrap:$rc"; exit $rc; }',
        "echo HARNESS_MARK:deploy",
        deploy,
        'rc=$?; [ $rc -ne 0 ] && { echo "HARNESS_FAIL:deploy:$rc"; exit $rc; }',
        "echo HARNESS_MARK:healthcheck",
        healthcheck,
        'rc=$?; [ $rc -ne 0 ] && { echo "HARNESS_FAIL:healthcheck:$rc"; exit $rc; }',
        "echo HARNESS_OK",
    ])


def run_service(profile, reg, service_id: str) -> tuple[str, str, str]:
    script = build_script(profile, reg, service_id)
    try:
        p = subprocess.run(
            ["docker", "run", "--rm", "-i", profile.image, "bash", "-c", script],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
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


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    distro = argv[1] if len(argv) > 1 else "rocky"
    only = set(argv[2:])
    profile = get_profile(distro)
    reg = get_registry()

    services = reg.available_services()
    if only:
        services = [s for s in services if s in only]

    print(f"# distro smoke test — distro={distro} image={profile.image} "
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
    # Non-zero exit if anything failed, so CI/scripts can gate on it.
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
