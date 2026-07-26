# Diagnostics System Improvements

**Date:** 2026-07-12  
**Context:** Addressing excessive token usage and low success rates in chain tests

---

## Problem Statement

Recent run analysis (`output/20260711T214319_...`) revealed:
- Only **1/4 entities passed** (25% success rate)
- Estimated **150k+ tokens wasted** on stuck retry loops
- `bash_history_credential_discovery` entity failed with implementation_bug but **retries couldn't fix it**
- Root cause: developer created world-readable file but forgot to `chmod 755 /home/stacy` (directory needs +x for traversal)
- Diagnostician repeatedly categorized as `implementation_bug` → developer regenerated code → same issue → wasted ~40k tokens per retry

---

## Changes Implemented

### 1. Entity Diagnostician: Enhanced Prompt Guidance

**File:** `goe/retry/diagnostician.py`

Added "Common Failure Patterns" section to teach the diagnostician about:

#### File Permission Issues
- **Problem**: File can be world-readable (0644) but inaccessible if parent directory lacks execute permission
- **Example**: `/home/stacy/.bash_history` is 0644, but `/home/stacy` is 0750 → www-data cannot traverse
- **Fix**: Developer must `chmod 755 /home/<user>` to allow directory traversal
- **Category**: `implementation_bug` (vulnerability setup is incomplete)

#### Process Debugging
- Process not in `ps aux` or port not in `ss -tlnp` → `design_flaw` (startup failed)
- Process running but misbehaving → `implementation_bug`

#### Permission Denied vs File Not Found
- "Permission denied" → check directory permissions (implementation_bug)
- "No such file or directory" → wrong path or file not created (procedure_bug or implementation_bug)

**Impact:**
- Diagnostician now understands filesystem permission mechanics
- Correctly identifies when developer needs to fix directory traversal
- No band-aid probes needed — guidance alone teaches correct diagnosis

---

### 2. Entity Retry: Stuck Loop Detection & Escalation

**File:** `goe/build.py`

**Problem:** Entity diagnostician could waste retries on same category:
```
Attempt 1: implementation_bug → developer regenerates
Attempt 2: implementation_bug (same issue) → developer regenerates
Attempt 3: implementation_bug (same issue) → developer regenerates
Result: 3 × ~40k tokens wasted, entity still fails
```

**Solution:** Track `diagnosis_history` and escalate when stuck:

```python
diagnosis_history = []
while not result.passed:
    diagnosis = diagnose(entity, artifact, result, env)
    
    # Detect stuck loop: same category 2x in a row
    if (len(diagnosis_history) >= 1 
        and diagnosis_history[-1] == diagnosis.category
        and diagnosis.category != DiagnosisCategory.design_flaw):
        # Escalate to design_flaw (full crew regeneration)
        diagnosis = Diagnosis(
            category=DiagnosisCategory.design_flaw,
            description=f"Escalated from {diagnosis_history[-1].value} after 2 consecutive failures...",
        )
    
    diagnosis_history.append(diagnosis.category)
```

**Impact:**
- Prevents wasted tokens on repeated developer/attacker regenerations
- Forces full crew (engineer + developer + attacker) regen after 2 consecutive same-category failures
- Example: `implementation_bug → implementation_bug` becomes `implementation_bug → design_flaw (escalated)`

**Test Coverage:** 4 new tests in `tests/test_retry_escalation.py`

---

### 3. Chain Test: Enhanced Evidence Collection

**File:** `goe/flow/chain_test.py`

**Before:**
```python
def _summarise_failure(result):
    for step in result.steps:
        if not step.passed:
            lines.append(f"Step '{step.step_id}' failed: {step.reason}")
            lines.append(f"  stdout: {stdout[:400]}")  # truncated
            lines.append(f"  stderr: {stderr[:200]}")  # truncated
            break  # only first failure
    return "\n".join(lines)
```

