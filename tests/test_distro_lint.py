"""Unit tests for the distro lint — pure, no network/Docker/LLM."""

from goe.construction_crew.distro_lint import lint, LintFinding
from goe.distros import get_profile
from goe.models.procedure import ExecTargetAction, Procedure, Step

DEBIAN = get_profile("debian")
UBUNTU = get_profile("ubuntu")


def _proc(command: str) -> Procedure:
    return Procedure(procedure=[
        Step(step_id="s1", action=ExecTargetAction(type="exec_target", command=command))
    ])


# --- systemctl (keyed on has_systemd, so flagged on both ubuntu and debian) ---

def test_systemctl_flagged_without_systemd():
    findings = lint(DEBIAN, "systemctl restart ssh", None)
    assert any(f.token == "systemctl" for f in findings)


def test_systemctl_flagged_on_ubuntu_too():
    # Ubuntu containers also have has_systemd=False, so this is not Ubuntu-specific.
    assert any(f.token == "systemctl" for f in lint(UBUNTU, "systemctl start nginx", None))


def test_clean_service_command_has_no_systemctl_finding():
    findings = lint(DEBIAN, "service ssh start; nohup smbd &", None)
    assert not any(f.token == "systemctl" for f in findings)


# --- Ubuntu-only tooling (only when target is not ubuntu) ---

def test_ppa_flagged_on_debian():
    findings = lint(DEBIAN, "add-apt-repository ppa:deadsnakes/ppa", None)
    tokens = {f.token for f in findings}
    assert "add-apt-repository" in tokens and "ppa:" in tokens


def test_ppa_not_flagged_on_ubuntu():
    # PPAs are legitimate on Ubuntu — no false positive there.
    findings = lint(UBUNTU, "add-apt-repository ppa:deadsnakes/ppa", None)
    assert not any(f.token in {"add-apt-repository", "ppa:"} for f in findings)


# --- package aliases ---

def test_canonical_package_flagged_on_debian():
    findings = lint(DEBIAN, "apt-get install -y netcat", None)
    assert any(f.token == "netcat" for f in findings)


def test_resolved_package_name_is_clean():
    # Using the distro's real package name must not be flagged, even though
    # "netcat" is a substring of "netcat-openbsd".
    assert lint(DEBIAN, "apt-get install -y netcat-openbsd", None) == []


# --- procedure steps are scanned, with step attribution ---

def test_procedure_step_is_linted_and_attributed():
    findings = lint(DEBIAN, None, _proc("sudo systemctl restart ssh"))
    assert len(findings) == 1
    assert findings[0].where == "procedure:s1"
    assert findings[0].token == "systemctl"


# --- foreign package managers (keyed off the profile's own manager) ---

def test_foreign_dnf_flagged_on_apt_target():
    findings = lint(DEBIAN, "dnf install -y redis", None)
    assert any(f.token == "dnf" for f in findings)


def test_foreign_apk_flagged_on_apt_target():
    assert any(f.token == "apk" for f in lint(DEBIAN, "apk add redis", None))


def test_native_apt_not_flagged_as_foreign():
    # apt is Debian's own manager — it must never be reported as a foreign manager.
    findings = lint(DEBIAN, "apt-get install -y openssh-server", None)
    assert not any(f.token in {"apt", "dnf", "yum", "apk", "pacman", "zypper"} for f in findings)


def test_foreign_manager_reported_once_per_manager():
    # `yum` and `rpm` both map to the dnf family -> a single finding, not two.
    findings = lint(DEBIAN, "yum install -y foo && rpm -i bar.rpm", None)
    assert len([f for f in findings if f.token == "dnf"]) == 1


# --- a fully clean build yields nothing ---

def test_clean_build_no_findings():
    deploy = "DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server netcat-openbsd\nservice ssh start"
    proc = _proc("sshpass -p pw ssh user@host 'sudo find / -exec id ;'")
    assert lint(DEBIAN, deploy, proc) == []
