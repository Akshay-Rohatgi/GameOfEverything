# Multi-Box Topology Design v2

## Problem Statement

GoE currently generates a single deployment script targeting one Ubuntu box. Real-world attack scenarios involve multiple machines — a DMZ web server, an internal database, a file server — connected by lateral movement paths. To generate CTF-grade infrastructure for DEFCON, we need the system to produce multi-machine topologies with cross-box attack chains.

## Design Principles

1. **Single-box is a topology of one.** No separate code paths. A single-box request produces a `NetworkTopology` with one `BoxDefinition` and zero pivots.
2. **Secrets are resolved at synthesis, not downstream.** The synthesis agent assigns concrete values to all shared credentials. No template variables, no late binding. A `SharedSecret` model documents the linkage for traceability and validation.
3. **Existing step functions are unchanged.** The per-box pipeline reuses every `run_*` function as-is. Multi-box orchestration constructs a temporary `GoEState` per box, runs the pipeline against it, and collects results. Zero refactor of step internals.
4. **Two-pass synthesis.** Pass 1 decides topology structure (boxes, roles, pivots). Pass 2 fills in per-box details (scopes, vectors, secrets). This keeps each LLM call focused and debuggable.
5. **Chain testing is logical, not network-enforced (MVP).** All containers share a flat Docker network. The attacker can reach any box, but needs credentials from previous steps. Network segmentation is a post-DEFCON enhancement.

---

## Data Models

### Core Topology Models

```python
class SharedSecret(BaseModel):
    """A concrete credential shared across boxes in the topology.

    The synthesis agent resolves ALL values. This model exists for:
    (a) traceability — documenting which boxes share which secret
    (b) validation — confirming the value appears in both boxes' scopes
    (c) chain test probe generation — programmatic pivot commands
    """
    key: str                        # "pivot_web_to_db"
    value: str                      # "Summer2024!"
    description: str                # Human-readable: what this secret is
    source_box: str                 # box_id where attacker discovers this
    target_box: str                 # box_id where this secret grants access
    target_user: str                # Username on the target: "dbadmin"
    access_method: str              # "ssh", "web_login", "smb", "ftp", "mysql"


class BoxDefinition(BaseModel):
    """One machine in the topology."""
    box_id: str                     # Unique: "webserver", "db-server"
    hostname: str                   # Network hostname: "web01"
    role: str                       # Narrative: "Public-facing web server"
    os: str = "ubuntu:22.04"        # Base image

    # Per-box pipeline inputs (same fields as SynthesizedScenario per box)
    misconfig_scope: str
    custom_app_scope: Optional[str] = None
    custom_vectors: List[CustomVector] = []

    # What services this box exposes (for docker-compose ports + README)
    services: List[str] = []        # ["ssh:22", "http:80", "mysql:3306"]


class PivotLink(BaseModel):
    """Directed edge: how the attacker moves from one box to the next."""
    from_box: str
    to_box: str
    method: str                     # "credential_reuse", "ssh_key_reuse", "tunnel"
    secret_ref: Optional[str] = None  # Key into SharedSecret
    description: str                # "SQLi on web app → extract dbadmin:Summer2024! → SSH to db-server"


class ChainProbe(BaseModel):
    """One step in the Layer 3 attack chain validation.

    For MVP, these are generated deterministically from PivotLinks + SharedSecrets.
    No LLM needed for simple credential-reuse pivots.
    """
    step: int                       # Ordinal: 1, 2, 3...
    from_container: str             # "attacker" or a box_id
    target_hostname: str            # Hostname to attack
    command: str                    # Bash command to execute
    success_pattern: str            # Regex that must appear in stdout for pass


class NetworkTopology(BaseModel):
    """Full multi-box scenario — the single synthesis output for all requests."""
    scenario_name: str              # "Corporate DMZ Breach"
    narrative: str                  # Full scenario description
    attack_narrative: str           # End-to-end attacker path across ALL boxes
    entry_point: str                # box_id the attacker hits first
    boxes: List[BoxDefinition]
    pivots: List[PivotLink]
    shared_secrets: List[SharedSecret]
    chain_probes: List[ChainProbe] = []   # Generated post-synthesis, not by LLM

    # Synthesis metadata
    shared_resources: List[str]
    explicit_decisions: List[str]
```

