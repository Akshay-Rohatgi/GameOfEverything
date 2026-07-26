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

## Phase 0: Foundation ✅ COMPLETE

**Goal**: Project scaffolding, data models, and the procedure executor. No LLM calls. Fully testable with hand-written fixtures.

### 0.1 — Data Models ✅
All Pydantic models implemented in `goe/models/`: `entity.py`, `procedure.py`, `artifacts.py`, `report.py`.

### 0.2 — Procedure Executor ✅
Implemented in `goe/executor/`: `runner.py`, `actions/` (http, shell, listen, sleep, browser), `assertions.py`, `interpolation.py`, `outputs.py`, `sessions.py`.

**Note**: 3 browser/listen tests marked `xfail` — known Docker/Playwright networking issues with the test harness (not production blockers).

### 0.3 — Container Orchestration ✅
`goe/container/environment.py` wraps v1's `TestEnvironmentTool` with a clean interface. Lifecycle methods: `setup`, `teardown`, `deploy`, `exec_in`, `exec_in_bg`, `reset_attacker`, `reset_target`.

**Fixture procedures passing**: `basic_exec`, `step_chaining`, `ssh_login`, `suid_privesc`.

---

## Phase 1: Single Entity — Construction Crew ✅ COMPLETE

**Goal**: One entity goes through the full construction crew (engineer → developer → attacker) and produces artifacts + procedure that pass L2.

### 1.1 — Bedrock Client ✅
`goe/bedrock.py` — direct `boto3.client("bedrock-runtime").converse()` wrapper. Eliminates crewAI/LiteLLM dependency.

### 1.2 — Construction Crew ✅
`goe/construction_crew/`: `engineer.py` (Opus), `developer.py` (Sonnet), `attacker.py` (Sonnet), `orchestrator.py`.

**Self-review pass**: Both developer and attacker run a second LLM turn post-generation to catch correctness issues (param queries, DB path, payload safety) before returning.

**fix_procedure()**: Attacker has a targeted fix function used by the retry router for `procedure_bug` diagnoses — patches the existing procedure rather than regenerating from scratch.

### 1.3 — Runtime Templates ✅
`goe/runtimes/registry.py` + templates: `express.yaml`, `flask.yaml`, `apache_php.yaml`.

Deterministic deploy script generation from `BuildArtifact`. Each template has `developer_rules` and `attacker_rules` fields — runtime-specific constraints injected into the relevant agent's prompt, keeping the system prompts generic.

**`BuildArtifact` fields**: `source_files`, `primary_source`, `port`, `app_dir`, `db_setup`, `extra_deps`, `system_deps` (apt packages, e.g. Puppeteer deps).

### 1.4 — Docker Images ✅
- `goe-target-express` — Ubuntu 22.04 + Node.js 20 (NodeSource) + Chromium (xtradeb PPA, real binary not snap stub)
- `goe-target-flask` — Ubuntu 22.04 + Python 3 + Flask
- `goe-target-php` — Ubuntu 22.04 + Apache 2 + PHP

### 1.5 — Retry Loop ✅
`goe/retry/diagnostician.py` + `goe/retry/router.py`.

Escalation ladder:
- `procedure_bug` → `fix_procedure()` attacker only (max 2)
- `implementation_bug` → developer + attacker re-run (max 2), target container reset
- `design_flaw` → full crew re-run (max 1), target container reset

Diagnostician reads: app log, admin bot log (`/tmp/adminbot.log`), `ps aux`, `ss -tlnp`.

Attacker container reset before every retry (kills detached background listeners). Target container reset on `implementation_bug`/`design_flaw` (clears old app state and DB files).

### 1.6 — Build Pipeline ✅
`goe/build.py` — `build_entity()` wires all phases together. CLI: `python -m goe.build --spec <yaml>`. Writes full source + procedure to `/tmp/goe_<id>_*/` for inspection.

### Confirmed Passing Entities
| Entity | Runtime | Status |
|--------|---------|--------|
| `sqli_express` | Express | ✅ pass |
| `cmdi_flask` | Flask | ✅ pass |
| `sqli_php` | PHP | ✅ pass |
| `xss_stored_php` | PHP | ✅ pass |
| `xss_admin_bot_express` | Express | ✅ pass |

