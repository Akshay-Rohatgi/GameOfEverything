# GEMINI.md - Game of Everything (GoE)

## Project Overview
**Game of Everything (GoE)** is an agentic framework for autonomously building vulnerable cybersecurity environments. It transforms high-level user requests into functional deployment scripts (bash, PowerShell) using a modular "Atoms" architecture and a crewAI-based multi-agent flow.

## Core Architecture
1. **Atoms**: Markdown files in `game_of_everything/atoms/` defining specific vulnerabilities or configurations (YAML frontmatter with `id`, `description`, `required_vars` + technical guidance sections).
2. **RAG Pipeline**:
   - **Database**: ChromaDB (`src/game_of_everything/chroma_db/`, collection `goe_collection`).
   - **Embeddings**: Amazon Bedrock (`amazon.titan-embed-text-v2:0`).
   - **Tool**: `SearchAtomsTool` (`search_vulnerability_atoms`) in `tools/search_atoms_tool.py` — semantic search with configurable `n_results` (default 3, supports 1 for best-match-only).
   - **Sync**: `scripts/rag_gen.py` handles ingestion with modification-time tracking; `scripts/query.py` for manual testing.
3. **Agentic Flow (`GoEFlow`)** — four active flow steps:
   - `engineer_requirements`: sequential crewAI crew running **Request Parser** → **Mapping Agent** → **Mapping Validator** → **Dependency Enumerator** → **Sequencing Agent**.
   - `generate_implementation`: separate crew running **Snippet Generation Agent** (with `ReadAtomTool` + `SearchAtomsTool`); outputs `GeneratedSnippets`.
   - `test_snippets`: hybrid Python loop + LLM verdict two-layer testing with Diagnostic Agent retry/log (see Testing Architecture below).
   - `finalize_script`: concatenates validated snippets, runs post-processor pipeline, writes `output/<timestamp>_deploy.sh`. Displays diagnostic history for skipped snippets.

### 4. Snippet Generation Agent
Receives `sequenced_atoms_json` (serialized `SequencedRequest`) via `inputs`. For each atom:
1. Calls `read_atom` to fetch full atom markdown (Logic Requirements, Synthesis Guidance, Testing Guidance).
2. Reads the atom's `context` field as the security intent brief — resolves ambiguities the template leaves open (password strength, binary choice, file content, share name, etc.). Context wins over template defaults; explicit `parameters` win over context.
3. Substitutes `parameters` into commands.
4. Emits `code_snippet` (no shebang — injected by post-processor) and `testing_snippet` per atom.
Outputs `GeneratedSnippets`.

**`ReadAtomTool`** (`read_atom`): reads `atoms/<atom_name>.md` by id. Returns full markdown or a clear error listing available atoms.

**`script_postprocessor.py`**: `SCRIPT_POST_PROCESSORS: List[Callable[[str], str]]` pipeline applied at finalization:
1. `inject_shebang` — strips stray shebangs, prepends exactly one `#!/bin/bash`
2. `ensure_set_e` — inserts `set -e` after the shebang
3. `normalize_blank_lines` — collapses 3+ blank lines to 2

Output written to `output/<timestamp>_deploy.sh` (mode 0755).
Extracts `context`, `initial_access_vectors: List[str]`, and `post_exploitation_goals: List[str]` from free-form user input. Outputs `ParsedRequest`. Never invents requirements not stated by the user.

### 2. Mapping Agent
For each item in `initial_access_vectors` and `post_exploitation_goals`, calls `search_vulnerability_atoms` (n_results=3) and maps results to `MappedAtom` objects with `name`, `context`, and `parameters` (from atom `required_vars`). Outputs `MappedRequest`.

### 2b. Mapping Validation Agent
Independent second-opinion mapper. Re-derives expected atoms from scratch by decomposing each raw requirement string into discrete atomized descriptions, calling `search_vulnerability_atoms` with `n_results=1` per description, and diffing against the existing `MappedRequest`. Adds any missing `MappedAtom` entries. Outputs updated `MappedRequest`.