### Single-Box Backwards Compatibility

A request like "vulnerable server with weak SSH and SUID privesc" produces:

```python
NetworkTopology(
    scenario_name="Vulnerable SSH Server",
    entry_point="target",
    boxes=[BoxDefinition(
        box_id="target",
        hostname="target",
        misconfig_scope="SSH with user admin:admin123, /usr/bin/find has SUID bit",
        custom_vectors=[],
        services=["ssh:22"],
    )],
    pivots=[],
    shared_secrets=[],
    chain_probes=[],
    ...
)
```

No pivots, no chain probes, no docker-compose needed. The flow detects `len(topology.boxes) == 1` and runs the existing single-box path with zero behavioral change.

### SynthesizedScenario Migration

`SynthesizedScenario` is **not immediately retired**. Instead:

- Phase 1: `NetworkTopology` is added alongside it. A utility converts between them.
- Phase 2: Synthesis agent starts producing `NetworkTopology` directly.
- Phase 3: `SynthesizedScenario` is removed once all code uses the new model.

The converter (used during migration):

```python
def topology_to_single_box_scenario(topo: NetworkTopology) -> SynthesizedScenario:
    """Extract the single box's fields into a SynthesizedScenario for existing steps."""
    box = topo.boxes[0]
    return SynthesizedScenario(
        narrative=topo.narrative,
        attack_narrative=topo.attack_narrative,
        shared_resources=topo.shared_resources,
        explicit_decisions=topo.explicit_decisions,
        misconfig_scope=box.misconfig_scope,
        custom_app_scope=box.custom_app_scope,
        custom_vectors=box.custom_vectors,
    )
```

---

## Two-Pass Synthesis

The current synthesis agent does a lot: interprets the prompt, resolves implicit decisions, writes scopes, generates custom vectors. For multi-box, asking one LLM call to also design a topology, coordinate secrets across boxes, and maintain consistency is too much.

### Pass 1: Topology Architect

**Agent**: `topology_architect_agent` (Opus 4.6)
**Input**: User prompt + available atoms list
**Output**: `TopologyBlueprint` (lightweight, no per-box details yet)

```python
class TopologyBlueprint(BaseModel):
    """Structural skeleton — what boxes exist and how they connect."""
    scenario_name: str
    box_roles: List[dict]           # [{"box_id": "webserver", "role": "...", "os": "ubuntu:22.04"}]
    pivot_sketch: List[dict]        # [{"from": "webserver", "to": "db-server", "method": "credential_reuse", "via": "SQLi extracts DB creds"}]
    shared_secret_sketch: List[dict]  # [{"key": "pivot_cred", "from_box": "webserver", "to_box": "db-server", "type": "ssh_password"}]
    narrative_sketch: str           # High-level attack path
```

The architect prompt is focused: "Given this request, how many machines are needed? What role does each play? How does the attacker move between them?" No concrete credentials, no detailed scopes — just structure.

For single-box requests, this pass produces one box with no pivots. Fast, cheap.

### Pass 2: Scenario Synthesizer (Per-Box + Cross-Box)

**Agent**: `scenario_synthesis_agent` (existing, upgraded)
**Input**: `TopologyBlueprint` + available atoms + user prompt
**Output**: `NetworkTopology` (complete, with all concrete values)

This is the existing synthesis agent, extended to:
- Produce per-box `misconfig_scope` and `custom_vectors` for each box in the blueprint
- Assign concrete values to shared secrets (usernames, passwords)
- Ensure the same credential value appears in both the source box's seed data and the target box's misconfig scope
- Write the full cross-box `attack_narrative`

For single-box requests, this pass is essentially the current synthesis step — same prompt, same output complexity.

### Post-Synthesis Validation

Programmatic checks run after synthesis, before the pipeline starts:

