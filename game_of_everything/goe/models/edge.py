from enum import Enum
from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Edges represent the relationships between entities in the attack graph, such as "network reachability" or "credential access". ParamValues allow for both structural and concrete values to be associated with an edge, supporting both the design and execution phases of the attack path.
# ---------------------------------------------------------------------------

class EdgeType(str, Enum):
    network_reach = "network_reach"
    shell_as = "shell_as"
    creds_for = "creds_for"
    db_session = "db_session"
    file_read = "file_read"
    file_write = "file_write"
    code_exec = "code_exec"
    token_for = "token_for"


class ParamValue(BaseModel):
    model_config = ConfigDict(strict=True)

    structural: str                 # What this param represents: "db_admin_password", "config_file_path"
    concrete: str | None = None     # Actual value, filled by builder post-build: "hunter2", "/app/.env"


class Edge(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    from_entity: str  # entity ID or "operator"
    to_entity: str | None  # None = terminal edge
    type: EdgeType
    params: dict[str, ParamValue]
