from pydantic import BaseModel, ConfigDict, field_validator

# ---------------------------------------------------------------------------
# System represents the machines in the attack graph, along with their network configuration and running services.
# ---------------------------------------------------------------------------


class ServiceSpec(BaseModel):
    """Specification for a service to deploy on a system.

    id: Service recipe ID (e.g., "mysql", "openssh", "nginx")
    config: Optional config overrides (e.g., {"root_password": "toor", "permit_root_login": "yes"})
    """
    model_config = ConfigDict(strict=True)

    id: str
    config: dict[str, str] = {}


class NetworkConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    hostname: str
    exposed_ports: list[int]
    internal_ports: list[int]


class System(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    os: str
    services: list[ServiceSpec]
    network: NetworkConfig

    @field_validator("services", mode="before")
    @classmethod
    def _coerce_strings(cls, v):
        """Accept bare strings for backward compat with existing YAML.

        Old format: services: ["mysql", "openssh"]
        New format: services: [{"id": "mysql", "config": {"root_password": "toor"}}, {"id": "openssh"}]
        """
        if not isinstance(v, list):
            return v
        return [ServiceSpec(id=s) if isinstance(s, str) else s for s in v]