```python
def validate_topology(topo: NetworkTopology) -> List[str]:
    errors = []

    # Every pivot references valid box_ids
    box_ids = {b.box_id for b in topo.boxes}
    for pivot in topo.pivots:
        if pivot.from_box not in box_ids:
            errors.append(f"Pivot references unknown from_box: {pivot.from_box}")
        if pivot.to_box not in box_ids:
            errors.append(f"Pivot references unknown to_box: {pivot.to_box}")

    # Every shared secret value appears in both boxes' scopes
    for secret in topo.shared_secrets:
        src = next((b for b in topo.boxes if b.box_id == secret.source_box), None)
        tgt = next((b for b in topo.boxes if b.box_id == secret.target_box), None)
        if src and secret.value not in _all_scope_text(src):
            errors.append(f"Secret '{secret.key}' value not found in source box '{src.box_id}' scope")
        if tgt and secret.value not in _all_scope_text(tgt):
            errors.append(f"Secret '{secret.key}' value not found in target box '{tgt.box_id}' scope")

    # Entry point exists
    if topo.entry_point not in box_ids:
        errors.append(f"Entry point '{topo.entry_point}' is not a valid box_id")

    # Attack graph is connected (every box is reachable from entry_point)
    reachable = _bfs_reachable(topo.entry_point, topo.pivots)
    unreachable = box_ids - reachable
    if unreachable:
        errors.append(f"Boxes not reachable from entry point: {unreachable}")

    return errors
```

If validation fails, the synthesis agent is re-prompted with the specific errors (up to 2 retries).

---

## Per-Box Pipeline: Zero Refactor

### The Key Insight

Every existing `run_*` step function takes `GoEState` as its first argument and reads/writes specific fields on it. We don't need a `BoxFlow` sub-class or a `BoxState` model. We construct a **temporary `GoEState`** per box, run the existing steps against it, and collect results.

```python
def _make_virtual_state(
    box: BoxDefinition,
    raw_request: str,
) -> GoEState:
    """Construct a GoEState scoped to a single box.

    Downstream steps see a normal GoEState and don't know
    they're operating on one box of a multi-box topology.
    """
    return GoEState(
        raw_request=raw_request,
        synthesized_scenario=SynthesizedScenario(
            narrative=box.role,
            attack_narrative="",
            shared_resources=[],
            explicit_decisions=[],
            misconfig_scope=box.misconfig_scope,
            custom_app_scope=box.custom_app_scope,
            custom_vectors=box.custom_vectors,
        ),
    )
```

### Per-Box Pipeline Execution

```python
def run_box_pipeline(
    box: BoxDefinition,
    raw_request: str,
    agents_config: dict,
    tasks_config: dict,
) -> GoEState:
    """Run the full atom pipeline for a single box. Returns the populated state."""
    state = _make_virtual_state(box, raw_request)

    # These are the EXISTING step functions — zero changes
    run_resolve_custom_apps(state)
    run_engineer_requirements(state, agents_config, tasks_config)
    run_generate_implementation(state, agents_config, tasks_config)
    run_test_snippets(state, agents_config, tasks_config)

    return state
```

That's it. No new Flow subclass, no state model refactor. Each box gets a fresh `GoEState`, the existing pipeline populates it, and we collect the results.

### Why Not BoxFlow?

