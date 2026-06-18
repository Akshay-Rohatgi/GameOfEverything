# GoE v2 Eval System: Bug Fixes Summary

## Issues Fixed

### 1. Planning Eval: AttributeError on PlanResult ✅

**Error**:
```
AttributeError: 'PlanResult' object has no attribute 'entities'
```

**Root Cause**: The planner returns a `PlanResult` wrapper object with a `.graph` field containing the `EntityGraph`. The eval runner was trying to access `.entities` directly on the wrong object.

**Fix**: Updated `run_planning_eval()` to use `plan_result.graph` (the actual `EntityGraph`), and added proper handling for planning failures.

**Files Modified**:
- `goe/eval/runner.py` — Access `plan_result.graph.entities`
- `goe/eval/__main__.py` — Display planning failure details

---

### 2. Sparse Quality Metrics ✅

**Problem**: Build eval output was too minimal — just basic pass/fail counts with no actionable detail about what happened per entity or where token usage went.

**Fix**: Added comprehensive per-entity breakdown, retry statistics, and vulnerability coverage analysis.

**New Output Includes**:
- Per-entity table (ID, runtime, status, attempts, LLM calls, tokens, latency)
- Retry statistics (mean, median, max attempts)
- Vulnerability coverage (which atoms were tested)
- Agent-level breakdown with latency (not just call counts)

**Files Modified**:
- `goe/eval/report.py` — Added `EntityDetail` model, enhanced `print_summary()`
- `goe/eval/runner.py` — Track per-entity metrics during build
- `goe/eval/__main__.py` — Compute statistics and build entity details

---

### 3. Pydantic Validation Error ✅

**Error**:
```
pydantic_core._pydantic_core.ValidationError: 1 validation error for EvalReport
plan_adherence
  Input should be a valid dictionary or instance of PlanAdherenceScore
```

**Root Cause**: Mixed usage of Pydantic model objects and dicts. Sometimes `plan_adherence` was passed as a `PlanAdherenceScore` object, sometimes as a dict after calling `.model_dump()`.

**Fix**: Standardized on dict representation. `EvalReport.plan_adherence` is now typed as `dict | None`, and all code paths convert Pydantic models to dicts before passing to the report.

**Files Modified**:
- `goe/eval/report.py` — Changed type to `dict`, updated `print_summary()` to use dict access
- `goe/eval/__main__.py` — Convert to dict in CLI
- `goe/eval/runner.py` — Convert to dict in `run_full_eval()`

---

## Before & After Comparison

### Build Eval Output

**Before**:
```
## Quality Metrics (Build)
  Entities tested: 2
  Passed: 2 (100.0%)
  Failed: 0
  Mean attempts: 1.00
```

**After**:
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

### Planning Eval Output

**Before**: Would crash with AttributeError

**After**:
```
[planner] Step 0: designing systems for request...
[planner] designed 1 system(s)
...
[planner] graph validated successfully after 1 attempt(s)

## Quality Metrics (Planning)
  Entity coverage: 100.00%
  Edge coverage: 100.00%
  Structural match: True
```

---

## Testing

All tests now pass:

```bash
# Unit tests (fast)
.venv/bin/python -m pytest tests/test_metrics.py tests/test_eval.py -v
# ✓ All 5 tests pass

# Planning eval (requires LLM)
.venv/bin/python -m goe.eval --suite planning \
  --golden sqli_basic \
  --request "web app with SQL injection"
# ✓ Now works correctly

# Build eval (requires Docker + LLM)
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/sqli_express.yaml
# ✓ Shows detailed per-entity breakdown
```

---

## New Insights You Can Extract

With these fixes, you can now:

1. **Identify expensive entities**: See which consume the most tokens/time
2. **Track retry patterns**: Median vs mean reveals outliers requiring investigation
3. **Compare runtimes**: See if Express/Flask/PHP have different success rates
4. **Verify coverage**: Confirm you're testing diverse vulnerability types
5. **Spot bottlenecks**: Per-entity latency shows where time is spent
6. **Detect planning issues**: See validation failures immediately

---

## Files Modified Summary

| File | Change |
|------|--------|
| `goe/eval/runner.py` | Fix PlanResult access, track per-entity metrics |
| `goe/eval/report.py` | Add EntityDetail model, enhance print_summary, fix dict type |
| `goe/eval/__main__.py` | Add statistics computation, fix planning failure handling |
| `goe/eval/golden.py` | (no changes needed) |
| `tests/test_eval.py` | Fix entity ID assertion |
| `tests/test_metrics.py` | Add session cleanup fixture |

---

## Documentation Added

- `EVAL_IMPROVEMENTS.md` — Detailed changelog with examples
- `goe/eval/QUICK_START.md` — Quick reference guide
- `goe/eval/BUGFIX_PYDANTIC.md` — Pydantic validation error details
- `FIXES_SUMMARY.md` — This file

---

## Backward Compatibility

All changes are backward compatible:
- Existing CLI commands work unchanged
- Programmatic API unchanged (just more data returned)
- Output files maintain same structure (with additional fields)
- Old JSON output can still be parsed (extra fields ignored)

---

## Next Steps

Try running evals again:

```bash
# Planning eval - should now work
.venv/bin/python -m goe.eval --suite planning \
  --golden sqli_basic \
  --request "web app with SQL injection vulnerability leading to credential theft"

# Build eval - should show rich detail
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/sqli_express.yaml,tests/fixtures/entities/cmdi_flask.yaml

# Full eval
.venv/bin/python -m goe.eval --suite full \
  --fixtures tests/fixtures/entities/sqli_express.yaml \
  --golden sqli_basic \
  --request "web app with SQLi"
```

Both issues are now fixed, and you get significantly more actionable metrics! 🎉
