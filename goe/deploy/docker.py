"""Persistent local deployment of packaged scenarios with Docker Compose."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field


DOCKER_DIRNAME = ".docker"
MANIFEST_FILENAME = "manifest.json"
COMPOSE_FILENAME = "docker-compose.yml"


class DockerDeploymentError(RuntimeError):
    """A local Docker deployment operation could not complete."""


class DockerDeploymentState(str, Enum):
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"


class DockerDeploymentManifest(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal[1] = 1
    provider: Literal["docker"] = "docker"
    run_id: str
    output_dir: str
    project_name: str
    compose_file: str = COMPOSE_FILENAME
    state: DockerDeploymentState
    created_at: str
    updated_at: str
    services: dict[str, dict] = Field(default_factory=dict)
    error: str | None = None


Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def manifest_path(out_dir: Path) -> Path:
    return Path(out_dir) / DOCKER_DIRNAME / MANIFEST_FILENAME


def _write_manifest(out_dir: Path, manifest: DockerDeploymentManifest) -> None:
    manifest.updated_at = _utc_now()
    path = manifest_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_manifest(out_dir: Path) -> DockerDeploymentManifest:
    path = manifest_path(out_dir)
    if not path.is_file():
        raise FileNotFoundError(f"Docker deployment manifest not found: {path}")
    return DockerDeploymentManifest.model_validate_json(
        path.read_text(encoding="utf-8"), strict=False
    )


def default_project_name(run_id: str) -> str:
    """Return a deterministic, Compose-safe project name for a package."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", run_id.lower()).strip("-_") or "scenario"
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:8]
    return f"goe-{slug[:42]}-{digest}"


def _validate_project_name(value: str) -> str:
    if len(value) > 63 or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
        raise DockerDeploymentError(
            "Docker project name must start with a lowercase letter or digit, contain only "
            "lowercase letters, digits, hyphens, or underscores, and be at most 63 characters"
        )
    return value


def _parse_compose_json(value: str) -> list[dict]:
    """Accept both JSON-array and line-delimited Compose ``ps`` output."""
    value = value.strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        rows = []
        for line in value.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DockerDeploymentError(
                    "Docker Compose returned unrecognized service status output"
                ) from exc
            if isinstance(row, dict):
                rows.append(row)
        return rows
    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)]
    if isinstance(parsed, dict):
        return [parsed]
    raise DockerDeploymentError("Docker Compose returned unrecognized service status output")


class DockerComposeRunner:
    """Small subprocess wrapper around the Docker Compose v2 CLI."""

    def __init__(self, compose_file: Path, project_name: str):
        self.compose_file = Path(compose_file).resolve()
        self.project_name = project_name
        self.cwd = self.compose_file.parent

    @property
    def _base_command(self) -> list[str]:
        return [
            "docker",
            "compose",
            "--project-name",
            self.project_name,
            "--file",
            str(self.compose_file),
        ]

    def _run(self, args: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                self._base_command + args,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise DockerDeploymentError(
                "Docker CLI was not found; install Docker Desktop or Docker Engine with Compose v2"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DockerDeploymentError("Docker Compose operation timed out") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise DockerDeploymentError(detail or "Docker Compose command failed")
        return result

    def check_available(self) -> None:
        self._run(["version"], timeout=30)

    def up(self, timeout: int) -> None:
        self._run(
            ["up", "--detach", "--wait", "--wait-timeout", str(timeout)],
            timeout=timeout + 30,
        )

    def status(self) -> list[dict]:
        result = self._run(["ps", "--all", "--format", "json"], timeout=30)
        return _parse_compose_json(result.stdout)

    def down(self) -> None:
        self._run(["down", "--remove-orphans", "--volumes"], timeout=120)


RunnerFactory = Callable[[Path, str], DockerComposeRunner]


@contextmanager
def _operation_lock(out_dir: Path) -> Iterator[None]:
    lock = Path(out_dir) / DOCKER_DIRNAME / "operation.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise DockerDeploymentError(
            f"another Docker deployment operation may be running ({lock}); remove the lock "
            "only after confirming no goe process is active"
        ) from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode())
        os.close(descriptor)
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _service_map(rows: list[dict]) -> dict[str, dict]:
    services: dict[str, dict] = {}
    for row in rows:
        service = str(row.get("Service") or row.get("Name") or "unknown")
        services[service] = row
    return services


