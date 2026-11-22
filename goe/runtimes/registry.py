"""RuntimeRegistry — turns a BuildArtifact into a deploy bash script deterministically."""

from __future__ import annotations

import base64
import posixpath
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from goe.models.artifacts import BuildArtifact

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _pick_family(value, family: str) -> str:
    """Resolve a template field that may be family-specific.

    A plain string is universal (used for every family). A ``{family: value}`` map
    selects the entry for ``family``, falling back to ``debian`` (the original,
    pre-Red-Hat default) then to any entry, so a template listing only some families
    still yields something sensible. Returns "" for a missing/empty field.
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        if family in value:
            return value[family] or ""
        if "debian" in value:
            return value["debian"] or ""
        return next((v for v in value.values() if v), "")
    return value


class RuntimeRegistry:
    """Loads runtime YAML templates and generates deploy scripts from BuildArtifacts."""

    def __init__(self):
        self._templates: dict[str, dict] = {}
        for path in _TEMPLATES_DIR.glob("*.yaml"):
            data = yaml.safe_load(path.read_text())
            self._templates[data["id"]] = data

    def get_template(self, runtime_id: str) -> dict:
        if runtime_id not in self._templates:
            raise ValueError(f"Unknown runtime: {runtime_id!r}. Available: {list(self._templates)}")
        return self._templates[runtime_id]

    def deploy(self, runtime_id: str, artifact: "BuildArtifact", profile=None) -> str:
        """Generate a complete bash deploy script for the given runtime and artifact.

        The script:
          1. Installs the runtime (packages, NodeSource/nodejs module, etc.)
          2. Creates app_dir and writes all source_files via base64-decoded heredocs
          3. Installs extra_deps and runtime-specific npm/pip packages
          4. Runs DB setup if artifact.db_setup is present
          5. Starts the service with nohup (Docker-compatible)

        ``profile`` is the target :class:`~goe.distros.DistroProfile`. Package installs
        render through it (apt on Debian, dnf on Red Hat), and template fields that
        differ by family (``install_runtime``, ``start_cmd``) may be a ``{family: value}``
        map resolved for the target family. On a prebuilt appliance image the packages
        are already present, so the install steps are an idempotent re-ensure; on a bare
        base image (e.g. the multi-host chain) they do the real install. Defaults to the
        ubuntu profile so existing Debian behavior is unchanged.
        """
        if profile is None:
            from goe.distros import get_profile
            profile = get_profile("ubuntu")

        t = self.get_template(runtime_id)
        app_dir = artifact.app_dir  # artifact owns the path; template provides the default
        lines = ["#!/bin/bash", "set -e", "export DEBIAN_FRONTEND=noninteractive", ""]

        # 1a. Install system deps (packages required by the app, e.g. chromium). Routed
        # through the profile so names resolve per distro (apt vs dnf + aliases).
        if artifact.system_deps:
            lines.append("# Install system dependencies")
            lines.append(profile.update_cmd())
            lines.append(profile.install_cmd(artifact.system_deps))
            lines.append("")

        # 1b. Install runtime (family-aware: template field may be a {family: value} map)
        install = _pick_family(t.get("install_runtime"), profile.family).strip()
        if install:
            lines.append("# Install runtime")
            if not artifact.system_deps:
                lines.append(profile.update_cmd())
            lines.append(install)
            lines.append("")

        # 2. Write source files via base64 to handle arbitrary content safely
        lines.append(f"mkdir -p {app_dir}")
        for filename, content in artifact.source_files.items():
            b64 = base64.b64encode(content.encode()).decode()
            dest = f"{app_dir}/{filename}"
            lines.append(f"# Write {filename}")
            # Ensure the file's parent dir exists — nested paths (e.g. views/index.ejs)
            # are preserved, and the redirection below fails under `set -e` otherwise.
            parent = posixpath.dirname(filename)
            if parent:
                lines.append(f'mkdir -p "$(dirname {dest})"')
            lines.append(f"echo '{b64}' | base64 -d > {dest}")
        lines.append("")

        # 3. Install runtime package-manager deps (base packages + extra_deps).
        # The template supplies a deps_install_template with an `{extra}` placeholder;
        # `extra` expands to the space-prefixed extra_deps (or "" when there are none),
        # so the same template covers both the deps and no-deps cases. Runtimes with no
        # package install (e.g. apache_php) simply omit deps_install_template. The value
        # may be a {family: value} map (e.g. flask gates pip's --break-system-packages,
        # a PEP-668 flag Debian 12+ needs but RHEL's older pip rejects) resolved for the
        # target family; a plain string stays universal.
        deps_template = _pick_family(t.get("deps_install_template"), profile.family).strip()
        if deps_template:
            extra = ("" if not artifact.extra_deps else " " + " ".join(artifact.extra_deps))
            lines.append(f"cd {app_dir}")
            lines.append(deps_template.format(extra=extra))
            lines.append("")

        # 4. DB setup
        if artifact.db_setup:
            db = artifact.db_setup
            lines.append("# Database setup")
            if db.db_type == "sqlite3":
                b64_schema = base64.b64encode(db.schema_sql.encode()).decode()
                b64_seed = base64.b64encode(db.seed_sql.encode()).decode()
                # sqlite3 (Debian) / sqlite (RHEL) — the profile aliases the name.
                lines.append(f"{profile.install_cmd(['sqlite3'])} 2>/dev/null || true")
                lines.append(f"echo '{b64_schema}' | base64 -d > /tmp/schema.sql")
                lines.append(f"echo '{b64_seed}' | base64 -d > /tmp/seed.sql")
                lines.append(f"sqlite3 {app_dir}/app.db < /tmp/schema.sql")
                lines.append(f"sqlite3 {app_dir}/app.db < /tmp/seed.sql")
            elif db.db_type in ("mysql", "mariadb"):
                b64_schema = base64.b64encode(db.schema_sql.encode()).decode()
                b64_seed = base64.b64encode(db.seed_sql.encode()).decode()
                lines.append(profile.install_cmd(["mariadb-server"]))
                if profile.family == "rhel":
                    # RHEL MariaDB doesn't auto-initialize its datadir; no systemd.
                    lines.append(
                        "[ -d /var/lib/mysql/mysql ] || mariadb-install-db "
                        "--user=mysql --datadir=/var/lib/mysql >/dev/null 2>&1"
                    )
                    lines.append("mysqld_safe & ")
                    lines.append("for i in $(seq 1 30); do mysqladmin ping --silent 2>/dev/null && break; sleep 1; done")
                else:
                    lines.append("service mariadb start")
                lines.append(f"echo '{b64_schema}' | base64 -d | mysql")
                lines.append(f"echo '{b64_seed}' | base64 -d | mysql")
            else:
                raise ValueError(f"Unsupported db_type: {db.db_type!r}")
            lines.append("")

        # 5. Runtime-specific pre-start setup (emitted verbatim from the template).
        pre_start = t.get("pre_start", "").strip()
        if pre_start:
            lines.append(pre_start)
            lines.append("")

        # 6. Start the service (nohup — works in Docker without systemd)
        start_cmd = _pick_family(t["start_cmd"], profile.family)
        run_as = t.get("run_as", "")
        log_file = "/var/log/webapp.log"
        lines.append("# Start application")
        lines.append(f"cd {app_dir}")
        if run_as:
            lines.append(f"id {run_as} &>/dev/null || useradd -r -s /bin/bash {run_as}")
            lines.append(f"chown -R {run_as}:{run_as} {app_dir}")
            lines.append(f"touch {log_file} && chown {run_as}:{run_as} {log_file}")
            lines.append(
                f"nohup runuser -u {run_as} -- {start_cmd} > {log_file} 2>&1 &"
            )
        else:
            lines.append(
                f"nohup {start_cmd} > {log_file} 2>&1 &"
            )
        lines.append("sleep 3")
        lines.append("")

        # 6. Healthcheck
        healthcheck = t.get("healthcheck", "")
        if healthcheck:
            lines.append("# Healthcheck")
            lines.append(f"{healthcheck} && echo 'deploy_ok' || echo 'deploy_failed'")

        return "\n".join(lines) + "\n"

    def port_for(self, runtime_id: str) -> int:
        return int(self.get_template(runtime_id)["port"])

    def image_for(self, runtime_id: str) -> str:
        """Docker image name for a runtime, from the template's `target_image` field."""
        t = self.get_template(runtime_id)
        image = t.get("target_image")
        if not image:
            raise ValueError(
                f"Runtime {runtime_id!r} template is missing a `target_image` field."
            )
        return image

    def has_runtime(self, runtime_id: str) -> bool:
        return runtime_id in self._templates

    def available_runtimes(self) -> list[str]:
        return list(self._templates)


# Module-level singleton
_registry: RuntimeRegistry | None = None


def get_registry() -> RuntimeRegistry:
    global _registry
    if _registry is None:
        _registry = RuntimeRegistry()
    return _registry
