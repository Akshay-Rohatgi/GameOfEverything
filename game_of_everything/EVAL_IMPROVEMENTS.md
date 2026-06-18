# Eval System Improvements

## Issues Fixed

### 1. Planning Eval Bug
**Problem**: `AttributeError: 'PlanResult' object has no attribute 'entities'`

**Root cause**: The planner returns a `PlanResult` wrapper object, not an `EntityGraph` directly. The eval runner was trying to access `.entities` on the wrong object.

**Fix**: Updated `run_planning_eval()` to use `plan_result.graph` (which contains the `EntityGraph`), and added handling for planning failures.

### 2. Sparse Quality Metrics
**Problem**: Build eval output was too minimal — just pass/fail counts with no detail about what actually happened per entity.

**Fix**: Added comprehensive per-entity breakdown including:
- Per-entity LLM calls, tokens, and latency
- Runtime and atom coverage
- Retry statistics (mean, median, max)
- Detailed table showing each entity's results

## New Output Format

### Before (Old Output)
```
## Quality Metrics (Build)
  Entities tested: 2
  Passed: 2 (100.0%)
  Failed: 0
  Mean attempts: 1.00
```

### After (New Output)
```
## Quality Metrics (Build)
  Entities tested: 2
  Passed: 2 (100.0%)
  Failed: 0

  Retry statistics:
    Mean attempts: 1.00
    Median attempts: 1.0
    Max attempts: 1

  Per-entity details:
    Entity ID            Runtime      Status   Attempts LLM Calls  Tokens     Latency   
    -------------------- ------------ -------- -------- ---------- ---------- ----------
    sqli_entity          express      ✓ PASSED 1        5          14,395     28.8s
    cmdi_entity          flask        ✓ PASSED 1        5          14,506     28.9s

  Vulnerability coverage:
    sqli_entity: sqli_union
    cmdi_entity: cmd_injection
```

## What You Now Get

### 1. Efficiency Metrics (Enhanced)
- **Per-agent breakdown**: See token usage AND latency per agent role
- **Granular caller tracking**: Distinguish between initial calls and retries (e.g., `developer` vs `developer.self_review` vs `developer.retry`)

### 2. Quality Metrics (Completely New Detail)

**Retry Statistics**:
- Mean attempts (average across all entities)
- Median attempts (more robust than mean when you have outliers)
- Max attempts (identifies worst-case entity)

**Per-Entity Table**:
- Entity ID and runtime
- Pass/fail status with visual indicator (✓/✗)
- Number of retry attempts
- LLM calls consumed by this entity
- Total tokens consumed by this entity
- Total latency for this entity

**Vulnerability Coverage**:
- Shows which atoms each entity actually used
- Useful for verifying test coverage across vulnerability types

### 3. Planning Metrics (Enhanced)
- Shows if planning succeeded or failed
- Lists validation violations if planning failed
- Shows extra entities/edges (not just missing ones)

## Example Use Cases

### Identify Expensive Entities
```
Per-entity details:
  xss_admin_bot_express  express      ✓ PASSED 3        15         42,183     125.3s
  sqli_entity            express      ✓ PASSED 1        5          14,395     28.8s
```
→ The XSS admin bot entity required 3 attempts and consumed 3× the tokens

### Track Retry Patterns
```
Retry statistics:
  Mean attempts: 1.67
  Median attempts: 1.0
  Max attempts: 3
```
→ Most entities pass on first try (median=1), but one entity needed 3 attempts

### Verify Vulnerability Coverage
```
Vulnerability coverage:
  sqli_entity: sqli_union
  cmdi_entity: cmd_injection
  xss_entity: xss_stored
  xss_bot_entity: xss_admin_bot
```
→ Confirms you're testing diverse vulnerability types

### Compare Runtimes
```
Per-entity details:
  sqli_express     express      ✓ PASSED 1        5          14,395     28.8s
  sqli_flask       flask        ✓ PASSED 1        5          14,621     29.2s
  sqli_php         apache_php   ✓ PASSED 2        10         28,841     58.3s
```
→ PHP runtime requires more retries on average

## Data Model Changes

### New Fields in `EvalReport`
```python
median_attempts: float = 0.0
max_attempts: int = 0
entity_details: list[EntityDetail] = []
```

### New `EntityDetail` Model
```python
class EntityDetail(BaseModel):
    id: str
    runtime: str
    atoms: list[str]
    status: str
    attempts: int
    failure_category: str | None = None
    failure_reason: str | None = None
    llm_calls: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
```

## Running the Improved Eval

Same commands work, output is just more detailed:

```bash
# Single entity
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/sqli_express.yaml

# Multiple entities (now shows per-entity comparison)
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/sqli_express.yaml,tests/fixtures/entities/cmdi_flask.yaml

# Planning eval (now shows planning success/failure)
.venv/bin/python -m goe.eval --suite planning \
  --golden sqli_basic \
  --request "web app with SQL injection"
```

## Files Modified

- `goe/eval/runner.py`: Track per-entity metrics, compute median/max attempts
- `goe/eval/report.py`: Added `EntityDetail` model, enhanced `print_summary()`
- `goe/eval/__main__.py`: Build entity details, compute statistics
- `tests/test_eval.py`: Fixed entity ID assertion

All existing functionality preserved — changes are purely additive.
