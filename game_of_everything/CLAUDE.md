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
Main entry point. Prompts for a vulnerable environment request and runs the full pipeline.

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

### Development
```bash
# Install dependencies
crewai install

# Set environment variables in .env
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
```

## High-Level Architecture

### Flow Pipeline
The main flow (`GoEFlow` in `main.py`) has these steps:
1. **engineer_requirements**: Parse user request → `ParsedRequest` (context, initial access vectors, post-exploitation goals)
2. **map_requirements**: Map each vector/goal to atoms via RAG → `MappedRequest`
3. **validate_mapping**: Independent second-opinion mapping to catch missed atoms
4. **enumerate_dependencies**: Add `install_package` atoms for implicit OS dependencies (samba → `samba`, zip file → `zip`, etc.)
5. **sequence_atoms**: Order atoms by dependency (packages first → users → shares → files → exploits)
6. **generate_implementation**: For each atom, read its markdown, generate `code_snippet`, `testing_snippet`, and `attack_snippet`
7. **test_snippets**: Two-layer incremental testing in Docker (Layer 1: internal state, Layer 2: external attack simulation)
8. **finalize_script**: Concatenate validated snippets, apply post-processors, write to `output/<timestamp>_deploy.sh`

### Atom System
Atoms are markdown files in `atoms/` with frontmatter defining:
- `id`: Unique identifier (e.g., `samba_insecure_share`)
- `required_vars`: Parameters the snippet generator must fill (e.g., `share_name`, `path`)
- `Logic Requirements`, `Synthesis Guidance`, `Testing Guidance` sections

Atoms are ingested into ChromaDB (`src/game_of_everything/chroma_db/`) using Amazon Bedrock Titan embeddings.

### RAG-Enhanced Mapping
The Mapping Agent uses `SearchAtomsTool` to query ChromaDB with semantic search. It returns top-3 results by default, but validators use `n_results=1` for best-match-only lookups.

### Two-Layer Testing with Diagnostic Agent
Testing happens in Docker with two containers on a bridge network:
- **goe_target** (Ubuntu 22.04): Where snippets are applied
- **goe_attacker** (Kali Linux): Where attack probes run from

**Layer 1 (Internal)**: Run `testing_snippet` inside target container. LLM judges if config was applied correctly.
**Layer 2 (External)**: Run `attack_snippet` from attacker container against target. LLM judges if exploit works.

Layer 2 is **incremental cumulative**: After applying snippet N, re-run attack probes for all snippets 0..N. This catches dependency misordering and regressions.

On Layer 1 failure, the **Diagnostic Agent** (tools: `ReadAtomTool`, `ExecInContainerTool`) attempts up to 2 fixes by analyzing failure context and atom requirements. On Layer 2 failure, it logs diagnosis but doesn't retry.

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

### LLM Configuration
The flow uses AWS Bedrock with Claude Sonnet 4.6. Model IDs are prefixed with `us.` inference profile to avoid on-demand throughput errors. LiteLLM routing requires `bedrock/` prefix.

### Data Flow is Prompt-Based
All inter-agent communication happens via crewAI `context=[...]` (task chaining) or `inputs={}` (direct injection). There's no programmatic structured handoff. The state object (`GoEState`) is populated by parsing Pydantic-structured LLM outputs.

### Docker Attacker Image
The Kali attacker image (`docker/attacker/Dockerfile`) pre-installs common tools (smbclient, sshpass, nmap, hydra, metasploit, redis-cli, psql, mysql).

**MongoDB Shell Installation**: Uses direct tarball download instead of apt repos to avoid Debian version detection issues in Kali rolling.

**Tool Availability**:
- `ensure_attacker_tools()` scans attack snippets and installs missing tools individually at runtime
- Returns `Dict[str, bool]` indicating installation success per package
- Failed installations log warnings but don't stop execution (graceful degradation)
- `validate_attack_prerequisites()` pre-checks tool availability before Layer 2 tests
- Automatic installation retry if tools are missing during pre-check

**Image Caching**: Images are cached for 7 days to avoid rebuilding on every test run. Set threshold via `IMAGE_MAX_AGE_DAYS` in `test_environment.py`.

### Atom Dependency Enumeration is Automatic
Users specify vulnerabilities, not infrastructure. The Dependency Enumeration Agent infers OS packages (samba, openssh-server, zip, apt packages for non-base SUID binaries) and adds `install_package` atoms automatically.

## File Structure

```
atoms/                              # Vulnerability definitions (markdown)
src/game_of_everything/
  main.py                           # GoEFlow definition
  models.py                         # Pydantic models for all data structures
  script_postprocessor.py           # Post-processing pipeline
  config/
    agents.yaml                     # Agent role/goal/backstory definitions
    tasks.yaml                      # Task configs with expected outputs
  tools/
    search_atoms_tool.py            # RAG semantic search (SearchAtomsTool)
    read_atom_tool.py               # Read atom markdown by ID
    test_environment.py             # Docker lifecycle manager (TestEnvironmentTool)
    exec_in_container_tool.py       # crewAI tool for Diagnostic Agent
    attack_from_container_tool.py   # crewAI tool (reserved, not currently used)
  chroma_db/                        # Persistent ChromaDB collection
scripts/
  rag_gen.py                        # Ingest atoms into ChromaDB
  query.py                          # Test RAG retrieval
docker/attacker/Dockerfile          # Kali Linux attacker container
output/                             # Generated deployment scripts (timestamped)
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

### Modifying Agent Behavior
- **Role/goal/backstory**: Edit `src/game_of_everything/config/agents.yaml`
- **Task instructions/expected output**: Edit `src/game_of_everything/config/tasks.yaml`
- **Context dependencies**: Modify `context=[...]` in `main.py` flow step

### Debugging Tool Calls
Set `verbose=True` on agents in `main.py`. For native Bedrock tool calls, remember that `BaseTool.run()` is called directly—there's no Pydantic validation before `_run()` unlike the ReAct path.

### Testing Changes End-to-End
Run `crewai run` and provide a request that exercises the modified component. Check:
- `output/<timestamp>_deploy.sh` for the final script
- Console output for layer 1/layer 2 test verdicts
- Diagnostic results for failed snippets (shown in finalize_script output)
