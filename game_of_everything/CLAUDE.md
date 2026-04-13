# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Game of Everything (GoE)** is a framework for agentically building vulnerable cybersecurity challenges. It takes natural language requests for vulnerable environments (e.g., "I want a vulnerable server with weak SSH credentials and a SUID privilege escalation path") and generates deployment scripts that set up those environments.

The system is built on **crewAI Flow** with a multi-agent architecture that breaks down requests, maps them to vulnerability "atoms," sequences dependencies, generates bash scripts, and validates them in Docker containers.

## Commands

### Running the Flow
```bash
crewai run
```
Main entry point. Prompts for a vulnerable environment request and runs the full pipeline. Console output is minimal — full agent logs are written to `output/<timestamp>.log`.

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
Pre-build the attacker image to separate build failures from test failures. The image is automatically cached for 7 days—rebuilds only occur if the image is missing or older than the threshold.

### Custom App Test Harness
```bash
# Generate app only (no Docker), save result to file
python scripts/test_custom_app.py --generate-only --save /tmp/app.json

# Run Docker L1+L2 from a previously saved generation (fast iteration)
python scripts/test_custom_app.py --from-file /tmp/app.json --no-rebuild

# Full end-to-end (generate + Docker)
python scripts/test_custom_app.py
```
Use `--from-file` to iterate on Docker/snippet issues without re-running the expensive Opus generation step.

### Configuration
```bash
# Copy the example config and fill in your values
cp goe.toml.example goe.toml
```
All settings (AWS credentials, model selection, EC2 deploy config) live in `goe.toml`. Environment variables override toml values. See `goe.toml.example` for the full schema.

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
- Terminal shows only structured progress (spinners, checkmarks, test results)
- Full agent reasoning is always available in `output/<timestamp>.log`
- All agents use `verbose=False` — no step callbacks
- When `ui=None` (e.g. `test_custom_app.py`), step functions fall back to `print()`

### Flow Pipeline
The main flow (`GoEFlow` in `main.py`) has these steps:
1. **synthesize_scenario**: Elaborate the user request into a fully-specified scenario — resolves all implicit decisions, defines `misconfig_scope`, `custom_app_scope`, and structured `custom_vectors`
2. **resolve_custom_apps**: Run `CustomAppFlow` for each `CustomVector` → `ResolvedCustomApp` with validated, self-contained deploy snippets
3. **engineer_requirements**: Parse `misconfig_scope` → `ParsedRequest` (context, initial access vectors, post-exploitation goals). Internally runs 5 sub-agents: parser → mapper → validator → dep-enumerator → sequencer
4. **generate_implementation**: For each atom, read its markdown, generate `code_snippet`, `testing_snippet`, and `attack_snippet`
5. **test_snippets**: Two-layer incremental testing in Docker (Layer 1: internal state, Layer 2: external attack simulation)
6. **finalize_script**: Custom app sections first, then misconfig snippets; apply post-processors; write to `output/<timestamp>_deploy.sh`
7. **deploy**: Optional one-click EC2 deployment of the validated script

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
- `vuln_atom_id`: Web vulnerability to embed (e.g., `sqli_union`, `ssti_jinja2`, `cmdi_os`)
- `runtime_id`: Web runtime (e.g., `flask`, `express`, `apache_php`)
- `attack_chain_goal`: What the exploit achieves (e.g., `credential_theft`, `rce_via_webshell`)

**CustomAppFlow steps**: `load_context` → `generate_app` → `validate_end_to_end` → `emit_result`

The generation agent uses Claude Opus 4.6 (`bedrock/us.anthropic.claude-opus-4-6`) to produce a `GeneratedApp` with:
- `app_filename`, `app_source`: Single application source file
- `schema_sql`, `seed_sql`, `setup_db_sh`: DB setup files (all `Optional` — omitted for DB-less apps)
- `deploy_snippet`, `testing_snippet`, `attack_snippet`

**Self-contained packaging**: `emit_result` calls `_package_deploy_snippet()` which prepends quoted heredocs for all app files into the `deploy_snippet`. The final script creates `/tmp/goe_app/` from embedded file content — no external staging directory needed. During Docker testing, `_stage_app_files()` stages files via `copy_to_target()` instead, which is redundant but harmless.

**Heredoc delimiter format**: `GOE_<FILENAME_WITHOUT_DOT>_EOF` (e.g., `GOE_APP_PY_EOF`, `GOE_SCHEMA_SQL_EOF`). Quoted to prevent shell expansion of embedded content.

**Web runtimes**: Defined in `src/game_of_everything/custom_apps/web_runtimes/*.yaml`. Flask and Express use a systemd+nohup detection pattern:
```bash
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload && systemctl enable myapp && systemctl start myapp
else
    nohup <start command> &
fi
```
This means: real deployments get a proper systemd service; Docker test containers (no systemd) use nohup.

