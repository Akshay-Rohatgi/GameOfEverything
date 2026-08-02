from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from goe.models.procedure import Procedure


class EntityStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class EntityResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")

    id: str
    status: EntityStatus
    attempts: int = 1
    failure_reason: str | None = None
    skip_reason: str | None = None
    failure_category: str | None = None  # DiagnosisCategory value
    metrics: list[dict] | None = None  # Optional serialized LLMCallRecords


@dataclass
class BuildOutcome:
    """Full result of build_entity() — the EntityResult plus the artifacts the
    orchestrator needs for value propagation and packaging.

    On failure, deploy_script/procedure are None and outgoing_values is empty.
    """

    result: "EntityResult"
    deploy_script: str | None = None
    procedure: "Procedure | None" = None
    outgoing_values: dict[str, dict[str, str]] = field(default_factory=dict)  # edge_id → {param: value}


class ChainTestStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ChainTestResult(BaseModel):
    model_config = ConfigDict(strict=True)

    status: ChainTestStatus
    broken_edge: str | None = None
    reason: str | None = None


class BuildReport(BaseModel):
    model_config = ConfigDict(strict=True)

    entities: list[EntityResult]
    chain_test: ChainTestResult | None = None