def deploy(
    out_dir: Path,
    *,
    project_name: str | None = None,
    timeout: int = 600,
    progress: Progress = _noop,
    runner_factory: RunnerFactory = DockerComposeRunner,
) -> DockerDeploymentManifest:
    """Start a packaged scenario locally and wait for provisioning to finish."""
    out_dir = Path(out_dir).resolve()
    if timeout < 1:
        raise DockerDeploymentError("Docker deployment timeout must be at least one second")
    compose_file = out_dir / COMPOSE_FILENAME
    if not compose_file.is_file():
        raise DockerDeploymentError(
            f"Docker Compose package not found: {compose_file}; regenerate the scenario package"
        )

    name = _validate_project_name(project_name or default_project_name(out_dir.name))
    with _operation_lock(out_dir):
        path = manifest_path(out_dir)
        if path.exists():
            existing = load_manifest(out_dir)
            if existing.state != DockerDeploymentState.DESTROYED:
                raise DockerDeploymentError(
                    f"output already has a Docker deployment in state {existing.state.value!r}; "
                    f"run 'goe status {out_dir} --provider docker' or "
                    f"'goe destroy {out_dir} --provider docker'"
                )

        now = _utc_now()
        manifest = DockerDeploymentManifest(
            run_id=out_dir.name,
            output_dir=str(out_dir),
            project_name=name,
            state=DockerDeploymentState.STARTING,
            created_at=now,
            updated_at=now,
        )
        _write_manifest(out_dir, manifest)
        runner = runner_factory(compose_file, name)
        try:
            progress("Checking Docker Compose")
            runner.check_available()
            progress("Starting and provisioning scenario containers")
            runner.up(timeout)
            rows = runner.status()
            if not rows:
                raise DockerDeploymentError("Docker Compose started no scenario services")
            manifest.services = _service_map(rows)
            manifest.state = DockerDeploymentState.READY
            manifest.error = None
            _write_manifest(out_dir, manifest)
            return manifest
        except Exception as exc:
            error = exc if isinstance(exc, DockerDeploymentError) else DockerDeploymentError(str(exc))
            manifest.state = DockerDeploymentState.FAILED
            manifest.error = str(error)
            try:
                manifest.services = _service_map(runner.status())
            except Exception:
                pass
            _write_manifest(out_dir, manifest)
            raise error


def status(
    out_dir: Path,
    *,
    runner_factory: RunnerFactory = DockerComposeRunner,
) -> tuple[DockerDeploymentManifest, list[dict]]:
    """Return the saved deployment and current Compose service state."""
    out_dir = Path(out_dir).resolve()
    manifest = load_manifest(out_dir)
    if manifest.state == DockerDeploymentState.DESTROYED:
        return manifest, []
    runner = runner_factory(out_dir / manifest.compose_file, manifest.project_name)
    runner.check_available()
    return manifest, runner.status()


def destroy(
    out_dir: Path,
    *,
    progress: Progress = _noop,
    runner_factory: RunnerFactory = DockerComposeRunner,
) -> DockerDeploymentManifest:
    """Remove a local scenario's containers, networks, and named volumes."""
    out_dir = Path(out_dir).resolve()
    with _operation_lock(out_dir):
        manifest = load_manifest(out_dir)
        if manifest.state == DockerDeploymentState.DESTROYED:
            return manifest
        manifest.state = DockerDeploymentState.DESTROYING
        _write_manifest(out_dir, manifest)
        runner = runner_factory(out_dir / manifest.compose_file, manifest.project_name)
        try:
            progress("Removing scenario containers and network")
            runner.check_available()
            runner.down()
        except Exception as exc:
            error = exc if isinstance(exc, DockerDeploymentError) else DockerDeploymentError(str(exc))
            manifest.state = DockerDeploymentState.FAILED
            manifest.error = str(error)
            _write_manifest(out_dir, manifest)
            raise error
        manifest.state = DockerDeploymentState.DESTROYED
        manifest.services = {}
        manifest.error = None
        _write_manifest(out_dir, manifest)
        return manifest
