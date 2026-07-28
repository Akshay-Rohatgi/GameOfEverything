# Workflow Artifacts

Opt-in persistence of LLM conversation history and all generated files from GoE v2 runs.

## Enabling

**goe.toml** (persistent default):
```toml
[artifacts]
enabled = true
dir     = "artifacts"   # root directory; timestamped subdirs created per run
```

**Environment variable** (per-shell override, takes priority over toml):
```bash
export GOE_SAVE_ARTIFACTS=1          # enable  (accepts: 1, true, yes, on)
export GOE_ARTIFACTS_DIR=/tmp/runs   # optional: override the output root
```

**CLI flag** (per-run override, takes priority over env + toml):
```bash
python -m goe.build --spec ... --artifacts      # enable for this run
python -m goe.build --spec ... --no-artifacts   # disable for this run
python -m goe.eval  --suite build --artifacts ...
python -m goe.planner "..." --artifacts
```

## On-disk layout

```
artifacts/
└── 2026-06-12T14-30-05/        # ISO timestamp, one directory per run
    ├── manifest.json           # run index — entry point, tokens, file list
    ├── conversations/
    │   ├── llm_calls.jsonl     # one LLMTranscriptRecord per line (machine)
    │   ├── developer.md        # human-readable: system + all turns + responses
    │   ├── attacker.md
    │   ├── engineer.md
    │   └── planner.md
    ├── entities/
    │   └── <entity_id>/
    │       ├── app/            # source_files (nested paths preserved)
    │       ├── db/
    │       │   ├── schema.sql
    │       │   └── seed.sql
    │       ├── artifact.json   # BuildArtifact metadata (port, app_dir, deps)
    │       ├── procedure.yaml  # attack Procedure
    │       ├── engineer_plan.json
    │       └── attempts/       # only present if retries occurred
    │           ├── attempt_1/  # updated app/, procedure.yaml, diagnosis.json
    │           ├── attempt_initial_to_1.diff   # unified diff of changes
    │           └── attempt_2/  # etc.
    ├── metrics/
    │   └── llm_calls.jsonl     # token/latency metrics (LLMCallRecord)
    └── summary.json            # eval runs only
```

For **eval runs** (`python -m goe.eval`), artifacts are co-located in the
existing `eval_results/<timestamp>/` directory alongside `summary.json`,
`entity_results.json`, and `plan_adherence.json`.

## Record schemas

### `conversations/llm_calls.jsonl` — `LLMTranscriptRecord`
```json
{
  "call_id": "uuid",
  "timestamp": 1718200000.0,
  "caller": "developer.self_review",
  "model_id": "us.anthropic.claude-sonnet-4-6-...",
  "system": "You are a developer...",
  "new_messages": [
    {"role": "user",      "content": "Review this code..."},
    {"role": "assistant", "content": "Here is the updated version..."}
  ],
  "response": "Here is the updated version...",
  "input_tokens": 1234,
  "output_tokens": 567
}
```

`new_messages` contains only the **delta** since the last call from the same
agent root (`developer`, `attacker`, etc.), so the records are non-overlapping.
Concatenating `new_messages` across all records for the same root reconstructs
the complete conversation.

### `manifest.json`
```json
{
  "run_id": "2026-06-12T14-30-05",
  "entry_point": "build",
  "command": "python -m goe.build --spec tests/fixtures/entities/sqli_express.yaml",
  "started_at": "...",
  "ended_at": "...",
  "llm": {
    "total_calls": 6,
    "total_tokens": 12345,
    "calls_by_caller": {"engineer": {"count": 1, ...}, ...}
  },
  "conversations": ["attacker", "developer", "engineer"],
  "entities": [{"id": "sqli_express", "status": "PASSED", "attempts": 1}],
  "files": ["conversations/developer.md", "entities/sqli_express/procedure.yaml", ...]
}
```

## Security note

Artifacts contain the generated **vulnerable app source code** and **attack
procedures** — that's the point. Do not share artifact directories publicly.
AWS credentials from `goe.toml` are never serialised into artifacts (only
whitelisted config fields appear in `manifest.json`).

## Known limitations

- The full multi-entity orchestrator (`goe/flow/`) exists only as compiled `.pyc`
  and cannot be edited. Conversation capture will fire if it calls `start_session()`,
  but generated-file persistence (which requires `artifact_run_dir` on the session)
  will not activate. The three committed entry points (`goe.build`, `goe.planner`,
  `goe.eval`) are fully covered.
- `ContextVar` is not propagated across bare `ThreadPoolExecutor` threads. A future
  parallel orchestrator should start one session per worker thread (or use
  `copy_context().run(...)`) to capture per-entity artifacts correctly.
