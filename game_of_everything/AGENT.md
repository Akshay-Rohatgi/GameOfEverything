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
- **`ReadAtomTool`** (`read_atom`): reads the full markdown of any Atom from `atoms/` by id. Used by the Snippet Generation Agent to fetch Logic Requirements, Synthesis Guidance, and Testing Guidance at generation time.
- **`script_postprocessor.py`**: Extensible post-processing pipeline (`SCRIPT_POST_PROCESSORS: List[Callable[[str], str]]`). Current processors (applied in order): `inject_shebang` (strips stray shebangs, prepends exactly one `#!/bin/bash`), `ensure_set_e` (inserts `set -e` after the shebang), `normalize_blank_lines` (collapses 3+ blank lines to 2).
- **Snippet Generation Agent**: Operational. Receives `sequenced_atoms_json` (serialized `SequencedRequest`), calls `read_atom` per atom, uses the atom's `context` field as the security intent brief to resolve ambiguities the template leaves open (password strength, binary choice, file content, etc.), substitutes `parameters`, and emits `code_snippet` + `testing_snippet` per atom. Outputs `GeneratedSnippets`. No shebang in generated snippets — injected by post-processor.
- **`finalize_script` flow step**: Concatenates `code_snippet` fields separated by `# --- <atom_name> ---` headers, runs the result through `apply_post_processors`, writes to `output/<timestamp>_deploy.sh` (mode 0755).
- **Configuration** (`agents.yaml` / `tasks.yaml`): Fully defined for all agents including `snippet_generation_agent`. Task context chain: `parse → map → validate → dep_enumerate → sequence`. Snippet generation task injects sequenced atoms via `inputs` rather than crewAI `context`.
- **Atoms Library**: `create_user.md`, `install_package.md`, `samba_insecure_share.md`, `sensitive_file.md`, `set_suid.md`.
- **Flow State** (`GoEState`): `raw_request`, `parsed_request`, `mapped_request` (from `dep_task`), `sequenced_request` (from `sequence_task`), `generated_snippets` (from `generate_task`), `final_script` (written to `output/`).

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
- **Context injection**: All data flow is prompt-based via crewAI `context=[...]` or `inputs={}`. There is no programmatic structured handoff.

### 🎯 Next Steps
1. **Non-interactive trigger**: Implement `run_with_trigger` in `main.py` to support passing a request via JSON payload instead of `input()`.
2. **Reduce RAG token cost**: Investigate embedding only frontmatter + section headers to reduce tokens passed to the Mapping Agent.
3. **Agentic retry on test failure**: Extend the Testing Agent to autonomously run diagnostic commands via `ExecInContainerTool` / `AttackFromContainerTool` when a verdict is ambiguous, and optionally regenerate a failing snippet.

---

## 🧪 Testing Architecture (Implemented)

Snippet validation uses two distinct layers with different trust boundaries, orchestrated by a **hybrid Python loop + LLM verdict** architecture.

### Design Principles

1. **LLM judges all command output.** Command outputs are messy, version-dependent, and context-sensitive. Rather than hardcoding expected outputs or regex patterns per atom, the Testing Agent (LLM) receives the atom's security intent + raw stdout/stderr/exit code and reasons about whether the output indicates success. This scales to any atom without per-atom parsing logic.

2. **Incremental cumulative testing.** After applying snippet N and passing its Layer 1 check, Layer 2 probes run for **all snippets 0..N** that have an `attack_snippet`. This catches:
   - **Dependency misordering**: If the Sequencing Agent put SSH login before user creation, the SSH probe fails immediately at that step — not masked by a later "all-at-once" run where the dependency is accidentally satisfied.
   - **Regressions**: If snippet N breaks snippet M's (M < N) functionality (e.g. a firewall rule closing a port), the re-run of snippet M's probe catches it at the exact snippet that caused the regression.

3. **Python loop controls progression; LLM only judges.** The incremental cumulative protocol is too structured for a single agent invocation to execute correctly. Python controls: which snippet to apply, when to stop, which probes to re-run. The LLM only interprets command output — it never decides what to run.

4. **Kali-based attacker container.** The `kalilinux/kali-rolling` base image is larger than Alpine, but Kali's apt repos ship pre-packaged offensive tools (metasploit, hydra, nmap, smbclient, sshpass). As the atom library grows beyond basic SMB/SSH, every new attack vector would require sourcing and building tools on Alpine. Kali eliminates that tax — additional tools are one `apt install` away with no custom repos.

### Layer 1 — Internal (state verification)
Run the `testing_snippet` inside the target container via `docker exec`. Answers: *"Was the configuration applied correctly?"*
- Tests filesystem state, service status, user existence, permissions, SUID bits — from root's perspective on the target machine.
- **Execution**: `TestEnvironmentTool.exec_in_target(snippet)` — runs `docker exec goe_target bash -c "<snippet>"`, returns stdout/stderr/exit code.
- **Judgment**: The raw output is passed to a one-task Testing Agent crew (`validate_snippets_task`) which returns a `TestVerdict(passed, reasoning)`.
- **Failure behavior**: A Layer 1 failure stops the entire test chain — downstream atoms may depend on this one. All remaining snippets are marked `validated = False`.

