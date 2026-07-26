# GoE v2 Eval System: Final Summary

## What We Built

A comprehensive evaluation system for the GoE v2 pipeline with:

### 1. **Efficiency Metrics** (Per-LLM-Call)
- Token usage (input/output) from Bedrock API
- Wall-clock latency per call
- Caller attribution (e.g., "engineer", "developer.self_review")
- Opt-in via `MetricsSession` context vars

### 2. **Quality Metrics** (System-Level)
- Per-entity breakdown (tokens, latency, attempts, status)
- Retry statistics (mean, median, max)
- Failure categorization (procedure_bug, implementation_bug, design_flaw)
- Vulnerability coverage tracking

### 3. **Planning Validation**
- **Golden tests:** Fast, deterministic atom coverage checks
- **LLM-as-a-judge:** Semantic graph validation for edge correctness

---

## Test Results

All systems tested and validated:

### Multi-Entity Build (2 entities)
- ✅ Both passed first try
- ✅ Per-entity breakdown shows similar performance (Express ~15k tokens, Flask ~15k tokens)
- ✅ Engineer is slowest (26s), attacker is fastest (4s)
- ✅ Total: 10 LLM calls, ~30k tokens, ~60s

### Planning with LLM Judge
- ✅ Golden test detected structural differences
- ✅ LLM judge validated semantic correctness
- ✅ Judge correctly identified "good quality" despite extras
- ✅ Total: 4 LLM calls, ~4k tokens, ~12s

### Unit Tests
- ✅ 4/4 fast tests passed
- ✅ Golden comparison logic works
- ✅ Missing entity detection works
- ✅ Metrics tracking works
- ✅ Report serialization works

---

## Key Insights

### 1. LLM Judge Solves the "Different but Correct" Problem

**Example from our tests:**
- Planner created `sqli_to_creds` edge (type: `creds_for`)
- Golden expected `database_credentials`
- **Both semantically correct**, different names

