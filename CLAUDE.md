# CLAUDE.md

Guidance for Claude Code when working with this repository.

> **Working directory**: all commands below are relative to `game_of_everything/` unless noted otherwise.

---

## GoE v2 (`goe/`) — Active Development Branch: `goe-rewrite`

Complete rewrite independent of v1 — no crewAI or LiteLLM. Direct boto3 Bedrock calls.

**Status**: Phases 0–4 complete. **Phase 5 (Polish and Parity) next** — atom integration into construction crew, EC2 deploy, cost optimization, preset apps.

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

**Phase 1 — Planning** (`goe/planner/`): NL → validated `EntityGraph`. Five LLM steps: design_systems → plan_entities (runtime + atoms, grounded in catalog) → grade_stubs (validates compatibility) → specify_entities (adds edges) → connect_edges. Deterministic resolve + validate. Catalog (`_atom_catalog.py`) prevents wrong runtimes and hallucinated atoms.

**Phase 2 — Building** (`goe/construction_crew/`): Per-entity, 3 agents (all with self-review):
- **Engineer** (Opus): spec → `EngineerPlan`
- **Developer** (Sonnet): plan → `BuildArtifact` (source, DB, edge values)
- **Attacker** (Sonnet): artifact → `Procedure` (YAML steps). Has `fix_procedure()` for targeted retries.

**Phase 3 — Orchestration** (`goe/flow/`): `orchestrator.py:run()` calls plan(), drives `BuildScheduler` (topo order + edge propagation), builds entities, packages results. `packager.py:package()` → `deploy.sh`, `playbook.yaml`, `README.md`. Checkpoint/resume in `checkpoint.py`.

**Phase 4 — Multi-entity chain test + multi-system packaging**: L3 chain test gates overall success. `TopologyEnvironment` deploys systems in Docker, `chain_attacker` synthesizes end-to-end `Procedure` with `${system.<id>.host/port}`. Multi-system output: per-system deploy scripts + `docker-compose.yml` + `chain_playbook.yaml`.

**Testing**: `registry.py` generates bash from `BuildArtifact`. `environment.py` spins up Docker (target + attacker). On L2 failure: `diagnostician.py` categorizes, `router.py` dispatches to agents.

### LLM Calls — All Routes Through `goe/bedrock.py`

Every LLM call is a direct `boto3.client("bedrock-runtime").converse()` call. No crewAI, no LiteLLM.

| Location | Role | Model config key | Calls per run |
|---|---|---|---|
| `planner/design_systems.py` | design systems | `planner` | 1 |
| `planner/plan_entities.py` | plan entities | `planner` | 1 |
| `planner/grade_stubs.py` | grade stubs | `planner` | 1 |
| `planner/specify_entities.py` | specify entities | `planner` | 1 (all entities in one call) |
| `planner/connect_edges.py` | connect edges | `planner` | 1 |
| `construction_crew/engineer.py` | engineer | `engineer` | 1 per entity (+ 1 on parse fail) |
| `construction_crew/developer.py` | developer | `developer` | 2 per entity (generate + self-review in same conversation) |
| `construction_crew/attacker.py` | attacker | `attacker` | 2 per entity (generate + self-review); `fix_procedure()` is 1 extra |
| `construction_crew/chain_attacker.py` | chain attacker | `chain_attacker` | 1 when len(built) > 1 (+ up to 2 retries) |
| `retry/diagnostician.py` | diagnostician | `diagnostician` | 1 (fires only on L2 failure) |

Model per role is configured in `goe.toml` under `[models.v2_overrides]`, overridable per-role via `GOE_MODEL_<ROLE>` env vars.

### Key Contracts

**`BuildArtifact`**: dict `source_files`, `primary_source` entry point. Ubuntu: bash script, `port=None`. Web: `app_dir=/opt/webapp`, port required.

**`Procedure`**: YAML steps. Interpolation: `${target_host}`, `${attacker_host}`, `${target_port}`, `${edge.<id>.<param>}`, `${steps.<step_id>.<output>}`.

