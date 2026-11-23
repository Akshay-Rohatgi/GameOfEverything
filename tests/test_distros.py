"""Unit tests for distro profiles — pure, no network/Docker/LLM.

Focus: the Red Hat–family (Rocky 9 / dnf) profile added in Week 8, alongside the
existing Debian-family (apt) profiles. Asserts package-manager command rendering,
package-name aliasing, the EPEL-aware bootstrap, registration/aliases, and that the
distro lint treats a dnf target correctly.
"""

import pytest

from goe.construction_crew.distro_lint import lint
from goe.distros import (
    RHEL_BASE_PACKAGES,
    available_distros,
    get_profile,
    normalize_os,
)

ROCKY = get_profile("rocky")
DEBIAN = get_profile("debian")
MINT = get_profile("mint")


# --- registration & normalization -------------------------------------------

def test_rocky_is_registered():
    assert "rocky" in available_distros()


def test_rocky_profile_shape():
    assert ROCKY.name == "rocky"
    assert ROCKY.family == "rhel"
    assert ROCKY.image == "rockylinux:9"
    assert ROCKY.package_manager == "dnf"
    assert ROCKY.has_systemd is False  # no systemd in our containers


@pytest.mark.parametrize(
    "spelling",
    ["rocky", "Rocky", "rockylinux", "rocky9", "rocky_9", "rhel", "rhel9",
     "redhat", "red_hat", "almalinux", "alma", "centos"],
)
def test_redhat_family_spellings_resolve_to_rocky(spelling):
    assert normalize_os(spelling) == "rocky"
    assert get_profile(spelling) is ROCKY


def test_unknown_os_still_raises():
    with pytest.raises(ValueError):
        normalize_os("plan9")


# --- Linux Mint (Debian family, Week 9) -------------------------------------

def test_mint_is_registered():
    assert "mint" in available_distros()


def test_mint_profile_shape():
    assert MINT.name == "mint"
    assert MINT.family == "debian"          # inherits every Debian recipe/template/lint
    assert MINT.package_manager == "apt"
    assert MINT.has_systemd is False
    # community image, pinned by digest for reproducibility/supply-chain integrity
    assert MINT.image.startswith("linuxmintd/mint21.3-amd64@sha256:")


@pytest.mark.parametrize(
    "spelling",
    ["mint", "Mint", "MINT", "linuxmint", "linux_mint", "mint21", "mint_21", "mint21.3"],
)
def test_mint_spellings_resolve_to_mint(spelling):
    assert normalize_os(spelling) == "mint"
    assert get_profile(spelling) is MINT


def test_mint_shares_debian_family_commands():
    # Mint uses apt exactly like Debian/Ubuntu — no dnf, no --allowerasing.
    assert MINT.install_cmd(["curl"]) == DEBIAN.install_cmd(["curl"])
    assert "apt-get" in MINT.install_cmd(["curl"])
    assert "dnf" not in MINT.install_cmd(["curl"])
    assert MINT.package_aliases is DEBIAN.package_aliases


def test_lint_does_not_flag_native_apt_on_mint():
    # apt-get on a Debian-family target is native, not foreign.
    findings = lint(MINT, "apt-get install -y nginx\n", None)
    assert not any(f.token in {"apt", "apt-get", "dpkg"} for f in findings)


# --- package-manager command rendering (the real code gap) ------------------

def test_dnf_update_cmd():
    assert ROCKY.update_cmd() == "dnf -y makecache --refresh"


def test_dnf_install_cmd_uses_allowerasing():
    cmd = ROCKY.install_cmd(["openssh-server"])
    assert cmd == "dnf -y install --allowerasing openssh-server"


def test_dnf_install_cmd_is_not_apt():
    cmd = ROCKY.install_cmd(["sudo", "wget"])
    assert "apt-get" not in cmd and "DEBIAN_FRONTEND" not in cmd


def test_apt_profile_unaffected_by_dnf_branch():
    assert DEBIAN.update_cmd() == "apt-get update -y"
    assert DEBIAN.install_cmd(["sudo"]).startswith("DEBIAN_FRONTEND=noninteractive apt-get install -y")


# --- package-name aliasing ---------------------------------------------------

@pytest.mark.parametrize(
    "canonical,expected",
    [
        ("netcat", "nmap-ncat"),
        ("nc", "nmap-ncat"),
        ("netcat-openbsd", "nmap-ncat"),  # Debian-resolved name re-maps on RHEL
        ("iproute2", "iproute"),
        ("iputils-ping", "iputils"),
        ("procps", "procps-ng"),
        ("sqlite3", "sqlite"),
        ("gnupg", "gnupg2"),
        ("libcap2-bin", "libcap"),
        # Service-recipe package names that differ on the RHEL family.
        ("apache2", "httpd"),
        ("mariadb-client", "mariadb"),
        ("postgresql", "postgresql-server"),
        ("postgresql-client", "postgresql"),
        ("smbclient", "samba-client"),
    ],
)
def test_rhel_package_aliases(canonical, expected):
    assert ROCKY.resolve_pkg(canonical) == expected