### 2c. Dependency Enumeration Agent
Identifies implicit OS-level package requirements from the validated mapping. Applies known rules:
- `samba_insecure_share` → `samba`
- `sensitive_file` with `.zip` path → `zip`
- SSH access context or `create_user` with SSH implied → `openssh-server`
- Non-base SUID binary (e.g. `vim`) → its apt package name

Diffs against existing `install_package` atoms, confirms each via a `n_results=1` RAG call, and appends new `install_package` MappedAtoms to `mapped_initial_access_atoms`. Outputs updated `MappedRequest`.

### 3. Sequencing Agent (no tools)
Receives the dependency-enriched `MappedRequest` and produces a single flat ordered `SequencedRequest` (`atoms: List[MappedAtom]`). Ordering rules:
1. `install_package` atoms first
2. `create_user` before atoms whose parameters reference that username
3. `samba_insecure_share` before `sensitive_file` if file_path is inside share path
4. All initial-access atoms before post-exploitation atoms
5. Preserve original order within groups otherwise

## Data Models (`models.py`)
```python
class ParsedRequest(BaseModel):
    initial_prompt: str
    context: str
    initial_access_vectors: List[str]
    post_exploitation_goals: List[str]

class MappedAtom(BaseModel):
    name: str           # exact Atom id (e.g. "samba_insecure_share")
    context: str        # why this Atom was chosen
    parameters: Optional[dict] = None  # required_vars values

class MappedRequest(BaseModel):
    section: str
    mapped_initial_access_atoms: List[MappedAtom]
    mapped_post_exploitation_goal_atoms: List[MappedAtom]

class GeneratedSnippet(BaseModel):
    atom_name: str
    code_snippet: str
    testing_snippet: str
    attack_snippet: Optional[str] = None  # Layer 2 adversarial probe (see Testing Architecture)
    mapped_atom: MappedAtom
    validated: bool = False

class GeneratedSnippets(BaseModel):
    snippets: List[GeneratedSnippet]

class SequencedRequest(BaseModel):
    atoms: List[MappedAtom]  # dependency-resolved execution order

class TestVerdict(BaseModel):
    passed: bool
    reasoning: str  # 1-3 sentences citing specific evidence from stdout/stderr

class DiagnosticResult(BaseModel):
    fixed_code_snippet: str       # corrected snippet (may be unchanged)
    fixed_testing_snippet: str    # corrected testing snippet
    diagnosis: str                # root cause + what was changed
    confidence: str               # "high" | "medium" | "low"

class TestResult(BaseModel):
    atom_name: str
    layer1_verdict: TestVerdict
    layer2_verdicts: Optional[List[TestVerdict]] = None  # one per cumulative probe run
    diagnostic_results: Optional[List[DiagnosticResult]] = None  # L1 retries + L2 logs
    error: Optional[str] = None
```

## Flow State (`GoEState`)
| Field | Populated from |
|---|---|
| `raw_request` | user `input()` |
| `parsed_request` | `parse_task.output.pydantic` |
| `mapped_request` | `dep_task.output.pydantic` (post dep-enumeration) |
| `sequenced_request` | `sequence_task.output.pydantic.atoms` |
| `generated_snippets` | `generate_task.output.pydantic.snippets` |
| `test_results` | populated by `test_snippets` flow step |
| `final_script` | post-processor output, written to `output/<timestamp>_deploy.sh` |

## Configuration Files
- `config/agents.yaml`: Defines `request_parser_agent`, `mapping_agent`, `mapping_validator_agent`, `dependency_enumeration_agent`, `sequencing_agent`, `snippet_generation_agent`, `testing_agent`, `diagnostic_agent`.
- `config/tasks.yaml`: Defines `parse_request_task`, `map_atoms_task`, `validate_mapping_task`, `enumerate_dependencies_task`, `sequence_atoms_task`, `generate_snippets_task`, `validate_snippets_task`, `diagnose_snippet_task`.
- Task context chain: `parse → map → validate → dep_enumerate → sequence`