**`EntityGraph`**: edges have `structural` (plan-time) and `concrete` (build-time) params.

**`BuildScheduler`**: topo state machine. `next_buildable()` → `(entity, edges)`. `report_complete()` propagates values. `report_failed()` returns skipped IDs.

### Runtime Templates

`goe/runtimes/templates/*.yaml`: install, start, healthcheck per runtime. `developer_rules`/`attacker_rules` injected to agent prompts. Use `mariadb-server` (not `mysql-server`) in Docker. Files written via `base64 -d`.

### Test Markers

`docker` (slow, ~1-3 min), `llm` (AWS creds required), `eval` (both). Run `pytest -m "not docker and not llm"` for fast unit tests.

### Entity Fixtures

`tests/fixtures/entities/*.yaml`: minimal `Entity` YAML inputs. Passing: `sqli_express`, `cmdi_flask`, `sqli_php`, `xss_stored_php`, `xss_admin_bot_express`.

### Adding a New Web Runtime

1. Add `goe/runtimes/templates/<id>.yaml`: `id`, `port`, `start_cmd`, `healthcheck`, `target_image`, `install_runtime`, optional `deps_install_template` (with `{extra}` placeholder), optional `pre_start`, `developer_rules`, `attacker_rules`.
2. Add `docker/target_<id>/Dockerfile` (name must match `target_image`).

Non-web base images (`ubuntu`, `preset`) live in `_BASE_IMAGES` in `goe/container/environment.py`.

### Evaluation & Metrics

**Location**: `goe/eval/`, `goe/metrics/`

Opt-in instrumentation via `MetricsSession`. Captures: tokens, latency, caller identity; planning adherence; build pass rates; retry counts.

**CLI**: `.venv/bin/python -m goe.eval --suite [build|planning|full]`

**Output**: `eval_results/<timestamp>/` with summary.json, llm_calls.jsonl, etc. See `goe/eval/README.md`.

### Workflow Artifacts

**Location**: `goe/artifacts/`

Opt-in persistence of LLM conversations and generated files. Enable via `[artifacts]` in `goe.toml`, `GOE_SAVE_ARTIFACTS=1`, or `--artifacts` CLI flag.

**Layout**: `artifacts/<timestamp>/` with manifest.json, conversations/ (jsonl + markdown), entities/<id>/ (source, DB, procedures), attempts/ (retry diffs).

See `goe/artifacts/README.md`.

### Phase 5: Next Steps

**Planned**: Atom integration into construction crew, EC2 deploy, cost optimization, observability, preset apps.

**Gaps vs v1**: No atom markdown in developer/attacker (only planner grounded), no EC2 deploy, no preset apps.

**Working**: Planner graphs, multi-entity/system builds, chain tests, eval/metrics.

---

## v1 System (`src/game_of_everything/`)

crewAI Flow-based multi-agent system for building vulnerable cybersecurity challenges. NL → validated Ubuntu 22.04 deployment scripts. Supports single/multi-box, custom web apps, preset apps (WordPress, phpBB).

### Commands

```bash
crewai run                                     # Main entry point
crewai run --resume output/.checkpoints/<id>/  # Resume
python scripts/rag_gen.py                      # Ingest atoms to ChromaDB
build_attacker                                 # Pre-build attacker image (7d cache)
python scripts/test_custom_app.py --from-file /tmp/app.json --no-rebuild  # Fast iteration
cp goe.toml.example goe.toml                   # Configure AWS, models, EC2
crewai install                                 # Install deps
```

### Architecture

**Config**: `config.py` singleton from `goe.toml`. Resolution: env var → toml override → `config/models.yaml` → defaults.

**CLI**: `GoEConsole` (`ui.py`) redirects verbose output to `output/<timestamp>.log`, shows structured progress.

**Flow** (`main.py`): synthesize_scenario → box_pipelines (parallel for multi-box) → chain_test (multi-box) → finalize_topology → deploy (EC2).

Per-box: resolve_custom_apps → resolve_preset_apps → engineer_requirements → generate_implementation → test_snippets → finalize_script.