**Golden test:** ❌ Edge coverage 0% (doesn't match expected name)  
**LLM judge:** ✅ Good quality (semantically correct attack path)

### 2. Per-Entity Metrics Enable Targeted Optimization

**Before (old system):**
```
Total tokens: 29,814
Total time: 60.7s
```
→ Can't tell which entity is expensive

**After (new system):**
```
sqli_entity:  15,151 tokens, 30.1s
cmdi_entity:  14,663 tokens, 30.5s
```
→ Know exactly where resources go

### 3. Agent Breakdown Reveals Bottlenecks

```
engineer:          26.2s ← investigate if this grows
developer:         15.9s
attacker:           4.0s
```
→ Can optimize specific agents

### 4. Retry Stats Would Catch Quality Regressions

**Good (current):**
```
Mean attempts: 1.0
Max attempts: 1
```

**Bad (hypothetical):**
```
Mean attempts: 2.5  ← Something broke!
Max attempts: 5     ← One entity very broken
```

---

## Usage Patterns

### Fast Iteration
```bash
# Quick check (golden tests only, <1s)
.venv/bin/python -m goe.eval --suite planning --golden sqli_basic --request "..."
```

### Thorough Validation
```bash
# With semantic validation (+2s, +$0.001)
.venv/bin/python -m goe.eval --suite planning --golden sqli_basic --request "..." --llm-judge
```

### Comprehensive Testing
```bash
# Multi-entity build eval
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/sqli_express.yaml,tests/fixtures/entities/cmdi_flask.yaml
```

### CI/Regression
```bash
# Full eval with baseline comparison
.venv/bin/python -m goe.eval --suite full \
  --fixtures <all-fixtures> \
  --golden sqli_basic --request "..." \
  --output baselines/pr_${PR_NUMBER}
```

---

## Cost Analysis

### Per Entity (Build)
- **Tokens:** ~15k
- **Time:** ~30s (with Docker)
- **Cost:** ~$0.015 (Sonnet pricing)

### Per Planning Scenario
- **Tokens:** ~4k
- **Time:** ~12s
- **Cost:** ~$0.004 (Sonnet pricing)
- **With LLM judge:** +~500 tokens, +2s, +$0.001

### Realistic Usage
- **Daily dev:** 10-20 build evals → $0.15-$0.30
- **CI (per PR):** 5-10 build evals + 2-3 planning evals → ~$0.10-$0.20
- **Monthly (100 PRs):** ~$10-$20

→ Very affordable for continuous quality monitoring

---

## What Makes This System Good

### 1. **Actionable Data**
Not just "pass/fail" — know exactly:
- Which entity is expensive
- Which agent is slow
- Where retries happen
- What failure modes occur

### 2. **Hybrid Validation**
- Golden tests: Fast, deterministic, cheap
- LLM judge: Semantic, robust to naming changes
- Use both appropriately

### 3. **Zero Overhead When Off**
Metrics are opt-in via context vars:
- Normal builds: no overhead
- Eval runs: full instrumentation

### 4. **Rich Output**
```
eval_results/<timestamp>/
  summary.json           # All metrics
  llm_calls.jsonl        # Per-call details
  entity_results.json    # Per-entity pass/fail
  plan_adherence.json    # Golden comparison
```

### 5. **Extensible**
Easy to add:
- Custom metrics
- New golden test types
- Additional LLM judges
- Comparative analysis

---

## Documentation Created

| File | Purpose |
|------|---------|
| `goe/eval/README.md` | Complete API reference |
| `goe/eval/QUICK_START.md` | Quick reference guide |
| `EVAL_SYSTEM.md` | System architecture overview |
| `EVAL_IMPROVEMENTS.md` | Bug fixes and enhancements |
| `FIXES_SUMMARY.md` | Issue resolution summary |
| `goe/eval/UNDERSTANDING_PLANNING_EVALS.md` | How to interpret planning evals |
| `goe/eval/LLM_JUDGE_GUIDE.md` | LLM-as-a-judge rationale |
| `goe/eval/BUGFIX_PYDANTIC.md` | Pydantic validation fix |
| `EVAL_TEST_RESULTS.md` | Comprehensive test results |

---

## Files Created/Modified

### New Files (24)
```
goe/metrics/__init__.py
goe/metrics/collector.py
goe/eval/__init__.py
goe/eval/golden.py
goe/eval/runner.py
goe/eval/report.py
goe/eval/__main__.py
goe/eval/llm_judge.py
goe/eval/README.md
goe/eval/QUICK_START.md
goe/eval/UNDERSTANDING_PLANNING_EVALS.md
goe/eval/LLM_JUDGE_GUIDE.md
goe/eval/BUGFIX_PYDANTIC.md
tests/test_metrics.py
tests/test_eval.py
tests/test_eval_comprehensive.py
tests/fixtures/golden_plans/sqli_basic.yaml
tests/fixtures/golden_plans/xss_simple.yaml
examples/eval_demo.py
EVAL_SYSTEM.md
EVAL_IMPROVEMENTS.md
FIXES_SUMMARY.md
EVAL_TEST_RESULTS.md
EVAL_FINAL_SUMMARY.md
```

### Modified Files (11)
```
goe/bedrock.py                  # Token extraction, latency tracking
goe/planner/_utils.py           # Caller threading
goe/planner/design_systems.py  # caller="planner.design_systems"
goe/planner/plan_entities.py   # caller="planner.plan_entities"
goe/planner/specify_entities.py # caller="planner.specify_entities"
goe/planner/connect_edges.py   # caller="planner.connect_edges"
goe/construction_crew/engineer.py # caller="engineer"
goe/construction_crew/developer.py # caller="developer", "developer.self_review"
goe/construction_crew/attacker.py # caller="attacker", "attacker.self_review"
goe/retry/diagnostician.py     # caller="diagnostician"
goe/models/report.py            # Added failure_category, median/max attempts
goe/build.py                    # Capture failure_category
pyproject.toml                  # Added "eval" pytest marker
CLAUDE.md                       # Documented eval system
```

---

## Bottom Line

✅ **Comprehensive metrics collection** — Know where every token goes  
✅ **Quality tracking** — Pass rates, retries, failure modes  
✅ **Per-entity insights** — Identify bottlenecks and regressions  
✅ **Hybrid validation** — Golden tests + LLM judge  
✅ **Production-ready** — All tests pass, documented, affordable  

The eval system is ready for production use! 🎉

---

## Next Steps

1. **Establish baseline:** Run full eval on all fixtures, save results
2. **Set CI thresholds:** Fail PR if pass rate < 90%
3. **Track trends:** Plot token usage / pass rate over time
4. **Iterate:** Use insights to improve planner quality

The system is built — now use it to drive continuous improvement! 🚀