def test_service_recipe_packages_resolve_for_rhel():
    # The service recipes declare Debian package names; on Rocky they must resolve
    # to real RHEL packages, or dnf's all-or-nothing transaction aborts the whole
    # install. (Regression guard for the Week-8 Rocky service smoke matrix.)
    # apache: apache2 -> httpd
    assert ROCKY.resolve_pkgs(["apache2", "curl"]) == ["httpd", "curl"]
    # mysql: server name shared, client mariadb-client -> mariadb
    assert ROCKY.resolve_pkgs(["mariadb-server", "mariadb-client"]) == [
        "mariadb-server",
        "mariadb",
    ]
    # postgres: Debian metapackage/client split maps to RHEL server/client split
    assert ROCKY.resolve_pkgs(["postgresql", "postgresql-client"]) == [
        "postgresql-server",
        "postgresql",
    ]
    # samba: daemon pkg shared, client smbclient -> samba-client
    assert ROCKY.resolve_pkgs(["samba", "smbclient"]) == ["samba", "samba-client"]


def test_service_aliases_do_not_affect_debian():
    # The RHEL service aliases must not leak onto the Debian family — the apt
    # profile keeps the original Debian package names.
    import goe.distros as d
    ubuntu = d.get_profile("ubuntu")
    assert ubuntu.resolve_pkg("apache2") == "apache2"
    assert ubuntu.resolve_pkg("mariadb-client") == "mariadb-client"
    assert ubuntu.resolve_pkg("postgresql") == "postgresql"
    assert ubuntu.resolve_pkg("smbclient") == "smbclient"


def test_target_tool_package_names_resolve_for_dnf():
    # Names flowing in from TARGET_TOOL_TO_PACKAGE (Debian-named) must resolve to
    # real RHEL packages when the target is Rocky, via install_cmd.
    cmd = ROCKY.install_cmd(["iproute2", "procps", "iputils-ping", "sqlite3"])
    assert "iproute" in cmd and "procps-ng" in cmd and "iputils" in cmd and "sqlite" in cmd
    # ...and the Debian-only names must NOT survive into the dnf command.
    assert "iproute2" not in cmd and "iputils-ping" not in cmd and "sqlite3" not in cmd


def test_unaliased_package_passes_through():
    assert ROCKY.resolve_pkg("httpd") == "httpd"


def test_redis_debian_split_collapses_to_single_rhel_package():
    # Debian's redis-server + redis-tools both map to RHEL's single `redis`
    # package, and resolve_pkgs dedupes so the install line names it once.
    assert ROCKY.resolve_pkg("redis-server") == "redis"
    assert ROCKY.resolve_pkg("redis-tools") == "redis"
    assert ROCKY.resolve_pkgs(["redis-server", "redis-tools"]) == ["redis"]
    assert ROCKY.install_cmd(["redis-server", "redis-tools"]) == (
        "dnf -y install --allowerasing redis"
    )


def test_resolve_pkgs_dedupe_preserves_order():
    assert ROCKY.resolve_pkgs(["wget", "redis-server", "curl", "redis-tools"]) == [
        "wget", "redis", "curl",
    ]


# --- base packages & bootstrap ----------------------------------------------

def test_rhel_base_packages_use_rhel_names():
    assert "libcap" in RHEL_BASE_PACKAGES
    assert "procps-ng" in RHEL_BASE_PACKAGES
    assert "iputils" in RHEL_BASE_PACKAGES
    # Debian-only / absent-on-RHEL names must not appear.
    assert "libcap2-bin" not in RHEL_BASE_PACKAGES
    assert "lsb-release" not in RHEL_BASE_PACKAGES
    assert "iproute2" not in RHEL_BASE_PACKAGES


def test_bootstrap_enables_epel_before_installing_sshpass():
    cmd = ROCKY.bootstrap_command()
    # sshpass lives in EPEL, so epel-release must be enabled first, and come before
    # the base install in the command.
    assert "epel-release" in cmd
    assert "sshpass" in cmd
    assert cmd.index("epel-release") < cmd.index("sshpass")
    assert cmd.startswith("dnf -y install epel-release")


def test_bootstrap_has_no_apt():
    assert "apt-get" not in ROCKY.bootstrap_command()


# --- service / init model is inherited correctly (no systemd) ----------------

def test_service_start_backgrounds_without_systemd():
    assert ROCKY.service_start("redis-server") == "nohup redis-server >/dev/null 2>&1 &"


def test_sanitize_systemctl_on_rocky():
    out = ROCKY.sanitize_service_control("systemctl restart sshd")
    # The active command is rewritten to a SysV `service` call; the original
    # `systemctl` survives only inside the trailing explanatory comment.
    assert "service sshd restart" in out
    code = out.split("#", 1)[0]
    assert "systemctl" not in code


# --- distro lint treats a dnf target correctly -------------------------------

def test_lint_flags_apt_as_foreign_on_rocky():
    findings = lint(ROCKY, "apt-get install -y openssh-server", None)
    assert any(f.token == "apt" for f in findings)


def test_lint_does_not_flag_native_dnf_on_rocky():
    findings = lint(ROCKY, "dnf -y install --allowerasing httpd", None)
    assert not any(f.token in {"dnf", "yum", "rpm"} for f in findings)


def test_lint_clean_dnf_build_has_no_findings():
    deploy = "dnf -y install --allowerasing httpd\nservice httpd start"
    assert lint(ROCKY, deploy, None) == []