Multi-box uses `ThreadPoolExecutor` + `PipelineRenderer` + `BoxEventEmitter` for serialized output.

### Atoms

Markdown in `atoms/` with frontmatter: `id`, `required_vars`, Logic/Synthesis/Testing sections. ChromaDB: `goe_collection` (misconfig/privesc), `web_vuln_atoms` (web vulns). RAG via `SearchAtomsTool`.

### Custom Apps

`CustomAppFlow`: `CustomVector` (vuln_atom_ids, attack_chain_goals, runtime_id) → Opus 4.6 → `GeneratedApp` (source, DB, deploy/test/attack snippets). Retry: up to 2 full regen cycles, Attack Agent gets 2 fix attempts per cycle. Heredocs: `GOE_<FILENAME>_EOF`. Runtimes in `custom_apps/web_runtimes/*.yaml`: systemd+nohup pattern. Use NodeSource for Node 20, `mariadb-server` (not `mysql-server`) in Docker.

### Preset Apps

`PresetAppFlow`: `PresetVector` (preset_id, vuln_profile_ids). Definitions in `preset_apps/`: presets/, stacks/, vuln_profiles/.

### Attack Agent

On L2 fail: probes containers with `BoundExecInAttackerTool`/`BoundExecInTargetTool`, fixes exploit before full regen. Saves ~$0.15 and ~50s.

### Testing

**Docker**: goe_target (runtime images: express/flask/php/preset, or ubuntu:22.04 base) + goe_attacker (Kali).

**L1** (internal): `testing_snippet` in target, LLM judges setup. Diagnostic Agent (2 fix attempts).
**L2** (external): `attack_snippet` from attacker, LLM judges exploit. Attack Agent (2 fix attempts for custom apps). Misconfig atoms: incremental cumulative (re-run all 0..N).
**L3** (chain test, multi-box): `ChainProbe` steps, first failure cascades.

**Sanitization**: `_si()` escapes `{{`/`}}` to prevent SSTI in crewAI Jinja2 templates. Tasks expect strict JSON output.

### Multi-Box

`NetworkTopology`: `BoxDefinition`, `PivotLink`, `SharedSecret`, `ChainProbe`. Output: `docker-compose.yml`, per-box scripts, `playbook.json`, `README.md`.

### EC2 Deploy

`ec2_deploy.py`: Latest Ubuntu 22.04 AMI, auto-creates security group, user_data script (gzip if >16KB). Configured in `[deploy]` section. Multi-box skips EC2.

### Post-Processing & Checkpointing

`script_postprocessor.py`: inject shebang, `set -e`, normalize blank lines. Checkpoint in `output/.checkpoints/<run_id>/`, resume with `crewai run --resume`.

### Key Details

**CrewAI Bedrock Bug**: Patch `.venv/.../crew_agent_executor.py:722`: `func_args = func_info.get("arguments") or tool_call.get("input", {})` (remove `"{}"` default). Check PR #4518 on upgrades.

**patches.py**: Auto-applied at import. (1) JSON repair via `json-repair`. (2) Bedrock assistant prefill rejection workaround.

**LLMs**: Bedrock Sonnet 4.6 (most agents), Opus 4.6 (app_generation_agent), `us.` inference profile prefix. Config in `goe.toml` + `config/models.yaml` via `llm_factory.py`.

**Data Flow**: crewAI `context=[...]` / `inputs={}`, Pydantic-structured LLM outputs → `GoEState`.

**Attacker Image**: Kali + common tools. `ensure_attacker_tools()` installs missing at runtime (graceful degradation).

**Dependency Enumeration**: Automatic OS package inference (samba, openssh-server, SUID binaries).

**misconfig_scope**: Must be attacker-facing (e.g., "SSH admin:admin123"), not imperative ("Deploy user...").

### File Structure

