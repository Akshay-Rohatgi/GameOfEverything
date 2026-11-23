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

# Base packages for every Red Hat–family (dnf) target container. These are the
# RHEL-side equivalents of APT_BASE_PACKAGES, named directly (no aliasing needed):
#   curl    -> shipped by curl-minimal, already present -> omitted (installing
#              `curl` conflicts with curl-minimal); install_cmd uses
#              --allowerasing so a recipe asking for curl still resolves cleanly.
#   gnupg        -> gnupg2        lsb-release  -> dropped (redhat-lsb-core is
#   iproute2     -> iproute                       deprecated/heavy on RHEL 9)
#   iputils-ping -> iputils       procps       -> procps-ng
#   libcap2-bin  -> libcap (provides setcap/getcap for the set_capability atom)
# sshpass lives in EPEL on RHEL, not the base repos, so bootstrap_command enables
# epel-release before installing these.
RHEL_BASE_PACKAGES: tuple[str, ...] = (
    "wget",
    "ca-certificates",
    "gnupg2",
    "iproute",
    "net-tools",
    "iputils",
    "procps-ng",
    "binutils",
    "sudo",
    "openssh-server",
    "sshpass",
    "libcap",
)

# Debian/canonical package name -> real package name on Red Hat–family distros.
# Covers both the canonical names recipes/atoms request and the Debian-named
# packages that flow in from TARGET_TOOL_TO_PACKAGE (the target tool-installer
# passes e.g. "iproute2"/"procps"/"sqlite3"), so install_cmd resolves them for dnf.
RHEL_FAMILY_ALIASES: dict[str, str] = {
    "netcat": "nmap-ncat",
    "nc": "nmap-ncat",
    "netcat-openbsd": "nmap-ncat",
    "iproute2": "iproute",
    "iputils-ping": "iputils",
    "procps": "procps-ng",
    "sqlite3": "sqlite",
    "gnupg": "gnupg2",
    "libcap2-bin": "libcap",
    "default-mysql-client": "mariadb",
    "dnsutils": "bind-utils",
    # Redis: Debian splits the daemon (redis-server) and CLI (redis-tools) into
    # separate packages; RHEL ships both binaries in a single `redis` package
    # (same /usr/bin/redis-server, /usr/bin/redis-cli, /etc/redis/redis.conf).
    "redis-server": "redis",
    "redis-tools": "redis",
    # Service-recipe package names that differ on the RHEL family. dnf installs a
    # transaction all-or-nothing, so one unknown name aborts the whole install —
    # these mappings are what let apache/mysql/postgres/samba install at all.
    # Apache HTTP server: Debian `apache2` -> RHEL `httpd` (ships apachectl, httpd,
    # DocumentRoot /var/www/html).
    "apache2": "httpd",
    # MariaDB client: Debian `mariadb-client` -> RHEL `mariadb` (the server pkg is
    # `mariadb-server` on both).
    "mariadb-client": "mariadb",
    # PostgreSQL: Debian `postgresql` is a server+client metapackage and
    # `postgresql-client` the client; RHEL splits them as `postgresql-server`
    # (daemon + initdb) and `postgresql` (client/libs).
    "postgresql": "postgresql-server",
    "postgresql-client": "postgresql",
    # Samba client (smbclient binary): Debian `smbclient` -> RHEL `samba-client`
    # (the daemon pkg is `samba` on both).
    "smbclient": "samba-client",
}


