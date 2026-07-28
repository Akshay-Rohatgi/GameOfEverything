# Game of Everything v2

Game of Everything (GoE) builds and validates intentionally vulnerable cybersecurity scenarios from natural-language requests. v2 models each scenario as an entity graph, builds the entities with direct Amazon Bedrock calls, validates their exploit procedures in Docker, and packages the result for use as a training environment.

## Requirements

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/)
- Docker
- AWS credentials with access to the configured Amazon Bedrock model

## Setup

```bash
cd game_of_everything
uv sync
cp goe.toml.example goe.toml
```

Set the AWS and model configuration in `goe.toml`, or provide it through the standard AWS environment variables.

## Run v2

```bash
# Plan, build, validate, and package a scenario.
uv run goe run "web app with SQL injection that leaks credentials"

# Produce only a graph plan.
uv run python -m goe.planner "web app with SQL injection leading to credential theft"

# Build one fixture end to end.
uv run python -m goe.build --spec tests/fixtures/entities/sqli_express.yaml

# Re-test a generated output directory without model calls.
uv run goe test output/<run_id>/

# Run the fast test suite.
uv run pytest -m "not docker and not llm"
```

See [the v2 specification](docs/architecture/v2_spec.md), [entity graph model](docs/architecture/entity_graph_model.md), and [implementation plan](docs/architecture/v2_implementation_plan.md) for the architecture and workflow details.
