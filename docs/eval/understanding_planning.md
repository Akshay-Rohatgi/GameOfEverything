# Understanding Planning Eval Results

## What the Eval Actually Tells You

Planning evals compare the planner's output against a **golden baseline** to detect:
1. **Reproducibility issues** (does the planner produce consistent results?)
2. **Regression** (did a code change break planning quality?)
3. **Expected structure** (are entities and edges following expected patterns?)

## Example Output Analysis

```
## Quality Metrics (Planning)
  Entity coverage: 100.00%
  Edge coverage: 0.00%
  Structural match: False

  Missing edges (1):
    - database_credentials -> database_credentials

  Extra entities (not in golden): 1
  Extra edges (not in golden): 2
```

### What This Means

**Entity coverage: 100%** ✅
- The planner created entities with the expected atom sets
- Example: We expected an entity with `sqli_union`, and we got one

**Edge coverage: 0%** ⚠️
- The planner didn't create the specific edges we expected
- **This could be normal** if the golden test was written for a different planner version

**Structural match: False** ⚠️
- The graph structure doesn't exactly match the golden baseline
- Could indicate: golden test is outdated, planner is non-deterministic, or real quality issue

**Extra entities/edges** ℹ️
- The planner created more than we expected
- This is **often normal** — the planner is creative and may add legitimate components

## Is This a Problem?

### NOT a Problem If:
✅ Entity coverage is 100%
✅ The extra entities/edges make sense for the scenario
✅ Build evals pass (L2 testing succeeds)
✅ The planner is just being more thorough than expected

### IS a Problem If:
❌ Entity coverage < 100% (missing critical entities)
❌ The planner is creating nonsensical entities
❌ Build evals fail (L2 testing fails)
❌ Results are wildly inconsistent across runs

## Your Specific Case

```
Entity coverage: 100.00%
Edge coverage: 0.00%
Missing edges (1):
  - database_credentials -> database_credentials
```

**Diagnosis**: The golden test expected edge type `database_credentials`, but the v2 planner uses different edge types like `creds_for` and `shell_as`. This is **not a planner bug** — it's a **golden test that needs updating**.

## How Golden Tests Should Be Written

### Bad Golden Test (Too Prescriptive)
```yaml
expected_edges:
  - source_provides: "database_credentials"
    target_requires: "database_credentials"
```
→ Fails if planner uses different edge type names

### Good Golden Test (Checks Atoms)
```yaml
expected_entities:
  - description: "Web app with SQLi"
    runtime: "apache_php"
    atoms:
      - sqli_union
```
→ Flexible about implementation details

### Best Golden Test (Terminal Scenarios)
```yaml
expected_entities:
  - description: "Web app with SQLi"
    atoms:
      - sqli_union

expected_edges: []  # Terminal scenario, no entity-to-entity edges
```
→ Only checks what matters (the vulnerability atom)

## When to Update Golden Tests

Update golden tests when:
1. **Planner output format changes** (edge types renamed, etc.)
2. **Test was written for v1 but you're running v2**
3. **You verify the new output is actually better**

Don't update golden tests when:
1. Random one-off failures (check reproducibility first)
2. You don't understand why the planner output changed
3. Build evals are failing (fix the planner, not the test)

## Practical Workflow

### Step 1: Run Planning Eval
```bash
.venv/bin/python -m goe.eval --suite planning \
  --golden sqli_basic \
  --request "web app with SQL injection"
```

### Step 2: Examine Actual Output
```bash
.venv/bin/python -m goe.planner "web app with SQL injection" > output.yaml
cat output.yaml
```

### Step 3: Decide
- **If actual output looks good**: Update golden test to match
- **If actual output looks wrong**: Investigate planner bug
- **If inconsistent**: Run multiple times to check reproducibility

### Step 4: Run Build Eval to Confirm
```bash
# Convert the planned graph to a fixture and build it
# If L2 testing passes, the plan is valid even if it doesn't match golden
```

## Current Limitations

1. **Non-deterministic planning**: The planner may create slightly different graphs each time
2. **Terminal edge matching**: Our comparison doesn't handle terminal edges (`to_entity: null`) well
3. **System naming**: Golden tests expect specific system names that may vary

## Recommended Golden Test Strategy

For now, focus golden tests on **atom coverage only**:

```yaml
request: "..."
expected_systems: []  # Don't be prescriptive about systems
expected_entities:
  - atoms: [sqli_union]  # Just check the vulnerability is present
expected_edges: []  # Don't check edge structure for single-entity scenarios
```

This is more robust to planner changes while still catching major regressions.

## Summary

**Your eval result is working correctly!** It just revealed that:
1. The golden test was written with outdated expectations
2. The v2 planner uses different edge types than expected
3. The planner created extra entities/edges (could be legitimate)

The eval system did its job — it detected a mismatch. Now you need to investigate whether the mismatch is:
- A bug in the planner (needs fixing)
- A stale golden test (needs updating)
- Non-deterministic planning (expected behavior)

In your case, it's most likely **a stale golden test** based on v1 assumptions about how edges work.
