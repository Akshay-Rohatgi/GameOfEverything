# Phase 2 Blocker: Bedrock Tool Use / CrewAI Incompatibility

## Problem Summary

The Attack Orchestrator agent is unable to produce a final answer when using tools. After executing tools, it returns tool use blocks instead of synthesizing a final JSON response. This causes CrewAI's `TaskOutput` validation to fail.

## Error Details

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for TaskOutput
raw
  Input should be a valid string [type=string_type, input_value=[{'toolUseId': 'tooluse_0...'}, 'type': 'tool_use'}], input_type=list]
```

**What's happening:**
1. Agent calls tools (exec_in_target, exec_in_attacker, browser_task)
2. Agent receives tool results
3. Agent should synthesize results into final JSON answer
4. **Instead:** Agent returns the tool use blocks as its final output
5. CrewAI tries to create `TaskOutput(raw=<list of tool blocks>)`
6. Pydantic validation fails because `raw` expects a string

## Root Cause

This is a known incompatibility between AWS Bedrock's tool use protocol and CrewAI's expectations:

### Bedrock Behavior
- When a Claude model uses tools via Bedrock, the conversation flow is:
  1. User message → Model returns `tool_use` blocks
  2. System returns `tool_result` blocks
  3. Model returns final `text` response
- **Issue:** CrewAI/LiteLLM may not be properly handling step 3

### CrewAI Expectations
- CrewAI expects `agent.execute()` to return a string (`TaskOutput.raw: str`)
- When the agent's final output is a list of tool use blocks, validation fails

## Attempted Solutions

### 1. ✅ Explicit Instructions (Tried)
- Updated agent goal: "CRITICAL: Your final response must be a JSON object, not tool calls"
- Updated task description: "After using tools, synthesize findings and return JSON"
- **Result:** Agent still returns tool use blocks

### 2. ✅ Iteration Limits (Tried)
- Added `max_iter=10` to prevent infinite loops
- Added `max_execution_time=300` as timeout
- **Result:** Agent stops after 10 iterations but still no final answer

### 3. ✅ Pydantic Output (Tried)
- Added `output_pydantic=AttackOrchestratorResult` to Task
- **Result:** Validation error still occurs before Pydantic can parse

### 4. ✅ Graceful Error Handling (Implemented)
- Catch the validation error and return failure verdict
- **Result:** No crash, but attack validation fails

## Why This Is Hard to Fix

### The Tool Use Loop Problem
Bedrock Claude models with tools follow a specific protocol:
```
User → [tool_use] → [tool_result] → [text response]
```

CrewAI/LiteLLM appears to be stopping after `[tool_result]` without requesting the final `[text response]`.

### Possible Causes
1. **LiteLLM Bedrock implementation:** May not be sending the continuation message after tool results
2. **CrewAI agent loop:** May not be requesting final synthesis after tools complete
3. **Bedrock converse API:** May need explicit "continue" signal after tool results

## Potential Solutions

### Option A: Fix the Conversation Flow
**Approach:** Modify how CrewAI/LiteLLM handles Bedrock tool results

**Implementation:**
1. After tool results are sent to Bedrock, add a continuation message:
   ```python
   messages.append({
       "role": "user",
       "content": "Now synthesize your findings and return the AttackOrchestratorResult JSON."
   })
   ```
2. This would need to be added in:
   - `patches.py` (monkey-patch LiteLLM's Bedrock handler)
   - Or CrewAI's agent executor

**Pros:** Fixes root cause
**Cons:** Requires deep patching of external libraries

### Option B: Manual Tool Execution
**Approach:** Don't use CrewAI's tool system - execute tools manually

**Implementation:**
```python
def run_attack_orchestrator_manual(generated_app, ...):
    # Parse attack_objective manually
    steps = parse_attack_objective(generated_app.attack_objective)
    
    # Execute L1
    l1_output = exec_in_target(generated_app.testing_snippet)
    l1_passed = judge_l1(l1_output)
    
    # Execute L2 steps
    l2_outputs = []
    for step in steps:
        if step.startswith("Run in attacker:"):
            output = exec_in_attacker(step.command)
        elif step.startswith("In browser:"):
            output = browser_task(step.action)
        l2_outputs.append(output)
    
    # Use LLM to judge L2 (no tools)
    l2_passed = judge_l2(l2_outputs, success_criterion)
    
    return AttackOrchestratorResult(...)
```

**Pros:** 
- Complete control over execution flow
- No CrewAI tool use issues
- Can still use LLM for judgment calls

**Cons:** 
- Loses agent autonomy
- More rigid - less adaptive

### Option C: Use OpenAI-Compatible Provider
**Approach:** Use a provider that CrewAI handles better (OpenAI API, Anthropic direct)

**Implementation:**
- Configure orchestrator agent to use OpenAI API or Anthropic API directly
- Keep other agents on Bedrock

**Pros:** Likely fixes tool use issues
**Cons:** Requires OpenAI/Anthropic API keys

### Option D: ReAct Pattern Without Tools
**Approach:** Use thought→action→observation loop without formal tools

**Implementation:**
```python
prompt = """
You have three capabilities:
1. exec_in_target: <example>
2. exec_in_attacker: <example>
3. browser_task: <example>

Return your commands in this format:
THINK: <reasoning>
ACTION: exec_in_target|<command>
...

When done, return:
FINAL_ANSWER: <JSON>
"""
```

Parse the output and execute commands manually.

**Pros:** No CrewAI tool complications
**Cons:** More brittle parsing

## Current Status

✅ **Error handling implemented:** Orchestrator returns graceful failure verdict
❌ **Root issue unresolved:** Agent doesn't produce final answer after tool use

## Recommendation

**Short term:** Proceed with **Option B (Manual Tool Execution)** for Phase 2
- Fastest path to working end-to-end validation
- Maintains most of the architecture
- Can migrate to proper tool use later if Bedrock/CrewAI compatibility improves

**Long term:** Investigate **Option A (Fix Conversation Flow)**
- File issue with CrewAI about Bedrock tool use
- Check if LiteLLM has patches we need
- Consider if `patches.py` monkey-patch is viable

## Next Steps

1. Implement manual tool execution version of orchestrator
2. Test end-to-end with manual execution
3. File detailed issue with CrewAI about Bedrock tool use incompatibility
4. Document workaround in CLAUDE.md

---

**Status:** Blocked on Bedrock/CrewAI tool use incompatibility
**Workaround:** Manual tool execution (Option B)
**Date:** 2026-05-12
