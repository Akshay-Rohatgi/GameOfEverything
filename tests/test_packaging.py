"""Unit tests for goe/packaging — no Docker, no LLM."""

from pathlib import Path
from unittest.mock import patch

import yaml

from goe.graph.models import EntityGraph
from goe.models.procedure import ExecAttackerAction, Procedure, Step
from goe.models.report import BuildOutcome, EntityResult, EntityStatus
from goe.packaging import package
from goe.packaging.grader import assemble_deploy_script

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


def _outcome(entity_id: str, script: str, *, attempts: int = 1) -> BuildOutcome:
    proc = Procedure(procedure=[
        Step(
            step_id=f"{entity_id}_s1",
            action=ExecAttackerAction(type="exec_attacker", command="echo hi"),
        )
    ])
    return BuildOutcome(
        result=EntityResult(id=entity_id, status=EntityStatus.PASSED, attempts=attempts),
        deploy_script=script,
        procedure=proc,
        outgoing_values={},
    )


def _graph() -> EntityGraph:
    return EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")


def test_package_writes_three_files(tmp_path):
    graph = _graph()
    built = {
        "sqli_entity": _outcome("sqli_entity", "echo sqli-deploy"),
        "ssh_entity": _outcome("ssh_entity", "echo ssh-deploy", attempts=2),
    }
    out = package(graph, built, tmp_path / "pkg", request="sqli to ssh")

    assert (out / "deploy.sh").exists()
    assert (out / "playbook.yaml").exists()
    assert (out / "README.md").exists()
    assert (out / "docker-compose.yml").exists()


def test_single_system_compose_waits_for_provisioning(tmp_path):
    graph = _graph()
    built = {"sqli_entity": _outcome("sqli_entity", "echo ready")}
    out = package(graph, built, tmp_path / "pkg")

    compose = yaml.safe_load((out / "docker-compose.yml").read_text())
    target = compose["services"]["target_system"]
    assert target["healthcheck"]["test"] == [
        "CMD", "test", "-f", "/tmp/goe-deploy-ready"
    ]
    assert "touch /tmp/goe-deploy-ready" in target["command"]
    assert "name" not in compose["networks"]["default"]
    assert all(mapping.startswith("127.0.0.1:") for mapping in target["ports"])


def test_deploy_topo_order_and_postprocess(tmp_path):
    graph = _graph()
    built = {
        "ssh_entity": _outcome("ssh_entity", "echo ssh-deploy"),
        "sqli_entity": _outcome("sqli_entity", "echo sqli-deploy"),
    }
    out = package(graph, built, tmp_path / "pkg")
    script = (out / "deploy.sh").read_text()

    # sqli_entity depends-before ssh_entity → must appear first in topo order
    assert script.index("# --- sqli_entity ---") < script.index("# --- ssh_entity ---")
    # shebang + set -e injected exactly once
    assert script.startswith("#!/bin/bash\n")
    assert script.count("#!/bin/bash") == 1
    assert script.count("set -e") == 1
    # executable bit
    assert (out / "deploy.sh").stat().st_mode & 0o111


def test_playbook_step_count(tmp_path):
    graph = _graph()
    built = {
        "sqli_entity": _outcome("sqli_entity", "x"),
        "ssh_entity": _outcome("ssh_entity", "y"),
    }
    out = package(graph, built, tmp_path / "pkg")
    steps = yaml.safe_load((out / "playbook.yaml").read_text())

    assert [s["entity_id"] for s in steps] == ["sqli_entity", "ssh_entity"]
    assert all(s["procedure"]["procedure"] for s in steps)


def test_readme_contents(tmp_path):
    graph = _graph()
    built = {"sqli_entity": _outcome("sqli_entity", "x")}
    out = package(graph, built, tmp_path / "pkg", request="leak creds via sqli")
    readme = (out / "README.md").read_text()

    assert "leak creds via sqli" in readme
    assert "sqli_entity" in readme
    assert "express" in readme  # runtime column
    assert "Edge Chain" in readme