crewAI's `Flow` class manages an event bus, state persistence, and `@listen` decorators. Creating N `Flow` instances in a loop:
- Fights the framework (designed for one flow per process)
- Adds complexity for zero benefit (we're calling steps sequentially anyway)
- Makes debugging harder (N interleaved event buses)

Calling `run_*` functions directly in a loop is simpler, more debuggable, and uses the framework as intended.

---

## TestEnvironmentTool: Scoped Container Names

Per-box testing needs isolated Docker environments. The current `TestEnvironmentTool` uses hardcoded names (`goe_target`, `goe_attacker`). Two changes:

### 1. Parameterized Names

```python
class TestEnvironmentTool:
    def __init__(self, scope: str = ""):
        """scope: optional prefix for container/network names (e.g., box_id)."""
        self._scope = scope
        self.client = docker.from_env()
        ...

    @property
    def _prefix(self) -> str:
        return f"goe_{self._scope}_" if self._scope else "goe_"

    @property
    def target_name(self) -> str:
        return f"{self._prefix}target"
```

For single-box: `TestEnvironmentTool()` → `goe_target`, `goe_attacker` (unchanged).
For multi-box: `TestEnvironmentTool(scope="webserver")` → `goe_webserver_target`, `goe_webserver_attacker`.

### 2. Sequential Per-Box Testing

Boxes are tested one at a time: setup → L1 → L2 → teardown → next box. This avoids resource contention and is simple. Parallel per-box testing is a future optimization.

---

## Chain Testing (Layer 3)

After all boxes pass individual L1+L2 testing, Layer 3 validates the full attack chain.

### ChainTestEnvironment

```python
class ChainTestEnvironment:
    """Stands up all boxes + attacker on a shared network for chain validation."""

    def setup(self, topology: NetworkTopology, deploy_scripts: Dict[str, str]):
        self.client = docker.from_env()

        # Single flat network for MVP
        self.network = self.client.networks.create("goe_chain_net", driver="bridge")

        # Start each box, apply its deploy script
        self.containers: Dict[str, Container] = {}
        for box in topology.boxes:
            container = self.client.containers.run(
                box.os,
                command="sleep infinity",
                name=f"goe_chain_{box.box_id}",
                hostname=box.hostname,
                network="goe_chain_net",
                detach=True,
            )
            # Bootstrap + apply deploy script inside container
            self._bootstrap(container)
            self._apply_script(container, deploy_scripts[box.box_id])
            self.containers[box.box_id] = container

        # Attacker container
        self.attacker = self.client.containers.run(
            ATTACKER_IMAGE_TAG,
            command="sleep infinity",
            name="goe_chain_attacker",
            hostname="attacker",
            network="goe_chain_net",
            detach=True,
        )

    def exec_on(self, container_id: str, command: str) -> Tuple[int, str, str]:
        """Execute command on a named container ('attacker' or a box_id)."""
        c = self.attacker if container_id == "attacker" else self.containers[container_id]
        exit_code, output = c.exec_run(["bash", "-c", command], demux=True)
        ...
```

### Deterministic Probe Generation

For MVP, chain probes are generated programmatically from `PivotLink` + `SharedSecret`. No LLM call needed for credential-reuse pivots.

```python
PIVOT_TEMPLATES = {
    "credential_reuse": {
        "ssh": "sshpass -p '{value}' ssh -o StrictHostKeyChecking=no {user}@{hostname} 'whoami && hostname'",
        "mysql": "mysql -h {hostname} -u {user} -p'{value}' -e 'SELECT 1'",
        "ftp": "curl -s ftp://{user}:{value}@{hostname}/",
        "smb": "smbclient -L //{hostname} -U {user}%{value} -N",
        "web_login": "curl -s -d 'username={user}&password={value}' http://{hostname}/login",
    },
    "ssh_key_reuse": {
        "ssh": "ssh -i /tmp/stolen_key_{key} -o StrictHostKeyChecking=no {user}@{hostname} 'whoami && hostname'",
    },
}

def generate_chain_probes(topology: NetworkTopology) -> List[ChainProbe]:
    """Build chain test probes from topology structure. No LLM needed."""
    probes = []
    # Step 0: Entry-point exploit (reuse per-box attack_snippet from L2)
    # Steps 1..N: Pivot probes from PivotLinks
    for i, pivot in enumerate(topology.pivots):
        secret = next((s for s in topology.shared_secrets if s.key == pivot.secret_ref), None)
        if not secret:
            continue
        target_box = next(b for b in topology.boxes if b.box_id == pivot.to_box)
        template = PIVOT_TEMPLATES.get(pivot.method, {}).get(secret.access_method)
        if not template:
            continue  # Unknown pivot type — skip or flag for manual probe
        command = template.format(
            value=secret.value,
            user=secret.target_user,
            hostname=target_box.hostname,
            key=secret.key,
        )
        probes.append(ChainProbe(
            step=i + 1,
            from_container="attacker",  # MVP: all probes from attacker (flat network)
            target_hostname=target_box.hostname,
            command=command,
            success_pattern=target_box.hostname,  # Expect hostname in output
        ))
    return probes
```

### Chain Test Execution

```python
def run_chain_test(
    env: ChainTestEnvironment,
    topology: NetworkTopology,
    agents_config: dict,
    tasks_config: dict,
) -> List[ChainTestResult]:
    results = []

    for probe in topology.chain_probes:
        exit_code, stdout, stderr = env.exec_on(probe.from_container, probe.command)

        passed = bool(re.search(probe.success_pattern, stdout))
        results.append(ChainTestResult(
            step=probe.step,
            command=probe.command,
            passed=passed,
            stdout=stdout[:2000],
            stderr=stderr[:2000],
        ))

        if not passed:
            # Chain is broken — remaining probes depend on this one
            for remaining in topology.chain_probes[probe.step:]:
                results.append(ChainTestResult(
                    step=remaining.step,
                    passed=False,
                    stdout="",
                    stderr="Skipped: previous chain step failed",
                ))
            break

    return results
```

Key detail: **chain failure is cascading**. If the pivot from box A → box B fails, all downstream probes are skipped. This prevents confusing failures and makes the break point obvious.

---

## Failure Handling

### Per-Box Failure

If a box's per-box pipeline fails (snippets don't validate), the box is marked as failed. Downstream boxes that depend on it via pivots are flagged:

