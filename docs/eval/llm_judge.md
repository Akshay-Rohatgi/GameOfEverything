## Summary: Yes, LLM-as-a-Judge is the Right Approach

**Your observation is spot-on**: Golden tests for edge structure are too brittle because:
- Edge names vary legitimately (`sqli_to_creds` vs `database_credentials`)
- Edge types evolve as the planner improves
- Structural matching can't distinguish "different but correct" from "actually wrong"

LLM-as-a-judge solves this by evaluating **semantic correctness** instead of structural matching.

---

## LLM-as-a-Judge for Planning Eval

### What It Evaluates

Instead of checking exact edge names, the LLM judge asks:

1. **Logical attack path**: Can the operator reach the goal via these edges?
2. **Missing critical edges**: Are there obvious connections the planner missed?
3. **Nonsensical edges**: Are there edges that don't make sense? (e.g., SSH to a web-only system)
4. **Edge type appropriateness**: Do edge types match their semantic purpose?

### What It Ignores

The judge is **lenient** about:
- ✅ Different edge naming (treats as synonyms)
- ✅ Terminal edges with `to_entity: null` (valid for objectives)
- ✅ Extra entities/edges if they're logically sound
- ✅ Implementation details (param names, etc.)

---

## Usage

### Enable LLM Judge
```bash
.venv/bin/python -m goe.eval --suite planning \
  --golden sqli_basic \
  --request "web app with SQL injection" \
  --llm-judge
```

### Output
```
## Quality Metrics (Planning)
  Entity coverage: 100.00%  ← Golden test (cheap, fast)
  Edge coverage: 0.00%      ← Golden test (too brittle!)
  
  LLM Judge Evaluation:
    Logical attack path: ✓ Yes
    Missing edges: None
    Nonsensical edges: None
    Overall quality: ✓ good
    Reasoning: "The sqli_to_creds edge correctly models credential
                theft from SQL injection. The terminal edge pattern
                is appropriate for a single-entity scenario."
```

---

## Why This Is Better

### Golden Tests (Structural)
```yaml
expected_edges:
  - source_provides: "database_credentials"
    target_requires: "database_credentials"
```
- ❌ Fails if planner uses different edge name
- ❌ Fails on legitimate variations
- ❌ Can't handle terminal edges
- ✅ Fast, deterministic

### LLM Judge (Semantic)
```
Does the graph make sense for "web app with SQLi"?
- sqli_to_creds edge: captures credential theft ✓
- Terminal edge pattern: appropriate ✓
- Attack path viable: yes ✓
```
- ✅ Handles naming variations
- ✅ Understands semantic correctness
- ✅ Handles terminal edges naturally
- ❌ Slower (~2-3s per eval)
- ❌ Non-deterministic (LLM variance)

---

## Recommended Strategy

### Tier 1: Fast Golden Tests (Always Run)
Check only **stable, deterministic** aspects:
```yaml
# tests/fixtures/golden_plans/sqli_basic.yaml
expected_entities:
  - atoms: [sqli_union]  # Vulnerability present?

# Don't check edges with golden tests
expected_edges: []
```

### Tier 2: LLM Judge (Optional Flag)
Use `--llm-judge` for **semantic validation**:
- Run during development to validate planner changes
- Run in CI for critical scenarios
- Skip for quick iteration (use golden tests only)

---

## Implementation Details

### Judge Prompt Strategy

The judge receives:
1. **User's scenario request** (the intent)
2. **Full graph dump** (entities, edges, params)
3. **Evaluation criteria** (what to check)

And outputs structured JSON:
```json
{
  "logical_path": true,
  "missing_edges": [],
  "nonsensical_edges": [],
  "overall_quality": "good",
  "reasoning": "explanation"
}
```

### What Makes a Good Judge Prompt

✅ **Do**:
- Provide full context (scenario + graph)
- Ask specific questions (logical path? missing edges?)
- Accept valid variations (don't be prescriptive about names)
- Request structured output (JSON schema)

❌ **Don't**:
- Ask "is this perfect?" (too vague)
- Expect exact edge names (too brittle)
- Judge implementation details (focus on semantics)
- Compare against golden (that's the old approach)

---

## Example: Your SQLi Case

### What Golden Test Said
```
Edge coverage: 0.00%
Missing edges: database_credentials -> database_credentials
```
→ FAIL (but planner output is actually correct!)

### What LLM Judge Would Say
```
Logical attack path: ✓ Yes
  The sqli_to_creds edge correctly captures credential theft
  from SQL injection. The edge type 'creds_for' is semantically
  appropriate for modeling password extraction.

Missing edges: None
  All critical connections are present. The terminal edge pattern
  (to_entity: null) is correct for a single-entity scenario where
  credentials are the final objective.

Overall quality: good
```
→ PASS (planner output validated as semantically correct)

---

## When to Use LLM Judge

### Always Use (Recommended)
- Validating planner changes (did I break something?)
- Investigating eval failures (is this a real bug?)
- Baseline quality checks (is the planner generally working?)

### Sometimes Use
- Regression testing (run on critical scenarios in CI)
- Reproducibility checks (does planner output vary?)

### Don't Use
- Fast iteration loops (golden tests are faster)
- Cost-sensitive environments (LLM calls add up)
- When you only care about atom coverage (golden test sufficient)

---

## Cost/Performance

**LLM judge call**:
- Model: Haiku 4.5 (fast, cheap)
- Cost: ~$0.001 per eval
- Latency: ~2-3s per graph

**Golden test**:
- Model: None
- Cost: $0
- Latency: <1ms

**Recommendation**: Use golden tests for fast feedback, LLM judge for quality validation.

---

## Future Enhancements

1. **Cache judge results**: Hash (request + graph) → result to avoid re-judging identical graphs
2. **Multi-judge consensus**: Run 3 judges, take majority vote (more robust)
3. **Judge training**: Fine-tune on known-good/known-bad graphs
4. **Comparative judging**: "Is graph A better than graph B for this scenario?"
5. **Judge explanation mining**: Extract common failure patterns for planner improvement

---

## Testing

Test the LLM judge itself:

```bash
# Should pass
.venv/bin/python -m goe.eval --suite planning \
  --golden sqli_basic \
  --request "web app with SQL injection" \
  --llm-judge

# Should detect issues
.venv/bin/python -m goe.eval --suite planning \
  --golden sqli_basic \
  --request "multi-box network with firewall bypass" \
  --llm-judge
```

The judge should:
- ✓ Accept your `sqli_to_creds` edge (semantically correct)
- ✗ Reject nonsensical edges (SSH to web-only, etc.)
- ✗ Detect missing edges (no path to goal)

---

## Summary

**Yes, use LLM-as-a-judge for edge validation.**

Your instinct is correct: golden tests are too brittle for edge structure because legitimate variation is high. LLM judge provides semantic validation that's robust to naming changes while still catching real bugs.

**Hybrid approach** (recommended):
- Golden tests: Fast, cheap, check atom coverage
- LLM judge: Thorough, semantic, validate graph quality

Run golden tests always, LLM judge when you need confidence the graph is actually correct.
