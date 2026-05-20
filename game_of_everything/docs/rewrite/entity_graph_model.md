# GoE v2 — Entity Graph Model

GoE v2 represents scenarios as a directed graph of **entities** (exploitable vulnerabilities) connected by **typed edges** (attacker capabilities), deployed onto **systems** (infrastructure).

## Core Objects

### System

Infrastructure that entities inhabit. Not exploitable — just a deployment target with an OS, services, and network identity.

```python
@dataclass
class System:
    id: str                         # e.g. "webserver", "db_server"
    os: str                         # "ubuntu_22.04"
    services: list[str]             # ["apache2", "mysql", "ssh"]
    network: NetworkConfig

@dataclass
class NetworkConfig:
    hostname: str                   # Docker/compose hostname
    exposed_ports: list[int]        # Ports reachable from operator
    internal_ports: list[int]       # Ports reachable from other systems only
```

### Entity

An exploitable vulnerability or misconfiguration living on a system. Each entity declares typed contracts: what incoming edges it requires to be reachable, and what outgoing edges a successful exploit produces.

An entity may produce **multiple outgoing edges** (fan-out). For example, a config file leak might provide both `creds_for(dbadmin, db_server)` and `creds_for(deploy, webserver)`.

```python
@dataclass
class Entity:
    id: str                         # Unique, snake_case
    description: str                # Natural language: what this vulnerability is
    system_id: str                  # Which System this lives on
    requires: list[Requirement]     # What must be true for this entity to be reachable
    provides: list[str]             # Edge IDs this entity produces on successful exploit
    app_spec: AppSpec | None        # If set, construction_crew generates a custom application
    atoms: list[str]                # Atom IDs for builder reference (optional)
```

### Requirement

A requirement is a reference to an edge that must exist and target this entity. Requirements use **AND semantics** — all listed requirements must be satisfied for the entity to be reachable.

```python
@dataclass
class Requirement:
    edge_id: str                    # ID of an edge that must target this entity
    optional: bool = False          # If true, entity is reachable without this (degrades capability)
```

**OR semantics (alternative paths)**: Modeled as separate entities with the same `provides` edges. If "get root via SUID" or "get root via cron hijack" are alternatives, create two entities that both provide `shell_as(root, host)`. The downstream entity requires that edge — either provider satisfies it. The builder builds both; the chain test uses whichever succeeds first.

### Edge

A typed, parameterized capability that an attacker gains. Edges are the fundamental unit of the graph — they connect entities and carry resolved values at runtime.

```python
@dataclass
class Edge:
    id: str                         # Unique, snake_case (e.g. "webapp_to_admin_token")
    from_entity: str                # Entity ID or "operator"
    to_entity: str                  # Entity ID
    type: EdgeType                  # From the closed vocabulary
    params: dict[str, ParamValue]   # Type-specific parameters
```

### ParamValue