### Layer 2 — External (attack simulation)
Run the `attack_snippet` from the Kali attacker container against the target on the same Docker bridge network. Answers: *"Is the initial access vector actually exploitable?"*
- Tests reachability, service exposure, and exploit success from an unauthenticated adversary's perspective.
- **Execution**: `TestEnvironmentTool.exec_in_attacker(snippet)` — runs `docker exec goe_attacker bash -c "<snippet>"`.
- **Judgment**: Same one-task Testing Agent crew, same `TestVerdict` output.
- **Incremental re-run**: After applying snippet N, Layer 2 runs for all snippets 0..N that have an `attack_snippet`. Regressions are flagged with a warning distinguishing "first-time failure" from "regression (was passing, now fails)."
- **Failure behavior**: Layer 2 failures flag the specific access vector as broken but do NOT stop the chain. Other probes continue.

### `attack_snippet` field
A third output produced by the Snippet Generation Agent alongside `code_snippet` and `testing_snippet`:
```python
class GeneratedSnippet(BaseModel):
    atom_name: str
    code_snippet: str
    testing_snippet: str      # Layer 1: internal state check
    attack_snippet: Optional[str] = None  # Layer 2: adversarial probe from attacker container
    mapped_atom: MappedAtom
    validated: bool = False
```
Atoms with no external attack surface (e.g. `install_package`, `set_suid`) leave `attack_snippet` as `None` and skip Layer 2.

### LLM Verdict Model
```python
class TestVerdict(BaseModel):
    passed: bool
    reasoning: str  # 1-3 sentences citing specific evidence from stdout/stderr

class TestResult(BaseModel):
    atom_name: str
    layer1_verdict: TestVerdict
    layer2_verdicts: Optional[List[TestVerdict]] = None  # one per cumulative probe run
    error: Optional[str] = None
```

### Docker Network Topology
```
docker network create goe_test_net
docker run -d --name goe_target --hostname target --network goe_test_net ubuntu:22.04 sleep infinity
docker run -d --name goe_attacker --hostname attacker --network goe_test_net goe-attacker:latest sleep infinity
```
- Target: `ubuntu:22.04` — snippets are applied here via `docker exec`.
- Attacker: `kalilinux/kali-rolling` + pre-installed offensive tools — attack probes run here.
- Both containers remain running for the entire test loop (avoids per-probe startup overhead).
- Both are torn down in a `finally` block after testing completes.

### Container Lifecycle (`TestEnvironmentTool`)
A Python helper class (not a crewAI tool) at `tools/test_environment.py` that owns Docker lifecycle via the `docker` Python SDK:
1. `setup()` — create bridge network, start target + attacker containers (builds Kali image from `docker/attacker/Dockerfile`). Force-cleans any stale resources from previous failed runs.
2. `exec_in_target(snippet)` — `docker exec goe_target bash -c "<snippet>"`, returns `(exit_code, stdout, stderr)`.
3. `exec_in_attacker(snippet)` — `docker exec goe_attacker bash -c "<snippet>"`, returns `(exit_code, stdout, stderr)`.
4. `teardown()` — stop + remove both containers and network.

### Hybrid Testing Loop (`test_snippets` flow step)
The `test_snippets` method in `GoEFlow` is decorated with `@listen(generate_implementation)` and runs before `finalize_script`:

```
engineer_requirements → generate_implementation → test_snippets → finalize_script
```

Pseudocode:
```python
env = TestEnvironmentTool()
env.setup()
try:
    for i, snippet in enumerate(snippets):
        # Apply code_snippet on target
        env.exec_in_target(snippet.code_snippet)

        # Layer 1: run testing_snippet, LLM judges output
        l1_exit, l1_stdout, l1_stderr = env.exec_in_target(snippet.testing_snippet)
        l1_verdict = run_verdict_crew(atom=snippet, layer="internal", output=...)
        if not l1_verdict.passed:
            mark snippet + all remaining as validated=False, STOP
            break

        # Layer 2: re-run ALL attack probes for snippets 0..i
        l2_verdicts = []
        for j in range(i + 1):
            if snippets[j].attack_snippet:
                a_exit, a_stdout, a_stderr = env.exec_in_attacker(snippets[j].attack_snippet)
                verdict = run_verdict_crew(atom=snippets[j], layer="external", output=...)
                l2_verdicts.append(verdict)
                if not verdict.passed and j < i:
                    WARN: "regression — snippet j was passing, now fails after snippet i"

        snippet.validated = l1_passed and all(l2_passed)
finally:
    env.teardown()
```

`run_verdict_crew()` creates a one-task crew with the Testing Agent, passes atom context + raw output via `inputs`, kicks off, and returns the `TestVerdict`. Each call is a lightweight LLM invocation — no tools, pure reasoning.

### Components Implemented
| Component | Type | Location |
|---|---|---|
| `attack_snippet` field | `GeneratedSnippet` model | `models.py` |
| `TestVerdict`, `TestResult` | Pydantic models | `models.py` |
| `TestEnvironmentTool` | Python helper | `tools/test_environment.py` |
| `ExecInContainerTool` | crewAI `BaseTool` | `tools/exec_in_container_tool.py` |
| `AttackFromContainerTool` | crewAI `BaseTool` | `tools/attack_from_container_tool.py` |
| Kali attacker image | Dockerfile | `docker/attacker/Dockerfile` |
| `testing_agent` | Agent config | `config/agents.yaml` |
| `validate_snippets_task` | Task config (verdict) | `config/tasks.yaml` |
| `test_snippets` flow step | Flow method | `main.py` |
| Updated `finalize_script` | Flow method | `main.py` (only includes `validated=True` snippets) |

**Note**: `ExecInContainerTool` and `AttackFromContainerTool` are implemented as crewAI `BaseTool`s but are **not used in the current test loop** — the loop calls `TestEnvironmentTool` directly. These tools exist for a future enhancement where the Testing Agent can autonomously run diagnostic commands (e.g. "the SMB probe failed, let me check if samba is running") as part of an agentic retry loop.