```python
def run_all_boxes(topology, ...):
    box_results: Dict[str, GoEState] = {}
    failed_boxes: Set[str] = set()

    for box in topological_order(topology):
        # Check if any upstream box (via pivots) failed
        upstream_failed = any(
            p.from_box in failed_boxes
            for p in topology.pivots if p.to_box == box.box_id
        )
        if upstream_failed:
            rich.print(f"[yellow]Skipping {box.box_id}: upstream box failed[/yellow]")
            failed_boxes.add(box.box_id)
            continue

        state = run_box_pipeline(box, ...)
        box_results[box.box_id] = state

        # Check if any snippets failed validation
        if not _box_has_valid_output(state):
            failed_boxes.add(box.box_id)
            rich.print(f"[red]{box.box_id} failed validation[/red]")

    return box_results, failed_boxes
```

### Chain Test Failure

Chain test failures produce diagnostic output but don't invalidate per-box scripts. The per-box scripts are individually valid — the chain failure means the cross-box pivot doesn't work as intended. The output includes both the per-box scripts and a diagnostic report.

---

## Output Format

### Directory Structure

```
output/<timestamp>_<scenario_name>/
  webserver_deploy.sh               # Per-box deploy script
  db-server_deploy.sh
  file-server_deploy.sh
  docker-compose.yml                # Orchestration
  playbook.json                     # Machine-readable attack chain
  README.md                         # Human-readable guide
```

### Docker-Compose: Runtime Deployment (Not Build-Time)

v1 proposed `RUN /deploy.sh` in a Dockerfile. This is broken — services started during `RUN` don't persist to runtime. v2 uses runtime deployment: the entrypoint runs the deploy script, then keeps the container alive.

```yaml
# Auto-generated by Game of Everything
# Scenario: Corporate DMZ Breach

networks:
  goe_net:
    driver: bridge

services:
  webserver:
    image: ubuntu:22.04
    hostname: web01
    networks:
      - goe_net
    ports:
      - "8080:80"
      - "2222:22"
    volumes:
      - ./webserver_deploy.sh:/opt/deploy.sh:ro
    entrypoint: ["bash", "-c", "chmod +x /opt/deploy.sh && /opt/deploy.sh && exec sleep infinity"]

  db-server:
    image: ubuntu:22.04
    hostname: db01
    networks:
      - goe_net
    volumes:
      - ./db-server_deploy.sh:/opt/deploy.sh:ro
    entrypoint: ["bash", "-c", "chmod +x /opt/deploy.sh && /opt/deploy.sh && exec sleep infinity"]

  file-server:
    image: ubuntu:22.04
    hostname: fs01
    networks:
      - goe_net
    volumes:
      - ./file-server_deploy.sh:/opt/deploy.sh:ro
    entrypoint: ["bash", "-c", "chmod +x /opt/deploy.sh && /opt/deploy.sh && exec sleep infinity"]
```

