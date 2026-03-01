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
3. **Agentic Flow (`GoEFlow`)** — two active flow steps:
   - `engineer_requirements`: sequential crewAI crew running **Request Parser** → **Mapping Agent** → **Mapping Validator** → **Dependency Enumerator** → **Sequencing Agent**.
   - `generate_implementation`: separate crew running **Snippet Generation Agent** (with `ReadAtomTool` + `SearchAtomsTool`); outputs `GeneratedSnippets`.
   - `finalize_script`: concatenates snippets, runs post-processor pipeline, writes `output/<timestamp>_deploy.sh`.
   - Testing step designed but not yet implemented (see Testing Architecture below).

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
```

## Flow State (`GoEState`)
| Field | Populated from |
|---|---|
| `raw_request` | user `input()` |
| `parsed_request` | `parse_task.output.pydantic` |
| `mapped_request` | `dep_task.output.pydantic` (post dep-enumeration) |
| `sequenced_request` | `sequence_task.output.pydantic.atoms` |
| `generated_snippets` | `generate_task.output.pydantic.snippets` |
| `final_script` | post-processor output, written to `output/<timestamp>_deploy.sh` |

## Configuration Files
- `config/agents.yaml`: Defines `request_parser_agent`, `mapping_agent`, `mapping_validator_agent`, `dependency_enumeration_agent`, `sequencing_agent`, `snippet_generation_agent`, `testing_agent`.
- `config/tasks.yaml`: Defines `parse_request_task`, `map_atoms_task`, `validate_mapping_task`, `enumerate_dependencies_task`, `sequence_atoms_task`, `generate_snippets_task`, `validate_snippets_task`.
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
1. **Testing Agent + Docker validation**: Implement two-layer snippet testing (see Testing Architecture below).
2. **Non-interactive trigger**: Implement `run_with_trigger` in `main.py` to accept requests via JSON payload.
3. **Reduce RAG token cost**: Return condensed atom representations (frontmatter + headers only) instead of full markdown.

---

## Testing Architecture Design

Snippet validation uses two distinct layers with different trust boundaries.

### Layer 1 — Internal (state verification)
Run `testing_snippet` inside the target container via `docker exec`. Answers: *"Was the configuration applied correctly?"*
- Tests filesystem state, service status, user existence, permissions, SUID bits — from root's perspective.
- **Tool**: `ExecInContainerTool` — `docker exec <id> bash -c "<snippet>"`, returns stdout/stderr/exit code.
- **Failure signal**: non-zero exit code or assertion mismatch.

### Layer 2 — External (attack simulation)
Spin up a separate attacker container on the same Docker bridge network. Answers: *"Is the access vector actually exploitable?"*
- Tests reachability and exploit success from an unauthenticated adversary's perspective.
- **Attacker container**: Alpine + `openssh-client` + `samba-client` (plain BusyBox lacks these clients).
- **Tool**: `AttackFromContainerTool` — takes target hostname, attack type (`ssh`, `smb`), and parameters; runs probe; returns pass/fail + output.
- **Examples**: `smbclient -L //<target>/<share> -N` for anonymous SMB; `sshpass -p <pw> ssh <user>@<target> id` for SSH.
- Atoms with no external attack surface (`install_package`, `create_user`) set `attack_snippet = None` and skip Layer 2.

### `attack_snippet` field
Added to `GeneratedSnippet` (see Data Models above). Produced by the Snippet Generation Agent at generation time — it already has the atom markdown and context in scope.

### Docker Network Topology
```
docker network create goe_test_net
docker run -d --name target --network goe_test_net ubuntu:22.04
docker run --rm --network goe_test_net <attacker-image> <attack_snippet>
```
Both containers addressed by hostname. Both torn down after the test crew finishes.

### Container Lifecycle (`TestEnvironmentTool` — Python helper, not crewAI tool)
1. `setup()` — create bridge network, start target container.
2. `apply_and_verify(snippets)` — execute snippets sequentially; Layer 1 check after each.
3. `run_external_probes(snippets)` — start attacker container; run `attack_snippet` per atom against fully-configured target.
4. `teardown()` — stop + remove both containers and network.

### Hybrid Testing Strategy
- Apply snippets sequentially; Layer 1 (internal) check after each atom. Failure stops the chain early.
- After all atoms pass Layer 1, run all Layer 2 (external) probes against the fully-configured machine.
- Layer 2 failures flag a specific access vector without blocking other probes.
- `validated = True` when both layers pass (or `attack_snippet` is `None` and Layer 1 passes).

### New Components Required
| Component | Type | Purpose |
|---|---|---|
| `attack_snippet` field | `GeneratedSnippet` model change | Adversarial probe command |
| `TestEnvironmentTool` | Python helper | Docker network + container lifecycle |
| `ExecInContainerTool` | crewAI `BaseTool` | Layer 1: `docker exec` inside target |
| `AttackFromContainerTool` | crewAI `BaseTool` | Layer 2: probe from attacker container |
| Attacker image | Dockerfile or pre-built | Alpine + `openssh-client` + `samba-client` |
| Updated snippet generation task | `agents.yaml` / `tasks.yaml` | Add `attack_snippet` generation step |
| `test_snippets` flow step | `main.py` | `@listen(generate_implementation)` before `finalize_script` |
