from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator

# ---------------------------------------------------------------------------
# Runtime enum — identifies the deployment target for an entity.
# system = OS-level/misconfig entity (no web runtime); others = web-app entities.
# NOTE: `system` is distro-independent — the actual distro comes from System.os.
# The legacy value "ubuntu" is still accepted on input (see _coerce_runtime) so
# old checkpoints, fixtures and planner output keep loading.
# ---------------------------------------------------------------------------

class Runtime(str, Enum):
    express = "express"
    flask = "flask"
    apache_php = "apache_php"
    system = "system"


# Legacy runtime spellings accepted on input and mapped onto a canonical value.
# "ubuntu" historically meant "OS-level entity", conflating the entity kind with
# a specific distro; it now maps to the distro-neutral `system`.
_RUNTIME_ALIASES: dict[str, str] = {
    "ubuntu": "system",
}


def normalize_runtime(value: str) -> str:
    """Normalize a raw runtime string to its canonical value (e.g. "ubuntu" -> "system").

    The Entity coercer applies this when loading models, but callers that handle a
    runtime as a plain string (e.g. TestEnvironment) must normalize at their boundary
    so legacy "ubuntu" input still resolves.
    """
    return _RUNTIME_ALIASES.get(value, value)


# ---------------------------------------------------------------------------
# Entity represents a node in the attack graph — either a vulnerable web
# application (runtime != system) or an OS-level/misconfig target (runtime == system).
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
    runtime: Runtime = Runtime.system
    requires: list[Requirement]
    provides: list[str]
    atoms: list[str] = []
    # Planner-pinned atom configuration. Generic atoms (e.g. database_expose) are
    # parameterized — the concrete choice (which `db_type`/`service`, which
    # credentials) is committed here at plan time, as structured data, rather than
    # being improvised by the build agent. This serves two consumers: the
    # atom->service bridge resolves `requires_service_by` against it to install the
    # right recipe deterministically, and the developer agent is constrained to
    # these exact values so generation matches the installed service.
    config: dict[str, str] = {}

    @field_validator("runtime", mode="before")
    @classmethod
    def _coerce_runtime(cls, v: object) -> "Runtime":
        """Allow plain strings (e.g. from YAML) to coerce to Runtime despite strict=True.

        Legacy spellings (notably "ubuntu") are normalized first so old data loads.
        """
        if isinstance(v, str):
            return Runtime(_RUNTIME_ALIASES.get(v, v))
        return v  # type: ignore[return-value]