```
goe.toml.example
atoms/                           # 22 misconfig/privesc atoms
  web_vulnerabilities/           # 10 web vuln atoms
docker/                          # Dockerfiles: attacker, preset_target, target_{express,flask,php}
scripts/                         # rag_gen.py, query.py, test_custom_app.py, bedrock_access.py
src/game_of_everything/
  main.py, models.py, state.py, config.py, patches.py, ui.py, ui_events.py
  ec2_deploy.py, llm_factory.py, script_postprocessor.py, checkpoint.py, topology_utils.py
  config/                        # agents.yaml (11), tasks.yaml (11), models.yaml
  steps/                         # synthesize_scenario, box_pipelines, resolve_*, engineer_requirements,
                                 # generate_implementation, test_snippets, finalize_*, chain_test, deploy
  custom_apps/                   # attack_goals/ (8), web_runtimes/ (3)
  preset_apps/                   # presets/, stacks/, vuln_profiles/
  tools/                         # search_atoms, read_atom, test_environment, exec_in_container,
                                 # bound_exec_tools, chain_test_environment
  chroma_db/                     # Persistent collections
output/                          # Generated scripts + logs
```

### Common Patterns

**Add misconfig atom**: Create `atoms/<id>.md` (frontmatter + sections), run `python scripts/rag_gen.py` → `goe_collection`.

**Add web vuln atom**: Create `atoms/web_vulnerabilities/<id>.md`, run `rag_gen.py` → `web_vuln_atoms`.

**Add attack goal**: Create `custom_apps/attack_goals/<id>.yaml` (goal, description, output_pattern, test_template).

**Add web runtime**: Create `custom_apps/web_runtimes/<id>.yaml` + `docker/target_<id>/Dockerfile`, add to `RUNTIME_TARGET_IMAGES`.

**Modify agents**: Edit `config/agents.yaml` (role/goal/backstory) or `config/tasks.yaml` (instructions/expected output) or `context=[...]` in steps.

**Debug**: Check `output/<timestamp>.log` or set `verbose=True` on Agent/Crew.

**Test**: `crewai run` → check `output/` dir. Custom apps: `--generate-only` + `--from-file` for fast iteration.

**crewAI upgrade**: Verify `crew_agent_executor.py:722` has `func_args = func_info.get("arguments") or tool_call.get("input", {})` (no `"{}"` default).


### Killchain Planning + Fan-out Support (Phase 4.5 — COMPLETED)

**Problem:** The planner outputted unordered entity sets. For "Ubuntu with SSH, sudo, and SUID", all entities independently needed SSH shell access → fan-out validation error.

**Solution (implemented):**

1. **Killchain step** (`goe/planner/plan_killchain.py`): New Step 0.5 between `design_systems` and `plan_entities`. LLM produces an ordered attack sequence (e.g., "1. SSH → shell, 2. sudo → root, 3. SUID → alt privesc"). This is passed as context to `plan_entities` to guide linear chaining. (LLM guidance only — not mechanically enforced.)

2. **Fan-out flag** on `Edge` model (`fan_out: bool = False`): When `True`, multiple entities can require the same edge (for legitimate parallel attack paths or red herrings). Validator respects the flag:
   - `_check_fan_out`: allows multiple consumers when `fan_out=True`
   - `_check_edge_coverage`: skips target check for edges with `to_entity=None` (fan-out edges)
   - `reachable_from_operator`: handles fan-out edges by checking all entities that require them

**Usage:** Set `"fan_out": true` in `connect_edges.md` prompt when multiple entities need the same upstream capability simultaneously.

**Files changed:**
- `goe/models/edge.py`: added `fan_out` field
- `goe/graph/validator.py`: updated `_check_fan_out` and `_check_edge_coverage`
- `goe/graph/topology.py`: updated `reachable_from_operator` for fan-out edges
- `goe/planner/plan_killchain.py`: new killchain planning step
- `goe/planner/prompts/plan_killchain.md`: new prompt
- `goe/planner/pipeline.py`: integrated killchain step
- `goe/planner/plan_entities.py`: accepts killchain context
- `goe/planner/prompts/connect_edges.md`: documented fan_out usage
- `tests/test_validator.py`: added `test_fan_out_with_flag_passes`