**Why runtime, not build-time?**
- Services (SSH, Apache, Flask) must be running at container runtime, not build time
- `apt-get install` runs at startup — takes ~2-3 min per box, but CTF environments run for hours so this is acceptable
- Deploy scripts work as-is: `service ssh start`, `nohup flask run &`, etc. all work in entrypoint context
- No script splitting into "configure" vs "start" phases — keep it simple

**Post-DEFCON optimization**: Pre-build images using `docker build` with a custom entrypoint that separates install from service start. Cuts startup from minutes to seconds.

### Playbook (Machine-Readable Attack Chain)

```json
{
  "scenario_name": "Corporate DMZ Breach",
  "entry_point": "webserver",
  "attack_chain": [
    {
      "step": 0,
      "box": "webserver",
      "action": "SQLi on /login endpoint extracts credentials from users table",
      "artifact": "dbadmin:Summer2024!",
      "tool": "sqlmap or manual UNION injection"
    },
    {
      "step": 1,
      "box": "db-server",
      "action": "SSH login with reused credentials",
      "command": "ssh dbadmin@db01",
      "artifact": "SSH private key at /home/dbadmin/.ssh/id_rsa"
    },
    {
      "step": 2,
      "box": "file-server",
      "action": "SSH with stolen key, then SUID find for root",
      "command": "ssh -i id_rsa dbadmin@fs01",
      "privesc": "find / -exec /bin/bash -p \\;"
    }
  ],
  "shared_secrets": [
    {"key": "pivot_web_to_db", "value": "Summer2024!", "user": "dbadmin"},
    {"key": "pivot_db_to_fs", "type": "ssh_key"}
  ],
  "flags": {}
}
```

This structure supports future CTFd integration — a flag registration script can read `playbook.json` and create challenges.

---

## Flow Architecture (Final)

```
                    ┌─────────────────────────┐
                    │     User Prompt          │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Pass 1: Topology        │  Opus 4.6
                    │  Architect               │  "How many boxes? What roles?
                    │  → TopologyBlueprint     │   How do they connect?"
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Pass 2: Scenario        │  Opus 4.6
                    │  Synthesizer             │  "Fill in concrete values,
                    │  → NetworkTopology       │   scopes, secrets"
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Validate Topology       │  Programmatic
                    │  (secrets, graph, refs)  │  Retry synthesis on failure
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Generate Chain Probes   │  Programmatic
                    │  (from pivots + secrets) │  No LLM needed
                    └────────────┬────────────┘
                                 │
               ┌─────────────────┼─────────────────┐
               │                 │                 │
    ┌──────────▼──┐   ┌─────────▼───┐   ┌─────────▼───┐
    │ Box Pipeline │   │ Box Pipeline │   │ Box Pipeline │
    │  webserver   │   │  db-server   │   │ file-server  │
    │              │   │              │   │              │
    │ custom_apps  │   │ custom_apps  │   │ custom_apps  │
    │ engineer_req │   │ engineer_req │   │ engineer_req │
    │ generate     │   │ generate     │   │ generate     │
    │ L1+L2 test   │   │ L1+L2 test   │   │ L1+L2 test   │
    │ finalize_box │   │ finalize_box │   │ finalize_box │
    └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
           │                  │                  │
           └─────────────┬────┘──────────────────┘
                         │
              ┌──────────▼──────────┐
              │  Layer 3: Chain     │  All boxes on shared network
              │  Test               │  Run probes in order
              │  (skip if 1 box)   │  Cascade on failure
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  Finalize Topology  │  Per-box scripts
              │                     │  docker-compose.yml
              │                     │  playbook.json
              │                     │  README.md
              └─────────────────────┘
```

### GoEFlow Implementation