def test_only_built_entities_packaged(tmp_path):
    graph = _graph()
    # ssh_entity failed → not in built; only sqli_entity is packaged
    built = {"sqli_entity": _outcome("sqli_entity", "echo only-sqli")}
    out = package(graph, built, tmp_path / "pkg")
    script = (out / "deploy.sh").read_text()

    assert "# --- sqli_entity ---" in script
    assert "# --- ssh_entity ---" not in script


# ---------------------------------------------------------------------------
# Shared deploy-script assembler (grader invoked in every assembly path)
# ---------------------------------------------------------------------------

def test_assemble_multi_section_invokes_grader_and_postprocesses():
    """With >1 section the grader runs; its fixed output is returned + post-processed."""
    fixed = "# --- a ---\nuseradd dbuser\necho 'dbuser:SuperSecret123' | chpasswd\n"
    with patch("goe.bedrock.call", return_value=fixed) as mock_call:
        script, warnings = assemble_deploy_script([
            ("a", "useradd dbuser\necho 'dbuser:SuperSecret123' | chpasswd"),
            ("b", "useradd dbuser\necho 'dbuser:dbuser123' | chpasswd"),
        ])

    mock_call.assert_called_once()
    # grader output flows through, post-processed (shebang + set -e)
    assert script.startswith("#!/bin/bash\n")
    assert script.count("set -e") == 1
    assert "SuperSecret123" in script
    assert "dbuser123" not in script
    # the clobbering second password was a real conflict → reported
    assert warnings


def test_assemble_single_section_skips_grader():
    """A single section has no cross-entity conflict → no LLM call."""
    with patch("goe.bedrock.call") as mock_call:
        script, warnings = assemble_deploy_script([("a", "echo hi")])

    mock_call.assert_not_called()
    assert warnings == []
    assert script.startswith("#!/bin/bash\n")
    assert "echo hi" in script


def test_chain_test_per_system_runs_grader():
    """The chain test deploys the SAME graded script the packager ships (not ungraded)."""
    from goe.flow.chain_test import _build_per_system_scripts

    graph = _graph()  # both entities on target_system
    built = {
        "sqli_entity": _outcome("sqli_entity", "echo sqli-deploy"),
        "ssh_entity": _outcome("ssh_entity", "echo ssh-deploy"),
    }
    fixed = "# --- graded ---\necho graded\n"
    with patch("goe.bedrock.call", return_value=fixed) as mock_call:
        per_system = _build_per_system_scripts(graph, built)

    mock_call.assert_called_once()  # 2 same-system entities → grader runs
    assert "echo graded" in per_system["target_system"]
    assert per_system["target_system"].startswith("#!/bin/bash\n")


# ---------------------------------------------------------------------------
# Service layer in packaged scripts (declared services install via ServiceRegistry,
# not via entity scripts — so an SMB-only system never installs SSH)
# ---------------------------------------------------------------------------

def _smb_ssh_graph() -> EntityGraph:
    return EntityGraph.from_yaml(FIXTURES / "valid_smb_ssh_keychain.yaml")


def test_service_section_per_system():
    from goe.packaging.grader import service_section

    graph = _smb_ssh_graph()
    smb = service_section(graph.system_by_id("smb_server"))
    ssh = service_section(graph.system_by_id("ssh_server"))

    assert smb is not None and ssh is not None
    smb_id, smb_script = smb
    ssh_id, ssh_script = ssh
    # SMB system installs samba and NOT openssh — the original leak.
    assert "samba" in smb_script
    assert "openssh-server" not in smb_script
    # SSH system installs openssh.
    assert "openssh-server" in ssh_script


def test_service_section_skips_pseudo_services():
    from goe.packaging.grader import service_section

    graph = _graph()  # target_system has services: [web]
    assert service_section(graph.system_by_id("target_system")) is None


