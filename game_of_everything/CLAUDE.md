# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## GoE v2 Rewrite (`goe/`)

Active development is on the `goe-rewrite` branch. The `goe/` directory is a complete rewrite of the pipeline — it does not use crewAI or LiteLLM and is independent of the v1 `src/game_of_everything/` code (except `goe/container/environment.py` which wraps v1's `TestEnvironmentTool`).

**Current status: Phases 0–4 (increment 1) complete.** Phase 3 delivered the single-system orchestrator (`goe/flow/`), packaging (`goe/packaging/`), and the `goe run` CLI. Phase 4 increment 1 delivered multi-entity/multi-system packaging and the L3 chain test. Phase 4 increment 2 (parallel entity builds + output serialization) is next.

### Commands

```bash
# All v2 commands must use the venv python
.venv/bin/python -m pytest tests/                          # full test suite
.venv/bin/python -m pytest tests/test_build.py -k sqli     # single test by keyword
.venv/bin/python -m pytest -m "not docker and not llm"     # skip Docker/LLM tests
.venv/bin/python -m pytest -m docker                       # Docker tests only

# Run the single-entity build pipeline against a fixture
.venv/bin/python -m goe.build --spec tests/fixtures/entities/sqli_express.yaml

# Run the planner only (Steps 0–3, outputs graph YAML)
.venv/bin/python -m goe.planner "web app with SQL injection leading to credential theft"

# Run the full single-system flow: plan → build all entities → package
.venv/bin/python -m goe.flow run "web app with SQL injection that leaks credentials"
.venv/bin/python -m goe.flow run --verbose "SSH server with weak credentials and SUID privesc"
.venv/bin/python -m goe.flow run --resume output/.checkpoints/<run_id>/   # resume a killed run
# Output: output/<run_id>/{deploy.sh, playbook.yaml, README.md}

# Re-test an existing output directory (no LLM calls — deploys and runs the playbook in Docker)
.venv/bin/python -m goe.flow test output/<run_id>/
.venv/bin/python -m goe.flow test output/<run_id>/ --runtime express   # override runtime if no checkpoint
# Containers stay up after the run; press Enter to tear down (useful for manual exploration)

# Run evaluation suite
.venv/bin/python -m goe.eval --suite build --fixtures tests/fixtures/entities/sqli_express.yaml
.venv/bin/python -m goe.eval --suite planning --golden sqli_basic --request "web app with SQLi"
```

### v2 Architecture

The pipeline has two phases:

**Phase 1 — Planning** (`goe/planner/`): natural language → validated `EntityGraph`. Four sequential LLM calls (design_systems → plan_entities → specify_entities → connect_edges), then deterministic resolve + validate. Validator retries connect_edges up to 2×; full re-plan up to 1×.

**Phase 2 — Building** (`goe/build.py` + `goe/construction_crew/`): per-entity pipeline. Three LLM agents in sequence:
- **Engineer** (`engineer.py`, Opus): entity spec + atoms → `EngineerPlan` (architecture, endpoints, data model, attack entry point)
- **Developer** (`developer.py`, Sonnet): plan → `BuildArtifact` (source files, DB setup, concrete outgoing edge values). Runs a self-review second turn in the same conversation.
- **Attacker** (`attacker.py`, Sonnet): artifact + plan → `Procedure` (YAML attack steps). Also runs a self-review second turn. Has `fix_procedure()` for targeted procedure-only retries.

**Phase 3 — Orchestration** (`goe/flow/` + `goe/packaging/`): `goe/flow/orchestrator.py:run()` ties the two phases together for single-system scenarios. It calls `plan()`, drives a `BuildScheduler` (topological build order + concrete edge-value propagation), calls `build_entity()` per entity, and packages the PASSED entities. `build_entity()` returns a `BuildOutcome` (the `EntityResult` plus the final `deploy_script`, `procedure`, and `outgoing_values`). `goe/packaging/packager.py:package()` concatenates per-entity deploy scripts (topo order) through `postprocessor.apply_post_processors` into one `deploy.sh`, plus `playbook.yaml` and `README.md`. Checkpoint/resume lives in `goe/flow/checkpoint.py` (`output/.checkpoints/<run_id>/state.json`) — resuming skips planning and already-built entities. CLI is `goe/flow/__main__.py` (`goe run`).

**Phase 4 (increment 1) — Multi-entity chain test + multi-system packaging** (`goe/flow/chain_test.py`, `goe/container/topology_environment.py`, `goe/construction_crew/chain_attacker.py`, `goe/packaging/packager.py`): When more than one entity builds successfully, the orchestrator runs an L3 chain test that **gates the overall run** (FAILED → `RunResult.success=False`, CLI exits non-zero). The chain test: (1) brings up `TopologyEnvironment` (one `ubuntu:22.04` container per system on a shared `goe_chain_net` Docker bridge, one shared Kali attacker — container hostnames are network aliases); (2) deploys each system's concatenated entity scripts; (3) calls the LLM `chain_attacker` agent (Opus, same style as `attacker.py`) to synthesise one end-to-end `Procedure` using `${system.<id>.host}` / `${system.<id>.port}` cross-system addressing; (4) executes it via the existing `runner.run()`; (5) retries up to 2× (`fix_chain`) on failure. Interpolation supports `${system.<system_id>.host/port}` in addition to existing `${target_host}`, `${edge.*}`, `${steps.*}`. Multi-system runs emit `<system_id>_deploy.sh` per system, `docker-compose.yml` (one service per system, hostnames as aliases, deploy script embedded as base64), and `chain_playbook.yaml`. Single-system runs retain the unchanged `deploy.sh` output. `goe flow test` auto-detects `chain_playbook.yaml` and replays via `TopologyEnvironment`.

After crew, `goe/runtimes/registry.py` generates a deterministic bash deploy script from the `BuildArtifact`. `goe/container/environment.py` spins up Docker containers (target + attacker), deploys the script, and runs the procedure executor (`goe/executor/`). On L2 failure: `goe/retry/diagnostician.py` categorises the failure (`procedure_bug` / `implementation_bug` / `design_flaw`), and `goe/retry/router.py` dispatches back into the appropriate crew agents.

### LLM Calls — All Routes Through `goe/bedrock.py`

Every LLM call is a direct `boto3.client("bedrock-runtime").converse()` call. No crewAI, no LiteLLM.

| Location | Role | Model config key | Calls per run |
|---|---|---|---|
| `planner/design_systems.py` | design systems | `planner` | 1 |
| `planner/plan_entities.py` | plan entities | `planner` | 1 |
| `planner/specify_entities.py` | specify entities (parallel) | `planner` | N (one per entity stub) |
| `planner/connect_edges.py` | connect edges | `planner` | 1 |
| `construction_crew/engineer.py` | engineer | `engineer` | 1 (+ 1 on parse fail) |
| `construction_crew/developer.py` | developer | `developer` | 2 always (generate + self-review in same conversation) |
| `construction_crew/attacker.py` | attacker | `attacker` | 2 always (generate + self-review); `fix_procedure()` is 1 extra |
| `retry/diagnostician.py` | diagnostician | `diagnostician` | 1 (fires only on L2 failure) |

Model per role is configured in `goe.toml` under `[models.v2_overrides]`, overridable per-role via `GOE_MODEL_<ROLE>` env vars.

### Key Contracts

**`BuildArtifact`** (`goe/models/artifacts.py`): what the developer produces. `source_files` is a `dict[filename → content]`. `primary_source` is the entry point. For ubuntu runtime, `primary_source` is a bash script and `port` is None. For web runtimes, `app_dir` defaults to `/opt/webapp` and `port` is required.

**`Procedure`** (`goe/models/procedure.py`): what the attacker produces. Steps use `${target_host}`, `${attacker_host}`, `${target_port}` for interpolation, plus `${edge.<id>.<param>}` for resolved edge values and `${steps.<step_id>.<output>}` for captured step outputs.

**`EntityGraph`** (`goe/graph/models.py`): edges have two-phase params — `structural` (plan-time descriptor) and `concrete` (build-time value filled by developer). Static validation operates on structural values only.

**`BuildScheduler`** (`goe/graph/build_scheduler.py`): topological state machine. `next_buildable()` → `(entity, incoming_edges_dict)`. `report_complete(id, outgoing_values)` propagates concrete values. `report_failed(id)` returns the list of transitively skipped entity IDs.

### Runtime Templates

`goe/runtimes/templates/*.yaml` define how to install, start, and healthcheck each runtime. `RuntimeRegistry.deploy()` generates the full bash deploy script deterministically — no LLM. The `developer_rules` and `attacker_rules` fields in each template are injected into the respective agents' prompts. Always use `mariadb-server` (not `mysql-server`) for MySQL in Docker. Source files are written via `base64 -d` to handle arbitrary content safely.

### Test Markers

`docker` — requires a running Docker daemon (slow, ~1-3 min per entity). `llm` — requires AWS credentials in `goe.toml` and makes real Bedrock API calls. `eval` — evaluation tests (requires both docker and llm). Tests in `test_build.py` are both `docker` and `llm`. Run `pytest -m "not docker and not llm"` for fast unit tests only.

### Entity Fixtures

`tests/fixtures/entities/*.yaml` are the primary test inputs for `build_entity()`. Each is a minimal `Entity` YAML with `id`, `description`, `system_id`, `runtime`, `requires`, `provides`, `atoms`. Confirmed passing: `sqli_express`, `cmdi_flask`, `sqli_php`, `xss_stored_php`, `xss_admin_bot_express`.

### Adding a New Web Runtime

A web runtime is fully described by a single template YAML — no Python edits are needed.

1. Add `goe/runtimes/templates/<id>.yaml` with:
   - `id`, `port`, `start_cmd`, `healthcheck`
   - `target_image`: Docker image name (e.g. `goe-target-<id>:latest`). `RuntimeRegistry.image_for()` / `goe/container/environment.py:_image_for()` read this; there is no separate image dict.
   - `install_runtime`: bash to install the runtime (apt packages, NodeSource, etc.)
   - `deps_install_template` (optional): package-manager install command with an `{extra}` placeholder. `{extra}` expands to the space-prefixed `extra_deps` (or `""` when none), so one template covers the deps and no-deps cases. Omit this field entirely if the runtime installs no per-app packages (e.g. apache_php).
   - `pre_start` (optional): shell commands emitted verbatim after DB setup and before the service starts (e.g. apache_php's `mkdir`/`chown` for www-data dirs).
   - `developer_rules`, `attacker_rules`: injected into the respective agent prompts.
2. Add `docker/target_<id>/Dockerfile` with the pre-installed runtime (image name must match `target_image`).

Non-web base images (`ubuntu`, `preset`) that have no template live in `_BASE_IMAGES` in `goe/container/environment.py`.

### Evaluation & Metrics System

**Location**: `goe/eval/`, `goe/metrics/`

All LLM calls are instrumented transparently through `goe/bedrock.py` to capture per-call efficiency metrics (tokens, latency, model, caller). Metrics are opt-in via `MetricsSession` context vars — no overhead when not evaluating.

**Efficiency metrics** (per LLM call):
- Input/output tokens from Bedrock Converse API `usage` response
- Wall-clock latency (ms)
- Caller identity (e.g., "engineer", "planner.design_systems", "attacker.self_review")

**Quality metrics** (system-level):
- Planning adherence: compare planner output against golden test cases (`tests/fixtures/golden_plans/*.yaml`)
- Build pass rates: L2 test success across fixtures
- Retry counts: mean attempts per entity, failure category breakdown (`DiagnosisCategory`)

**CLI**:
```bash
# Build eval
.venv/bin/python -m goe.eval --suite build --fixtures tests/fixtures/entities/sqli_express.yaml

# Planning eval
.venv/bin/python -m goe.eval --suite planning --golden sqli_basic --request "web app with SQLi"

# Full eval (both)
.venv/bin/python -m goe.eval --suite full --fixtures <paths> --golden <name> --request "<text>"
```

**Output**: `eval_results/<timestamp>/summary.json`, `llm_calls.jsonl`, `entity_results.json`, `plan_adherence.json`

**Programmatic**:
```python
from goe.metrics import start_session, end_session
session = start_session()
# ... run pipeline ...
session = end_session()
summary = session.summary()  # total_calls, total_tokens, calls_by_caller
```

See `goe/eval/README.md` for details.

### Workflow Artifacts

**Location**: `goe/artifacts/`

Opt-in persistence of LLM conversation history and all generated files (app source, DB schema/seed SQL, attack procedure YAML, engineer plan) for every run. Off by default — zero overhead when disabled.

**Enable**:
```toml
# goe.toml
[artifacts]
enabled = true
dir     = "artifacts"   # timestamped subdirs created under this root
```
Or per-run: `GOE_SAVE_ARTIFACTS=1` env var, or `--artifacts` / `--no-artifacts` CLI flag on any entry point.

**CLI flags** (work on all entry points):
```bash
.venv/bin/python -m goe.build   --spec ... --artifacts
.venv/bin/python -m goe.planner "..." --artifacts
.venv/bin/python -m goe.eval    --suite build --fixtures ... --artifacts
```

**On-disk layout** per run:
```
artifacts/2026-06-12T14-30-05/
├── manifest.json            # run index: entry point, token totals, file list
├── conversations/
│   ├── llm_calls.jsonl      # full LLMTranscriptRecord per call (machine)
│   ├── developer.md         # human-readable: system + turns + responses
│   ├── attacker.md  engineer.md  planner.md
├── entities/<id>/
│   ├── app/<source_files>   # nested paths preserved; path traversal blocked
│   ├── db/{schema.sql,seed.sql}
│   ├── artifact.json  procedure.yaml  engineer_plan.json
│   └── attempts/attempt_<n>/  # retry artifacts + attempt_*_to_*.diff
├── metrics/llm_calls.jsonl  # token/latency records
```

For eval runs, artifacts are co-located inside the existing `eval_results/<timestamp>/` dir.

**Conversation de-duplication**: `developer.py`/`attacker.py` grow a single `messages` list across `generate` → `self_review` → `retry` calls. The capture logic stores only the **delta** per call (keyed on the caller root), so `developer.md` shows a clean non-overlapping conversation. `fix_procedure` (fresh list) automatically resets the counter.

**Retry diffs**: On L2 failure, each retry attempt's updated app and procedure are saved under `attempts/attempt_N/`, and a unified diff (`attempt_<prev>_to_<N>.diff`) is written alongside for easy review.

See `goe/artifacts/README.md` for full schema documentation.

---

## v1 System (`src/game_of_everything/`)

## Project Overview

**Game of Everything (GoE)** is a framework for agentically building vulnerable cybersecurity challenges. It takes natural language requests for vulnerable environments and generates validated deployment scripts that set up those environments on Ubuntu 22.04.

The system is built on **crewAI Flow** with a multi-agent architecture that breaks down requests, maps them to vulnerability "atoms," sequences dependencies, generates bash scripts, and validates them in Docker containers. It supports single-box, multi-box (networked VMs), custom-generated web apps, and pre-built preset apps (WordPress, phpBB).

## Commands

### Running the Flow
```bash
crewai run
```
Main entry point. Prompts for a vulnerable environment request and runs the full pipeline. Console output is minimal — full agent logs are written to `output/<timestamp>.log`.

```bash
# Resume from a previous checkpoint
crewai run --resume output/.checkpoints/<run_id>/
```

### RAG Pipeline Management
```bash
# Ingest/update atoms into ChromaDB
python scripts/rag_gen.py

# Test RAG retrieval
python scripts/query.py "search query"
```
Run `rag_gen.py` whenever atoms are added/modified to sync the vector database.

### Docker Image Management
```bash
# Build attacker image independently (useful for debugging Dockerfile issues)
build_attacker

# Or via Python module
python -m game_of_everything.main build_attacker_image
```
Pre-build the attacker image to separate build failures from test failures. The image is automatically cached for 7 days — rebuilds only occur if the image is missing or older than the threshold.

Per-runtime target images (`goe-target-express`, `goe-target-flask`, `goe-target-php`) are built automatically on first use.

### Custom App Test Harness
```bash
# Generate app only (no Docker), save result to file
python scripts/test_custom_app.py --generate-only --save /tmp/app.json

# Run Docker L1+L2 from a previously saved generation (fast iteration)
python scripts/test_custom_app.py --from-file /tmp/app.json --no-rebuild

# Full end-to-end (generate + Docker)
python scripts/test_custom_app.py

# Override the vulnerability/goal/runtime
python scripts/test_custom_app.py --vuln xss_admin_bot --goal session_theft_via_xss --runtime express
```
Use `--from-file` to iterate on Docker/snippet issues without re-running the expensive Opus generation step.

### Configuration
```bash
# Copy the example config and fill in your values
cp goe.toml.example goe.toml
```
All settings (AWS credentials, model selection, EC2 deploy config) live in `goe.toml`. Environment variables override toml values.

### Development
```bash
# Install dependencies
crewai install
```

## High-Level Architecture

### Configuration System
All configuration is centralized in `config.py` via the `GoEConfig` singleton, loaded from `goe.toml`:
- **AWS credentials**: `[aws]` section (overridden by `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` env vars)
- **Model selection**: `[models]` section with per-agent overrides in `[models.overrides]`
- **EC2 deploy**: `[deploy]` section (instance type, key pair, security group, subnet)

Resolution order for models: env var → toml override → `config/models.yaml` override → toml default → yaml default → hardcoded fallback.

### CLI Output System
The `GoEConsole` class (`ui.py`) provides clean, minimal terminal output:
- All crewAI/liteLLM verbose output is redirected to a log file via `ui.capture()` context manager
- Terminal shows only structured progress: spinners (`status`), checkmarks (`step_done`), test result rows (`test_result`)
- Full agent reasoning is always available in `output/<timestamp>.log`
- All agents use `verbose=False` — no step callbacks
- When `ui=None` (e.g. `test_custom_app.py`), step functions fall back to `print()`

### Flow Pipeline
The main flow (`GoEFlow` in `main.py`) runs these steps in sequence:
1. **synthesize_scenario**: Elaborate the user request into a fully-specified scenario — resolves all implicit decisions, defines `misconfig_scope`, `custom_app_scope`, `custom_vectors`, `preset_vectors`, and multi-box `topology`
2. **box_pipelines**: Run the full per-box pipeline for every box in the topology (in parallel for multi-box). Each box runs: resolve_custom_apps → resolve_preset_apps → engineer_requirements → generate_implementation → test_snippets → finalize_script
3. **chain_test**: Multi-box only — validates the end-to-end attack chain across all boxes
4. **finalize_topology**: Multi-box only — writes output package (docker-compose, per-box scripts, README, playbook)
5. **deploy**: Optional one-click EC2 deployment

**Single-box flow**: `box_pipelines` runs inline (no threading), passes `ui` directly to step functions for real-time terminal output.

**Multi-box flow**: Each box runs in a `ThreadPoolExecutor` thread. A `PipelineRenderer` + `BoxEventEmitter` system serialises all terminal output through a single thread to avoid interleaved output.

### Atom System
Atoms are markdown files in `atoms/` with frontmatter defining:
- `id`: Unique identifier (e.g., `samba_insecure_share`)
- `required_vars`: Parameters the snippet generator must fill (e.g., `share_name`, `path`)
- `Logic Requirements`, `Synthesis Guidance`, `Testing Guidance` sections

Atoms are ingested into ChromaDB (`src/game_of_everything/chroma_db/`) using Amazon Bedrock Titan embeddings. There are two ChromaDB collections:
- `goe_collection`: Misconfig/privesc atoms (used by the main mapping pipeline)
- `web_vuln_atoms`: Web vulnerability atoms for custom app generation (used by `CustomAppFlow`)

### Custom Application Pipeline
Custom apps are fully-generated vulnerable web applications built by `CustomAppFlow` (`steps/custom_app_flow.py`). Each app is defined by a `CustomVector` specifying:
- `vuln_atom_ids`: List of web vulnerabilities to embed (e.g., `["sqli_union"]`, `["file_upload_bypass", "path_traversal_lfi"]`)
- `attack_chain_goals`: List of exploit objectives (e.g., `["credential_theft"]`, `["rce_via_webshell"]`)
- `runtime_id`: Web runtime (`"flask"`, `"express"`, `"apache_php"`)

`CustomVector` accepts singular `vuln_atom_id` / `attack_chain_goal` fields for backward compatibility and coerces them to lists.

**CustomAppFlow steps**: `load_context` → `generate_app` → `validate_end_to_end` → `emit_result`

The `app_generation_agent` uses Claude Opus 4.6 to produce a `GeneratedApp` with:
- `app_filename`, `app_source`: Single application source file
- `schema_sql`, `seed_sql`, `setup_db_sh`: DB setup files (all `Optional` — omitted for DB-less apps)
- `deploy_snippet`, `testing_snippet`, `attack_snippet`

**Retry loop**: `validate_end_to_end` runs up to `MAX_GENERATE_RETRIES = 2` full regeneration cycles. Within each cycle, if L2 fails, the **Attack Agent** gets up to `MAX_ATTACK_RETRIES = 2` attempts to fix the exploit before falling through to full app regeneration.

**Self-contained packaging**: `emit_result` calls `_package_deploy_snippet()` which prepends quoted heredocs for all app files into the `deploy_snippet`. The final script creates `/tmp/goe_app/` from embedded file content — no external staging directory needed. During Docker testing, `_stage_app_files()` stages files via `copy_to_target()` instead.

**Heredoc delimiter format**: `GOE_<FILENAME_WITHOUT_DOT>_EOF` (e.g., `GOE_APP_PY_EOF`, `GOE_SCHEMA_SQL_EOF`). Quoted to prevent shell expansion of embedded content.

**Web runtimes**: Defined in `src/game_of_everything/custom_apps/web_runtimes/*.yaml`. Flask and Express use a systemd+nohup detection pattern:
```bash
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload && systemctl enable myapp && systemctl start myapp
else
    nohup <start command> &
fi
```
Real deployments get a proper systemd service; Docker test containers (no systemd) use nohup.

**Node.js on Express**: Use NodeSource to install Node.js 20 — Ubuntu 22.04's apt ships v12 (EOL, breaks modern npm packages). See `express.yaml` for the install pattern.

**Database**: Always use `mariadb-server` (never `mysql-server`) for Docker environments. `mysql-server` fails to configure without systemd.

### Preset Application Pipeline
Preset apps are pre-defined real-world applications (WordPress, phpBB) deployed via `PresetAppFlow` (`steps/preset_app_flow.py`). Each is defined by a `PresetVector` specifying:
- `preset_id`: App identifier (`"wordpress"`, `"phpbb"`)
- `vuln_profile_ids`: List of vulnerability profiles to apply (e.g., `["wp_default_creds"]`)

Preset definitions live in `src/game_of_everything/preset_apps/`:
- `presets/`: App-level YAML (WordPress, phpBB)
- `stacks/`: Infrastructure YAML (LAMP stack)
- `vuln_profiles/`: Per-vulnerability YAML configs

### Attack Agent
When a custom app's L2 (external attack probe) fails, the **Attack Agent** (`attack_agent` in `agents.yaml`, task `fix_attack_snippet_task` in `tasks.yaml`) is invoked before falling back to full app regeneration. It:
1. Reads the app source to understand actual endpoints and parameters
2. Uses `BoundExecInAttackerTool` and `BoundExecInTargetTool` to probe the live containers
3. Constructs a working exploit and tests it before returning
4. Returns `AttackDiagnosticResult(fixed_attack_snippet, diagnosis, confidence)`

This saves ~$0.15 and ~50s per failure by fixing the exploit script rather than regenerating the entire app. The attack agent uses Claude Sonnet 4.6.

**Bound exec tools** (`tools/bound_exec_tools.py`): Pre-bound to a specific container at construction time. The LLM only supplies `snippet: str` — no container ID needed. `BoundExecInTargetTool` uses `privileged=True`.

### RAG-Enhanced Mapping
The Mapping Agent uses `SearchAtomsTool` to query ChromaDB with semantic search. It returns top-3 results by default, but validators use `n_results=1` for best-match-only lookups.

### Two-Layer Testing with Diagnostic and Attack Agents
Testing happens in Docker with two containers on a bridge network:
- **goe_target** (runtime-specific image): Where snippets and apps are deployed
- **goe_attacker** (Kali Linux): Where attack probes run from

**Per-runtime target images** (pre-built, avoids re-installing runtimes per test):
- `goe-target-express` — Ubuntu 22.04 + Node.js 20 via NodeSource
- `goe-target-flask` — Ubuntu 22.04 + Python 3 + Flask
- `goe-target-php` — Ubuntu 22.04 + Apache 2 + PHP
- `goe-preset-target` — Ubuntu 22.04 + WP-CLI (for preset apps)

For misconfig atoms, the base `ubuntu:22.04` image is used with bootstrap tools installed at startup.

**Layer 1 (Internal)**: Run `testing_snippet` inside target container. LLM judges if config/app was set up correctly.
**Layer 2 (External)**: Run `attack_snippet` from attacker container against target. LLM judges if exploit works.

Layer 2 for misconfig atoms is **incremental cumulative**: after applying snippet N, re-run attack probes for all snippets 0..N. This catches dependency misordering and regressions.

**On Layer 1 failure**: The **Diagnostic Agent** (tools: `ReadAtomTool`, `ExecInContainerTool`) attempts up to 2 fixes.
**On Layer 2 failure** (custom apps): The **Attack Agent** (tools: `BoundExecInAttackerTool`, `BoundExecInTargetTool`) attempts up to 2 exploit fixes before triggering full app regeneration.
**On Layer 2 failure** (misconfig atoms): Logs diagnosis only, does not retry.

**Template syntax sanitization**: All string inputs passed to crewAI crew `kickoff()` are sanitized with `_si()` which replaces `{{` → `{ {` and `}}` → `} }`. This prevents SSTI payloads in generated content (e.g., `{{7*7}}`) from breaking crewAI's Jinja2-based input templating.

**Expected output format**: Verdict and diagnostic task `expected_output` in `tasks.yaml` must specify strict JSON-only (no markdown, no surrounding text). LLM output is parsed directly as JSON.

### Multi-Box Topology
When a scenario involves multiple machines, GoE builds a `NetworkTopology` with:
- `BoxDefinition` per machine (box_id, hostname, misconfig_scope, custom_vectors)
- `PivotLink` directed edges (credential reuse between boxes)
- `SharedSecret` credential bridges
- `ChainProbe` steps for Layer 3 attack chain validation

Each box runs its full pipeline (resolve_custom_apps → engineer_requirements → ... → finalize_script) in a separate thread. Output is serialised through `PipelineRenderer` + `BoxEventEmitter` via a `queue.Queue`.

**Layer 3 (Chain Test)**: After all boxes pass L1/L2, `run_chain_test` deploys the full multi-box topology and executes each `ChainProbe` sequentially. First failure cascades — remaining probes are skipped.

**Output structure** (multi-box): `output/<timestamp>_<scenario_slug>/` containing `docker-compose.yml`, per-box `<box_id>_deploy.sh`, `playbook.json`, `README.md`.

### EC2 One-Click Deploy
After validation, the flow optionally deploys the script to a new EC2 instance (`ec2_deploy.py`):
- Looks up the latest Ubuntu 22.04 AMI via `describe_images`
- Auto-creates a security group with common challenge ports (SSH, HTTP, SMB, DB ports, etc.)
- Passes the deploy script as EC2 `user_data` — runs as root on first boot, no SSH/paramiko needed
- Scripts > 16KB are gzip-compressed and wrapped in a self-extracting bootstrap
- Configured via `[deploy]` section in `goe.toml` (instance type, key pair, security group, subnet)
- Multi-box runs skip EC2 deploy (use docker-compose instead)

### Script Post-Processing
Generated snippets are concatenated and run through `script_postprocessor.py`:
1. `inject_shebang`: Strip any shebangs, prepend exactly one `#!/bin/bash`
2. `ensure_set_e`: Add `set -e` after shebang (fail-fast behavior)
3. `normalize_blank_lines`: Collapse 3+ consecutive blank lines to 2

### Checkpoint / Resume System
Each flow step writes a checkpoint to `output/.checkpoints/<run_id>/` after completion. To resume a failed run:
```bash
crewai run --resume output/.checkpoints/<run_id>/
```
Already-completed steps are skipped. State is fully serialised via Pydantic JSON.

## Key Technical Details

### CrewAI Bedrock Bug (Manual Patch Required)
CrewAI has a bug in `.venv/lib/python3.12/site-packages/crewai/agents/crew_agent_executor.py:722` that drops Bedrock native tool call arguments.

**Fix**: Change:
```python
func_args = func_info.get("arguments", "{}") or tool_call.get("input", {})
```
to:
```python
func_args = func_info.get("arguments") or tool_call.get("input", {})
```

**When upgrading crewai**, check if [PR #4518](https://github.com/crewAIInc/crewAI/pull/4518) is merged. If not, reapply the patch.

### patches.py — Runtime Monkey-Patches
`patches.py` is imported at the top of `main.py` and `test_custom_app.py`. It applies two patches automatically:

**Patch 1 — JSON Repair**: LLMs on Bedrock occasionally produce JSON with unquoted keys, Python-style True/False/None, trailing commas, or markdown fences. Monkey-patches `crewai.utilities.converter.convert_to_model` (and the imported reference in `crewai.task`) to run `json-repair` before parsing. Safe on valid JSON (no-op).

**Patch 2 — Bedrock Assistant Prefill Rejection**: Claude 4.x models on Bedrock reject conversations ending with an assistant message (treated as "prefill"). crewAI's `handle_max_iterations_exceeded()` appends an assistant message then calls the LLM, triggering this error. Monkey-patches `BedrockCompletion._format_messages_for_converse()` to append a user continuation message whenever the last message is from the assistant.

### LLM Configuration
The flow uses AWS Bedrock with Claude Sonnet 4.6 for most agents. The `app_generation_agent` uses Claude Opus 4.6 for higher-quality code generation. The `attack_agent` uses Claude Sonnet 4.6. Model IDs are prefixed with `us.` inference profile to avoid on-demand throughput errors. The native Bedrock provider (not LiteLLM) is used when `bedrock/` prefix is in the model string.

Model configuration lives in `goe.toml` and `config/models.yaml`. The `llm_factory.py` module centralizes LLM construction with per-agent resolution.

### Data Flow is Prompt-Based
All inter-agent communication happens via crewAI `context=[...]` (task chaining) or `inputs={}` (direct injection). There's no programmatic structured handoff. The state object (`GoEState`) is populated by parsing Pydantic-structured LLM outputs.

### Docker Attacker Image
The Kali attacker image (`docker/attacker/Dockerfile`) pre-installs common tools: smbclient, sshpass, nmap, hydra, metasploit-framework, redis-tools, psql, mysql-client, mongosh, ncat, nikto, enum4linux.

**Tool Availability**:
- `ensure_attacker_tools()` scans attack snippets and installs missing tools individually at runtime
- Failed installations log warnings but don't stop execution (graceful degradation)

### Atom Dependency Enumeration is Automatic
Users specify vulnerabilities, not infrastructure. The Dependency Enumeration Agent infers OS packages (samba, openssh-server, zip, apt packages for non-base SUID binaries) and adds `install_package` atoms automatically.

### misconfig_scope Must Be Attacker-Facing
The `misconfig_scope` field in `SynthesizedScenario` is passed to the `engineer_requirements` parser agent. It must describe vulnerabilities from an attacker's perspective (e.g., "SSH service with username `admin` and password `admin123`"), **not** deployment instructions (e.g., "Deploy one OS user..."). The parser only extracts attack vectors — imperative instructions produce no atoms.

### Summary Counts Custom and Preset Apps
`main.py`'s summary step counts both misconfig snippets (`state.generated_snippets`) and custom/preset apps (`resolved_custom_apps`, `resolved_preset_apps`) across all box states. A custom-app-only run shows `✓ 1/1 apps validated` rather than `✓ 0/0 atoms validated`.

## File Structure

```
goe.toml.example                    # Configuration template (copy to goe.toml)
atoms/                              # Misconfig/privesc vulnerability definitions (markdown)
  bash_history_leak.md
  cms_default_creds.md
  create_user.md
  cron_job_hijack.md
  database_expose.md
  exposed_env_vars.md
  ftp_anon_upload.md
  install_package.md
  mongodb_disable_auth.md
  motd_command_injection.md
  phpmyadmin_disable_auth.md
  postgres_rce.md
  python_path_hijack.md
  redis_disable_auth.md
  redis_replication_leak.md
  samba_insecure_share.md
  sensitive_file.md
  set_capability.md
  set_suid.md
  sudoers_no_passwd.md
  weak_service_password.md
  writable_systemd_service.md
  web_vulnerabilities/              # Web vulnerability atoms (ChromaDB: web_vuln_atoms)
    cmd_injection.md
    file_upload_bypass.md
    path_traversal_lfi.md
    sqli_blind.md
    sqli_tautology.md
    sqli_union.md
    ssti_jinja2.md
    xss_admin_bot.md                # Stored XSS + Puppeteer admin bot cookie theft
    xss_reflected.md
    xss_stored.md
docker/
  attacker/Dockerfile               # Kali Linux attacker container
  preset_target/Dockerfile          # Ubuntu 22.04 + WP-CLI (preset apps)
  target_express/Dockerfile         # Ubuntu 22.04 + Node.js 20 (Express apps)
  target_flask/Dockerfile           # Ubuntu 22.04 + Python 3 + Flask
  target_php/Dockerfile             # Ubuntu 22.04 + Apache 2 + PHP
scripts/
  rag_gen.py                        # Ingest atoms into ChromaDB
  query.py                          # Test RAG retrieval
  test_custom_app.py                # Standalone custom app test harness
  bedrock_access.py                 # Debug Bedrock connectivity
src/game_of_everything/
  main.py                           # GoEFlow definition + orchestration
  models.py                         # Pydantic models for all data structures
  state.py                          # GoEState definition
  config.py                         # GoEConfig singleton (loads goe.toml)
  patches.py                        # Runtime monkey-patches (JSON repair, Bedrock prefill)
  ui.py                             # GoEConsole — clean CLI output + log capture
  ui_events.py                      # Multi-box parallel rendering (BoxEventEmitter, PipelineRenderer)
  ec2_deploy.py                     # One-click EC2 deployment
  llm_factory.py                    # Per-agent LLM construction
  script_postprocessor.py           # Post-processing pipeline
  checkpoint.py                     # Checkpoint save/load/resume
  topology_utils.py                 # Multi-box topology helpers
  config/
    agents.yaml                     # Agent role/goal/backstory definitions (11 agents)
    tasks.yaml                      # Task configs with expected outputs (11 tasks)
    models.yaml                     # Per-agent model overrides
  steps/                            # One file per flow step
    synthesize_scenario.py          # Step 0: user request → SynthesizedScenario + NetworkTopology
    synthesize_topology.py          # Thin wrapper delegating to synthesize_scenario
    run_box_pipelines.py            # Step 1: run all box pipelines (parallel for multi-box)
    resolve_custom_apps.py          # Runs CustomAppFlow for each CustomVector
    resolve_preset_apps.py          # Runs PresetAppFlow for each PresetVector
    engineer_requirements.py        # 5 sub-agents: parse → map → validate → deps → sequence
    generate_implementation.py      # Generate code/testing/attack snippets per atom
    test_snippets.py                # L1/L2 Docker testing + diagnostic retry loop
    finalize_script.py              # Concatenate + post-process → deploy.sh
    finalize_topology.py            # Multi-box: docker-compose + README + playbook
    test_chain.py                   # Layer 3 multi-box attack chain validation
    deploy.py                       # Optional EC2 deployment
    custom_app_flow.py              # CustomAppFlow + Attack Agent retry loop
    preset_app_flow.py              # PresetAppFlow (WordPress, phpBB)
  custom_apps/
    attack_goals/                   # YAML attack goal definitions (8 goals)
      auth_bypass.yaml
      credential_theft.yaml
      lfi_to_rce.yaml
      rce_via_cmd_injection.yaml
      rce_via_sqli.yaml
      rce_via_webshell.yaml
      session_theft_via_xss.yaml
      upload_lfi_rce.yaml
    web_runtimes/                   # YAML runtime definitions
      apache_php.yaml
      express.yaml                  # Node.js 20 via NodeSource
      flask.yaml
  preset_apps/
    presets/                        # App definitions (wordpress.yaml, phpbb.yaml)
    stacks/                         # Infrastructure (lamp.yaml)
    vuln_profiles/                  # Vuln configs (wp_default_creds, phpbb_default_creds, etc.)
  tools/
    search_atoms_tool.py            # RAG semantic search (SearchAtomsTool)
    read_atom_tool.py               # Read atom markdown by ID (ReadAtomTool)
    test_environment.py             # Docker lifecycle manager (TestEnvironmentTool)
    exec_in_container_tool.py       # crewAI tool for Diagnostic Agent
    attack_from_container_tool.py   # crewAI tool (reserved)
    bound_exec_tools.py             # BoundExecInAttackerTool, BoundExecInTargetTool (Attack Agent)
    chain_test_environment.py       # Multi-box chain test orchestration
  chroma_db/                        # Persistent ChromaDB collections
output/                             # Generated deployment scripts + logs (timestamped)
```

## Common Patterns

### Adding a New Misconfig Atom
1. Create `atoms/<atom_id>.md` with frontmatter:
   ```yaml
   id: atom_id
   required_vars:
     - var_name: description
   ```
2. Add `Logic Requirements`, `Synthesis Guidance`, `Testing Guidance` sections
3. Run `python scripts/rag_gen.py` to ingest into ChromaDB (`goe_collection`)

### Adding a New Web Vulnerability Atom
1. Create `atoms/web_vulnerabilities/<id>.md`
2. Run `python scripts/rag_gen.py` — ingests into `web_vuln_atoms` collection

### Adding a New Attack Goal
1. Create `src/game_of_everything/custom_apps/attack_goals/<goal_id>.yaml`
2. Define: `goal`, `description`, `output_pattern` (regex for L2 success), `test_template`
3. Reference in `CustomVector.attack_chain_goals`

### Adding a New Web Runtime
1. Create `src/game_of_everything/custom_apps/web_runtimes/<runtime_id>.yaml`
2. Create `docker/target_<runtime_id>/Dockerfile` with pre-installed runtime
3. Add entry to `RUNTIME_TARGET_IMAGES` in `tools/test_environment.py`

### Modifying Agent Behavior
- **Role/goal/backstory**: Edit `src/game_of_everything/config/agents.yaml`
- **Task instructions/expected output**: Edit `src/game_of_everything/config/tasks.yaml`
- **Context dependencies**: Modify `context=[...]` in step files

### Debugging Agent Output
Agent verbose output is off by default. To see full agent reasoning for a run, check `output/<timestamp>.log`. To re-enable verbose output for debugging, set `verbose=True` on the relevant Agent/Crew in the step file.

### Testing Changes End-to-End
Run `crewai run` and provide a request that exercises the modified component. Check:
- `output/<timestamp>_deploy.sh` (single-box) or `output/<timestamp>_<scenario>/` (multi-box) for the final script
- `output/<timestamp>.log` for full agent output and test verdicts
- Terminal output for structured pass/fail results

For custom app changes, use the test harness with `--generate-only` + `--from-file` to iterate quickly.

### Applying the crewAI Tool-Call Argument Bug Fix
After any `crewai` package upgrade, verify that `crew_agent_executor.py:722` still has the correct fix:
```python
# Correct (after fix):
func_args = func_info.get("arguments") or tool_call.get("input", {})

# Broken (original):
func_args = func_info.get("arguments", "{}") or tool_call.get("input", {})
```
The `"{}"`  default causes a non-None truthy-empty-string that prevents fallback to `tool_call.get("input", {})`, dropping Bedrock tool arguments.
