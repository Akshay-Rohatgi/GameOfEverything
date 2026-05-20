from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# System represents the machines in the attack graph, along with their network configuration and running services.
# ---------------------------------------------------------------------------

class NetworkConfig(BaseModel):
    model_config = ConfigDict(strict=True)

    hostname: str
    exposed_ports: list[int]
    internal_ports: list[int]


class System(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    os: str
    services: list[str]
    network: NetworkConfig
