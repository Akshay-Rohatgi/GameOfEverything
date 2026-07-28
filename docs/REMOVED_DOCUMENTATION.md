# Removed Documentation Manifest

The following documentation was removed because it described deleted v1 code,
historical implementation states, generated run results, or unsupported v2
capabilities. Treat the `game_of_everything/goe/` code and its tests as the
source of truth when regenerating documentation.

## Removed with the v2-only cleanup

| Removed files | Why removed | Regenerate only if needed |
| --- | --- | --- |
| `AGENT.md`, `CLAUDE.md` | Mixed v1/v2 developer guidance that referenced the deleted `src/game_of_everything/` implementation. | A v2-only contributor and agent guide based on the current package layout and test commands. |
| `docs/design/browser_use.md` | Historical browser-use design for the removed v1 architecture. | Browser/executor design based on `goe/executor/` and `goe/container/`. |
| `docs/design/custom_apps.md` | Historical custom-app plan for the deleted v1 flow. | Construction-crew and runtime-template design based on `goe/construction_crew/` and `goe/runtimes/`. |
| `docs/design/multi_box_topology.md` | Superseded topology design. | Multi-system flow design based on `goe/flow/`, `goe/graph/`, and `goe/container/topology.py`. |
| `docs/design/preset_apps.md` | Described deleted preset-app support. | Only if preset apps are deliberately reintroduced. |

## Removed in this documentation cleanup

| Removed files | Why removed | Regenerate only if needed |
| --- | --- | --- |
| `docs/architecture/v2_implementation_plan.md` | A completed migration plan that still said v2 wrapped v1 infrastructure and listed deleted/pending preset-app work. | A current roadmap with verified status and explicit future scope. |
| `docs/samples/requests.md` | Mixed old flow terminology and requests for unsupported WordPress/preset-app scenarios. | A curated v2 request cookbook validated against the current atoms, runtimes, and graph planner. |
| `docs/notes/BUGFIX_PYDANTIC.md` | One-time evaluation-model bug report. | A changelog entry only if historical release notes are required. |
| `docs/notes/DIAGNOSTICS_IMPROVEMENTS.md` | Point-in-time run analysis and tuning notes. | A diagnostics operations guide based on current retry behavior and metrics. |
| `docs/notes/EVAL_FINAL_SUMMARY.md`, `docs/notes/EVAL_IMPROVEMENTS.md`, `docs/notes/EVAL_TEST_RESULTS.md`, `docs/notes/FIXES_SUMMARY.md` | Historical evaluation implementation summaries and results. | A current evaluation guide generated from `goe/eval/`, its tests, and fresh eval results. |
| `docs/notes/phase1_complete.md`, `docs/notes/phase2_bedrock_tool_blocker.md`, `docs/notes/phase2_implementation_summary.md`, `docs/notes/phase2_status.md` | Historical CrewAI/browser implementation notes referencing deleted v1 paths and behavior. | A v2 construction/execution design only if it is needed beyond the code-derived architecture. |

## Documentation retained

- `docs/architecture/entity_graph_model.md` — conceptual entity-graph reference.
- `docs/architecture/v2_spec.md` — v2 behavior and procedure-DSL specification.
- `docs/eval/` and `goe/eval/README.md` — current evaluation reference material.

Before publishing regenerated docs, verify every command, path, runtime, and
supported capability against the repository rather than copying claims from
this manifest.
