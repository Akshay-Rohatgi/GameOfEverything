# Custom Application Vulnerability Generation — Implementation Plan

## Codebase Orientation

Key files a new instance should read first:
- `src/game_of_everything/main.py` — `GoEFlow(Flow[GoEState])`, `@start()`/`@listen()` step decorators
- `src/game_of_everything/state.py` — `GoEState`, the shared state object passed between steps
- `src/game_of_everything/models.py` — all Pydantic models including `ParsedRequest`
- `src/game_of_everything/steps/engineer_requirements.py` — the step being split in Phase 1
- `src/game_of_everything/tools/search_atoms_tool.py` — `SearchAtomsTool`, queries ChromaDB
- `scripts/rag_gen.py` — ingests atoms into ChromaDB; needs collection routing in Phase 2

All new flow steps follow the same pattern as existing ones in `src/game_of_everything/steps/`.

---

## Core Principles

- Generated app code lives in `/tmp/{session_id}/` — never committed
- LLMs write scaffolding; vulnerability logic comes from atoms
- The user's prompt is a starting point, not a specification — a synthesis step
  elaborates it into a fully consistent scenario before any parsing happens
- Shared resource conflicts (e.g. MySQL used by both app and misconfig) are resolved
  in synthesis, not in per-component conflict detection logic
- Tier 1 only: single-file apps, apt-installable dependencies, no ORMs or sessions

---

## Phase 1 — Scenario Synthesis (changes to existing flow)

**What changes:** `engineer_requirements` is split into two steps.

**Why:** The current step parses the user prompt directly, which breaks down when
shared resources (MySQL, Apache, etc.) are touched by both the custom app and
misconfig pipelines. An LLM reasoning about the whole box upfront resolves this
generically without hard-coded conflict rules.

### New step: `synthesize_scenario`

Runs before `engineer_requirements`. Takes the raw user prompt, produces a fully
elaborated prose scenario with all implicit decisions made explicit.

```python
class SynthesizedScenario(BaseModel):
    narrative: str                    # Full box description, all config decisions explicit
    attack_narrative: str             # End-to-end attacker path
    shared_resources: List[str]       # e.g. "MySQL serves app backend + misconfig surface"
    explicit_decisions: List[str]     # What the LLM decided that wasn't in the prompt
    misconfig_scope: str              # What to hand to the misconfig pipeline
    custom_app_scope: Optional[str]   # What to hand to the custom app pipeline
```
### Example

User Prompt: 
```
I want a PHP login portal with an SQL Injection vulnerability that leads to credential theft via exposed system information past login. The user should be able to login with the stolen credentials and then escalate to root via a misconfigured sudoers rule.
```

Synthesized Scenario:
```python
SynthesizedScenario(
    narrative="""
        The target box runs Ubuntu 22.04 with Apache 2.4, PHP 7.4, and MySQL 8.0.
        Apache hosts a single-file PHP login portal at /var/www/html/login.php.
        The login form POSTs username and password to the same file. The username
        field is concatenated directly into a MySQL query without parameterization.

        MySQL contains a 'webapp' database with a 'users' table (id, username,
        password). Passwords are stored in plaintext. Upon successful login, the
        application displays the authenticated user's full record including their
        plaintext password. This information exposure is intentional — it is the
        mechanism by which the attacker recovers credentials.

        An OS user 'developer' exists with password 'Summer2024!' — matching the
        credentials seeded into the users table. SSH is enabled and accepts password
        authentication. MySQL binds to localhost only and is not directly accessible
        from the network.

        A sudoers rule grants 'developer' passwordless sudo access to /usr/bin/vim.
        This is the intended privilege escalation path via vim's shell escape.
    """,

    attack_narrative="""
        1. Discover login portal on port 80
        2. Test SQLi: submit ' OR '1'='1 as username — application logs in and
           displays the full users table row including plaintext password 'Summer2024!'
        3. SSH to box as developer / Summer2024!
        4. Run: sudo vim -c ':!/bin/bash' to spawn a root shell via sudoers rule
    """,

    shared_resources=[
        "MySQL is the app's backend only — not a direct network attack surface. "
        "No misconfig atom should alter MySQL's bind-address or root password.",
    ],

    explicit_decisions=[
        "Passwords stored as plaintext (not hashed) so no cracking step is needed — "
        "the credential is readable directly from the login response.",
        "MySQL binds to localhost only — the misconfig surface is sudoers, not MySQL.",
        "Sudoers rule targets /usr/bin/vim specifically (GTFOBins shell escape).",
        "SSH password authentication is enabled — required for the credential theft "
        "OS user 'developer' password matches the DB seed data exactly.",
    ],

    misconfig_scope="""
        Create OS user 'developer' with password 'Summer2024!'. Enable SSH with
        password authentication. Add sudoers rule: developer ALL=(ALL) NOPASSWD:
        /usr/bin/vim. Do not alter MySQL configuration.
    """,

    custom_app_scope="""
        Single-file PHP login portal. SQLi in username field via string concatenation.
        On successful login, display the full user row including plaintext password.
        MySQL backend: webapp database, users table (id, username, password plaintext).
        Seed with one row: username='developer', password='Summer2024!'.
    """
)
```

