"""Models persisted by the AWS deployment lifecycle."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


AWS_DIRNAME = ".aws"
MANIFEST_FILENAME = "manifest.json"
INVENTORY_FILENAME = "aws_inventory.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentState(str, Enum):
    PLANNING = "planning"
    APPLYING = "applying"
    PROVISIONING = "provisioning"
    VERIFYING = "verifying"
    READY = "ready"
    FAILED = "failed"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"


class ProvisionStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class SystemDeployment(BaseModel):
    model_config = ConfigDict(strict=True)

    system_id: str
    instance_id: str
    private_ip: str
    public_ip: str | None = None
    security_group_id: str
    ssm_ready: bool = False
    provision_status: ProvisionStatus = ProvisionStatus.PENDING
    provision_exit_code: int | None = None
    readiness: dict[str, bool] = Field(default_factory=dict)
    error: str | None = None


class DeploymentManifest(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal[1] = 1
    provider: Literal["aws"] = "aws"
    run_id: str
    output_dir: str
    region: str
    profile: str | None = None
    account_id: str
    instance_type: str
    attacker_cidr: str
    state: DeploymentState
    created_at: str
    updated_at: str
    infrastructure_started: bool = False
    vpc_id: str | None = None
    artifact_bucket: str | None = None
    systems: dict[str, SystemDeployment] = Field(default_factory=dict)
    error: str | None = None


def manifest_path(out_dir: Path) -> Path:
    return Path(out_dir) / AWS_DIRNAME / MANIFEST_FILENAME


def inventory_path(out_dir: Path) -> Path:
    return Path(out_dir) / INVENTORY_FILENAME


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_manifest(out_dir: Path, manifest: DeploymentManifest) -> None:
    manifest.updated_at = utc_now()
    payload = manifest.model_dump(mode="json")
    _atomic_json(manifest_path(out_dir), payload)
    _atomic_json(inventory_path(out_dir), payload)


def load_manifest(out_dir: Path) -> DeploymentManifest:
    path = manifest_path(out_dir)
    if not path.is_file():
        raise FileNotFoundError(f"AWS deployment manifest not found: {path}")
    # JSON necessarily stores enums as strings; allow their normal round-trip
    # coercion while keeping strict construction for in-process callers.
    return DeploymentManifest.model_validate(
        json.loads(path.read_text(encoding="utf-8")), strict=False
    )
