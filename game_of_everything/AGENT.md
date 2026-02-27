# agent.md: Game of Everything (GoE)

## 🎯 Project Overview
**Game of Everything (GoE)** is a framework for agentically building vulnerable cybersecurity challenges. It allows users to request for deployment scripts of vulnerable applications, servers, and other resources to quickly spin up a vulnerable environment for training, testing, or educational purposes. This file provides an overview for any coding agents (e.g. Claude, Copilot, Gemini, Codex) tasked with assisting in the development or maintenance of the GoE project. 

## Project Architecture
To avoid generating entire vulnerable machines from scratch, GoE relies on a modular architecture:
1. Atoms: Small, discrete markdown files found in `game_of_everything/atoms/` containing the general description of a specific configuration or vulnerability. (e.g. create_user, samba_insecure_share, etc.)
2. RAG Pipeline: To scale the Mapping Agent's ability to select from a large library of Atoms, a Retrieval-Augmented Generation (RAG) system is used:
    - **Database**: ChromaDB (located at `src/game_of_everything/chroma_db/`).
    - **Embeddings**: Amazon Bedrock (`amazon.titan-embed-text-v2:0`).
    - **Ingestion**: `scripts/rag_gen.py` synchronizes Markdown Atoms from the `atoms/` directory into the vector database, tracking last-modified timestamps to avoid redundant upserts.
    - **Retrieval**: `scripts/query.py` provides similarity search capabilities to find the most relevant Atoms for a given request component.
    - **Custom Tool**: `src/game_of_everything/tools/simple.py` implements the `search_atoms` tool, allowing agents to perform semantic searches directly against the ChromaDB collection within the crewAI Flow.
3. Game of Everything (GoE): An agentic system that:
    - Takes in a user request for a vulnerable environment (e.g. "I want a vulnerable machine with an open samba share and a user with a weak password")
    - Breaks down the request into a set of Atoms (e.g. create_user, samba_insecure_share) using the RAG-enhanced Mapping Agent.
    - Iterates through each atom:
        - Reads the markdown file for that atom
        - Generates a script in (bash, powershell) to implement the configuration or vulnerability described in the atom. The agent may have to mutate the script based on the specific parameters of the user request (e.g. username, password, samba share name, etc.)
        - Uses tools to test the generated script in a safe environment (e.g. a docker container) to ensure it works as intended and does not cause unintended consequences.
    - Combines the generated scripts for each atom into a final deployment script that can be used to set up the vulnerable environment.

## Game of Everything (GoE) Agents
The GoE system may consist of multiple agents, each with specific roles in the process:
1. **Request Parser Agent**: Responsible for parsing the user's request and breaking it down three sections:
    - Context: The server alongside its contextualization (e.g. "an ubuntu server used for software development")
    - Initial Access Vector(s): The vulnerabilities or configurations that will allow an attacker to gain initial access to the machine (e.g. "a user with a weak password", "an open samba share")
    - Post-Exploitation Goal(s): The ultimate goals for the attacker once they have gained access to the machine (e.g. "escalate privileges to root via SUID binaries", "exfiltrate a file containing sensitive information")

GoE needs to support varying degrees of specficity in user requests. For example, a user may provide a very high-level request ("I want a vulnerable machine with an open samba share and a user with a weak password") or a more specific one ("I want an ubuntu server with an open samba share named 'public' and a user named 'john' with the password 'password123'"). The Request Parser Agent should be able to handle both types of requests and extract the necessary information to generate the appropriate scripts.

A parsed user request will result in a ParsedRequest object that includes the initial prompt, and each section of the parsed request (context, initial access vector(s), post-exploitation goal(s)) as separate fields. Each of these fields will be a string that will later be used by the mapping agent to create a MappedRequest object that includes the relevant Atoms and parameters for each section of the request.

Example object:
```python
class ParsedRequest:
    def __init__(self, initial_prompt: str, context: str, initial_access_vectors: str, post_exploitation_goals: str):
        self.initial_prompt = initial_prompt
        self.context = context
        self.initial_access_vectors = initial_access_vectors
        self.post_exploitation_goals = post_exploitation_goals
```