### Entity Fixture Matrix (untested)
| Atom | Express | Flask | PHP |
|------|---------|-------|-----|
| `sqli_union` | ✅ | fixture ready | ✅ |
| `cmd_injection` | fixture ready | ✅ | fixture ready |
| `xss_stored` | fixture ready | fixture ready | ✅ |
| `xss_reflected` | fixture ready | fixture ready | — |
| `xss_admin_bot` | ✅ | — | — |
| `path_traversal_lfi` | fixture ready | fixture ready | — |
| `ssti_jinja2` | — | fixture ready | — |
| `file_upload_bypass` | — | — | fixture ready |
| `insecure_deserialization` | — | fixture ready | — |

---

## Phase 2: Entity Graph — Planning Pipeline ✅ COMPLETE

**Goal**: User request → validated entity graph. No building yet — just the planning LLM calls and static validator.

### 2.1 — Static Validator
Build this FIRST (before the planners). Pure code, no LLM, defines the contract planners must satisfy.

```
goe/graph/
  validator.py          # The 7 validation checks
  topology.py           # Topological sort, dependency resolution
```

Write against hand-crafted graph fixtures:
- `fixtures/graphs/valid_2entity_chain.yaml`
- `fixtures/graphs/missing_edge.yaml`
- `fixtures/graphs/orphan_entity.yaml`
- `fixtures/graphs/cycle.yaml`

**Deliverable**: `pytest tests/test_validator.py` with 10+ cases covering all 7 checks.

### 2.2 — Value Propagation
```
goe/graph/
  propagation.py        # Update edge params after builder completes
  build_scheduler.py    # Topological build ordering with dependency tracking
```

```python
class BuildScheduler:
    def next_buildable(self) -> Entity | None
    def report_complete(self, entity_id: str, values: dict[str, str]) -> None
    def report_failed(self, entity_id: str) -> list[str]  # returns entities to SKIP
```

**Deliverable**: Unit tests with a 4-entity chain — complete in order, verify propagation. Fail entity 2, verify 3+4 are skipped.

### 2.3 — Planning Agents
```
goe/planner/
  design_systems.py     # Step 0: user request → systems
  plan_entities.py      # Step 1: systems + request → entity stubs
  specify_entities.py   # Step 2: stubs → full entity specs (parallel)
  connect_edges.py      # Step 3: entities → edges
  resolve.py            # Step 4: fill structural params (code, no LLM)
  pipeline.py           # Orchestrates steps 0-5 with validator at end
```

Validator retry strategy: if validation fails after step 5:
- **Option A** (start here): Return violations to step 3 (connect_edges) and retry, max 2.
- **Option B** (fallback): Return to step 1, full re-plan, max 1.

**Deliverable**: `python -m goe.plan "web app with SQL injection leading to credential theft and SSH pivot to database server"` produces a valid graph YAML.

---

## Phase 3: End-to-End Single System ✅ (single-system flow complete)

**Goal**: Full pipeline for single-system scenarios. User request → graph → build all entities → all L2 pass → packaged output.

**Status**: Implemented. `goe/flow/orchestrator.py:run()` wires `planner.pipeline.plan` →
`graph.BuildScheduler` → `build.build_entity` → `packaging.package`. `build_entity()` now
returns a `BuildOutcome` (result + deploy_script + procedure + outgoing_values) so the
orchestrator can propagate edge values and package. CLI: `python -m goe.flow run "..."`
(also the `goe` console script), with `--verbose`, `--resume`, and `--artifacts`.
Checkpoint/resume via `goe/flow/checkpoint.py` (`output/.checkpoints/<run_id>/state.json`).
Follow-ups: multi-web-entity port collisions only warn (Phase 4 splits per-system).

### 3.1 — Orchestrator
`goe/flow/orchestrator.py` — wires planning pipeline + build scheduler + construction crew + value propagation + failure handling + packaging.

