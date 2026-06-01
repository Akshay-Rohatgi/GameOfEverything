"""RuntimeRegistry — turns a BuildArtifact into a deploy bash script deterministically."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from goe.models.artifacts import BuildArtifact

_TEMPLATES_DIR = Path(__file__).parent / "templates"


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

    def deploy(self, runtime_id: str, artifact: "BuildArtifact") -> str:
        """Generate a complete bash deploy script for the given runtime and artifact.

        The script:
          1. Installs the runtime (apt packages, NodeSource for express, etc.)
          2. Creates app_dir and writes all source_files via base64-decoded heredocs
          3. Installs extra_deps and runtime-specific npm/pip packages
          4. Runs DB setup if artifact.db_setup is present
          5. Starts the service with nohup (Docker-compatible)
        """
        t = self.get_template(runtime_id)
        app_dir = artifact.app_dir  # artifact owns the path; template provides the default
        lines = ["#!/bin/bash", "set -e", "export DEBIAN_FRONTEND=noninteractive", ""]

        # 1a. Install system deps (apt packages required by the app, e.g. chromium-browser)
        if artifact.system_deps:
            pkgs = " ".join(artifact.system_deps)
            lines.append("# Install system dependencies")
            lines.append("apt-get update -qq")
            lines.append(f"apt-get install -y {pkgs}")
            lines.append("")

        # 1b. Install runtime
        install = t.get("install_runtime", "").strip()
        if install:
            lines.append("# Install runtime")
            if not artifact.system_deps:
                lines.append("apt-get update -qq")
            lines.append(install)
            lines.append("")

        # 2. Write source files via base64 to handle arbitrary content safely
        lines.append(f"mkdir -p {app_dir}")
        for filename, content in artifact.source_files.items():
            b64 = base64.b64encode(content.encode()).decode()
            dest = f"{app_dir}/{filename}"
            lines.append(f"# Write {filename}")
            lines.append(f"echo '{b64}' | base64 -d > {dest}")
        lines.append("")

        # 3. Install extra deps + runtime package manager deps
        install_deps = t.get("install_deps_cmd", "").strip()
        if artifact.extra_deps or install_deps:
            lines.append(f"cd {app_dir}")
        if install_deps:
            if artifact.extra_deps:
                # Merge extra deps into the install command
                extra = " ".join(artifact.extra_deps)
                if runtime_id == "express":
                    lines.append(f"npm init -y && npm install express {extra}")
                elif runtime_id == "flask":
                    lines.append(f"pip3 install flask {extra}")
                else:
                    lines.append(install_deps)
            else:
                lines.append(install_deps)
            lines.append("")

        # 4. DB setup
        if artifact.db_setup:
            db = artifact.db_setup
            lines.append("# Database setup")
            if db.db_type == "sqlite3":
                b64_schema = base64.b64encode(db.schema_sql.encode()).decode()
                b64_seed = base64.b64encode(db.seed_sql.encode()).decode()
                lines.append("apt-get install -y sqlite3 -qq 2>/dev/null || true")
                lines.append(f"echo '{b64_schema}' | base64 -d > /tmp/schema.sql")
                lines.append(f"echo '{b64_seed}' | base64 -d > /tmp/seed.sql")
                lines.append(f"sqlite3 {app_dir}/app.db < /tmp/schema.sql")
                lines.append(f"sqlite3 {app_dir}/app.db < /tmp/seed.sql")
            elif db.db_type in ("mysql", "mariadb"):
                b64_schema = base64.b64encode(db.schema_sql.encode()).decode()
                b64_seed = base64.b64encode(db.seed_sql.encode()).decode()
                lines.append("apt-get install -y mariadb-server -qq")
                lines.append("service mariadb start")
                lines.append(f"echo '{b64_schema}' | base64 -d | mysql")
                lines.append(f"echo '{b64_seed}' | base64 -d | mysql")
            lines.append("")

        # 5. Runtime-specific pre-start setup
        if runtime_id == "apache_php":
            lines.append("# Ensure www-data writable dirs")
            lines.append("mkdir -p /var/db && chown www-data:www-data /var/db")
            lines.append("mkdir -p /var/www/html/uploads && chown www-data:www-data /var/www/html/uploads")
            lines.append("")

        # 6. Start the service (nohup — works in Docker without systemd)
        start_cmd = t["start_cmd"]
        log_file = "/var/log/webapp.log"
        lines.append("# Start application")
        lines.append(f"cd {app_dir}")
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

    def available_runtimes(self) -> list[str]:
        return list(self._templates)


# Module-level singleton
_registry: RuntimeRegistry | None = None


def get_registry() -> RuntimeRegistry:
    global _registry
    if _registry is None:
        _registry = RuntimeRegistry()
    return _registry
