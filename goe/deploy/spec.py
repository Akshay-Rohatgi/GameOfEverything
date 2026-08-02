"""Durable, provider-specific contract between packaging and AWS deployment.

The deploy command deliberately consumes this small specification instead of
reaching into ``output/.checkpoints``.  It contains topology and filenames, but
not generated edge secrets or script contents.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

if TYPE_CHECKING:
    from goe.graph.models import EntityGraph


SPEC_FILENAME = "aws_spec.json"


class AwsSystemSpec(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    hostname: str
    os: str
    script: str
    public: bool
    exposed_ports: list[int]
    internal_ports: list[int]

    @field_validator("id")
    @classmethod
    def _id_is_safe(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}", value):
            raise ValueError("system id must be a short alphanumeric identifier")
        return value

    @field_validator("hostname")
    @classmethod
    def _hostname_is_safe(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", value):
            raise ValueError("hostname must be a single DNS-safe label")
        return value

    @field_validator("script")
    @classmethod
    def _script_is_a_filename(cls, value: str) -> str:
        if not value or PurePath(value).name != value or value in (".", ".."):
            raise ValueError("script must be a filename inside the output package")
        return value

    @field_validator("exposed_ports", "internal_ports")
    @classmethod
    def _ports_are_valid(cls, value: list[int]) -> list[int]:
        if any(port < 1 or port > 65535 for port in value):
            raise ValueError("ports must be between 1 and 65535")
        if len(value) != len(set(value)):
            raise ValueError("ports must not contain duplicates")
        return sorted(value)

    @model_validator(mode="after")
    def _port_sets_do_not_overlap(self) -> "AwsSystemSpec":
        overlap = set(self.exposed_ports) & set(self.internal_ports)
        if overlap:
            raise ValueError(f"ports cannot be both exposed and internal: {sorted(overlap)}")
        return self


class AwsDeploymentSpec(BaseModel):
    model_config = ConfigDict(strict=True)

    schema_version: Literal[1] = 1
    run_id: str
    entry_system_ids: list[str]
    systems: list[AwsSystemSpec]

    @model_validator(mode="after")
    def _validate_topology(self) -> "AwsDeploymentSpec":
        ids = [system.id for system in self.systems]
        if not ids:
            raise ValueError("deployment must contain at least one system")
        if len(ids) != len(set(ids)):
            raise ValueError("system ids must be unique")
        hostnames = [system.hostname for system in self.systems]
        if len(hostnames) != len(set(hostnames)):
            raise ValueError("system hostnames must be unique")
        unknown = set(self.entry_system_ids) - set(ids)
        if unknown:
            raise ValueError(f"entry systems do not exist: {sorted(unknown)}")
        if not self.entry_system_ids:
            raise ValueError("deployment must contain at least one entry system")
        if len(self.entry_system_ids) != len(set(self.entry_system_ids)):
            raise ValueError("entry system ids must be unique")
        public = {system.id for system in self.systems if system.public}
        if public != set(self.entry_system_ids):
            raise ValueError("public systems must exactly match entry_system_ids")
        return self


def _entry_system_ids(graph: "EntityGraph") -> list[str]:
    """Infer externally reachable systems from explicit operator reachability.

    Operator-originated ``network_reach`` edges are the strongest signal that a
    system needs a public address.  An entity with no required non-optional edge
    is also an initial-access entity and therefore a fallback entry point.
    """
    entry: set[str] = set()
    for edge in graph.edges:
        if edge.from_entity != "operator" or edge.type.value != "network_reach":
            continue
        if edge.to_entity:
            entity = graph.entity_by_id(edge.to_entity)
            if entity is not None:
                entry.add(entity.system_id)

    for entity in graph.entities:
        required = [requirement for requirement in entity.requires if not requirement.optional]
        all_from_operator = bool(required) and all(
            (edge := graph.edge_by_id(requirement.edge_id)) is not None
            and edge.from_entity == "operator"
            for requirement in required
        )
        if not required or all_from_operator:
            entry.add(entity.system_id)

    system_order = [system.id for system in graph.systems]
    return [system_id for system_id in system_order if system_id in entry]


def build_deployment_spec(graph: "EntityGraph", out_dir: Path) -> AwsDeploymentSpec:
    """Translate an entity graph and package layout into an AWS deployment spec."""
    out_dir = Path(out_dir)
    entry_system_ids = _entry_system_ids(graph)
    multi = len(graph.systems) > 1
    systems = []
    for system in graph.systems:
        script = f"{system.id}_deploy.sh" if multi else "deploy.sh"
        systems.append(
            AwsSystemSpec(
                id=system.id,
                hostname=system.network.hostname,
                os=system.os,
                script=script,
                public=system.id in entry_system_ids,
                exposed_ports=system.network.exposed_ports,
                internal_ports=system.network.internal_ports,
            )
        )
    return AwsDeploymentSpec(
        run_id=out_dir.name,
        entry_system_ids=entry_system_ids,
        systems=systems,
    )


def write_deployment_spec(graph: "EntityGraph", out_dir: Path) -> Path:
    spec = build_deployment_spec(graph, out_dir)
    path = Path(out_dir) / SPEC_FILENAME
    path.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_deployment_spec(out_dir: Path) -> AwsDeploymentSpec:
    path = Path(out_dir) / SPEC_FILENAME
    if not path.is_file():
        raise FileNotFoundError(f"AWS deployment specification not found: {path}")
    return AwsDeploymentSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))
