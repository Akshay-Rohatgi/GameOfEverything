"""Atom <-> distro-family restriction: loader, catalog surfacing, fail-fast check."""

import pytest

from goe.construction_crew.atoms import supported_families
from goe.planner._atom_catalog import misconfig_atom_catalog
from goe.planner._family_check import check_atom_family_compat, AtomFamilyConflict


class _Stub:
    def __init__(self, id, system_id, atoms):
        self.id = id
        self.system_id = system_id
        self.atoms = atoms


class _Sys:
    def __init__(self, id, os):
        self.id = id
        self.os = os


# --- loader ------------------------------------------------------------------

def test_restricted_atom_reports_its_families():
    # motd relies on Debian/Ubuntu's /etc/update-motd.d — no RHEL equivalent.
    assert supported_families("motd_command_injection") == ["debian"]


def test_universal_atom_reports_no_families():
    # No `families:` frontmatter == works on every family.
    assert supported_families("sudoers_no_passwd") == []
    assert supported_families("set_suid") == []


# --- catalog surfacing (planner grounding) -----------------------------------

def test_catalog_shows_family_column():
    cat = misconfig_atom_catalog()
    assert "| Families |" in cat
    motd_row = next(l for l in cat.splitlines() if "motd_command_injection" in l)
    assert "| debian |" in motd_row
    # a universal atom shows "all"
    sudo_row = next(l for l in cat.splitlines() if "sudoers_no_passwd" in l)
    assert "| all |" in sudo_row


# --- fail-fast check ---------------------------------------------------------

def test_conflict_raises_with_actionable_message():
    with pytest.raises(AtomFamilyConflict) as ei:
        check_atom_family_compat(
            [_Stub("e1", "h1", ["motd_command_injection"])],
            [_Sys("h1", "rocky")],
            os_forced=True,
        )
    msg = str(ei.value)
    assert "motd_command_injection" in msg and "debian" in msg
    assert "--os" in msg  # remediation hint for the forced case


def test_compatible_placement_passes():
    # motd on a Debian host + a universal atom on a Rocky host: no conflict.
    check_atom_family_compat(
        [
            _Stub("e1", "h1", ["motd_command_injection"]),
            _Stub("e2", "h2", ["sudoers_no_passwd"]),
        ],
        [_Sys("h1", "debian"), _Sys("h2", "rocky")],
        os_forced=False,
    )


def test_universal_atom_allowed_on_any_family():
    check_atom_family_compat(
        [_Stub("e1", "h1", ["set_suid", "create_user"])],
        [_Sys("h1", "rocky")],
        os_forced=True,
    )


def test_unknown_system_id_is_skipped_not_crashed():
    # A stub whose system_id isn't in systems is ignored (no KeyError).
    check_atom_family_compat(
        [_Stub("e1", "ghost", ["motd_command_injection"])],
        [_Sys("h1", "rocky")],
        os_forced=False,
    )


def test_ubuntu_is_debian_family_so_motd_ok():
    # Ubuntu is the Debian family — motd is fine there.
    check_atom_family_compat(
        [_Stub("e1", "h1", ["motd_command_injection"])],
        [_Sys("h1", "ubuntu")],
        os_forced=True,
    )
