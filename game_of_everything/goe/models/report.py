from enum import Enum
from pydantic import BaseModel, ConfigDict


class EntityStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class EntityResult(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    status: EntityStatus
    attempts: int = 1
    failure_reason: str | None = None
    skip_reason: str | None = None


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