### Synthesis Prompt Design

The prompt must provide:
- **GoE context** — CTF-style box, runs in Docker, single deployable script output
- **Available attack chain goals** — synthesis picks from the defined list, never invents new ones
- **Escalation categories** — instead of atom IDs, give categories (SUID, sudoers, writable cron, capabilities) so synthesis doesn't specify paths that no atom covers
- **Tier 1 constraint** — single-file app, no sessions/ORMs, apt-only dependencies; synthesis must simplify complex app requests down to one endpoint
- **Specificity rules** — describe what an attacker observes and does, not how the box is built (no field names, file paths, software versions, or GTFOBins specifics)

**Pre-output checklist the prompt requires the agent to satisfy:**
```
[ ] attack_chain_goal is explicitly named and matches one from the provided list
[ ] bridged credentials appear with identical values in BOTH custom_app_scope
    and misconfig_scope
[ ] every shared resource has an explicit allocation decision with rationale
[ ] escalation path uses only the provided atom categories
[ ] app is a single endpoint (Tier 1)
[ ] credential hash type and plaintext value are both specified — Layer 2 tests
    crack any hash type in one shot since the plaintext is known at seed time
[ ] attack path is written as attacker-observable steps, not implementation steps
```

### `engineer_requirements` becomes parsing only

Reads `SynthesizedScenario`, extracts structured fields. No longer reasons about
intent — synthesis already did that.

### Updated pipeline

```
user_prompt
  → synthesize_scenario     # LLM reasons about whole box, resolves shared resources
  → engineer_requirements   # Parse SynthesizedScenario → ParsedRequest
  → map_requirements        # Unchanged
  → ...
```

### Files to change
- `src/game_of_everything/steps/engineer_requirements.py` — strip reasoning, add parsing from scenario
- `src/game_of_everything/main.py` — add `synthesize_scenario` step before `engineer_requirements`
- `src/game_of_everything/models.py` — add `SynthesizedScenario`
- `src/game_of_everything/config/agents.yaml` — add `scenario_synthesis_agent`
- `src/game_of_everything/config/tasks.yaml` — add `synthesize_scenario_task`

> **Open question:** Skip synthesis for misconfig-only boxes to avoid latency?
> Could detect "no custom app needed" early and short-circuit.

---

## Phase 2 — Web Vulnerability Atoms + RAG Separation

**What changes:** New atom type ingested into a separate ChromaDB collection.

### New atom directory

```
atoms/web_vulnerabilities/
  sqli_union.md
  sqli_tautology.md
  sqli_blind.md
  xss_stored.md
  xss_reflected.md
  ssti_jinja2.md
  cmd_injection.md
  file_upload_bypass.md
  path_traversal_lfi.md
```

Atom format follows existing convention. Frontmatter includes `type: web_vulnerability`.
Body provides logic requirements, synthesis guidance with 2–3 language examples, and
testing guidance. LLM uses examples as guidance — not copy-paste.

### Two ChromaDB collections

| Collection | Contents | Queried by |
|---|---|---|
| `atoms` | Misconfiguration atoms (existing) | `map_requirements`, `validate_mapping` |
| `web_vuln_atoms` | Web vulnerability atoms | `generate_app` in CustomAppFlow only |

Never cross-queried. Prevents semantic overlap ("weak authentication" meaning
different things in SSH vs. web login contexts).

### Files to change
- `scripts/rag_gen.py` — route to collection based on `type` frontmatter field
- `src/game_of_everything/tools/search_atoms_tool.py` — add `collection` parameter
  (defaults to `"atoms"` so existing steps need no changes); instantiate the
  `web_vuln_atoms` collection in the same `__init__` where `atoms` is created
- `atoms/web_vulnerabilities/` — new atom files (content work)

---

## Phase 3 — CustomAppFlow + Attack Goals

**What changes:** New parallel flow for generating and validating custom apps.
`CustomAppFlow` follows the same `Flow[CustomAppState]` pattern as `GoEFlow` and
lives at `src/game_of_everything/steps/custom_app_flow.py`.

### New files

```
src/game_of_everything/
  steps/custom_app_flow.py          # New CustomAppFlow (~200 lines)
  custom_apps/
    attack_goals/                   # YAML configs — one per goal
      rce_via_webshell.yaml
      rce_via_sqli.yaml
      rce_via_cmd_injection.yaml
      auth_bypass.yaml
      credential_theft.yaml
      lfi_to_rce.yaml
    web_runtimes/                   # YAML configs — one per runtime
      apache_php.yaml
      flask.yaml
      express.yaml
```

### New models

