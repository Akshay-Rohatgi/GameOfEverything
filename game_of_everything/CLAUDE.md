# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