2. **Mapping Agent**: Responsible for mapping the parsed user request to the appropriate Atoms in the `game_of_everything/atoms/` directory. This may involve some level of reasoning to determine which Atoms are relevant based on the user's request. Each mapping object should include the name of the atom, the parameters to be used for that atom (e.g. username, password, samba share name, etc.), and any relevant contextual information that may be needed for atom mutation (e.g. the operating system of the target machine, weak or strong password requirements, technical sophistication of the attack vector).

A MappedRequest object will be created for each request. For each of the three sections of the parsed request (context, initial access vector(s), post-exploitation goal(s)), the Mapping Agent will identify the relevant Atoms and parameters, and create a MappedRequest object that includes this information.

Example object:
```python
class MappedAtom(BaseModel):
    name: str        # exact Atom id (e.g., "samba_insecure_share")
    context: str     # why this Atom was chosen
    parameters: Optional[dict] = None  # required_vars values (e.g., {"share_name": "public", "path": "/srv/share"})

class MappedRequest(BaseModel):
    section: str
    mapped_initial_access_atoms: List[MappedAtom]
    mapped_post_exploitation_goal_atoms: List[MappedAtom]
```

2b. **Mapping Validation Agent**: An independent second-opinion mapper that audits the `MappedRequest` produced by the Mapping Agent. It does NOT trust the mapper's output — instead it re-derives expected atoms from scratch:
    1. Takes each raw string from `ParsedRequest.initial_access_vectors` and `ParsedRequest.post_exploitation_goals`.
    2. Independently decomposes each string into discrete atomized descriptions (e.g. "SMB share with anonymous access containing a sensitive file" → `["insecure samba share with anonymous access", "sensitive file placed in share"]`).
    3. Calls `search_vulnerability_atoms` with `n_results=1` for each atomized description (best-match only).
    4. Diffs found atom names against those already in the `MappedRequest`.
    5. For any atom not already present, creates a new `MappedAtom` (populating `parameters` from `required_vars` and the request context) and adds it to the correct list.
    6. Returns the updated `MappedRequest` — unchanged if the mapper was complete, extended if atoms were missing.

2c. **Dependency Enumeration Agent**: Identifies every OS-level package implicitly required by the validated mapping and adds `install_package` atoms for any that are missing. Users specify vulnerabilities, not infrastructure — this agent fills that gap automatically:
    1. Reads every atom in the validated `MappedRequest` (name, context, parameters).
    2. Applies known dependency rules (e.g. `samba_insecure_share` → `samba`, `.zip` sensitive file → `zip`, SSH access implied → `openssh-server`, non-base SUID binary → its apt package) plus open-ended reasoning for unlisted atoms.
    3. Deduplicates the required package list and diffs against `install_package` atoms already present.
    4. Calls `search_vulnerability_atoms` with `n_results=1` per missing package to confirm the `install_package` atom format from real atom data.
    5. Appends new `install_package` MappedAtoms to `mapped_initial_access_atoms`.
    6. Returns the updated `MappedRequest` — unchanged if all packages were already present.

3. **Sequencing Agent**: Responsible for determining the order in which the Atoms should be executed based on the user's request and the dependencies between the Atoms. For example, if a user requests a vulnerable machine with an open samba share and a user with a weak password, the Sequencing Agent should determine that the Atom to create the user with a weak password should be executed before the Atom to create the open samba share, as the samba share may need to be configured to contain a file with the user's credentials for the initial access vector to work effectively.

A sequenced_request list will be created that includes the ordered list of Atoms to be executed, along with any necessary parameters for each Atom.
Example object:
```python
sequenced_request: List[MappedAtom] 
```

4. **Snippet Generation Agent**: Responsible for generating the actual code snippets (e.g. bash, powershell) for each Atom based on the parameters identified in the MappedRequest object. This agent may also be responsible for mutating the generated code snippets based on the specific parameters of the user's request (e.g. password, API use case, writable script name etc.) and any relevant contextual information (e.g. operating system of the target machine, weak or strong password requirements, technical sophistication of the attack vector).

