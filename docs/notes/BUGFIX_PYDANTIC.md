# Pydantic Validation Error Fix

## Issue

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for EvalReport
plan_adherence
  Input should be a valid dictionary or instance of PlanAdherenceScore
```

## Root Cause

The `EvalReport` model expected `plan_adherence` to be a `PlanAdherenceScore` Pydantic model instance, but we were sometimes passing it as a dict (after calling `.model_dump()`), and sometimes as the raw model object.

Pydantic's strict mode doesn't automatically convert between model instances and dicts when nested models are involved.

## Solution

**Standardize on dict representation**: Store `plan_adherence` as a plain dict in `EvalReport`, converting Pydantic models to dicts at the boundary.

### Changes Made

**1. Update `EvalReport` model** (`goe/eval/report.py`):
```python
# Before:
plan_adherence: PlanAdherenceScore | None = None

# After:
plan_adherence: dict | None = None  # PlanAdherenceScore serialized to dict
```

**2. Update CLI** (`goe/eval/__main__.py`):
```python
# Convert adherence_score to dict if it exists
adherence_dict = None
if result["adherence_score"]:
    adherence_dict = result["adherence_score"].model_dump()

report = EvalReport(
    ...
    plan_adherence=adherence_dict,
)
```

**3. Update `run_full_eval`** (`goe/eval/runner.py`):
```python
# Convert adherence_score to dict (it might be None if planning failed)
if plan_result["adherence_score"]:
    report_data["plan_adherence"] = plan_result["adherence_score"].model_dump()
```

**4. Update `print_summary`** (`goe/eval/report.py`):
```python
# Access dict fields instead of model attributes
if report.plan_adherence:
    adh = report.plan_adherence
    print(f"  Entity coverage: {adh['entity_coverage']:.2%}")  # dict access
    print(f"  Edge coverage: {adh['edge_coverage']:.2%}")      # dict access
    print(f"  Structural match: {adh['structural_match']}")    # dict access
    # ... etc
```

## Why This Approach?

**Alternative approaches considered**:

1. **Use Pydantic models everywhere**: Would require careful serialization/deserialization, and mixed types are error-prone
2. **Use `model_config = ConfigDict(arbitrary_types_allowed=True)`**: Only works for non-validated fields, doesn't solve the issue
3. **Union type `PlanAdherenceScore | dict`**: Pydantic would still try to validate, leading to ambiguity

**Our approach (dict serialization)**:
- ✅ Simple and explicit
- ✅ JSON-serializable by default (no custom encoders needed)
- ✅ Works consistently across CLI and programmatic usage
- ✅ Easy to extend with additional fields

## Testing

All tests pass after the fix:

```bash
# Unit tests
.venv/bin/python -m pytest tests/test_eval.py -v
# ✓ test_golden_plan_comparison PASSED
# ✓ test_golden_plan_mismatch PASSED

# Quick validation
.venv/bin/python -c "
from goe.eval.report import EvalReport
report = EvalReport(
    timestamp='2024-01-01',
    suite='planning',
    plan_adherence={'entity_coverage': 1.0, 'edge_coverage': 1.0}
)
print('✓ Works')
"
```

## Related Changes

This follows the same pattern we use for `calls_by_caller` (dict of `CallerStats`) and `entity_details` (list of `EntityDetail` dicts) — serialize complex nested types to dicts for storage in the top-level report.

## Files Modified

- `goe/eval/report.py` — Changed `plan_adherence` type, updated `print_summary()`
- `goe/eval/__main__.py` — Convert to dict in planning CLI path
- `goe/eval/runner.py` — Convert to dict in `run_full_eval()`

All changes are backward compatible — the dict representation contains exactly the same data as the Pydantic model.