```python
class GoEFlow(Flow[GoEState]):

    @start()
    def synthesize_topology(self):
        """Two-pass: architect → synthesizer → validate → generate probes."""
        blueprint = run_topology_architect(self.state, ...)
        topology = run_scenario_synthesizer(self.state, blueprint, ...)
        errors = validate_topology(topology)
        if errors:
            topology = run_scenario_synthesizer(self.state, blueprint, ..., errors=errors)
        topology.chain_probes = generate_chain_probes(topology)
        self.state.topology = topology

    @listen(synthesize_topology)
    def run_box_pipelines(self):
        """Run per-box pipeline for each box. Sequential, topological order."""
        for box in self._topological_order():
            state = run_box_pipeline(box, self.state.raw_request, ...)
            self.state.box_states[box.box_id] = state
            self.state.deploy_scripts[box.box_id] = state.final_script

    @listen(run_box_pipelines)
    def test_chain(self):
        """Layer 3: validate cross-box attack chain. Skip for single-box."""
        if len(self.state.topology.pivots) == 0:
            return
        env = ChainTestEnvironment()
        try:
            env.setup(self.state.topology, self.state.deploy_scripts)
            self.state.chain_test_results = run_chain_test(env, self.state.topology, ...)
        finally:
            env.teardown()

    @listen(test_chain)
    def finalize_topology(self):
        """Write per-box scripts, docker-compose, playbook, README."""
        run_finalize_topology(self.state)
```

---

## Implementation Phases

### Phase 1: Models + Migration Scaffolding
**Goal**: Add new models, keep existing pipeline working identically.

- Add `NetworkTopology`, `BoxDefinition`, `PivotLink`, `SharedSecret`, `ChainProbe` to `models.py`
- Add `topology: Optional[NetworkTopology]` to `GoEState` alongside existing `synthesized_scenario`
- Write `topology_to_single_box_scenario()` converter
- Update `synthesize_scenario` to produce `NetworkTopology` for single-box requests, convert to `SynthesizedScenario` for downstream steps
- **Test**: Existing single-box prompts produce identical output

### Phase 2: Two-Pass Synthesis
**Goal**: Multi-box requests produce valid topologies.

- Add `topology_architect_agent` + `topology_architect_task` to agents.yaml / tasks.yaml
- Implement `run_topology_architect()` → `TopologyBlueprint`
- Upgrade `run_scenario_synthesizer()` to take blueprint + produce `NetworkTopology`
- Implement `validate_topology()` with retry
- Implement `generate_chain_probes()` for credential-reuse pivots
- **Test**: Multi-box prompts produce valid `NetworkTopology` with consistent secrets

### Phase 3: Per-Box Pipeline Loop
**Goal**: Each box runs through the existing pipeline.

- Implement `_make_virtual_state()` and `run_box_pipeline()`
- Parameterize `TestEnvironmentTool` with `scope` for container naming
- Update `GoEFlow` to loop over boxes
- Implement failure cascading (skip downstream boxes on upstream failure)
- **Test**: 2-box prompts produce two valid deploy scripts

### Phase 4: Output Package
**Goal**: Produce a deployable package.

- Implement `finalize_topology()` — writes per-box scripts + docker-compose.yml
- Generate `playbook.json` from topology structure
- Generate `README.md` from attack narrative + shared secrets
- **Test**: `docker-compose up` boots a working multi-box environment

### Phase 5: Chain Testing (Layer 3)
**Goal**: Validate the cross-box attack chain.

- Implement `ChainTestEnvironment` (setup, teardown, exec_on)
- Implement `run_chain_test()` with cascading failure
- Wire into `GoEFlow.test_chain()`
- **Test**: Chain probes validate credential-reuse pivots between boxes

### Post-DEFCON Enhancements
- Network segmentation (per-segment Docker networks, firewall rules)
- Pre-built Docker images for faster startup
- Complex pivot types (tunnels, port forwards, proxychains)
- CTFd flag integration (read playbook.json, create challenges)
- Parallel per-box pipeline execution
- Windows box support (via Vagrant/cloud VMs)

---

## Example: 3-Box Corporate Breach

