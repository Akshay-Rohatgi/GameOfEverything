# GoE v2 Evaluation & Metrics System

## Overview

A comprehensive evaluation system for the GoE v2 pipeline that tracks both **efficiency metrics** (per-LLM-call token usage and latency) and **quality metrics** (planning adherence, build success rates, retry patterns).

## Implementation Summary

### Core Components

1. **`goe/metrics/`** — Efficiency metrics collection
   - `collector.py`: `LLMCallRecord` dataclass, `MetricsSession` context manager
   - Opt-in via context vars — no overhead when not evaluating
   - Transparent instrumentation of `goe/bedrock.py`

2. **`goe/eval/`** — Quality metrics and evaluation harness
   - `golden.py`: Golden test case comparison (planning adherence)
   - `runner.py`: Eval orchestrator (planning, build, full suites)
   - `report.py`: `EvalReport` model and CLI output formatting
   - `__main__.py`: CLI entry point

3. **Instrumentation Points** (9 locations, ~8 one-line changes)
   - `goe/bedrock.py`: Added `caller` param, extract token usage, measure latency
   - `goe/planner/_utils.py`: Thread `caller` through `call_json`
   - `goe/planner/*.py`: 4 files (design_systems, plan_entities, specify_entities, connect_edges)
   - `goe/construction_crew/*.py`: 3 files (architect, developer, attacker)
   - `goe/retry/diagnostician.py`: 1 file

4. **Data Model Extensions**
   - `goe/models/report.py`: Added `failure_category` and optional `metrics` fields to `EntityResult`
   - `goe/build.py`: Capture `DiagnosisCategory` in retry loop

### Output Format

Results written to `eval_results/<timestamp>/`:
```
summary.json           # EvalReport (tokens, latency, pass rates, failure breakdown)
llm_calls.jsonl        # All LLMCallRecord entries
entity_results.json    # Per-entity build results
plan_adherence.json    # Golden plan comparison (planning eval only)
```

### Golden Test Cases

Format: `tests/fixtures/golden_plans/<name>.yaml`
```yaml
request: "web app with SQL injection vulnerability"
expected_systems: ["web_server"]
expected_entities:
  - description: "Web app with SQLi"
    runtime: "express"
    atoms: ["sqli_union"]
expected_edges:
  - source_provides: "database_credentials"
    target_requires: "database_credentials"
```

Comparison algorithm matches entities by atom set (order-independent) and edges by provides/requires types.

## Usage

### CLI

```bash
# Build eval on specific fixtures
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/sqli_express.yaml,tests/fixtures/entities/cmdi_flask.yaml

# Planning eval against golden baseline
.venv/bin/python -m goe.eval --suite planning \
  --golden sqli_basic \
  --request "web app with SQL injection"

# Full eval suite
.venv/bin/python -m goe.eval --suite full \
  --fixtures tests/fixtures/entities/sqli_express.yaml \
  --golden sqli_basic \
  --request "web app with SQLi" \
  --output eval_results/
```

### Programmatic

```python
from goe.metrics import start_session, end_session
from goe.build import build_entity

session = start_session()
result = build_entity(entity, verbose=False)
session = end_session()

summary = session.summary()
print(f"Total tokens: {summary['total_tokens']:,}")
print(f"Calls by agent: {summary['calls_by_caller']}")
```

### Pytest Integration

```python
@pytest.mark.eval
@pytest.mark.docker
@pytest.mark.llm
def test_build_eval_sqli():
    from goe.eval.runner import run_build_eval
    result = run_build_eval(["tests/fixtures/entities/sqli_express.yaml"])
    assert result["results"][0].status.value == "PASSED"
```

## Key Design Decisions

1. **Single instrumentation point**: All LLM calls flow through `goe/bedrock.py`, enabling transparent metrics collection without touching every agent.

2. **Opt-in via context vars**: `MetricsSession` uses `contextvars.ContextVar`, so metrics are only collected when explicitly requested. No overhead for normal pipeline runs.

3. **Caller identification**: Each call site passes a string like `"architect"` or `"planner.design_systems"` for attribution. Self-review turns and retries are tagged distinctly (`"developer.self_review"`, `"architect.retry"`).

4. **Token usage from Bedrock API**: The `response["usage"]` field from Bedrock Converse API was previously discarded. Now extracted and recorded.

5. **No database**: Results are JSON on disk — diffable, versionable, CI-friendly.

6. **Backward compatible**: All changes are additive. The `caller` param defaults to `""`, and metrics are no-op when no session is active.

## Files Created

```
goe/metrics/
  __init__.py
  collector.py

goe/eval/
  __init__.py
  golden.py
  runner.py
  report.py
  __main__.py
  README.md

tests/
  test_metrics.py
  test_eval.py

tests/fixtures/golden_plans/
  sqli_basic.yaml

examples/
  eval_demo.py

EVAL_SYSTEM.md  (this file)
```

## Files Modified

```
goe/bedrock.py                      # Added caller param, token extraction, latency
goe/planner/_utils.py               # Thread caller through call_json
goe/planner/design_systems.py      # caller="planner.design_systems"
goe/planner/plan_entities.py       # caller="planner.plan_entities"
goe/planner/specify_entities.py    # caller="planner.specify_entities"
goe/planner/connect_edges.py       # caller="planner.connect_edges"
goe/construction_crew/architect.py  # caller="architect", "architect.retry"
goe/construction_crew/developer.py # caller="developer", "developer.self_review", "developer.retry"
goe/construction_crew/attacker.py  # caller="attacker", "attacker.self_review", "attacker.fix_procedure"
goe/retry/diagnostician.py         # caller="diagnostician"
goe/models/report.py                # Added failure_category, metrics fields
goe/build.py                        # Capture failure_category in retry loop
pyproject.toml                      # Added "eval" pytest marker
CLAUDE.md                           # Documented eval system
```

## Testing

```bash
# Unit tests (no Docker/LLM)
.venv/bin/python -m pytest tests/test_metrics.py tests/test_eval.py -v

# Full test suite
.venv/bin/python -m pytest tests/ -m "not docker and not llm"

# Integration test (requires Docker + AWS creds)
.venv/bin/python -m pytest tests/test_eval.py::test_build_eval_sqli_express -v -m eval
```

## Example Output

```
============================================================
Eval Report: build
Timestamp: 2026-06-12T14:30:00
============================================================

## Efficiency Metrics
  Total LLM calls: 8
  Total tokens: 12,543 (in: 8,234, out: 4,309)
  Total latency: 15.3s
  Avg latency per call: 1,912ms

  Calls by agent:
    architect                      :  1 calls,    2,145 tokens
    developer                     :  1 calls,    3,421 tokens
    developer.self_review         :  1 calls,    2,987 tokens
    attacker                      :  1 calls,    1,876 tokens
    attacker.self_review          :  1 calls,    1,654 tokens
    diagnostician                 :  1 calls      460 tokens

## Quality Metrics (Build)
  Entities tested: 1
  Passed: 1 (100.0%)
  Failed: 0
  Mean attempts: 1.00

============================================================
```

## Future Enhancements

- **Reproducibility testing**: Run planner N times on same request, measure variance in entity count/atom selection
- **Cost estimation**: Add per-model pricing table, compute estimated $ cost
- **Streaming metrics**: Real-time dashboard during long eval runs
- **Regression detection**: Compare against historical baseline, flag anomalies
- **Custom metrics**: Plugin system for domain-specific quality checks