## Known Bugs & Workarounds

### CrewAI 1.9.3 — Bedrock Tool Calling Args Lost (patched in venv)
In `crew_agent_executor.py` L722, `func_args = func_info.get("arguments", "{}") or tool_call.get("input", {})` — the truthy default `"{}"` prevents Bedrock's `input` dict from ever being read, so tools receive `{}`.

**Fix** (applied to venv, pending [PR #4518](https://github.com/crewAIInc/crewAI/pull/4518)):
```python
func_args = func_info.get("arguments") or tool_call.get("input", {})
```
Reapply to `.venv/lib/python3.12/site-packages/crewai/agents/crew_agent_executor.py` after any `crewai` upgrade.

### CrewAI 1.9.3 — Event Bus Mismatch Warnings (suppressed)
`ToolUsageFinished` emitted without matching `ToolUsageStarted`. Suppressed in `main.py`:
```python
_event_context_config.set(EventContextConfig(
    mismatch_behavior=MismatchBehavior.SILENT,
    empty_pop_behavior=MismatchBehavior.SILENT,
))
```

### Bedrock Model ID
Must use inference profile prefix to avoid throughput errors:
```python
model_id = "us.anthropic.claude-sonnet-4-6"  # note us. prefix
llm = LLM(model=f"bedrock/{model_id}", ...)
```

## Execution Commands
- `uv run kickoff`: Runs the interactive flow (prompts for user request via `input()`).
- `python scripts/rag_gen.py`: Syncs Atoms to ChromaDB.
- `python scripts/query.py`: Manual RAG retrieval testing.

## Atoms Library
| Atom ID | Description | required_vars |
|---|---|---|
| `create_user` | Creates system user with shell and home dir | `username`, `password` (optional) |
| `install_package` | Installs apt packages non-interactively | `package_name` |
| `samba_insecure_share` | World-readable/writable anonymous Samba share | `share_name`, `path` |
| `sensitive_file` | Creates file with weak permissions and sensitive content | `file_path`, `file_content` |
| `set_suid` | Sets SUID bit on a binary for privilege escalation | `binary_path` |

## Next Steps
1. **Non-interactive trigger**: Implement `run_with_trigger` in `main.py` to accept requests via JSON payload.
2. **Reduce RAG token cost**: Return condensed atom representations (frontmatter + headers only) instead of full markdown.
3. **End-to-end integration test**: Run the full GoE flow against a real user request and validate all layers and the Diagnostic Agent in combination.

---

## Testing Architecture

Snippet validation uses two distinct layers with an LLM verdict and a Diagnostic Agent for automated repair, orchestrated by a **hybrid Python loop** (not a single agent invocation). All components are implemented.

### Design Principles
1. **LLM judges all command output.** Command output is messy and context-dependent. Rather than hardcoded patterns, the Testing Agent receives atom security intent + raw stdout/stderr/exit code and reasons about success.
2. **Incremental cumulative testing.** After snippet N passes Layer 1, Layer 2 probes run for **all snippets 0..N**. This catches sequencing bugs (e.g. SSH probe fails because user wasn't created yet) and regressions (snippet N breaks M's previously passing probe).
3. **Python loop controls progression; LLM only judges.** The structured incremental protocol is too precise for a single agent. Python decides what to run and when to stop; the LLM interprets output.
4. **Kali attacker container.** `kalilinux/kali-rolling` ships pre-packaged offensive tools (metasploit, hydra, nmap, smbclient, sshpass). No custom tool builds needed — `apt install` covers new attack vectors.

### Layer 1 — Internal (state verification)
Run `testing_snippet` in the target container via `docker exec`. Answers: *"Was the configuration applied correctly?"*
- Tests filesystem state, service status, user existence, permissions, SUID bits — from root's perspective.
- **Execution**: `TestEnvironmentTool.exec_in_target(snippet)` → returns `(exit_code, stdout, stderr)`.
- **Judgment**: `_run_verdict_crew()` sends atom context + raw output to a one-task Testing Agent crew; returns `TestVerdict`.
- **Failure with retry**: On failure, `_run_diagnostic_crew()` fires the Diagnostic Agent (tools: `ReadAtomTool` + `ExecInContainerTool`), which returns a `DiagnosticResult` with patched snippets. Re-apply and re-test. Up to **2 retries**. Chain stops if all retries exhausted.

### Layer 2 — External (attack simulation)
Run `attack_snippet` from the Kali attacker container against the target on the same Docker bridge. Answers: *"Is the access vector actually exploitable?"*
- Tests reachability and exploit success from an unauthenticated adversary's perspective.
- **Execution**: `TestEnvironmentTool.exec_in_attacker(attack_snippet)` → returns `(exit_code, stdout, stderr)`.
- **Judgment**: same one-task Testing Agent crew; returns `TestVerdict`.
- **Incremental re-run**: after snippet N, Layer 2 runs for all 0..N that have `attack_snippet`. Regressions flagged with a warning.
- **Failure — diagnose-and-log only**: Diagnostic Agent runs for L2 failures but **no retry** — results stored in `TestResult.diagnostic_results`. Chain continues.
- Atoms with no external attack surface (`install_package`, `create_user`) leave `attack_snippet = None` and skip Layer 2.

### Docker Network Topology
```
docker network create goe_test_net
docker run -d --name goe_target --hostname target --network goe_test_net ubuntu:22.04 sleep infinity
docker run -d --name goe_attacker --hostname attacker --network goe_test_net goe-attacker:latest sleep infinity
```
- Target: `ubuntu:22.04` — snippets applied here via `docker exec`.
- Attacker: built from `docker/attacker/Dockerfile` (`kalilinux/kali-rolling` + smbclient, sshpass, nmap, hydra, metasploit-framework, etc.).
- Both run for the entire test loop, torn down in a `finally` block.

### Diagnostic Agent
`diagnostic_agent` + `diagnose_snippet_task` in config files; `_run_diagnostic_crew()` in `main.py`.

Receives: atom name + markdown (via `ReadAtomTool`), original snippets, apply stderr, L1/L2 exit code + stdout/stderr, verdict reasoning, attempt number. Can run diagnostic commands in the target container via `ExecInContainerTool`. Returns `DiagnosticResult`:
```python
class DiagnosticResult(BaseModel):
    fixed_code_snippet: str    # corrected snippet (may be unchanged)
    fixed_testing_snippet: str
    diagnosis: str             # root cause + what was changed
    confidence: str            # "high" | "medium" | "low"
```
Fallback on parse failure: returns original snippets unchanged so the retry proceeds without crashing.

### Components
| Component | Type | Location |
|---|---|---|
| `attack_snippet` field | `GeneratedSnippet` model | `models.py` |
| `TestVerdict`, `TestResult`, `DiagnosticResult` | Pydantic models | `models.py` |
| `TestEnvironmentTool` | Python helper (Docker lifecycle) | `tools/test_environment.py` |
| `ExecInContainerTool` | crewAI `BaseTool` | `tools/exec_in_container_tool.py` |
| `AttackFromContainerTool` | crewAI `BaseTool` | `tools/attack_from_container_tool.py` |
| Kali attacker image | Dockerfile | `docker/attacker/Dockerfile` |
| `testing_agent` | Agent config | `config/agents.yaml` |
| `diagnostic_agent` | Agent config | `config/agents.yaml` |
| `validate_snippets_task` | Task config | `config/tasks.yaml` |
| `diagnose_snippet_task` | Task config | `config/tasks.yaml` |
| `test_snippets` | Flow method | `main.py` |
| `_run_verdict_crew()` | Helper method | `main.py` |
| `_run_diagnostic_crew()` | Helper method | `main.py` |
