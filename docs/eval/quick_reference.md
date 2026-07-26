# GoE v2 Eval System: Quick Reference

## One-Line Summary
**Comprehensive eval system with per-call efficiency metrics, per-entity quality tracking, and LLM-as-a-judge for semantic validation.**

---

## Basic Commands

```bash
# Build eval (single entity)
.venv/bin/python -m goe.eval --suite build --fixtures tests/fixtures/entities/sqli_express.yaml

# Build eval (multiple entities)
.venv/bin/python -m goe.eval --suite build --fixtures tests/fixtures/entities/sqli_express.yaml,tests/fixtures/entities/cmdi_flask.yaml

# Planning eval (golden test only)
.venv/bin/python -m goe.eval --suite planning --golden sqli_basic --request "web app with SQLi"

# Planning eval (with LLM judge)
.venv/bin/python -m goe.eval --suite planning --golden sqli_basic --request "web app with SQLi" --llm-judge

# Full eval (planning + build)
.venv/bin/python -m goe.eval --suite full --fixtures <paths> --golden <name> --request "<text>"
```

---

## What You Get

### Efficiency Metrics
```
Total LLM calls: 10
Total tokens: 29,814 (in: 24,773, out: 5,041)
Total latency: 60.7s
Avg latency per call: 6067ms

Calls by agent:
  engineer                      :   2 calls,    5,271 tokens,  26.2s
  developer                     :   2 calls,    6,232 tokens,  15.9s
  developer.self_review         :   2 calls,    8,134 tokens,  10.9s
```

### Quality Metrics
```
Entities tested: 2
Passed: 2 (100.0%)
Mean attempts: 1.00
Median attempts: 1.0
Max attempts: 1

Per-entity details:
  Entity ID            Runtime      Status   Attempts LLM Calls  Tokens     Latency   
  sqli_entity          express      ✓ PASSED 1        5          15,151         30.1s
  cmdi_entity          flask        ✓ PASSED 1        5          14,663         30.5s

Vulnerability coverage:
  sqli_entity: sqli_union
  cmdi_entity: cmd_injection
```

### LLM Judge (Optional)
```
LLM Judge Evaluation:
  Logical attack path: ✓ Yes
  Missing edges: None
  Nonsensical edges: None
  Overall quality: ✓ good
```

---

## Output Files

```
eval_results/<timestamp>/
  summary.json           # Full EvalReport
  llm_calls.jsonl        # Per-call details
  entity_results.json    # Per-entity pass/fail
  plan_adherence.json    # Golden comparison (planning only)
```

---

## Common Patterns

### Baseline
```bash
# Establish baseline
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/*.yaml \
  --output baselines/baseline_$(date +%Y%m%d)
```

### Regression Check
```bash
# After code change
.venv/bin/python -m goe.eval --suite build --fixtures <same-as-baseline>
# Compare token usage / pass rate / retry rate
```

### CI Integration
```bash
# In CI pipeline
.venv/bin/python -m goe.eval --suite build --fixtures <critical-fixtures>
# Fail if pass rate < 90%
```

---

## Key Metrics to Watch

| Metric | Good | Warning | Bad |
|--------|------|---------|-----|
| Pass rate | 100% | 80-99% | <80% |
| Mean attempts | 1.0 | 1.0-1.5 | >1.5 |
| Median attempts | 1.0 | 1.0 | >1.0 |
| Max attempts | 1 | 2-3 | >3 |
| Token/entity | ~15k | 15-25k | >25k |

---

## Troubleshooting

**Q: Planning eval shows 0% edge coverage but LLM judge says "good"?**  
A: Golden test is too prescriptive. The planner output is semantically correct but uses different edge names. Trust the LLM judge.

**Q: Per-entity tokens vary wildly (10k vs 40k)?**  
A: Check retry counts. High-token entities likely required multiple attempts. Investigate failure categories.

**Q: Mean attempts is 1.0 but one entity failed?**  
A: That entity exceeded max retries. Check `failure_category` in entity details for root cause.

**Q: LLM judge says "poor" but entity passed?**  
A: L2 testing passed but graph structure is questionable. Review the judge reasoning for specific issues.

---

## Cost Estimates

| Operation | Tokens | Time | Cost (Sonnet) |
|-----------|--------|------|---------------|
| Build eval (per entity) | ~15k | ~30s | ~$0.015 |
| Planning eval | ~4k | ~12s | ~$0.004 |
| LLM judge (add-on) | +500 | +2s | +$0.001 |

**Daily dev usage:** 10-20 evals → $0.15-$0.30  
**CI per PR:** 5-10 evals → $0.10-$0.20  
**Monthly (100 PRs):** ~$10-$20

---

## Documentation

- `goe/eval/README.md` — Full API docs
- `goe/eval/QUICK_START.md` — Detailed quick start
- `EVAL_FINAL_SUMMARY.md` — Complete summary
- `EVAL_TEST_RESULTS.md` — Test results analysis
- `goe/eval/LLM_JUDGE_GUIDE.md` — When to use LLM judge

---

## Quick Decision Tree

```
Need to validate code changes?
├─ Fast feedback → Golden test only
├─ Thorough check → Golden test + LLM judge
└─ Full confidence → Build eval on all fixtures

Investigating a failure?
├─ Check per-entity details → tokens, latency, attempts
├─ Check failure_category → procedure_bug? implementation_bug?
└─ Check agent breakdown → which agent is slow/failing?

Planning not matching golden?
├─ Run with --llm-judge
├─ If judge says "good" → update golden test
└─ If judge says "poor" → investigate planner
```

---

## TL;DR

```bash
# Most common command for development
.venv/bin/python -m goe.eval --suite build \
  --fixtures tests/fixtures/entities/sqli_express.yaml

# Most common command for validation
.venv/bin/python -m goe.eval --suite planning \
  --golden sqli_basic \
  --request "web app with SQL injection" \
  --llm-judge
```

**That's it!** Everything else is in the detailed docs.
