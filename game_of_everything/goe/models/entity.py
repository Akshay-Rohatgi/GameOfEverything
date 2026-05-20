from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# System represents the machines in the attack graph, along with their network configuration and running services.
# ---------------------------------------------------------------------------

class AppSpec(BaseModel):
    model_config = ConfigDict(strict=True)

    runtime: str  # "express" | "flask" | "apache_php"
    vulnerabilities: list[str]
    goal: str


class Requirement(BaseModel):
    model_config = ConfigDict(strict=True)

    edge_id: str
    optional: bool = False


class Entity(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    description: str
    system_id: str
    requires: list[Requirement]
    provides: list[str]
    app_spec: AppSpec | None = None
    atoms: list[str] = []