```python
class CustomVector(BaseModel):
    app_template: str               # "php_login", "flask_search", "php_upload"
    vuln_atom_id: str               # "sqli_union", "ssti_jinja2"
    attack_chain_goal: str          # "credential_theft", "rce_via_webshell"
    runtime_atom_id: str            # "web_runtime_apache_php"
    install_path: str = "/var/www/html/app"
    port: int = 80
    db_name: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    seed_username: Optional[str] = None   # Matches OS user for credential_theft chains
    seed_password: Optional[str] = None
    synthesis_context: str = ""           # Populated by engineer_requirements from
                                          # SynthesizedScenario.custom_app_scope

class ResolvedCustomApp(BaseModel):
    vector: CustomVector
    deploy_snippet: str
    testing_snippet: str
    attack_snippet: str
    validation_passed: bool
```

### Attack goal config format

Configs describe **success criteria and test templates only** — not infrastructure
requirements (those are resolved by synthesis).

```yaml
id: credential_theft
description: Extract credentials from the app DB via web vulnerability.
compatible_vuln_patterns: [sqli_union, sqli_blind]
success_criteria:
  output_pattern: "\\$2[aby]\\$|md5:|[a-f0-9]{32}"
test_template: |
  curl -s -X POST http://target:{{port}}/{{endpoint}} \
    -d "{{input_parameter}}=' UNION SELECT 1,password,3 FROM users-- -" \
    | grep -E '\$2[aby]\$|[a-f0-9]{32}'
```

### CustomAppFlow steps

```
1. load_context       — load vuln atom from web_vuln_atoms collection + attack goal YAML
2. generate_app       — Opus-class agent writes app guided by atom + synthesis_context
                        outputs: app code, schema.sql, seed.sql, setup_db.sh,
                                 testing_snippet, attack_snippet
3. validate_syntax    — php -l / python3 -m py_compile / node --check
                        on failure: retry generate_app (max 1 retry)
4. validate_end_to_end — reuses existing test_environment.py containers
                         a. copy app into goe_target
                         b. run setup_db.sh
                         c. apply web runtime setup
                         d. Layer 1: testing_snippet in goe_target
                         e. Layer 2: attack_snippet from goe_attacker
                         f. clean up from goe_target
                        on failure: retry generate_app (max 2 retries)
                        on exhausted retries: raise AppGenerationError
5. emit_result        — package into ResolvedCustomApp
```

### Database handling

The database is an implementation detail of the app — owned by CustomAppFlow.
`setup_db.sh` creates the DB, user, schema, and seed data. `mysql-server` is inferred
as an `install_package` dependency by the existing dependency enumeration agent.
Seed credentials are set by synthesis (matching OS users when the attack chain
continues past the web app).

---

## Phase 4 — GoEFlow Integration

**What changes:** Wire CustomAppFlow into the main pipeline.

### Updated ParsedRequest

```python
class ParsedRequest(BaseModel):
    synthesized_scenario: SynthesizedScenario
    context: str
    initial_access_vectors: List[str]
    post_exploitation_goals: List[str]
    custom_vectors: List[CustomVector] = []     # NEW
```

### New step: `resolve_custom_apps`

```python
@listen(engineer_requirements)
def resolve_custom_apps(self):
    for vector in self.state.parsed_request.custom_vectors:
        flow = CustomAppFlow()
        result = flow.kickoff(inputs={"session_id": self.state.session_id, "vector": vector})
        self.state.resolved_custom_apps.append(result)

@listen(resolve_custom_apps)
def map_requirements(self):
    ...  # unchanged
```

### Sequencing

```
1. install_package atoms      (apache2, php, mysql-server, etc.)
2. create_user atoms
3. CUSTOM APP DEPLOYMENT      (setup_db.sh + app files + web server config)
4. Misconfiguration atoms     (SMB, file permissions, etc.)
5. Post-exploitation atoms    (SUID, cron, capabilities)
```

### Testing

```
CustomAppFlow.validate_end_to_end()   ← isolated: app works + goal achieved
        ↓ passes
GoEFlow.test_snippets() Layer 2       ← integrated: app still works with full box
```

Failure in CustomAppFlow = generation problem. Failure in GoEFlow = integration problem.

### Files to change
- `src/game_of_everything/main.py` — add `resolve_custom_apps` step
- `src/game_of_everything/models.py` — update `ParsedRequest`, add `CustomVector`, `ResolvedCustomApp`
- `src/game_of_everything/state.py` — add `resolved_custom_apps: List[ResolvedCustomApp]`

---

## Open Questions

1. **Synthesis for misconfig-only boxes** — Does `synthesize_scenario` add value or
   just latency when no custom app is involved? Consider making it conditional.

2. **Synthesis validation** — Should `engineer_requirements` sanity-check the scenario
   before parsing? A lightweight check could catch hallucinations early.

3. **Multiple custom vectors** — Two apps on one box means port allocation and DB
   isolation decisions. Defer until a concrete use case arises; synthesis would handle
   it naturally when the time comes.

4. **Attack goal discovery** — Goals are currently a static list passed in context.
   If the count grows past ~20, add RAG. Track this.
