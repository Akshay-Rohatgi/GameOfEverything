# GoE v2 Evaluation System

This package provides metrics collection and quality evaluation for the GoE v2 pipeline.

## Features

### 1. Efficiency Metrics (Per-LLM-Call)

Every LLM call through `goe.bedrock.call()` is instrumented to capture:
- Input/output token counts
- Latency (ms)
- Model ID
- Caller identity (e.g., "architect", "planner.design_systems")

Metrics are collected in an opt-in `MetricsSession` context.

### 2. Quality Metrics (System-Level)

- **Planning adherence**: Compare planner output against golden test cases
- **Build pass rates**: Track L2 test success/failure across fixtures
- **Retry counts**: Mean attempts per entity
- **Failure breakdown**: Distribution of `DiagnosisCategory` values

## Usage

### CLI

```bash
# Run build eval on specific fixtures
.venv/bin/python -m goe.eval --suite build --fixtures tests/fixtures/entities/sqli_express.yaml

# Run planning eval against a golden case
.venv/bin/python -m goe.eval --suite planning --golden sqli_basic --request "web app with SQLi"

# Run full eval suite
.venv/bin/python -m goe.eval --suite full \
  --fixtures tests/fixtures/entities/sqli_express.yaml,tests/fixtures/entities/cmdi_flask.yaml \
  --golden sqli_basic \
  --request "web app with SQL injection"
```

### Programmatic

```python
from goe.metrics import start_session, end_session
from goe.build import build_entity

# Start metrics collection
session = start_session()

# Run pipeline work
result = build_entity(entity, verbose=False)

# End session and get metrics
session = end_session()
summary = session.summary()

print(f"Total calls: {summary['total_calls']}")
print(f"Total tokens: {summary['total_tokens']}")
print(f"Calls by caller: {summary['calls_by_caller']}")
```

### Golden Test Cases

Golden plans are stored in `tests/fixtures/golden_plans/` as YAML:

```yaml
request: "web app with SQL injection vulnerability"

expected_systems:
  - web_server

expected_entities:
  - description: "Web application with SQL injection"
    runtime: "express"
    atoms:
      - sqli_union

expected_edges:
  - source_provides: "database_credentials"
    target_requires: "database_credentials"
```

The comparison algorithm matches entities by atom set (order-independent) and edges by provides/requires types.

## Output Format

Results are written to `eval_results/<timestamp>/`:

```
summary.json           # EvalReport model
llm_calls.jsonl        # All LLMCallRecord entries
entity_results.json    # Per-entity build results
plan_adherence.json    # Golden plan comparison (if planning eval)
```

## Pytest Integration

Mark eval tests with `@pytest.mark.eval`:

```python
@pytest.mark.eval
@pytest.mark.docker
@pytest.mark.llm
def test_build_eval_sqli():
    from goe.eval.runner import run_build_eval
    result = run_build_eval(["tests/fixtures/entities/sqli_express.yaml"])
    assert result["results"][0].status.value == "PASSED"
```

## Design Notes

- **Single instrumentation point**: All LLM calls flow through `goe.bedrock.call()`, making metrics collection transparent.
- **Opt-in**: `MetricsSession` is a context var. When no session is active, calls proceed normally with no overhead.
- **Caller identification**: Each call site passes a `caller=` string (e.g., "architect", "planner.design_systems") for attribution.
- **No database**: Results are JSON on disk — diffable, CI-friendly, and easy to version.
