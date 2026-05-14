# Game of Everything Rewrite

GoE v2 is a complete rewrite of the original Game of Everything, designed to be more flexible, extensible, and powerful. Instead of systems being described as individual sets of initial access measures and post-exploitation goals GoE v2 models scenarios as a directed graph, see [Entity Graph Model](goe_rewrite.md#entity-graph-model). 

## Flow

### **Environment Definition**: The environment architect defines a scenario using the Entity Graph Model, describing systems, entities, and their relationships.
```
  [design_systems]  →  [plan_entities]  →  [specify_entities]  →  [connect_edges]  →  [resolve]  →  [validate]
     sonnet              sonnet              haiku (parallel)        sonnet             haiku/code     code
```
0. Design Systems: Define the infrastructure (systems) that entities will inhabit.
1. Plan Entities: Outline the exploitable vulnerabilities (entities) at a high level.
2. Specify Entities: Flesh out entity details, including descriptions required/provided edges, and app specs if needed. Entities declare what they require and provide.
3. Connect Edges: Wire the entities together by defining realistic edges that satisfy their requirements, using the closed vocabulary of edge types.
4. Resolve: Generate concrete credentials, file paths, usernames, etc. for edge parameters. 
5. Validate: A simple graph walker ensures all entity requirements are satisfied by provided edges, and that the graph is well-formed.


### **Environment Construction**: Build the validated graph into a working environment.

#### Build Order
Topological sort of the entity graph — systems first, then entities in dependency order:
1. Provision systems (base OS, services, packages — deterministic, no LLM)
2. Build entities in topological order (entity only starts when all its `requires` edges are satisfied)
3. Entities on different systems parallelize; same-system entities are sequential

#### Per-Entity Build Loop

The Builder receives the `entity spec`, `resolved edge values`, and `relevant atoms`, then proceeds through two phases:

**1. Generation Phase**
Depending on whether an `app_spec` is provided:

*With `app_spec`:*
```text
  [plan_architecture]  →  [implement_source]
         opus                   sonnet
```

*Without `app_spec` (simple misconfigs):*
```text
  [generate_snippet]
        sonnet
```

**2. Deployment & Validation Phase**
```text
                                                        (pass) → [return_artifacts]
                                                       /
  [deploy]  →  [healthcheck]  →  [god_view_validate] --
    code           code                 code           \
      ^                                                 (fail)
      |------------- [fix_and_retry] <-----------------/
                          builder
```

#### Runtime Templates (Not LLM-Generated)
The LLM outputs app source + port number. Dependencies are parsed from source automatically. Runtime templates handle install, start, and healthcheck deterministically.
```yaml
express:
  base_image: goe-target-express
  setup_steps: ["npm init -y", "npm install ${deps}"]
  start_command: "node app.js"
  healthcheck: "curl -sf http://localhost:${port}/"
```

#### Testing
- **L2 first** (operator-view attack via procedure). If pass, entity is validated.
- **L1 as diagnostic only** — fires when L2 fails. God-view check (query DB, read files, inspect state) to tell the builder *why* it failed.
- **Chain test last** (multi-system only) — deploy full topology, walk edges end-to-end from operator perspective. Proves the kill chain works as a whole.

#### Builder Output (Per Entity)
Each builder produces:
- **Deploy artifact** — app source file(s) or bash config snippet
- **Attack procedure** — concrete, executable steps with assertions (dynamic, specific to what was generated)
- **Metadata** — port, dependencies, service name (consumed by runtime template and packaging)

#### Retry Semantics
- Max N attempts per entity (configurable, default 3)
- On failure: builder sees raw error output + god-view diagnostic
- **Implementation bug** (code doesn't run): retry Sonnet only, same plan
- **Design flaw** (wrong approach entirely): retry from Opus plan
- If all attempts exhausted: entity marked failed, build continues for remaining entities, final report shows what broke

#### Chain Test (Multi-System Only)
Runs after all entities pass individual validation:
1. Deploy full topology (all systems, all entities)
2. Execute attack procedures sequentially, following edge order from operator inward
3. Each step's output feeds the next (e.g., stolen creds used in SSH pivot)
4. First failure stops the chain — reports which edge broke and why

#### Final Packaging
Once all entities validated (and chain test passes if multi-system):
- **Single system**: concatenate deploy snippets in topological order → one `deploy.sh`
- **Multi-system**: per-system `deploy.sh` + `docker-compose.yml` defining network topology
- Post-processing: shebang injection, `set -e`, blank line normalization
- Output includes: deploy script(s), attack playbook (ordered procedures), README with topology summary

#### Builder vs. Deterministic Responsibilities

| Builder (LLM) | Deterministic (code) |
|---|---|
| App source code | Runtime install/setup |
| Misconfig snippets | Dependency parsing from source |
| Attack procedure | Healthcheck polling |
| Fix decisions on failure | Topological build ordering |
| | Container lifecycle |
| | Resolved credential/secret values |
| | Final script packaging |

