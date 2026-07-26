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

### Current State (Phases 0–4 complete, Phase 5 next)

**What works now:**
- Full end-to-end single-system flow: `goe.flow run "..."` → plan → build all entities → package output
- Multi-system flow: parallel builds across systems, `TopologyEnvironment`, `docker-compose.yml` + `chain_playbook.yaml`
- L3 chain test gates overall success; `chain_attacker` synthesizes end-to-end procedures
- Checkpoint/resume: `--resume output/.checkpoints/<run_id>/`
- Artifacts/eval: opt-in LLM conversation persistence and evaluation suites
- 3 runtimes: Express (Node.js 20), Flask (Python 3), Apache/PHP
- 13 web vulnerability atoms (SQLi, CMDi, XSS, SSTI, file upload, path traversal, deserialization, etc.)
- Confirmed passing single entities: SQLi/Express, CMDi/Flask, SQLi/PHP, XSS-stored/PHP, XSS-admin-bot/Express

**What's next (Phase 5 — Polish and Parity):**
- Atom integration into construction crew (engineer receives relevant atoms via RAG)
- EC2 deploy (port v1's `ec2_deploy.py`)
- Cost optimization, observability, preset apps

### Key Components

```
goe/
  bedrock.py              Direct boto3 Bedrock wrapper (no crewAI)
  build.py                Single-entity pipeline + CLI entry point
  construction_crew/      Engineer → Developer → Attacker agents (+ chain_attacker)
  executor/               Procedure DSL runner (HTTP, shell, browser)
  runtimes/               Deterministic deploy script generation
  retry/                  Diagnostician + escalation router
  container/              TestEnvironment adapter over v1 Docker tools
  graph/                  EntityGraph, validator (7 checks), topology, BuildScheduler
  planner/                design_systems, plan_killchain, plan_entities, specify_entities, connect_edges, resolve, pipeline
  flow/                   Orchestrator (plan → build → package), CLI entry point, checkpoint/resume
  packaging/              deploy.sh + playbook.yaml + README generation
  metrics/                MetricsSession, token/latency/cost instrumentation
  artifacts/              Opt-in LLM conversation + file persistence
  eval/                   Evaluation suites (build, planning, full)
  services/               Shared service helpers
```

### Running v2

```bash
# Full end-to-end run (requires AWS creds + Docker)
cd game_of_everything
.venv/bin/python -m goe.flow run "web app with SQL injection that leaks credentials"
.venv/bin/python -m goe.flow run --verbose "SSH server with weak credentials and SUID privesc"
.venv/bin/python -m goe.flow run --resume output/.checkpoints/<run_id>/

# Re-test an existing output directory (no LLM — deploys in Docker and runs playbook)
.venv/bin/python -m goe.flow test output/<run_id>/

# Plan only (Steps 0–3, outputs graph YAML)
.venv/bin/python -m goe.planner "web app with SQL injection leading to credential theft"

# Build a single entity end-to-end
.venv/bin/python -m goe.build --spec tests/fixtures/entities/sqli_express.yaml

# Fast unit tests (no Docker, no LLM)
.venv/bin/python -m pytest -m "not docker and not llm"

# Full test suite (requires Docker + AWS creds)
.venv/bin/python -m pytest tests/
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
| 2 — Graph Planning | ✅ Complete | `goe.planner "..."` → valid entity graph |
| 3 — E2E Single System | ✅ Complete | `goe.flow run "..."` → deploy script + playbook |
| 4 — Multi-System | ✅ Complete | docker-compose + chain test |
| 5 — Polish and Parity | ⬜ Planned | Atom RAG, EC2, cost optimization, preset apps |