@dataclass(frozen=True)
class DistroProfile:
    """Everything the container/packaging layers need to run and operate one OS.

    name            canonical name, e.g. "ubuntu"
    family          distro family for grouping, e.g. "debian" (Ubuntu is Debian-family)
    image           docker base image, e.g. "ubuntu:22.04"
    package_manager "apt" (Debian family) or "dnf" (Red Hat family); room for "apk" later
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
        # Order-preserving dedupe: several canonical names can collapse to one real
        # package (e.g. redis-server + redis-tools -> `redis` on RHEL), and we don't
        # want to emit it twice on the install line.
        seen: set[str] = set()
        out: list[str] = []
        for n in names:
            pkg = self.resolve_pkg(n)
            if pkg not in seen:
                seen.add(pkg)
                out.append(pkg)
        return out

    # Package-manager operations

    def update_cmd(self) -> str:
        """Refresh the package index."""
        if self.package_manager == "apt":
            return "apt-get update -y"
        if self.package_manager == "dnf":
            # dnf validates/refreshes metadata on each transaction, so an explicit
            # refresh is cheap and keeps the two-line (update; install) shape callers
            # emit. `--refresh` forces a metadata check even within the cache window.
            return "dnf -y makecache --refresh"
        raise ValueError(self._unsupported_pm())

    def install_cmd(self, packages) -> str:
        """Install the given (canonical) packages, resolving names for this distro."""
        pkgs = " ".join(self.resolve_pkgs(packages))
        if self.package_manager == "apt":
            return f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkgs}"
        if self.package_manager == "dnf":
            # --allowerasing lets a requested package replace a preinstalled
            # conflicting one (e.g. `curl` over curl-minimal on RHEL 9) instead of
            # failing the transaction.
            return f"dnf -y install --allowerasing {pkgs}"
        raise ValueError(self._unsupported_pm())

    def bootstrap_command(self) -> str:
        """Command that installs the base packages into a fresh container."""
        if self.package_manager == "dnf":
            # sshpass (and some tooling) lives in EPEL on RHEL, not the base repos,
            # so enable EPEL before the base install. epel-release ships in Rocky's
            # default-enabled extras repo. dnf refreshes metadata on this install, so
            # the newly enabled EPEL repo is visible to the base-package transaction.
            return f"dnf -y install epel-release && {self.install_cmd(self.base_packages)}"
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

    # Verbs that map to a SysV `service` action; anything else (enable, daemon-reload,
    # mask, is-active, status, …) has no meaning in a no-systemd container → dropped.
    _SYSTEMCTL_ACTION = {
        "start": "start", "restart": "restart", "reload": "restart",
        "stop": "stop", "try-restart": "restart", "force-reload": "restart",
    }

    def sanitize_service_control(self, script: str) -> str:
        """Rewrite ``systemctl`` lines for a no-systemd container.

        On our containers systemd is not PID 1, so ``systemctl`` always fails — yet the
        LLM keeps emitting it to (re)start a service the recipe layer already owns. Rather
        than loop on the distro lint, translate deterministically: an action verb becomes a
        best-effort SysV ``service`` call (never fails the deploy); a non-action verb is
        dropped. Idempotent and a no-op on a systemd profile or a script without systemctl.
        """
        if self.has_systemd or not script or "systemctl" not in script:
            return script

        out: list[str] = []
        for raw in script.splitlines():
            stripped = raw.lstrip()
            indent = raw[: len(raw) - len(stripped)]
            prefix = ""
            if stripped.startswith("sudo "):
                prefix, stripped = "sudo ", stripped[5:].lstrip()
            if not stripped.startswith("systemctl"):
                out.append(raw)
                continue
            # Parse: systemctl [--flags] <verb> [<service>] ...
            toks = stripped[len("systemctl"):].split()
            verb = svc = None
            for t in toks:
                if t.startswith("-"):
                    continue
                if verb is None:
                    verb = t
                elif svc is None:
                    svc = t.split(".service")[0]
                    break
            action = self._SYSTEMCTL_ACTION.get(verb or "")
            if action and svc:
                out.append(
                    f"{indent}{prefix}service {svc} {action} 2>/dev/null || true"
                    f"  # translated from `systemctl {verb} {svc}` (no systemd)"
                )
            else:
                out.append(f"{indent}: # dropped `{stripped}` (no systemd in this container)")
        return "\n".join(out)

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
    "rocky": DistroProfile(
        name="rocky",
        family="rhel",
        image="rockylinux:9",
        package_manager="dnf",
        base_packages=RHEL_BASE_PACKAGES,
        package_aliases=RHEL_FAMILY_ALIASES,
        has_systemd=False,
    ),
    # Linux Mint 21.3 "Virginia" — Debian family, apt, built on Ubuntu 22.04 (jammy),
    # so it inherits every Debian/Ubuntu recipe, template and lint rule unchanged. No
    # Official Docker image exists for Mint, so we use Mint's own Docker Hub org image
    # (linuxmintd), pinned by digest for reproducibility and supply-chain integrity.
    "mint": DistroProfile(
        name="mint",
        family="debian",
        image="linuxmintd/mint21.3-amd64@sha256:c21780951eb8b1a626078d06e761047d21b3479aa242c11569e141581f012d11",
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
    # Red Hat family — Rocky 9 is our RHEL 9 stand-in, so RHEL/clone spellings
    # all resolve to it. The security lab needs "a RHEL box", not a specific clone.
    "rocky_linux": "rocky",
    "rockylinux": "rocky",
    "rocky9": "rocky",
    "rocky_9": "rocky",
    "rhel": "rocky",
    "rhel9": "rocky",
    "redhat": "rocky",
    "red_hat": "rocky",
    "almalinux": "rocky",
    "alma": "rocky",
    "centos": "rocky",
    # Linux Mint (Debian family) — common spellings resolve to the mint profile.
    "linuxmint": "mint",
    "linux_mint": "mint",
    "mint21": "mint",
    "mint_21": "mint",
    "mint21.3": "mint",
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
