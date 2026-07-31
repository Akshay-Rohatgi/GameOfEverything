# Quick Start: GoE v2 Evaluation System

## 30-Second Overview

Run comprehensive evals on your GoE v2 pipeline:
- **Efficiency**: Token usage, latency per LLM call
- **Quality**: Pass rates, retry patterns, per-entity breakdown

## Basic Commands

```bash
# Test a single entity build
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/sqli_express.yaml

# Compare multiple entities
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/sqli_express.yaml,tests/fixtures/entities/cmdi_flask.yaml

# Test planner reproducibility
.venv/bin/python -m goe.eval --suite planning \
  --golden sqli_basic \
  --request "web app with SQL injection"
```

## What You Get

### Per-Entity Breakdown
```
Per-entity details:
  Entity ID            Runtime      Status   Attempts LLM Calls  Tokens     Latency   
  sqli_entity          express      ✓ PASSED 1        5          14,395     28.8s
  cmdi_entity          flask        ✓ PASSED 1        5          14,506     28.9s
  xss_bot_entity       express      ✓ PASSED 3        15         42,183     125.3s
```

**Insights**:
- Most entities pass on first try
- XSS admin bot required 3 attempts (expensive!)
- Express and Flask have similar performance

### Agent-Level Metrics
```
Calls by agent:
  architect                      :   2 calls,    5,282 tokens,  10.6s
  developer                     :   2 calls,    6,055 tokens,  12.1s
  developer.self_review         :   2 calls,    7,773 tokens,  15.5s
  attacker                      :   2 calls,    4,506 tokens,   9.0s
  attacker.self_review          :   2 calls,    5,285 tokens,  10.6s
```

**Insights**:
- Self-review adds ~30% token overhead but improves quality
- Developer is most expensive stage
- Attacker is cheapest

### Retry Analysis
```
Retry statistics:
  Mean attempts: 1.67
  Median attempts: 1.0
  Max attempts: 3

Failure breakdown:
  procedure_bug: 1
  implementation_bug: 1
```

**Insights**:
- Median of 1 means most entities succeed first try
- Mean > median indicates outliers exist
- Can track common failure modes

## Advanced Usage

### Run All Confirmed-Passing Fixtures
```bash
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/sqli_express.yaml,tests/fixtures/entities/cmdi_flask.yaml,tests/fixtures/entities/sqli_php.yaml,tests/fixtures/entities/xss_stored_php.yaml,tests/fixtures/entities/xss_admin_bot_express.yaml
```

### Save Results with Timestamp
```bash
.venv/bin/python -m goe.eval --suite build \
  --fixtures <paths> \
  --output eval_$(date +%Y%m%d_%H%M%S)
```

### Programmatic API
```python
from goe.metrics import start_session, end_session
from goe.build import build_entity

session = start_session()
result = build_entity(entity, verbose=False)
session = end_session()

summary = session.summary()
print(f"Total tokens: {summary['total_tokens']:,}")
print(f"Agent breakdown: {summary['calls_by_caller']}")
```

## Interpreting Results

### High Token Usage?
Check the per-entity table — one entity might be consuming most tokens due to retries.

### High Retry Rate?
Look at `failure_breakdown` to see if it's mostly `procedure_bug` (easy fix) or `implementation_bug` (harder fix).

### Slow Performance?
Check latency column — network issues or model availability can cause spikes.

### Planning Failures?
Planning eval will show validation violations — usually missing edges or invalid connections.

## Output Files

Results saved to `eval_results/<timestamp>/`:
- `summary.json` — Full EvalReport (all metrics)
- `llm_calls.jsonl` — Every LLM call (for detailed analysis)
- `entity_results.json` — Per-entity pass/fail status
- `plan_adherence.json` — Golden plan comparison (planning only)

## Common Patterns

**Baseline benchmark**: Run all fixtures, save results
```bash
.venv/bin/python -m goe.eval --suite build \
  --fixtures <all-fixtures> \
  --output baselines/baseline_$(date +%Y%m%d)
```

**Compare before/after**: Run eval before and after code changes, diff the `summary.json` files

**Track regression**: Run eval in CI, fail if pass rate drops below threshold

**Cost estimation**: Sum `total_tokens` × model price to estimate API cost

## Troubleshooting

**Planning eval fails with AttributeError**: Make sure you're using the latest code (planning eval was fixed to handle `PlanResult`)

**No entity details shown**: You might be running an old version — pull latest changes

**Metrics not collected**: Make sure you're using the CLI (not calling `build_entity` directly without a metrics session)

## Next Steps

1. Run a baseline eval with all your fixtures
2. Compare runtimes (Express vs Flask vs PHP)
3. Identify expensive entities and investigate why
4. Track retry patterns to find quality issues
5. Use in CI to catch regressions

See `goe/eval/README.md` for full documentation.