Edge parameters have two resolution phases: structural (at plan time) and concrete (at build time). This separation is critical — see [Resolution Contract](#resolution-contract).

```python
@dataclass
class ParamValue:
    structural: str                 # What this param represents: "db_admin_password", "config_file_path"
    concrete: str | None = None     # Actual value, filled by builder post-build: "hunter2", "/app/.env"
```

### Operator

Special source node representing the external attacker. Not an entity — it has no requirements and provides only `network_reach` edges to initial access points.

Entities whose requirements consist solely of `network_reach` edges from operator are **initial access points**.

---

## Edge Type Vocabulary

| Type | Params | Meaning |
|------|--------|---------|
| `network_reach` | `host`, `port` | Can send packets to a service |
| `shell_as` | `user`, `host` | Interactive shell as a specific user |
| `creds_for` | `user`, `host`, `cred_type` | Known credentials (password, key, token) |
| `db_session` | `db_type`, `host`, `user` | Authenticated database connection |
| `file_read` | `path`, `host`, `as_user` | Can read a specific file |
| `file_write` | `path`, `host`, `as_user` | Can write to a specific file/directory |
| `code_exec` | `runtime`, `host`, `as_user` | Execute code within a runtime context |
| `token_for` | `service`, `host`, `scope` | Session cookie, JWT, API key |

### Param Definitions

- `host`: System hostname (must match a `system.network.hostname`)
- `port`: Integer, must be in system's `exposed_ports` or `internal_ports`
- `user`: Unix username or application-level username
- `cred_type`: One of `password`, `ssh_key`, `api_key`
- `as_user`: The identity executing the action (encodes privilege level)
- `db_type`: One of `mysql`, `postgresql`, `mongodb`, `redis`
- `runtime`: One of `python`, `node`, `php`, `bash`
- `service`: Application identifier (e.g. `admin_panel`, `api`)
- `scope`: Permission scope of the token (e.g. `admin`, `read_only`)
- `path`: Filesystem path — structural value is descriptive ("app config file"), concrete value is the actual path (`/app/.env`)

### Identity via `as_user`

Rather than a separate identity node layer, identity is encoded as a param on edges that involve executing actions. The `as_user` param on `file_read`, `file_write`, and `code_exec` distinguishes "www-data reads /etc/shadow" (fails) from "root reads /etc/shadow" (succeeds). Shell upgrades are modeled as a chain: `shell_as(www-data, host)` → privesc entity → `shell_as(root, host)`.

### Modeling Non-Obvious Scenarios

**SSRF**: The application makes the request, not the operator. Model as an entity that requires `network_reach(host, 80)` from operator and provides `network_reach(internal_host, internal_port)` — the outgoing edge's implicit identity is the application itself (encoded in the entity description, not the edge type).

**Information disclosure**: Model as a pass-through entity. Requires `network_reach`, provides `creds_for` or `file_read` depending on what's leaked. Enumeration/recon that doesn't directly yield a typed capability is modeled as part of a larger entity's internal procedure (e.g., "enumerate users then brute-force" is one entity that provides `creds_for`).

**Ephemeral/conditional access**: Model as a normal entity with a note in the description. The builder handles timing constraints in the implementation (e.g., race condition exploit in the procedure). The graph doesn't model time — it models reachability.

---

## Resolution Contract

Edge params are resolved in two phases:

### Phase 1: Structural Resolution (Plan Time)

During the `resolve` step, params get **structural identifiers** — human-readable names that describe *what* the value represents without committing to a specific value.

```yaml
edge: webapp_to_config_read
params:
  path: { structural: "app_config_file", concrete: null }
  host: { structural: "webserver", concrete: "webserver" }  # hostnames are known at plan time
  as_user: { structural: "webapp_service_user", concrete: null }
```

Static validation operates on structural values: "does every requirement have a provider with matching edge type and structural param compatibility?"

### Phase 2: Concrete Resolution (Build Time)

After each builder completes, it **reports back** the concrete values it used:

```yaml
edge: webapp_to_config_read
params:
  path: { structural: "app_config_file", concrete: "/app/.env" }
  host: { structural: "webserver", concrete: "webserver" }
  as_user: { structural: "webapp_service_user", concrete: "www-data" }
```

Downstream builders receive these concrete values as input. This means:
- Builders have full design freedom (put the config file wherever makes sense)
- Downstream entities use the *actual* values (no guessing)
- Build order matters: a consuming entity cannot start until its providing entity has finished and reported values

### What This Means for Build Ordering

Topological sort already enforces "provider builds before consumer." The resolution contract adds: **after provider builds, propagate its concrete values to all consuming entities before they start.**

```
Entity A builds → reports concrete values → 
    values propagated to Edge(A→B).params → 
        Entity B receives concrete values as input → Entity B builds
```

---

## Custom Apps

Entities that need a generated custom application carry an `app_spec`:

```python
@dataclass
class AppSpec:
    runtime: str                    # "express" | "flask" | "apache_php"
    vulnerabilities: list[str]      # Vuln atom IDs or descriptions
    goal: str                       # What successful exploitation achieves (maps to provides edges)
```

The construction_crew receives the `app_spec` plus all resolved incoming edge values and generates:
- Application source file(s)
- Attack procedure (see Procedure DSL)
- Concrete param values for all outgoing edges

---

## Static Validation (Pre-Build)

Runs in milliseconds after the graph is planned. Checks:

1. **Edge coverage**: Every entity's `requires` list references an edge that has `to_entity` matching this entity
2. **Type compatibility**: The edge type's params are structurally compatible with what consumer expects
3. **Reachability**: Every entity is reachable from operator via a path of edges (no orphans)
4. **Fan-out consistency**: If multiple entities require the same edge, the providing entity's builder must produce values usable by all consumers
5. **System reference validity**: Every `entity.system_id` and every `host` param references an existing system
6. **No cycles**: The entity dependency graph is a DAG (edges form a topological order)
7. **Initial access exists**: At least one entity requires only `network_reach` from operator

Validation operates on **structural** param values only. It cannot verify that concrete values will be correct — that's the builder's job and the test's verification.

---

## Example

```yaml
systems:
  - id: webserver
    os: ubuntu_22.04
    services: [nginx, node, mysql]
    network:
      hostname: webserver
      exposed_ports: [80]
      internal_ports: [3306]

  - id: db_server
    os: ubuntu_22.04
    services: [ssh, postgresql]
    network:
      hostname: db_server
      exposed_ports: []
      internal_ports: [22, 5432]

entities:
  - id: vuln_webapp
    description: "E-commerce app with stored XSS in product reviews that steals admin session cookies via an admin bot"
    system_id: webserver
    requires:
      - edge_id: operator_to_webapp
    provides: [webapp_to_admin_token]
    app_spec:
      runtime: express
      vulnerabilities: [xss_stored]
      goal: "steal admin session cookie via stored XSS triggering on admin bot visit"

  - id: admin_panel_rce
    description: "Admin panel with unrestricted file upload allowing PHP webshell execution"
    system_id: webserver
    requires:
      - edge_id: webapp_to_admin_token
    provides: [admin_rce_to_shell]
    app_spec:
      runtime: apache_php
      vulnerabilities: [file_upload_bypass]
      goal: "upload and execute a webshell to gain code execution as www-data"

  - id: db_creds_in_config
    description: "Database credentials stored in plaintext config file readable by www-data"
    system_id: webserver
    requires:
      - edge_id: admin_rce_to_shell
    provides: [config_to_db_creds]
    atoms: [exposed_env_vars]

  - id: ssh_reuse
    description: "PostgreSQL admin reuses the same password for SSH access"
    system_id: db_server
    requires:
      - edge_id: config_to_db_creds
    provides: [db_creds_to_ssh]
    atoms: [weak_service_password]

edges:
  - id: operator_to_webapp
    from_entity: operator
    to_entity: vuln_webapp
    type: network_reach
    params:
      host: { structural: webserver, concrete: webserver }
      port: { structural: http_port, concrete: 80 }

  - id: webapp_to_admin_token
    from_entity: vuln_webapp
    to_entity: admin_panel_rce
    type: token_for
    params:
      service: { structural: admin_panel, concrete: null }
      host: { structural: webserver, concrete: webserver }
      scope: { structural: admin, concrete: null }

  - id: admin_rce_to_shell
    from_entity: admin_panel_rce
    to_entity: db_creds_in_config
    type: shell_as
    params:
      user: { structural: webapp_service_user, concrete: null }
      host: { structural: webserver, concrete: webserver }

  - id: config_to_db_creds
    from_entity: db_creds_in_config
    to_entity: ssh_reuse
    type: creds_for
    params:
      user: { structural: db_admin_user, concrete: null }
      host: { structural: db_server, concrete: db_server }
      cred_type: { structural: password, concrete: password }

  - id: db_creds_to_ssh
    from_entity: ssh_reuse
    to_entity: null  # terminal edge — final objective
    type: shell_as
    params:
      user: { structural: db_admin_user, concrete: null }
      host: { structural: db_server, concrete: db_server }
```

### Graph Visualization

```
                         [webserver]                              [db_server]
                 ┌────────────────────────────────┐          ┌─────────────────┐
                 │                                │          │                 │
 ┌──────────┐    │  ┌─────────────┐               │          │                 │
 │ OPERATOR │────┼─>│ vuln_webapp │               │          │                 │
 └──────────┘    │  └──────┬──────┘               │          │                 │
  network_reach  │         │ token_for            │          │                 │
  :80            │         v                      │          │                 │
                 │  ┌────────────────┐            │          │                 │
                 │  │admin_panel_rce │            │          │                 │
                 │  └───────┬────────┘            │          │                 │
                 │          │ shell_as(www-data)  │          │                 │
                 │          v                     │          │                 │
                 │  ┌────────────────┐            │          │                 │
                 │  │db_creds_in_conf│            │          │                 │
                 │  └───────┬────────┘            │          │                 │
                 │          │                     │          │                 │
                 └──────────┼─────────────────────┘          │                 │
                            │ creds_for(dbadmin)             │  ┌───────────┐  │
                            └────────────────────────────────┼─>│ ssh_reuse │  │
                                                             │  └─────┬─────┘  │
                                                             │        │        │
                                                             │        v        │
                                                             │  shell_as       │
                                                             │  (dbadmin)      │
                                                             │  [OBJECTIVE]    │
                                                             └─────────────────┘
```
