"""Adaptive probe registry for diagnostician evidence collection.

Probes are declarative evidence-gathering units that run only when relevant
to the entity's runtime, atoms, and artifact metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from goe.container.environment import TestEnvironment


@dataclass
class ProbeContext:
    """Metadata extracted from entity/artifact for probe selection."""

    runtime: str  # "express", "flask", "apache_php", "ubuntu"
    atoms: list[str]  # atom IDs
    has_db: bool  # artifact.db_setup is not None
    db_type: str | None  # "sqlite3", "mysql", "mariadb", None
    app_dir: str  # "/opt/webapp" or ""
    port: int | None  # None for ubuntu entities
    source_files: list[str]  # filenames in artifact
    failed_step: str | None  # step_id that failed
    system_deps: list[str]  # extra apt packages


@dataclass
class Probe:
    """Declarative evidence-gathering unit."""

    id: str
    label: str  # Human-readable label shown to LLM
    command: str  # Bash command, may contain {app_dir}, {port}
    relevance: Callable[[ProbeContext], bool]  # When to include this probe
    priority: int = 5  # Lower = more important (for budget trimming)
    max_output: int = 1500  # Truncate stdout beyond this


def _atom_mentions(atoms: list[str], keywords: set[str]) -> bool:
    """True if any atom ID contains any of the keywords (fuzzy matching)."""
    return any(kw in atom.lower() for atom in atoms for kw in keywords)


# ============================================================================
# PROBE DEFINITIONS
# ============================================================================

# Universal probes (always included)
UNIVERSAL_PROBES = [
    Probe(
        id="processes",
        label="Running Processes (top 25 by memory)",
        command="ps aux --sort=-rss 2>/dev/null | head -25 || ps aux | head -25",
        relevance=lambda ctx: True,
        priority=1,
    ),
    Probe(
        id="listening_ports",
        label="Listening Ports",
        command="ss -tlnp 2>/dev/null | head -20 || netstat -tlnp 2>/dev/null | head -20",
        relevance=lambda ctx: True,
        priority=1,
    ),
    Probe(
        id="recent_errors",
        label="Recent System Errors",
        command="journalctl --no-pager -n 20 --priority=err 2>/dev/null || dmesg | tail -10",
        relevance=lambda ctx: True,
        priority=3,
    ),
]

# Web runtime probes
WEB_RUNTIME_PROBES = [
    Probe(
        id="webapp_log",
        label="Web App Log (last 50 lines)",
        command="tail -50 /var/log/webapp.log 2>/dev/null || echo '(no log)'",
        relevance=lambda ctx: ctx.runtime != "ubuntu",
        priority=2,
    ),
    Probe(
        id="app_healthcheck",
        label="App Health Check",
        command="curl -sf --max-time 3 http://localhost:{port}/ 2>&1 || echo 'UNREACHABLE'",
        relevance=lambda ctx: ctx.port is not None,
        priority=2,
    ),
    Probe(
        id="app_dir_listing",
        label="App Directory Listing",
        command="ls -la {app_dir}/ 2>/dev/null | head -20 || echo '(directory not found)'",
        relevance=lambda ctx: ctx.runtime != "ubuntu" and ctx.app_dir,
        priority=4,
    ),
]

# Express/Node probes
EXPRESS_PROBES = [
    Probe(
        id="adminbot_log",
        label="Admin Bot Log (last 30 lines)",
        command="tail -30 /tmp/adminbot.log 2>/dev/null || echo '(no bot log)'",
        relevance=lambda ctx: (
            ctx.runtime == "express"
            and _atom_mentions(ctx.atoms, {"xss", "admin", "bot"})
        ),
        priority=3,
    ),
    Probe(
        id="node_modules_check",
        label="Node Modules Status",
        command="[ -d {app_dir}/node_modules ] && echo 'npm packages installed' || echo 'NO node_modules'",
        relevance=lambda ctx: ctx.runtime == "express",
        priority=5,
    ),
]

# Apache/PHP probes
APACHE_PROBES = [
    Probe(
        id="apache_error_log",
        label="Apache Error Log (last 30 lines)",
        command="tail -30 /var/log/apache2/error.log 2>/dev/null || echo '(no apache log)'",
        relevance=lambda ctx: ctx.runtime == "apache_php",
        priority=3,
    ),
]

# Database probes
DATABASE_PROBES = [
    Probe(
        id="sqlite_tables",
        label="SQLite Tables",
        command="sqlite3 {app_dir}/app.db '.tables' 2>/dev/null || echo '(no db)'",
        relevance=lambda ctx: ctx.db_type == "sqlite3",
        priority=3,
    ),
    Probe(
        id="sqlite_schema",
        label="SQLite Schema (first 30 lines)",
        command="sqlite3 {app_dir}/app.db '.schema' 2>/dev/null | head -30 || echo '(no schema)'",
        relevance=lambda ctx: ctx.db_type == "sqlite3",
        priority=4,
    ),
    Probe(
        id="mysql_status",
        label="MySQL Status",
        command="mysqladmin ping -h localhost --silent 2>&1 && mysql -e 'SHOW DATABASES;' 2>&1 | head -10 || echo '(mysql unreachable)'",
        relevance=lambda ctx: ctx.db_type in ("mysql", "mariadb"),
        priority=3,
    ),
]

# Ubuntu/misconfig probes
UBUNTU_PROBES = [
    Probe(
        id="users_and_groups",
        label="Users and Groups",
        command="cat /etc/passwd | grep -v nologin | grep -v '/bin/false' | head -20",
        relevance=lambda ctx: ctx.runtime == "ubuntu",
        priority=2,
    ),
]

# Atom-specific probes
ATOM_SPECIFIC_PROBES = [
    Probe(
        id="suid_binaries",
        label="SUID Binaries (first 15)",
        command="find / -perm -4000 -type f 2>/dev/null | head -15 || echo '(none found)'",
        relevance=lambda ctx: _atom_mentions(ctx.atoms, {"suid", "setuid"}),
        priority=3,
    ),
    Probe(
        id="capabilities",
        label="File Capabilities (first 10)",
        command="getcap -r / 2>/dev/null | head -10 || echo '(none found)'",
        relevance=lambda ctx: _atom_mentions(ctx.atoms, {"capability", "cap_"}),
        priority=3,
    ),
    Probe(
        id="sudoers",
        label="Sudoers Configuration",
        command="cat /etc/sudoers /etc/sudoers.d/* 2>/dev/null | grep -v '^#' | grep -v '^$' | head -20",
        relevance=lambda ctx: _atom_mentions(ctx.atoms, {"sudo"}),
        priority=3,
    ),
    Probe(
        id="sshd_config",
        label="SSH Server Configuration",
        command="cat /etc/ssh/sshd_config 2>/dev/null | grep -v '^#' | grep -v '^$' | head -30",
        relevance=lambda ctx: (
            _atom_mentions(ctx.atoms, {"ssh", "weak_service"})
            or "openssh" in ctx.system_deps
            or "sshpass" in ctx.system_deps
        ),
        priority=3,
    ),
    Probe(
        id="samba_config",
        label="Samba Configuration",
        command="testparm -s 2>/dev/null | head -30 || echo '(samba not configured)'",
        relevance=lambda ctx: _atom_mentions(ctx.atoms, {"samba", "smb", "cifs"}),
        priority=3,
    ),
    Probe(
        id="cron_jobs",
        label="Cron Jobs",
        command="crontab -l 2>/dev/null; ls -la /etc/cron.d/ /var/spool/cron/crontabs/ 2>/dev/null | head -15",
        relevance=lambda ctx: _atom_mentions(ctx.atoms, {"cron"}),
        priority=4,
    ),
    Probe(
        id="file_permissions",
        label="World-Writable Files (first 15)",
        command="find /etc /opt /home /tmp -perm -o+w -type f 2>/dev/null | head -15 || echo '(none found)'",
        relevance=lambda ctx: _atom_mentions(
            ctx.atoms, {"writable", "sensitive_file", "systemd"}
        ),
        priority=4,
    ),
]

# Combine all probes
ALL_PROBES = (
    UNIVERSAL_PROBES
    + WEB_RUNTIME_PROBES
    + EXPRESS_PROBES
    + APACHE_PROBES
    + DATABASE_PROBES
    + UBUNTU_PROBES
    + ATOM_SPECIFIC_PROBES
)


# ============================================================================
# SELECTION AND EXECUTION
# ============================================================================


def select_probes(ctx: ProbeContext) -> list[Probe]:
    """Select relevant probes, respecting the budget of max 10 commands."""
    selected = [p for p in ALL_PROBES if p.relevance(ctx)]
    selected.sort(key=lambda p: p.priority)
    return selected[:10]  # Hard cap


def execute_probes(
    env: "TestEnvironment", probes: list[Probe], ctx: ProbeContext
) -> list[tuple[str, str]]:
    """Run each probe and return (label, output) pairs."""
    results = []
    for probe in probes:
        # Interpolate template variables
        cmd = probe.command.format(
            app_dir=ctx.app_dir or "/opt/webapp", port=ctx.port or 80
        )

        _, stdout, stderr = env.exec_in("target", cmd)
        output = stdout or stderr or "(empty)"

        # Truncate to max_output
        if len(output) > probe.max_output:
            output = output[: probe.max_output] + "\n... (truncated)"

        results.append((probe.label, output))

    return results