### 3.2 — Packaging
Port v1's `script_postprocessor.py`:
- Concatenate deploy scripts in topological order
- Inject shebang + `set -e`
- Generate `playbook.yaml` (all procedures in chain order)
- Generate summary README

### 3.3 — CLI
```bash
goe run "SSH server with weak credentials and SUID privesc to root"
goe run --resume output/.checkpoints/<run_id>/
goe run --verbose "..."
```

Port v1's checkpoint system. Port v1's `GoEConsole` for minimal terminal output.

**Deliverable**: `goe run "..."` for 3 different single-system scenarios all produce working deploy scripts + playbooks.

---

## Phase 4: Multi-System

### 4.1 — Multi-System Build
Extend build scheduler for parallel builds across systems (ThreadPoolExecutor). Cross-system edges still enforce ordering. Port v1's `PipelineRenderer` + `BoxEventEmitter`.

### 4.2 — Chain Test
After all entities pass individual L2, deploy full topology and execute procedures in edge order, piping outputs between entities.

### 4.3 — Docker Compose Generation
Per-system `deploy_<system_id>.sh` + `docker-compose.yml` + chain-validated playbook.

---

## Phase 5: Polish and Parity

- **Atom integration**: Wire ChromaDB RAG into the construction crew (engineer receives top-3 relevant atoms).
- **EC2 deploy**: Port v1's `ec2_deploy.py`.
- **Cost optimization**: Cache engineer plans, short-circuit procedure-only fixes.
- **Observability**: Structured logging, cost tracking, failure mode statistics.
- **Preset apps** (WordPress, phpBB): Defer — entity model can express them but builder needs different flow.

---

## What NOT to Build (For Now)

- **Preset apps** — defer to Phase 5+. Graph model can express them; builder needs a different flow.
- **Graph editor UI** — YAML is the interface.
- **LLM-powered chain test repair** — report chain failures, don't auto-fix.
- **Edge type extension mechanism** — 8 types is enough to ship.
- **Go/C TCP runtimes** — natural fit once Phase 2 exists (two-entity graph: server + client). Not worth forcing into single-entity model now.

---

## Current Directory Structure

```
goe/
  bedrock.py                    ✅ Direct Bedrock API wrapper
  build.py                      ✅ Single-entity pipeline + CLI
  config.py                     ✅ GoEConfig (shared with v1)
  models/
    entity.py                   ✅
    procedure.py                ✅
    artifacts.py                ✅ includes system_deps, app_dir
    report.py                   ✅
  executor/
    runner.py                   ✅
    interpolation.py            ✅
    assertions.py               ✅
    outputs.py                  ✅
    sessions.py                 ✅
    actions/ (http, shell, listen, sleep, browser) ✅
  construction_crew/
    orchestrator.py             ✅
    engineer.py                 ✅ Opus, self-review pass
    developer.py                ✅ Sonnet, self-review pass
    attacker.py                 ✅ Sonnet, self-review + fix_procedure()
    prompts/ (3 system prompts) ✅
  graph/                        ✅ models, validator, topology, build_scheduler
  planner/                      ✅ design_systems, plan_entities, specify_entities, connect_edges, resolve, pipeline, __main__
  runtimes/
    registry.py                 ✅
    templates/ (express, flask, apache_php) ✅
  retry/
    diagnostician.py            ✅
    router.py                   ✅
  container/
    environment.py              ✅ reset_attacker, reset_target, exec_in_bg
  flow/                         ✅ orchestrator, __main__ (goe run), console, checkpoint
  packaging/                    ✅ packager (deploy.sh + playbook.yaml + README), postprocessor

docker/
  target_express/Dockerfile     ✅ Node.js 20 + Chromium (xtradeb)
  target_flask/Dockerfile       ✅
  target_php/Dockerfile         ✅
  attacker/Dockerfile           ✅ (v1, shared)

tests/
  test_executor.py              ✅ 3 xfail browser tests
  test_build.py                 ✅ full cross-product matrix (12 untested fixtures)
  test_bedrock.py               ✅ mocked
  test_runtimes.py              ✅ unit
  fixtures/
    procedures/ (4 fixtures)    ✅
    entities/ (17 fixtures)     ✅
```
