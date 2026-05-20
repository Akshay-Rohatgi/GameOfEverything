# GoE v2 — Implementation Plan

Build from the inside out: get a single entity through construction crew + procedure executor first. Layer graph planning on top once the builder/executor contract is proven. Every phase ends with a runnable demo that produces output.

---

## Guiding Principles

1. **First green test ASAP.** Every architectural decision that delays seeing a passing L2 test is suspect.
2. **Build the executor before the DSL generator.** If the executor is solid, you can hand-fix procedures during development. If the generator is solid but the executor is buggy, you can't tell what's broken.
3. **Reuse v1 infrastructure where possible.** Docker containers, attacker image, target images, ChromaDB, config system — these work. Don't rewrite them.
4. **One entity type at a time.** Get simple misconfigs working, then custom apps, then multi-entity chains, then multi-system.
5. **Test with hardcoded inputs before LLM inputs.** Each component should work with hand-written fixtures before you wire an LLM to produce the input.

---

## Phase 0: Foundation

**Goal**: Project scaffolding, data models, and the procedure executor — the thing that runs procedures against containers. No LLM calls. Fully testable with hand-written fixtures.

### 0.1 — Data Models

Implement all Pydantic models from the entity graph spec:

```
models/
  system.py         # System, NetworkConfig
  entity.py         # Entity, Requirement, AppSpec
  edge.py           # Edge, ParamValue, EdgeType enum
  procedure.py      # Procedure, Step, Action variants, Assertion variants, Session
  artifacts.py      # BuildArtifact, DBSetup
  report.py         # BuildReport, EntityResult
```

Each model should round-trip through YAML serialization (procedures are YAML, graph definitions are YAML). Write a `conftest.py` with fixture factories for every model.

**Deliverable**: `pytest tests/test_models.py` passes — all models instantiate, serialize, validate.

### 0.2 — Procedure Executor

The critical path. This is the runtime that takes a `Procedure` and executes it step-by-step against Docker containers.

```
executor/
  runner.py             # Main loop: iterate steps, dispatch by action type, check assertions
  actions/
    http.py             # http_request: uses requests library from attacker container
    shell.py            # exec_attacker, exec_target: docker exec
    listen.py           # listen: ncat in background, poll for content
    sleep.py            # sleep: time.sleep
    browser.py          # All browser actions: navigate, click, fill, etc.
  assertions.py         # Assertion checking: status, contains, regex, selector_*, etc.
  interpolation.py      # ${variable} resolution
  outputs.py            # Output capture (regex, header, json, extracted_value, etc.)
  sessions.py           # Browser session lifecycle (Playwright context management)
```

**Implementation order within the executor:**