def test_package_multisystem_prepends_services(tmp_path):
    from goe.packaging import grader

    graph = _smb_ssh_graph()
    built = {
        "smb_key_share": _outcome("smb_key_share", "echo configure-share"),
        "ssh_login": _outcome("ssh_login", "echo authorize-key"),
    }
    # Bypass the LLM grader deterministically (service + entity section = 2 sections).
    with patch.object(grader, "grade_and_fix_script", side_effect=lambda c, s, **k: (c, [])):
        out = package(graph, built, tmp_path / "pkg", request="smb to ssh")

    smb_sh = (out / "smb_server_deploy.sh").read_text()
    ssh_sh = (out / "ssh_server_deploy.sh").read_text()
    # SMB box configures its share but never stands up SSH.
    assert "samba" in smb_sh and "echo configure-share" in smb_sh
    assert "openssh-server" not in smb_sh
    # SSH box installs openssh and authorizes the key.
    assert "openssh-server" in ssh_sh and "echo authorize-key" in ssh_sh
    # Services come before entity config in each script.
    assert smb_sh.index("samba") < smb_sh.index("echo configure-share")


# ---------------------------------------------------------------------------
# solve.sh
# ---------------------------------------------------------------------------

import shutil
import subprocess

from goe.models.procedure import StdoutContainsAssertion


def _chain(command: str, needle: str) -> Procedure:
    return Procedure(procedure=[
        Step(
            step_id="win",
            action=ExecAttackerAction(type="exec_attacker", command=command),
            expect=StdoutContainsAssertion(stdout_contains=needle),
        )
    ])


def _bash_ok(path: Path) -> None:
    if not shutil.which("bash"):
        return
    r = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_package_writes_solve_script(tmp_path):
    graph = _graph()
    built = {
        "sqli_entity": _outcome("sqli_entity", "echo sqli"),
        "ssh_entity": _outcome("ssh_entity", "echo ssh"),
    }
    chain = _chain("ssh ${system.target_system.host} id", "uid=0(root)")
    out = package(graph, built, tmp_path / "pkg", chain_procedure=chain)

    solve = out / "solve.sh"
    assert solve.exists()
    assert solve.stat().st_mode & 0o111, "solve.sh must be executable"
    _bash_ok(solve)
    body = solve.read_text()
    assert "SYSTEM_TARGET_SYSTEM_HOST" in body
    assert "grep -qF -- 'uid=0(root)'" in body
    assert "## Solving" in (out / "README.md").read_text()


def test_package_single_entity_uses_sole_procedure(tmp_path):
    # No chain procedure, single built entity → solve.sh from that entity's procedure.
    graph = _graph()
    built = {"sqli_entity": _outcome("sqli_entity", "echo sqli")}
    out = package(graph, built, tmp_path / "pkg")
    assert (out / "solve.sh").exists()
    _bash_ok(out / "solve.sh")


def test_multisystem_compose_gets_attacker_service(tmp_path):
    from goe.packaging import grader

    graph = _smb_ssh_graph()
    built = {
        "smb_key_share": _outcome("smb_key_share", "echo share"),
        "ssh_login": _outcome("ssh_login", "echo login"),
    }
    chain = _chain("ssh ${system.ssh_server.host} id", "uid=0(root)")
    with patch.object(grader, "grade_and_fix_script", side_effect=lambda c, s, **k: (c, [])):
        out = package(graph, built, tmp_path / "pkg", chain_procedure=chain)

    assert (out / "solve.sh").exists()
    compose = yaml.safe_load((out / "docker-compose.yml").read_text())
    assert "attacker" in compose["services"]
    atk = compose["services"]["attacker"]
    assert atk["image"] == "goe-attacker:latest"
    assert "./solve.sh:/goe/solve.sh:ro" in atk["volumes"]
    assert "docker-compose exec attacker" in (out / "README.md").read_text()


def test_unsupported_procedure_skips_solve_script(tmp_path):
    from goe.models.procedure import NavigateAction

    graph = _graph()
    built = {"sqli_entity": _outcome("sqli_entity", "echo sqli")}
    browser_chain = Procedure(procedure=[
        Step(step_id="nav", action=NavigateAction(type="navigate", path="/"))
    ])
    out = package(graph, built, tmp_path / "pkg", chain_procedure=browser_chain)
    assert not (out / "solve.sh").exists()
    # README records why it was skipped.
    assert "solve.sh not generated" in (out / "README.md").read_text()