**Database**: Always use `mariadb-server` (never `mysql-server`) for Docker environments. `mysql-server` fails to configure without systemd.

### RAG-Enhanced Mapping
The Mapping Agent uses `SearchAtomsTool` to query ChromaDB with semantic search. It returns top-3 results by default, but validators use `n_results=1` for best-match-only lookups.

### Two-Layer Testing with Diagnostic Agent
Testing happens in Docker with two containers on a bridge network:
- **goe_target** (Ubuntu 22.04): Where snippets are applied
- **goe_attacker** (Kali Linux): Where attack probes run from

The target container is bootstrapped on startup with `curl wget ca-certificates gnupg lsb-release` since the base `ubuntu:22.04` image lacks these.

**Layer 1 (Internal)**: Run `testing_snippet` inside target container. LLM judges if config was applied correctly.
**Layer 2 (External)**: Run `attack_snippet` from attacker container against target. LLM judges if exploit works.

Layer 2 is **incremental cumulative**: After applying snippet N, re-run attack probes for all snippets 0..N. This catches dependency misordering and regressions.

On Layer 1 failure, the **Diagnostic Agent** (tools: `ReadAtomTool`, `ExecInContainerTool`) attempts up to 2 fixes by analyzing failure context and atom requirements. On Layer 2 failure, it logs diagnosis but doesn't retry.

**Template syntax sanitization**: All string inputs passed to crewAI crew `kickoff()` are sanitized with `_si()` which replaces `{{` → `{ {` and `}}` → `} }`. This prevents SSTI payloads in generated content (e.g., `{{7*7}}`) from breaking crewAI's Jinja2-based input templating.

**Expected output format**: Verdict and diagnostic task `expected_output` in `tasks.yaml` must specify strict JSON-only (no markdown, no surrounding text). LLM output is parsed directly as JSON.

### EC2 One-Click Deploy
After validation, the flow optionally deploys the script to a new EC2 instance (`ec2_deploy.py`):
- Looks up the latest Ubuntu 22.04 AMI via `describe_images`
- Auto-creates a security group with common challenge ports (SSH, HTTP, SMB, DB ports, etc.)
- Passes the deploy script as EC2 `user_data` — runs as root on first boot, no SSH/paramiko needed
- Scripts > 16KB are gzip-compressed and wrapped in a self-extracting bootstrap
- Configured via `[deploy]` section in `goe.toml` (instance type, key pair, security group, subnet)

### Script Post-Processing
Generated snippets are concatenated and run through `script_postprocessor.py`:
1. `inject_shebang`: Strip any shebangs, prepend exactly one `#!/bin/bash`
2. `ensure_set_e`: Add `set -e` after shebang (fail-fast behavior)
3. `normalize_blank_lines`: Collapse 3+ consecutive blank lines to 2

## Key Technical Details

### CrewAI 1.9.3 Bedrock Bug (Manual Patch Required)
CrewAI 1.9.3 has a bug in `.venv/lib/python3.12/site-packages/crewai/agents/crew_agent_executor.py:722` that drops Bedrock native tool call arguments.

**Fix**: Change:
```python
func_args = func_info.get("arguments", "{}") or tool_call.get("input", {})
```
to:
```python
func_args = func_info.get("arguments") or tool_call.get("input", {})
```

