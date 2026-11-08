"""Distro profiles — per-OS configuration for target containers.

A ``DistroProfile`` is a reusable record describing how to bring up and operate a
target container for one operating system: which base image to run, which package
manager it uses (and how to invoke it), how canonical package names map to that
distro's real package names, and how services are managed.

The ``System.os`` field selects a profile by its canonical name. New OSes are
added by registering another ``DistroProfile`` here — the container, packaging,
and (later) agent layers ask the profile instead of hardcoding distro commands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

# Base packages installed into every apt-based target container, so the
# self-installing deploy scripts have the tools they rely on (curl, ssh, etc.).
# Both Ubuntu and Debian ship these under identical names.
APT_BASE_PACKAGES: tuple[str, ...] = (
    # Core utilities
    "curl",
    "wget",
    "ca-certificates",
    "gnupg",
    "lsb-release",
    # Networking tools
    "iproute2",
    "net-tools",
    "iputils-ping",
    # Process management
    "procps",
    # Binary/string inspection — attack procedures routinely use `strings`
    # (from binutils) to verify file-write primitives (e.g. keys written to a
    # Redis dump). Distro-neutral: binutils ships on every supported family.
    "binutils",
    # Common attack surface packages (pre-installed on real servers)
    "sudo",
    "openssh-server",
    "sshpass",
    # Capability tooling — provides setcap/getcap for the set_capability atom, so
    # the atom never has to install a package manager itself. (Red Hat renames this
    # to `libcap`; a future profile handles that via package_aliases.)
    "libcap2-bin",
)

# Canonical package name -> real package name on Debian-family distros.
# Lets recipes/atoms ask for a tool by a stable name and get the right package
# per distro (e.g. plain `netcat` has no install candidate on Debian 12).
DEBIAN_FAMILY_ALIASES: dict[str, str] = {
    "netcat": "netcat-openbsd",
    "nc": "netcat-openbsd",
}


@dataclass(frozen=True)
class DistroProfile:
    """Everything the container/packaging layers need to run and operate one OS.

    name            canonical name, e.g. "ubuntu"
    family          distro family for grouping, e.g. "debian" (Ubuntu is Debian-family)
    image           docker base image, e.g. "ubuntu:22.04"
    package_manager "apt" today; room for "dnf"/"apk" later
    base_packages   packages installed into every fresh container of this OS
    package_aliases canonical package name -> this distro's real package name
    has_systemd     whether systemd runs as init (False in our containers -> use
                    background processes instead of `systemctl`)
    """

    name: str
    family: str
    image: str
    package_manager: str
    base_packages: tuple[str, ...]
    package_aliases: Mapping[str, str] = field(default_factory=dict)
    has_systemd: bool = False

    # Package-name resolution

    def resolve_pkg(self, name: str) -> str:
        """Map a canonical package name to this distro's real package name."""
        return self.package_aliases.get(name, name)

    def resolve_pkgs(self, names) -> list[str]:
        return [self.resolve_pkg(n) for n in names]

    # Package-manager operations

    def update_cmd(self) -> str:
        """Refresh the package index."""
        if self.package_manager == "apt":
            return "apt-get update -y"
        raise ValueError(self._unsupported_pm())

    def install_cmd(self, packages) -> str:
        """Install the given (canonical) packages, resolving names for this distro."""
        pkgs = " ".join(self.resolve_pkgs(packages))
        if self.package_manager == "apt":
            return f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkgs}"
        raise ValueError(self._unsupported_pm())

    def bootstrap_command(self) -> str:
        """Command that installs the base packages into a fresh container."""
        return f"{self.update_cmd()} && {self.install_cmd(self.base_packages)}"

    # Service / init model

    def service_start(self, command: str) -> str:
        """Render how to start a long-running service `command` under this init.

        Our containers run without systemd, so services are started as detached
        background processes rather than via `systemctl`.
        """
        if self.has_systemd:
            return command  # a future systemd profile would translate to systemctl
        return f"nohup {command} >/dev/null 2>&1 &"

    def _unsupported_pm(self) -> str:
        return (
            f"DistroProfile {self.name!r}: unsupported package manager "
            f"{self.package_manager!r}"
        )


# Canonical profiles, keyed by canonical OS name.
_PROFILES: dict[str, DistroProfile] = {
    "ubuntu": DistroProfile(
        name="ubuntu",
        family="debian",
        image="ubuntu:22.04",
        package_manager="apt",
        base_packages=APT_BASE_PACKAGES,
        package_aliases=DEBIAN_FAMILY_ALIASES,
        has_systemd=False,
    ),
    "debian": DistroProfile(
        name="debian",
        family="debian",
        image="debian:12",
        package_manager="apt",
        base_packages=APT_BASE_PACKAGES,
        package_aliases=DEBIAN_FAMILY_ALIASES,
        has_systemd=False,
    ),
}

# Legacy / alternate spellings mapped onto a canonical profile name.
_ALIASES: dict[str, str] = {
    "ubuntu_22_04": "ubuntu",
    "ubuntu2204": "ubuntu",
    "debian_12": "debian",
    "debian12": "debian",
    "bookworm": "debian",
}


def normalize_os(os: str) -> str:
    """Return the canonical distro name for ``os``, or raise on an unknown OS."""
    key = os.strip().lower()
    key = _ALIASES.get(key, key)
    if key not in _PROFILES:
        raise ValueError(
            f"Unknown OS {os!r}. Supported: {sorted(_PROFILES)} "
            f"(aliases: {sorted(_ALIASES)})"
        )
    return key


def get_profile(os: str) -> DistroProfile:
    """Resolve the ``DistroProfile`` for ``os`` (canonical name or alias)."""
    return _PROFILES[normalize_os(os)]


def available_distros() -> list[str]:
    """Canonical names of all registered distro profiles."""
    return sorted(_PROFILES)
