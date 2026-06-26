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
