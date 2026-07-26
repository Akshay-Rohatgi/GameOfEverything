# Phase 2 Status: Current State & Next Steps

## ✅ What's Working

### 1. Browser Infrastructure (Phase 1)
- ✅ Browser container builds and starts successfully
- ✅ CDP endpoint exposed and reachable
- ✅ Browser can navigate to Docker internal hostnames (e.g., `http://target:3000`)
- ✅ socat port forwarding working
- ✅ Security flags disabled (`--disable-web-security`, `--disable-features=BlockInsecurePrivateNetworkRequests`)

**Test Result:**
```
Result: Navigated to http://target:3000/. Page title: 
```
Navigation successful! (Empty title is expected for test HTML)

### 2. Attack Orchestrator Implementation
- ✅ `AttackOrchestratorResult` model created
- ✅ `GeneratedApp.attack_objective` replaces `attack_snippet`
- ✅ `attack_orchestrator_agent` defined in agents.yaml
- ✅ `attack_orchestrator_task` defined in tasks.yaml
- ✅ `run_attack_orchestrator_crew()` implemented
- ✅ Three tools created: exec_in_target, exec_in_attacker, browser_task
- ✅ Custom app flow integrated

### 3. LLM Factory Integration
- ✅ Fixed import: `make_llm()` instead of `create_llm_for_agent()`
- ✅ Model resolution working

## ⚠️ Current Issue

### CrewAI Task Output Format Error

**Error:**
```
ValidationError: 1 validation error for TaskOutput
raw
  Input should be a valid string [type=string_type, input_value=[{'toolUseId': 'tooluse_H...'}, 'type': 'tool_use'}], input_type=list]
```

**Root Cause:**
The attack orchestrator agent is returning raw tool call objects instead of synthesizing them into a final JSON response. CrewAI's `TaskOutput.raw` expects a string but is receiving a list of tool use dictionaries.

**Solution Applied:**
1. Updated `attack_orchestrator_task` expected_output with explicit JSON example
2. Added instruction: "After using your tools, you MUST synthesize your findings and return a final JSON result"
3. Updated agent goal to emphasize: "Your final response must be a JSON object, not a tool call result"

**Status:** Changes committed, ready for re-test

## 🔧 Files Modified (Latest Round)

### Browser Connectivity Fixes
- `docker/browser/Dockerfile`: Added `--disable-features=BlockInsecurePrivateNetworkRequests` and other security bypass flags
- `tools/bound_browser_tool.py`: Changed `wait_until='networkidle'`, added error handling

### CrewAI Output Format Fixes
- `config/agents.yaml`: Added "CRITICAL: Your final response must be a JSON object" to attack_orchestrator_agent
- `config/tasks.yaml`: Added explicit JSON example and synthesis instruction

### LLM Integration Fix
- `crews/attack_orchestrator_crew.py`: Fixed `create_llm_for_agent` → `make_llm`

## 📋 Test Plan

### Next Test Run
```bash
uv run python3 scripts/test_custom_app.py \
  --vuln xss_stored \
  --goal session_theft_via_xss \
  --runtime express
```

### Expected Behavior
1. ✅ 3 containers start (target, attacker, browser)
2. ✅ Browser CDP ready
3. ✅ App deploys successfully
4. ✅ Attack orchestrator invoked
5. ⏳ Agent uses tools (exec_in_target, exec_in_attacker, browser_task)
6. ⏳ Agent synthesizes findings into AttackOrchestratorResult JSON
7. ⏳ L1 and L2 verdicts returned
8. ⏳ App marked as validated

### What to Watch For
- Does the agent return JSON or tool calls?
- Does browser_task successfully navigate and interact with the app?
- Are L1/L2 verdicts accurate?

## 🎯 Remaining Work

### Phase 2 Completion
- [ ] Verify agent returns JSON (not tool calls)
- [ ] Test end-to-end with XSS scenario
- [ ] Test end-to-end with SQLi scenario (no browser needed)
- [ ] Verify attack_objective format is parsed correctly
- [ ] Validate browser tool routing works

### Phase 3 (Future)
- [ ] Extract `run_attack_crew()` for misconfig atoms
- [ ] Update `test_snippets.py` to use attack agent for L2 failures
- [ ] Generalize `attack_agent` for infrastructure exploits

## 🐛 Known Issues

### 1. CrewAI Agent Output Format
**Status:** Fix applied, awaiting test
**Impact:** Agent returns tool calls instead of final JSON
**Fix:** Added explicit synthesis instructions and JSON examples

### 2. Browser Security Blocks (RESOLVED)
**Status:** ✅ Fixed
**Impact:** Browser couldn't navigate to internal Docker hostnames
**Fix:** Added `--disable-features=BlockInsecurePrivateNetworkRequests`

## 💡 Key Learnings

### Browser in Docker
- Chrome blocks private network requests by default (ERR_BLOCKED_BY_CLIENT)
- Must disable `BlockInsecurePrivateNetworkRequests` feature
- CDP connection works from host to container via port mapping
- Container-to-container DNS works (browser resolves "target" hostname)

### CrewAI Tool Output
- Agents may return raw tool outputs instead of synthesizing
- Need explicit instructions to "analyze and return JSON"
- Expected output must include examples
- Task description should emphasize synthesis step

### LLM Factory Pattern
- Use `make_llm(agent_name)` for per-agent model resolution
- Follows env var → toml → yaml → default precedence
- Bedrock provider adds "us." prefix automatically

## 📝 Summary

**Phase 1:** ✅ Complete - Browser infrastructure working
**Phase 2:** ⚠️ 95% Complete - Waiting on CrewAI output format fix verification

The attack orchestrator is implemented and integrated. All infrastructure works. The remaining issue is ensuring the agent returns synthesized JSON instead of raw tool calls. The fix has been applied and needs testing.

---

**Last Updated:** 2026-05-12
**Next Action:** Re-run `test_custom_app.py` to verify JSON output fix
