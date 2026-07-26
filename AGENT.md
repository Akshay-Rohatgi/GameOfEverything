# agent.md: Game of Everything (GoE)

## Project Overview

**Game of Everything (GoE)** is a framework for agentically building vulnerable cybersecurity challenges. It takes natural language requests and generates validated, tested deployment scripts for vulnerable environments.

The project is in active rewrite. There are two parallel codebases:
- **`src/game_of_everything/`** — v1 (crewAI-based, production, single/multi-box misconfig chains + preset apps)
- **`goe/`** — v2 (direct Bedrock API, entity graph model, custom app generation)

**Active development is on v2.** v1 continues to work during the rewrite. Kill v1 when v2 reaches Phase 3 (CLI works end-to-end).

---

## v2 Architecture

v2 models scenarios as a directed graph of **entities** (exploitable vulnerabilities) connected by **typed edges** (attacker capabilities). See `docs/rewrite/entity_graph_model.md` for full spec.

### Current State (Phase 2 complete, Phase 3 next)

**What works now:**
- Single entity → full construction crew (Engineer/Opus → Developer/Sonnet → Attacker/Sonnet) → deploy → L2 test → retry escalation
- 3 runtimes: Express (Node.js 20), Flask (Python 3), Apache/PHP
- 13 web vulnerability atoms (SQLi, CMDi, XSS, SSTI, file upload, path traversal, deserialization, etc.)
- Confirmed passing: SQLi/Express, CMDi/Flask, SQLi/PHP, XSS-stored/PHP, XSS-admin-bot/Express
- `python -m goe.planner "..."` → validated entity graph YAML (all planning agents use Sonnet)
- Static validator (7 checks), BuildScheduler (topological ordering + value propagation)

**What's next (Phase 3):**
- Flow orchestrator: graph → build all entities → package output
- `goe run "..."` CLI end-to-end
- Deliverable: user request → validated deploy script + playbook

### Key Components

```
goe/
  bedrock.py              Direct boto3 Bedrock wrapper (no crewAI)
  build.py                Single-entity pipeline + CLI entry point
  construction_crew/      Engineer → Developer → Attacker agents
  executor/               Procedure DSL runner (HTTP, shell, browser)
  runtimes/               Deterministic deploy script generation
  retry/                  Diagnostician + escalation router
  container/              TestEnvironment adapter over v1 Docker tools
  graph/                  EntityGraph, validator (7 checks), topology, BuildScheduler
  planner/                design_systems, plan_entities, specify_entities, connect_edges, resolve, pipeline
```

### Running v2

```bash
# Plan an attack graph from natural language (requires AWS creds)
cd game_of_everything
.venv/bin/python -m goe.planner "web app with SQL injection leading to credential theft"
.venv/bin/python -m goe.planner "..." --output graph.yaml --verbose

# Build a single entity end-to-end
.venv/bin/python -m goe.build --spec tests/fixtures/entities/sqli_express.yaml

# Run the entity test suite (requires Docker + AWS creds)
.venv/bin/pytest tests/test_build.py -v -m "llm and docker" -k "sqli"

# Fast unit tests (no Docker, no LLM)
.venv/bin/pytest tests/test_bedrock.py tests/test_runtimes.py -v
.venv/bin/pytest tests/test_graph_models.py tests/test_topology.py tests/test_validator.py tests/test_build_scheduler.py tests/test_resolve.py tests/test_planner.py -v
```

### Procedure DSL

Attack procedures are YAML files with a strict schema. Actions: `http_request`, `exec_attacker`, `exec_attacker_bg` (detached background), `exec_target`, `listen`, `sleep`, browser actions (`navigate`, `click`, `fill_and_submit`, `evaluate`, etc.). See `docs/rewrite/goe_rewrite.md` for full DSL reference.

### Runtime Templates

Each runtime YAML (`goe/runtimes/templates/`) has:
- `developer_rules` — mandatory constraints for the Developer agent (e.g. SQLite path, no `stdio: 'ignore'` for bots)
- `attacker_rules` — mandatory constraints for the Attacker agent (e.g. POST redirect patterns, listener verification)

These keep the system prompts generic — runtime-specific knowledge lives in the template files.

---

## v1 Architecture (Reference)

v1 uses crewAI Flow with a multi-agent pipeline. See `CLAUDE.md` for full v1 documentation.

**Key quirks to be aware of if touching v1:**
- CrewAI Bedrock bug: `crew_agent_executor.py:722` drops tool arguments — patch must be reapplied after crewAI upgrades (see CLAUDE.md)
- `patches.py` applies JSON repair and Bedrock prefill rejection monkey-patches at import time
- All inter-agent data is prompt-based (crewAI `context=[]`) — no programmatic handoff

---

## Shared Assets

| Asset | Used By |
|-------|---------|
| `atoms/` | Both v1 and v2 |
| `docker/` Dockerfiles | Both (v2 adds Chromium to target_express) |
| `goe.toml` | Both |
| `src/game_of_everything/tools/test_environment.py` | Both (v2 wraps via `goe/container/environment.py`) |
| `output/` | Both (different file structure) |

---

## Implementation Roadmap

See `docs/rewrite/implementation_plan.md` for the full phased plan with status.

| Phase | Status | Deliverable |
|-------|--------|-------------|
| 0 — Foundation | ✅ Complete | Models, executor, container adapter |
| 1 — Construction Crew | ✅ Complete | Single entity E2E with retry |
| 2 — Graph Planning | ✅ Complete | `goe plan "..."` → valid entity graph |
| 3 — E2E Single System | ⬜ Planned | `goe run "..."` → deploy script + playbook |
| 4 — Multi-System | ⬜ Planned | docker-compose + chain test |
| 5 — Polish | ⬜ Planned | Atom RAG, EC2, cost optimization |