A GeneratedSnippet object will be created for each Atom that includes the generated code snippet, the parameters used for generation, and any relevant contextual information.
```python
class GeneratedSnippet:
    def __init__(self, atom_name: str, code_snippet: str, testing_snippet: str, mapped_atom: MappedAtom):
        self.atom_name = atom_name
        self.code_snippet = code_snippet
        self.testing_snippet = testing_snippet
        self.mapped_atom = mapped_atom
        self.validated = False

    def set_validated(self, validated: bool):
        self.validated = validated
```

5. **Testing Agent**: Responsible for testing the generated code snippets in a safe environment (e.g. a docker container) with the testing snippet to ensure they work as intended and do not cause unintended consequences. This may involve setting up a test environment, executing the generated code snippets, and verifying that the expected vulnerabilities or configurations have been successfully implemented.

The GeneratedSnippet objects will be passed to the Testing Agent, which will execute the code snippets in a safe environment and update the `validated` field of each GeneratedSnippet object based on the results of the testing process.
```python
g = GeneratedSnippet()
g.set_validated(validated=True)
```

6. **Final Script Generation**: This is not an agent, but rather the final step in the process where the validated code snippets for each Atom are combined into a final deployment script that can be used to set up the vulnerable environment. This may involve some additional formatting or organization to ensure that the final script is easy to understand and execute. The final deployment script will be generated by combining the `code_snippet` fields of each validated GeneratedSnippet object in the order determined by the Sequencing Agent.
```python
final_deployment_script = ""
for generated_snippet in sequenced_request:
    if generated_snippet.validated:
        final_deployment_script += generated_snippet.code_snippet + "\n"
```

## 🛠 Current Implementation Status (as of Feb 2026)

### ✅ Completed
- **Project Structure**: `crewAI` Flow architecture established in `main.py` using `GoEFlow(Flow[GoEState])`.
- **Data Models** (`models.py`):
  - `ParsedRequest` — `initial_prompt`, `context`, `initial_access_vectors: List[str]`, `post_exploitation_goals: List[str]`
  - `MappedAtom` — `name: str`, `context: str`, `parameters: Optional[dict]` *(parameters added to match agent output)*
  - `MappedRequest` — `section: str`, `mapped_initial_access_atoms: List[MappedAtom]`, `mapped_post_exploitation_goal_atoms: List[MappedAtom]`
  - `GeneratedSnippet`, `SequencedRequest`, `GeneratedSnippets`
- **Request Parser Agent**: Fully operational. Extracts `context`, `initial_access_vectors`, and `post_exploitation_goals` as structured lists from free-form user input.
- **RAG Pipeline**:
  - `chroma_db/`: PersistentClient ChromaDB at `src/game_of_everything/chroma_db/`, collection named `goe_collection`.
  - `scripts/rag_gen.py`: Atom ingestion with modification-time tracking.
  - `scripts/query.py`: Retrieval testing script.
  - `tools/search_atoms_tool.py`: `SearchAtomsTool` (`search_vulnerability_atoms`) — semantic search against ChromaDB using Bedrock Titan embeddings. Returns up to 3 ranked Atom results per query, formatted as `--- ATOM: <id> ---\n<markdown content>`.
- **Mapping Agent**: Operational. Calls `search_vulnerability_atoms` for each access vector and post-exploitation goal. Maps results to `MappedAtom` objects including `parameters` populated from `required_vars` in the Atom frontmatter.
- **Mapping Validation Agent**: Operational. Independently re-derives expected atoms from raw `ParsedRequest` strings, calls `search_vulnerability_atoms` with `n_results=1` per atomized description, diffs against existing `MappedRequest`, and adds any missing atoms.
- **Dependency Enumeration Agent**: Operational. Reads the validated `MappedRequest`, reasons about implicit OS package requirements per atom (samba → `samba`, zip file → `zip`, SSH context → `openssh-server`, non-base SUID binary → its apt package), diffs against existing `install_package` atoms, confirms format via RAG, and appends new `install_package` MappedAtoms. `GoEState.mapped_request` is populated from `dep_task.output.pydantic`.
- **Sequencing Agent**: Operational. Receives the dependency-enriched `MappedRequest` via context, applies ordering rules (`install_package` first → `create_user` → share before file if path overlaps → initial access before post-exploitation), outputs `SequencedRequest`. `GoEState.sequenced_request` populated from `sequence_task.output.pydantic.atoms`.
- **`SearchAtomsTool`**: Updated to accept an optional `n_results` parameter (default `3`), allowing validator and dep-enumerator to call with `n_results=1` for best-match-only lookups.
- **Configuration** (`agents.yaml` / `tasks.yaml`): Fully defined for Parser, Mapper, Validator, Dep-Enumerator, and Sequencer. Task context chain: `parse → map → validate → dep_enumerate → sequence`.
- **Atoms Library**: `create_user.md`, `install_package.md`, `samba_insecure_share.md`, `sensitive_file.md`, `set_suid.md`.
- **Flow State** (`GoEState`): `raw_request`, `parsed_request`, `mapped_request` (from `dep_task`), `sequenced_request` (from `sequence_task`), `generated_snippets`, `final_script`.

