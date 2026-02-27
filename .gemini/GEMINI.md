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
3. **Agentic Flow (`GoEFlow`)** — single `engineer_requirements` step runs a sequential crewAI crew:
   - **Request Parser** → **Mapping Agent** → **Mapping Validator** → **Dependency Enumerator** → **Sequencing Agent**
   - Snippet Generation and Testing are stubbed but not yet activated.

## Agent Pipeline (as of Feb 2026)

### 1. Request Parser Agent
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
| `generated_snippets` | (stubbed) |
| `final_script` | (stubbed) |

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
1. **Snippet Generation + Testing**: Activate the commented-out `generate_implementation` crew (Snippet Generation Agent + Testing Agent with Docker execution).
2. **Reduce RAG token cost**: Return condensed atom representations (frontmatter + headers only) instead of full markdown.
3. **Non-interactive trigger**: Implement `run_with_trigger` in `main.py` to accept requests via JSON payload.
