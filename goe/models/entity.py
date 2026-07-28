from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator

# ---------------------------------------------------------------------------
# Runtime enum — identifies the deployment target for an entity.
# ubuntu = system/misconfig entity (no web runtime); others = web-app entities.
# ---------------------------------------------------------------------------

class Runtime(str, Enum):
    express = "express"
    flask = "flask"
    apache_php = "apache_php"
    ubuntu = "ubuntu"


# ---------------------------------------------------------------------------
# Entity represents a node in the attack graph — either a vulnerable web
# application (runtime != ubuntu) or a system/misconfig target (runtime == ubuntu).
# ---------------------------------------------------------------------------

class Requirement(BaseModel):
    model_config = ConfigDict(strict=True)

    edge_id: str
    optional: bool = False


class Entity(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    description: str
    system_id: str
    runtime: Runtime = Runtime.ubuntu
    requires: list[Requirement]
    provides: list[str]
    atoms: list[str] = []

    @field_validator("runtime", mode="before")
    @classmethod
    def _coerce_runtime(cls, v: object) -> "Runtime":
        """Allow plain strings (e.g. from YAML) to coerce to Runtime despite strict=True."""
        if isinstance(v, str):
            return Runtime(v)
        return v  # type: ignore[return-value]