### 🐛 Known Bugs & Workarounds

#### CrewAI 1.9.3 — Bedrock Native Tool Calling Args Lost (patched in venv)
**Root cause**: In `crew_agent_executor.py` L722, the expression:
```python
func_args = func_info.get("arguments", "{}") or tool_call.get("input", {})
```
The default `"{}"` is a truthy non-empty string, so the `or` short-circuits and `tool_call.get("input", {})` — which contains the actual Bedrock arguments — is **never evaluated**. The tool gets called with `{}`.

**Fix applied** to the venv (pending upstream merge via [PR #4518](https://github.com/crewAIInc/crewAI/pull/4518)):
```python
func_args = func_info.get("arguments") or tool_call.get("input", {})
```
When upgrading `crewai`, re-check if this is merged. If not, reapply the patch to `.venv/lib/python3.12/site-packages/crewai/agents/crew_agent_executor.py`.

**How this was diagnosed**: Debug logging confirmed the LLM (Bedrock Claude) correctly returned `"input": {"query": "..."}` in the native tool call response, but `_run(**kwargs)` was receiving `{}`. The Bedrock tool call format is `{"toolUseId": "...", "name": "...", "input": {...}, "type": "tool_use"}` — it has no `"function"` key, which triggered the bug.

#### CrewAI 1.9.3 — Native vs ReAct Path
Bedrock Claude supports native function calling, so CrewAI takes the **native path** (`_invoke_loop_native_tools`), not the ReAct text-parsing path. Key differences:
- **Native**: `BaseTool.run(**args_dict)` → `self._run(**kwargs)`. No Pydantic validation before call.
- **ReAct**: `CrewStructuredTool.invoke()` → `_parse_args()` → `args_schema.model_validate()` → `self._run(**kwargs)`.
- `function_calling_llm` set on `Crew` is **not** a switch for native calling — it is only a fallback JSON parser for malformed ReAct `Action Input:` strings.

#### CrewAI 1.9.3 — Event Bus Mismatch Warnings
Spurious `[CrewAIEventsBus] Warning: Event pairing mismatch` warnings appear due to `ToolUsageFinished` being emitted without a matching `ToolUsageStarted`. These are suppressed in `main.py` via:
```python
_event_context_config.set(EventContextConfig(
    mismatch_behavior=MismatchBehavior.SILENT,
    empty_pop_behavior=MismatchBehavior.SILENT,
))
```

### 🚧 In Progress
- **Active agents**: `engineer_requirements` runs Parser → Mapper → Validator → Dep-Enumerator → Sequencer. Snippet Generation and Testing steps are stubbed/commented.
- **Context injection**: All data flow is prompt-based via crewAI `context=[...]`. CrewAI concatenates prior task raw outputs into each subsequent task's prompt as plain text. There is no programmatic structured handoff.

### 🎯 Next Steps
1. **Reduce RAG token cost**: Currently each ChromaDB result returns the full Atom markdown. Investigate embedding a condensed representation (e.g., only frontmatter + section headers) to reduce tokens passed to the Mapping Agent without losing the information needed for parameter extraction.
2. **Snippet Generation + Testing**: Activate and refine the commented-out generation and validation stages.
3. **Non-interactive trigger**: Implement `run_with_trigger` in `main.py` to support passing a request via JSON payload instead of `input()`.