**When upgrading crewai**, check if [PR #4518](https://github.com/crewAIInc/crewAI/pull/4518) is merged. If not, reapply the patch.

### CrewAI JSON Repair Patch (Runtime Monkey-Patch)
LLMs on Bedrock occasionally produce JSON with unquoted keys (Python dict style `{key: "value"}` instead of `{"key": "value"}`), which crashes crewAI's `convert_to_model` in `converter.py`. The `patches.py` module monkey-patches `convert_to_model` to run `json-repair` on the raw string before parsing. Applied automatically on import in `main.py`. Safe on valid JSON (no-op). The `json-repair` library is already a transitive dependency of crewAI.

### LLM Configuration
The flow uses AWS Bedrock with Claude Sonnet 4.6 for most agents. The custom app generation agent uses Claude Opus 4.6 for higher-quality code generation. Model IDs are prefixed with `us.` inference profile to avoid on-demand throughput errors. LiteLLM routing requires `bedrock/` prefix.

Model configuration lives in `goe.toml` and `config/models.yaml`. The `llm_factory.py` module centralizes LLM construction with per-agent resolution.

### Data Flow is Prompt-Based
All inter-agent communication happens via crewAI `context=[...]` (task chaining) or `inputs={}` (direct injection). There's no programmatic structured handoff. The state object (`GoEState`) is populated by parsing Pydantic-structured LLM outputs.

### Docker Attacker Image
The Kali attacker image (`docker/attacker/Dockerfile`) pre-installs common tools (smbclient, sshpass, nmap, hydra, metasploit, redis-cli, psql, mysql).

**Tool Availability**:
- `ensure_attacker_tools()` scans attack snippets and installs missing tools individually at runtime
- Failed installations log warnings but don't stop execution (graceful degradation)

### Atom Dependency Enumeration is Automatic
Users specify vulnerabilities, not infrastructure. The Dependency Enumeration Agent infers OS packages (samba, openssh-server, zip, apt packages for non-base SUID binaries) and adds `install_package` atoms automatically.

### misconfig_scope Must Be Attacker-Facing
The `misconfig_scope` field in `SynthesizedScenario` is passed to the `engineer_requirements` parser agent. It must describe vulnerabilities from an attacker's perspective (e.g., "SSH service with username `admin` and password `admin123`"), **not** deployment instructions (e.g., "Deploy one OS user..."). The parser only extracts attack vectors — imperative instructions produce no atoms.

## File Structure

```
goe.toml.example                    # Configuration template (copy to goe.toml)
atoms/                              # Misconfig/privesc vulnerability definitions (markdown)
atoms/web_vulnerabilities/          # Web vulnerability atoms (ChromaDB: web_vuln_atoms)
src/game_of_everything/
  main.py                           # GoEFlow definition + orchestration
  models.py                         # Pydantic models for all data structures
  state.py                          # GoEState definition
  config.py                         # GoEConfig singleton (loads goe.toml)
  ui.py                             # GoEConsole — clean CLI output + log capture
  ec2_deploy.py                     # One-click EC2 deployment
  llm_factory.py                    # Per-agent LLM construction
  script_postprocessor.py           # Post-processing pipeline
  config/
    agents.yaml                     # Agent role/goal/backstory definitions
    tasks.yaml                      # Task configs with expected outputs
    models.yaml                     # Per-agent model overrides
  steps/                            # One file per flow step
    synthesize_scenario.py
    resolve_custom_apps.py          # Runs CustomAppFlow for each CustomVector
    engineer_requirements.py        # 5 sub-agents: parse → map → validate → deps → sequence
    generate_implementation.py
    test_snippets.py
    finalize_script.py
    deploy.py                       # Optional EC2 deployment
    custom_app_flow.py              # CustomAppFlow + _package_deploy_snippet()
  custom_apps/
    attack_goals/                   # YAML attack goal definitions
    web_runtimes/                   # YAML runtime definitions (flask.yaml, express.yaml, apache_php.yaml)
  tools/
    search_atoms_tool.py            # RAG semantic search (SearchAtomsTool)
    read_atom_tool.py               # Read atom markdown by ID
    test_environment.py             # Docker lifecycle manager (TestEnvironmentTool)
    exec_in_container_tool.py       # crewAI tool for Diagnostic Agent
    attack_from_container_tool.py   # crewAI tool (reserved, not currently used)
  chroma_db/                        # Persistent ChromaDB collections
scripts/
  rag_gen.py                        # Ingest atoms into ChromaDB
  query.py                          # Test RAG retrieval
  test_custom_app.py                # Standalone custom app test harness
docker/attacker/Dockerfile          # Kali Linux attacker container
output/                             # Generated deployment scripts + logs (timestamped)
```

## Common Patterns

### Adding a New Atom
1. Create markdown file in `atoms/<atom_id>.md` with frontmatter:
   ```yaml
   id: atom_id
   required_vars:
     - var_name: description
   ```
2. Add `Logic Requirements`, `Synthesis Guidance`, `Testing Guidance` sections
3. Run `python scripts/rag_gen.py` to ingest into ChromaDB

### Adding a New Web Vulnerability Atom
1. Create markdown file in `atoms/web_vulnerabilities/<id>.md`
2. Run `python scripts/rag_gen.py` — it ingests both `atoms/` and `web_vulnerabilities/` into their respective collections

### Modifying Agent Behavior
- **Role/goal/backstory**: Edit `src/game_of_everything/config/agents.yaml`
- **Task instructions/expected output**: Edit `src/game_of_everything/config/tasks.yaml`
- **Context dependencies**: Modify `context=[...]` in step files

### Debugging Agent Output
Agent verbose output is off by default. To see full agent reasoning for a run, check `output/<timestamp>.log`. To re-enable verbose output for debugging, set `verbose=True` on the relevant Agent/Crew in the step file.

### Testing Changes End-to-End
Run `crewai run` and provide a request that exercises the modified component. Check:
- `output/<timestamp>_deploy.sh` for the final script
- `output/<timestamp>.log` for full agent output and test verdicts
- Terminal output for structured pass/fail results

For custom app changes, use the test harness with `--generate-only` + `--from-file` to iterate quickly.