**User prompt**: "A corporate network with a vulnerable web application on the DMZ, a database server on the internal network with reused credentials, and a file server with a SUID privilege escalation path"

### Pass 1 Output (TopologyBlueprint)

```python
TopologyBlueprint(
    scenario_name="Corporate DMZ Breach",
    box_roles=[
        {"box_id": "webserver", "role": "DMZ web server with vulnerable app", "os": "ubuntu:22.04"},
        {"box_id": "db-server", "role": "Internal database server", "os": "ubuntu:22.04"},
        {"box_id": "file-server", "role": "Internal file server with privesc path", "os": "ubuntu:22.04"},
    ],
    pivot_sketch=[
        {"from": "webserver", "to": "db-server", "method": "credential_reuse", "via": "Creds extracted from web app DB"},
        {"from": "db-server", "to": "file-server", "method": "ssh_key_reuse", "via": "SSH key found in user home"},
    ],
    shared_secret_sketch=[
        {"key": "pivot_web_to_db", "from_box": "webserver", "to_box": "db-server", "type": "ssh_password"},
        {"key": "pivot_db_to_fs", "from_box": "db-server", "to_box": "file-server", "type": "ssh_key"},
    ],
    narrative_sketch="Attacker exploits web app → extracts creds → SSH to DB server → finds SSH key → pivots to file server → SUID privesc → root",
)
```

### Pass 2 Output (NetworkTopology)

```
          ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
          │  webserver    │      │  db-server   │      │ file-server  │
          │  (web01)      │─────▶│  (db01)      │─────▶│  (fs01)      │
          │               │ SSH  │              │ SSH  │              │
          │  Flask+SQLi   │creds │  MariaDB     │ key  │  SUID find   │
          │  Port 80, 22  │      │  Port 22     │      │  Port 22     │
          └──────────────┘      └──────────────┘      └──────────────┘
                ▲
                │
             attacker
```

**Shared secrets**:
- `pivot_web_to_db`: password `Summer2024!`, user `dbadmin`, access `ssh`
- `pivot_db_to_fs`: SSH private key content, user `dbadmin`, access `ssh`

**Generated chain probes**:
1. `sshpass -p 'Summer2024!' ssh -o StrictHostKeyChecking=no dbadmin@db01 'whoami && hostname'`
2. `ssh -i /tmp/stolen_key_pivot_db_to_fs -o StrictHostKeyChecking=no dbadmin@fs01 'whoami && hostname'`

**Per-box misconfig_scope**:
- **webserver**: "Flask web application on port 80 with SQL injection on the login form. The application's MariaDB database contains a `users` table with the row `dbadmin / Summer2024!`."
- **db-server**: "SSH service on port 22 with user `dbadmin` and password `Summer2024!`. MariaDB on default port. The user `dbadmin` has an SSH private key at `~/.ssh/id_rsa` that authenticates to `fs01`."
- **file-server**: "SSH service on port 22 accepting key-based authentication from `dbadmin`. The `find` binary has the SUID bit set (`chmod u+s /usr/bin/find`)."

---

## Open Questions

1. **SSH key pivots**: The chain probe for ssh_key_reuse assumes the attacker has extracted the key to `/tmp/stolen_key_*`. In practice, the attacker would `cat` the key from box B and save it locally. Should chain testing automate this (exec `cat ~/.ssh/id_rsa` on the source box, write to attacker container), or should the probe chain be multi-command?

2. **Single-box detection**: Should the topology architect always be called, or should we detect single-box requests early and skip Pass 1? Skipping saves one LLM call. A simple heuristic: if the prompt contains no multi-machine language ("network", "servers", "lateral", "pivot", "internal"), go single-box.

3. **Atom coverage for pivots**: Do we need new atoms for cross-box primitives (e.g., `ssh_key_planted`, `credential_seeded_in_db`)? Or do existing atoms + custom app pipeline cover it?

4. **Max box count**: Should we cap at 3-4 boxes for DEFCON MVP? More boxes = longer pipeline, more LLM calls, more things that can fail. 2-3 box scenarios are already impressive.