1. `interpolation.py` — variable substitution (pure string processing, easy to test)
2. `assertions.py` — all assertion types as pure functions (input: actual value, assertion spec → bool)
3. `runner.py` — step loop with dispatch table, output accumulation, timeout handling
4. `actions/shell.py` — docker exec wrapper (reuse v1's `exec_in_container` pattern)
5. `actions/http.py` — requests from attacker container (docker exec + curl, or exec + python requests)
6. `actions/listen.py` — background ncat + poll
7. `actions/sleep.py` — trivial
8. `actions/browser.py` — Playwright session management + all browser actions
9. `sessions.py` — browser context pool, auth pre-login, screenshot-on-failure

**Testing strategy**: Write 3-4 hand-crafted procedure YAML files:
- `test_procedures/simple_http.yaml` — GET request, check status
- `test_procedures/exec_and_listen.yaml` — start listener, exec command that sends data, verify receipt
- `test_procedures/browser_login.yaml` — navigate, fill_and_submit, check URL
- `test_procedures/mixed_xss.yaml` — listener + browser inject + browser trigger + verify capture

Run each against a trivial test app (hardcoded Express app with known endpoints). The executor should pass all four before any LLM is involved.

**Deliverable**: `pytest tests/test_executor.py` passes all four fixture procedures against a running Docker environment.

### 0.3 — Container Orchestration

Reuse v1's Docker management (`tools/test_environment.py`) but refactor into a clean interface:

```python
class TestEnvironment:
    def deploy(self, system: System, artifacts: list[BuildArtifact]) -> None
    def healthcheck(self, system: System) -> bool
    def exec_in(self, container: str, command: str, privileged: bool = False) -> ExecResult
    def get_attacker_host(self) -> str
    def get_target_host(self, system_id: str) -> str
    def teardown(self) -> None
```

Also needs a Playwright-ready attacker image. Extend the existing Kali `Dockerfile`:

```dockerfile
# Add to docker/attacker/Dockerfile
RUN pip install playwright && playwright install chromium --with-deps
```

**Deliverable**: Can spin up target + attacker, deploy a hardcoded app, run the procedure executor against it, tear down. All from a single `pytest` invocation.

---

## Phase 1: Single Entity — Construction Crew

**Goal**: One entity goes through the full construction crew (engineer → developer → attacker) and produces artifacts + procedure that pass L2. Still no graph — entity is defined by a hand-written spec.

### 1.1 — Construction Crew Orchestration

```
construction_crew/
  orchestrator.py       # Runs engineer → developer → attacker sequentially
  engineer.py           # Opus call: entity spec → architecture plan
  developer.py          # Sonnet call: plan → BuildArtifact + concrete edge values
  attacker.py           # Sonnet call: artifacts + edge values → Procedure
```

The orchestrator takes:
- `Entity` (with app_spec)
- Resolved incoming edge values (hardcoded for now)
- Atom content (optional)

And produces:
- `BuildArtifact` (source files, port, DB setup)
- `Procedure` (attack steps)
- Concrete outgoing edge values

**Prompt design** is the key work here. Each agent needs:
- A system prompt describing its role and output format
- The procedure DSL spec (for the attacker) or the BuildArtifact schema (for the developer) as reference
- Few-shot examples of correct output

Start with the **attacker prompt** — it must produce valid procedure YAML. Give it the full DSL spec and 2-3 examples. Validate its output against the Pydantic `Procedure` model immediately after generation. If it fails to parse, retry with the validation error (cheap, no full regen).

### 1.2 — Runtime Templates

Port v1's runtime template system, simplified:

```
runtimes/
  registry.py           # Maps runtime_id → RuntimeTemplate
  templates/
    express.yaml
    flask.yaml
    apache_php.yaml
```

The template takes a `BuildArtifact` and produces a deploy script (deterministic, no LLM):
1. Copy source files to `/app/`
2. Detect/install dependencies
3. Run DB setup if present
4. Start the application
5. Healthcheck

### 1.3 — Test Harness (Single Entity)

Wire it all together:

```python
def test_single_entity(entity_spec: Entity, incoming_edges: dict) -> EntityResult:
    # 1. Construction crew generates artifacts + procedure
    artifacts, procedure, outgoing_values = construction_crew.build(entity_spec, incoming_edges)
    
    # 2. Runtime template produces deploy script
    deploy_script = runtime_registry.deploy(entity_spec.app_spec.runtime, artifacts)
    
    # 3. Spin up environment and deploy
    env = TestEnvironment()
    env.deploy(system, artifacts)
    
    # 4. Run L2 (procedure executor)
    result = executor.run(procedure, env)
    
    if result.passed:
        return EntityResult(status=PASSED, artifacts=artifacts, procedure=procedure)
    
    # 5. L1 diagnostic + retry (see Phase 1.4)
    ...
```

**Deliverable**: Run `python -m goe.test_single_entity --spec fixtures/sqli_entity.yaml` and get a passing L2 test with a generated Express app containing a SQL injection vulnerability.

### 1.4 — Retry Loop

Implement the escalation ladder:

```
retry/
  diagnostician.py      # L1: god-view inspection → diagnosis category
  retry_router.py       # Maps diagnosis → which crew member to re-run
```

The diagnostician receives:
- L2 failure output (which step failed, what assertion expected vs got)
- God-view inspection (exec_target access to read files, check processes, query DBs)

It produces a `Diagnosis`:
```python
class Diagnosis:
    category: Literal["procedure_bug", "implementation_bug", "design_flaw", "unknown"]
    explanation: str        # What specifically is wrong
    suggestion: str         # What to fix
```

Retry router:
- `procedure_bug` → re-run attacker with diagnosis context (max 2)
- `implementation_bug` → re-run developer + attacker with diagnosis context (max 2)
- `design_flaw` → re-run full crew with diagnosis context (max 1)

**Deliverable**: Intentionally break a fixture (wrong endpoint in procedure) and verify the retry loop fixes it without full regeneration.

---

## Phase 2: Entity Graph — Planning Pipeline

**Goal**: User request → validated entity graph. No building yet — just the planning LLM calls and static validator.

### 2.1 — Static Validator

Build this FIRST (before the planners). It's pure code, no LLM, and it defines the contract that planners must satisfy.

```
graph/
  validator.py          # The 7 validation checks
  topology.py           # Topological sort, dependency resolution
```

Write against hand-crafted graph fixtures:
- `fixtures/valid_2entity_chain.yaml` — passes all checks
- `fixtures/missing_edge.yaml` — fails check 1 (edge coverage)
- `fixtures/orphan_entity.yaml` — fails check 3 (reachability)
- `fixtures/cycle.yaml` — fails check 6

**Deliverable**: `pytest tests/test_validator.py` with 10+ cases covering all 7 checks and edge cases (fan-out, multiple initial access points, etc).

### 2.2 — Value Propagation

The system that takes concrete values from a completed builder and updates downstream edges:

```
graph/
  propagation.py        # Update edge params after builder completes
  build_scheduler.py    # Topological build ordering with dependency tracking
```

```python
class BuildScheduler:
    def __init__(self, graph: EntityGraph):
        self.order = topological_sort(graph)
        self.completed: dict[str, ConcreteValues] = {}
    
    def next_buildable(self) -> Entity | None:
        """Return next entity whose dependencies are all in self.completed"""
    
    def report_complete(self, entity_id: str, values: dict[str, str]) -> None:
        """Record concrete values, propagate to downstream edges"""
    
    def report_failed(self, entity_id: str) -> list[str]:
        """Mark failed, return list of entities to SKIP"""
```

**Deliverable**: Unit tests with a 4-entity chain — complete them in order, verify propagation. Fail entity 2, verify entity 3+4 are skipped.

### 2.3 — Planning Agents

Now build the LLM pipeline that produces graphs:

```
planner/
  design_systems.py     # Step 0: user request → systems
  plan_entities.py      # Step 1: systems + request → entity stubs
  specify_entities.py   # Step 2: stubs → full entity specs (parallel)
  connect_edges.py      # Step 3: entities → edges
  resolve.py            # Step 4: fill structural params (code, no LLM)
  pipeline.py           # Orchestrates steps 0-5 with validator at the end
```

Each planner agent gets:
- The edge type vocabulary (as system prompt reference)
- The data model schemas (so it knows what format to produce)
- 2-3 few-shot examples of correct output for its step

**Key decision**: If the validator fails after step 5, what do you do? Options:
- **A**: Return the violation list to step 3 (connect_edges) and retry. Most failures will be wiring issues.
- **B**: Return to step 1 and re-plan from scratch.
- **C**: Attempt a targeted fix (LLM receives violations + current graph, outputs patches).

Start with **A** (max 2 retries on edge connection). If that fails, fall back to **B** (max 1 full re-plan). This mirrors the entity-level escalation ladder.

**Deliverable**: `python -m goe.plan "web app with SQL injection leading to credential theft and SSH pivot to database server"` produces a valid graph YAML file.

---

## Phase 3: End-to-End Single System

**Goal**: Full pipeline for single-system scenarios. User request → graph → build all entities → all L2 pass → packaged output.

### 3.1 — Orchestrator

```
flow/
  orchestrator.py       # Top-level: plan → validate → build → test → package
```

Wires together:
1. Planning pipeline (Phase 2) → validated graph
2. Build scheduler → entity build order
3. For each entity: construction crew (Phase 1) → artifacts + procedure
4. Value propagation after each successful build
5. Failure handling (skip dependents)
6. Final packaging

### 3.2 — Packaging

Port v1's `script_postprocessor.py` and `finalize_script.py`:
- Concatenate deploy scripts in topological order
- Inject shebang + `set -e`
- Generate `playbook.yaml` (all procedures concatenated in chain order)
- Generate summary README

### 3.3 — CLI

```bash
# Main entry point
goe run "SSH server with weak credentials and SUID privesc to root"

# Resume from checkpoint
goe run --resume output/.checkpoints/<run_id>/

# Verbose (show agent reasoning)
goe run --verbose "..."
```

Port v1's checkpoint system. Checkpoint after: planning complete, each entity built, packaging done.

### 3.4 — Console Output

Port v1's `GoEConsole` — minimal terminal output with spinners, step completion, test results. Full agent logs to file.

**Deliverable**: `goe run "..."` for 3 different single-system scenarios (simple misconfig, custom app with SQLi, 3-entity privesc chain) all produce working deploy scripts + playbooks.

---

## Phase 4: Multi-System

**Goal**: Multi-system scenarios with cross-system edges and chain test.

### 4.1 — Multi-System Build

Extend the build scheduler to handle parallel builds across systems:
- Entities on different systems with no edge dependencies build in parallel (ThreadPoolExecutor)
- Cross-system edges still enforce ordering (system B entity waits for system A entity's concrete values)

Port v1's `PipelineRenderer` + `BoxEventEmitter` for serialized multi-thread output.

### 4.2 — Chain Test

After all entities pass individual L2:

1. Deploy full topology (docker-compose with all systems)
2. Execute procedures in edge order, feeding outputs between entities
3. Report which edge broke on failure

```
chain_test/
  deployer.py           # docker-compose up with all systems
  chain_runner.py       # Execute procedures in order, pipe outputs
```

The chain runner is essentially the procedure executor running across multiple procedures sequentially, with step outputs from procedure N available as inputs to procedure N+1.

### 4.3 — Docker Compose Generation

```python
def generate_compose(systems: list[System], network_name: str) -> str:
    """Produce docker-compose.yml with per-system services + shared network"""
```

**Deliverable**: `goe run "web app on server A with credential leak pivoting to SSH on server B"` produces `docker-compose.yml` + per-system deploy scripts + chain-validated playbook.

---

## Phase 5: Polish and Parity

**Goal**: Feature parity with v1 (minus preset apps for now), production-quality error handling, cost optimization.

### 5.1 — Atom Integration

Wire ChromaDB RAG into the construction crew:
- Engineer receives top-3 relevant atoms as reference material
- Developer can reference atom implementation patterns
- Not required for generation (atoms improve quality but aren't mandatory)

### 5.2 — EC2 Deploy

Port v1's `ec2_deploy.py` — works on single-system packaged output. Multi-system stays docker-compose only.

### 5.3 — Cost Optimization

- Cache engineer plans for similar entity specs (if entity description + app_spec match within cosine similarity threshold, reuse plan)
- Batch specify_entities calls where possible (Sonnet can handle 2-3 entities per call if they're independent)
- Short-circuit retry: if L1 diagnostic is "endpoint typo," patch the procedure directly without re-running the attacker agent

### 5.4 — Observability

- Structured logging (JSON) with correlation IDs per entity
- Cost tracking per entity and per run
- Timing breakdown (planning vs. building vs. testing)
- Failure mode statistics (what % of failures are procedure bugs vs. implementation bugs vs. design flaws)

---

## What NOT to Build (For Now)

- **Preset apps** (WordPress, phpBB) — defer to v3. The graph model can express them (entity with `preset_id` instead of `app_spec`) but the builder needs a different flow (deploy existing app + apply vuln config rather than generate from scratch).
- **Graph editor UI** — the YAML format is the interface for now.
- **LLM-powered chain test repair** — if chain test fails, report the failure. Don't auto-fix cross-entity integration issues (too complex, too expensive, too unreliable).
- **Edge type extension mechanism** — 8 types is enough to ship. Add new types when a real scenario demands it, not speculatively.

---

## Technology Decisions

| Component | Choice | Rationale |
|-----------|--------|-----------|
| LLM provider | AWS Bedrock (Claude) | Same as v1, no migration needed |
| Orchestration | Custom Python (no crewAI) | crewAI's abstractions don't map to the construction crew model. Direct Bedrock API calls with structured output parsing. Eliminates the tool-call arg bug, Jinja2 SSTI issues, and monkey-patches from v1. |
| Browser automation | Playwright (Python) | Better API than Puppeteer for automation. Python keeps everything in one language. Runs in attacker container. |
| Container management | Docker SDK for Python | Same as v1 |
| Procedure format | YAML | Human-readable, LLM-friendly, easy to hand-edit during development |
| Graph serialization | YAML | Same reasons |
| Data validation | Pydantic v2 | Same as v1, strict mode for all models |
| Test framework | pytest | Standard |
| Config | TOML (goe.toml) | Reuse v1's config system |

### Dropping crewAI

v1's biggest maintenance burden is crewAI: the tool-call arg bug, the Jinja2 template injection, the Bedrock prefill rejection, verbose output suppression, and general opacity of agent execution. None of these are load-bearing — crewAI provides agent loop + tool dispatch, which is ~50 lines of direct Bedrock API code.

v2 replaces crewAI with direct Claude API calls:
1. Build the prompt (system + user + few-shot examples)
2. Call Bedrock with structured output (tool_use for structured responses)
3. Parse response into Pydantic model
4. If parse fails, retry with error context (max 2)

Each "agent" is a function: `(inputs) → structured_output`. The construction crew orchestrator calls them sequentially. No framework, no magic.

---

## Milestones and Estimated Timeline

| Phase | Milestone | Testable Outcome | Dependencies |
|-------|-----------|------------------|--------------|
| 0.1 | Data models | All models serialize/deserialize | None |
| 0.2 | Procedure executor | 4 fixture procedures pass against Docker | 0.1 |
| 0.3 | Container orchestration | Attacker + target spin up, deploy, healthcheck | 0.2 |
| 1.1 | Construction crew | Single entity generates valid artifacts + procedure | 0.3 |
| 1.2 | Runtime templates | Developer output auto-deployed | 0.3 |
| 1.3 | Single entity E2E | One entity passes L2 via generated procedure | 1.1, 1.2 |
| 1.4 | Retry loop | Intentionally broken entity self-heals | 1.3 |
| 2.1 | Static validator | All 7 checks pass/fail correctly on fixtures | 0.1 |
| 2.2 | Value propagation | 4-entity chain propagates values, handles failure | 2.1 |
| 2.3 | Planning agents | User request → valid graph YAML | 2.1 |
| 3.1 | Orchestrator | Plan → build → test → output (single system) | 1.4, 2.3 |
| 3.2 | Packaging | Deploy script + playbook generated | 3.1 |
| 3.3 | CLI | `goe run "..."` works end-to-end | 3.2 |
| 4.1 | Multi-system build | Parallel entity builds across systems | 3.3 |
| 4.2 | Chain test | Cross-system attack chain validated | 4.1 |
| 4.3 | Compose generation | docker-compose + per-system scripts | 4.2 |
| 5.x | Polish | Atoms, EC2, cost optimization, observability | 4.3 |

**Critical path**: 0.1 → 0.2 → 0.3 → 1.1 → 1.3 → 1.4 → 3.1 → 3.3

Phases 2.1-2.3 (graph planning) can be built **in parallel** with Phase 1 — they share only the data models (0.1). The graph planning team produces fixtures that the orchestrator (3.1) consumes; the construction crew team produces a builder that the orchestrator calls.

---

## Directory Structure (Target)

```
goe/
  __init__.py
  cli.py                        # Entry point, arg parsing
  config.py                     # GoEConfig (port from v1)
  
  models/
    __init__.py
    system.py
    entity.py
    edge.py
    procedure.py
    artifacts.py
    report.py
  
  executor/
    __init__.py
    runner.py
    interpolation.py
    assertions.py
    outputs.py
    sessions.py
    actions/
      __init__.py
      http.py
      shell.py
      listen.py
      sleep.py
      browser.py
  
  construction_crew/
    __init__.py
    orchestrator.py
    engineer.py
    developer.py
    attacker.py
  
  graph/
    __init__.py
    validator.py
    topology.py
    propagation.py
    build_scheduler.py
  
  planner/
    __init__.py
    design_systems.py
    plan_entities.py
    specify_entities.py
    connect_edges.py
    resolve.py
    pipeline.py
  
  runtimes/
    __init__.py
    registry.py
    templates/
      express.yaml
      flask.yaml
      apache_php.yaml
  
  retry/
    __init__.py
    diagnostician.py
    retry_router.py
  
  chain_test/
    __init__.py
    deployer.py
    chain_runner.py
  
  packaging/
    __init__.py
    script_processor.py
    playbook_generator.py
    compose_generator.py
  
  flow/
    __init__.py
    orchestrator.py
    checkpoint.py
  
  ui/
    __init__.py
    console.py
    renderer.py               # Multi-system parallel output
  
  llm/
    __init__.py
    client.py                 # Direct Bedrock API wrapper
    prompts/                  # System prompts + few-shot examples per agent
      engineer.md
      developer.md
      attacker.md
      diagnostician.md
      planner_systems.md
      planner_entities.md
      planner_edges.md

docker/
  attacker/Dockerfile         # Extended with Playwright + Chromium
  target_express/Dockerfile
  target_flask/Dockerfile
  target_php/Dockerfile

tests/
  conftest.py                 # Fixtures, model factories
  test_models.py
  test_executor.py
  test_validator.py
  test_propagation.py
  test_construction_crew.py
  test_planner.py
  test_e2e.py
  fixtures/
    procedures/               # Hand-written procedure YAMLs
    graphs/                   # Hand-written graph YAMLs for validator
    entities/                 # Entity specs for construction crew testing
    apps/                     # Simple test apps for executor testing

atoms/                        # Carried over from v1
  ...

output/                       # Generated outputs
  ...
```

---

## Migration from v1

v1 continues to work during v2 development. No shared runtime code — v2 is a clean rewrite in a separate package (`goe/` vs `src/game_of_everything/`). Shared assets:

| Asset | Reuse Strategy |
|-------|---------------|
| `atoms/` | Shared directory, unchanged format |
| `docker/` Dockerfiles | Extended (add Playwright to attacker), not replaced |
| `goe.toml` | Same config file, add `[v2]` section if needed |
| ChromaDB collections | Same embeddings, same query interface |
| Target Docker images | Reused directly |
| `output/` directory | Same output location, different file structure |

v1 lives in `src/game_of_everything/`, v2 in `goe/`. Both importable, both runnable. Kill v1 when v2 reaches Phase 3.3 (CLI works end-to-end for single system).