**Problems:**
- Only first failure shown (subsequent failures might reveal root cause)
- Truncated output (400/200 chars may cut critical error messages)
- No context about what worked before failure
- No container probing (can't see if services are running)

**After:**
```python
def _summarise_failure(result, env, procedure):
    # Show last 3 successful steps for context
    successes = [s for s in result.steps if s.passed][-3:]
    for s in successes:
        lines.append(f"✓ {s.step_id}")
    
    # Show ALL failures, not just first
    failures = [s for s in result.steps if not s.passed]
    for s in failures:
        lines.append(f"✗ {s.step_id}: {s.reason}")
        lines.append(f"  stdout: {stdout[:800]}")  # increased
        lines.append(f"  stderr: {stderr[:400]}")  # increased
    
    # Probe containers for evidence
    if env:
        # Attacker container
        _, ps_out, _ = env.exec_in("attacker", "ps aux | head -15")
        lines.append(f"[Attacker] Processes: {ps_out[:300]}")
        
        # Target system (if identifiable from failed step)
        target_system = _extract_target_system(failure, procedure)
        if target_system:
            _, sys_ps, _ = env.exec_in(target_system, "ps aux | head -15")
            lines.append(f"[{target_system}] Processes: {sys_ps[:300]}")
```

**New Helper: `_extract_target_system()`**
- Parses failed step's action (command/URL)
- Extracts `${system.<id>.host}` to identify which container to probe
- Returns system_id for targeted evidence collection

**Impact:**
- More context: see what worked before failure
- Complete picture: all failures shown, not just first
- Live evidence: probe containers to see if services are running
- Larger excerpts: 2x more stdout/stderr (800/400 vs 400/200)

**Test Coverage:** 9 new tests in `tests/test_chain_diagnosis.py`

---

### 4. Chain fix_chain: Better Diagnostic Guidance

**File:** `goe/construction_crew/chain_attacker.py`

Added "Common Failure Patterns and Fixes" section to fix_chain prompt:

#### Interpolation Issues
- Wrong `${system.<id>.host}` → check graph for correct ID
- Missing `${edge.<id>.<param>}` → use `.value` fallback

#### Service/Process Not Running
- If container evidence shows process missing or port not listening → **entity is broken**
- Cannot be fixed by patching procedure → entity needs L2 retest

#### Permission Denied on Files
- Home directory permission issues indicate **entity implementation bug**
- Cannot be fixed by procedure patch

#### Connection Refused / Timeout
- Check container evidence: is service listening?
- Check hostname interpolation
- If service not running → **entity is broken**

#### When NOT to Patch
> If diagnosis shows entity-level issues (process not running, service not listening, deploy failure), you CANNOT fix this by patching the procedure. The entity itself is broken.

**Impact:**
- fix_chain now understands when problems are unfixable at procedure level
- Preserves procedure when entity is broken (avoids wasted speculation)
- Focuses fixes on actual procedure bugs (interpolation, sequencing, assertions)

---

## Summary of Improvements

| Improvement | Problem Solved | Impact | Tests |
|-------------|---------------|--------|-------|
| **Entity diagnostician prompt** | Didn't understand file permission mechanics | Correct categorization of permission issues | Implicit (LLM behavior) |
| **Entity stuck loop detection** | Wasted tokens on repeated same-category failures | Auto-escalates after 2 consecutive same-category diagnoses | 4 tests |
| **Chain evidence collection** | Insufficient context for diagnosis | Shows context, all failures, container probes | 9 tests |
| **Chain fix_chain prompt** | Tried to fix entity-level issues | Recognizes when entity is broken vs procedure bug | Implicit (LLM behavior) |

**Total new tests:** 13 (4 entity escalation + 9 chain diagnosis)  
**All tests passing:** 32/32 ✅

---

## Example: bash_history Scenario (Before vs After)

### Before Changes

**Attempt 1:**
```
L2 test fails: Permission denied on /home/stacy/.bash_history
Diagnostician: implementation_bug (file permissions wrong)
Developer: Regenerates setup.sh, sets file to 0644
Result: Still fails (forgot directory permissions)
```

**Attempt 2:**
```
L2 test fails: Permission denied (same)
Diagnostician: implementation_bug (still categorized as file issue)
Developer: Regenerates again, still sets only file to 0644
Result: Still fails
Tokens wasted: ~40k
```

**Attempt 3:**
```
L2 test fails: Permission denied (same)
Diagnostician: implementation_bug
Developer: Gives up after max retries
Result: Entity FAILED
Total tokens wasted: ~120k on retries that couldn't fix the root cause
```

### After Changes

**Attempt 1:**
```
L2 test fails: Permission denied on /home/stacy/.bash_history
Diagnostician: implementation_bug
  "File is world-readable (0644) but parent directory /home/stacy 
   likely has restrictive permissions (0750). www-data needs execute 
   permission on ALL parent directories. Developer must chmod 755 /home/stacy."
Developer: Regenerates with chmod 755 /home/stacy
Result: PASSES
Tokens saved: ~80k (avoided 2 more retry attempts)
```

**OR if developer still doesn't fix it:**

**Attempt 2:**
```
L2 test fails: Permission denied (same)
Diagnostician: implementation_bug
Stuck loop detected: implementation_bug → implementation_bug
Auto-escalated to: design_flaw
Engineer + Developer + Attacker: Full crew regeneration
Result: PASSES (engineer redesigns approach)
Tokens saved: ~40k (avoided 1 more developer-only retry)
```

---

## Expected Outcomes

1. **Higher success rates**: Diagnostician understands permission mechanics → correct fixes
2. **Lower token usage**: Stuck loop detection prevents wasted retries
3. **Better visibility**: Chain tests show what worked, what failed, and why
4. **Smarter fixes**: fix_chain recognizes when entity is broken vs procedure bug

---

## Future Work (Not Implemented)

**Medium Priority:**
- Stuck loop detection for chain tests (same pattern as entity tests)
- Entity-bug detection in chain tests → route back to L2

**Low Priority:**
- Structured categorization for chain failures (procedure_bug vs entity_bug vs chain_design_flaw)
- Adaptive probes for chain tests based on failure type
