from pydantic import BaseModel, ConfigDict


class DBSetup(BaseModel):
    model_config = ConfigDict(strict=True)

    db_type: str  # "mysql" | "postgresql"
    schema_sql: str
    seed_sql: str


class BuildArtifact(BaseModel):
    model_config = ConfigDict(strict=True)

    source_files: dict[str, str]  # filename → content
    primary_source: str
    port: int
    db_setup: DBSetup | None = None
    extra_deps: list[str] = []
