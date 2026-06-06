"""EntityGraph container model with YAML serialization."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from goe.models.edge import Edge, EdgeType, ParamValue
from goe.models.entity import AppSpec, Entity, Requirement
from goe.models.system import NetworkConfig, System


class Violation(BaseModel):
    model_config = ConfigDict(strict=True)

    check: str
    entity_id: str | None = None
    edge_id: str | None = None
    message: str


class ValidationResult(BaseModel):
    model_config = ConfigDict(strict=True)

    valid: bool
    violations: list[Violation] = []


class EntityGraph(BaseModel):
    model_config = ConfigDict(strict=True)

    systems: list[System]
    entities: list[Entity]
    edges: list[Edge]

    def entity_by_id(self, eid: str) -> Entity | None:
        for e in self.entities:
            if e.id == eid:
                return e
        return None

    def edge_by_id(self, eid: str) -> Edge | None:
        for e in self.edges:
            if e.id == eid:
                return e
        return None

    def system_by_id(self, sid: str) -> System | None:
        for s in self.systems:
            if s.id == sid:
                return s
        return None

    def edges_to(self, entity_id: str) -> list[Edge]:
        return [e for e in self.edges if e.to_entity == entity_id]

    def edges_from(self, entity_id: str) -> list[Edge]:
        return [e for e in self.edges if e.from_entity == entity_id]

    @classmethod
    def from_yaml(cls, path: Path) -> "EntityGraph":
        data = yaml.safe_load(path.read_text())
        return cls.model_validate(data, strict=False)

    def to_yaml(self, path: Path) -> None:
        path.write_text(self.to_yaml_str())

    def to_yaml_str(self) -> str:
        data = self.model_dump(mode="json")
        return yaml.dump(data, default_flow_style=False, sort_keys=False)
