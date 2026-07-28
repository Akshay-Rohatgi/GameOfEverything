"""Top-level orchestrator: plan → schedule → build → chain test → package.

Phase 1 — plan:    planner.pipeline.plan → EntityGraph
Phase 2 — build:   BuildScheduler drives build.build_entity per entity
Phase 3 — test:    optional L3 chain test via TopologyEnvironment + chain_attacker
Phase 4 — package: packaging.package writes deploy.sh / playbook.yaml / README

build_entity manages its own per-entity Docker container lifecycle, so the
orchestrator never touches Docker directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from goe.graph.build_scheduler import BuildScheduler
from goe.graph.models import EntityGraph
from goe.flow import checkpoint as ckpt

OUTPUT_ROOT = Path("output")


@dataclass
class RunResult:
    graph: EntityGraph | None
    results: list  # list[EntityResult]
    output_dir: Path | None
    success: bool
    failed: dict[str, str] = field(default_factory=dict)
    final_violations: list = field(default_factory=list)
    chain_test: object = None  # ChainTestResult | None
    edge_gaps: list = field(default_factory=list)  # ["edge_id.param", ...] unfilled params


def _populate_edge_concrete(
    graph: EntityGraph, entity_id: str, outgoing_values: dict[str, dict[str, str]]
) -> None:
    """Write build-time concrete values back onto graph edge params.

    The developer emits a per-param dict for each outgoing edge
    (`{edge_id: {param: value}}`). We write each value onto the matching, already-declared
    edge param — keys are fixed at plan time, so we never add, rename, or invent params.
    Undeclared keys are rejected at developer parse time; we ignore them defensively here.
    """
    for edge in graph.edges:
        payload = outgoing_values.get(edge.id)
        if not payload:
            continue
        for param_name, value in payload.items():
            param = edge.params.get(param_name)
            if param is not None:
                param.concrete = value


def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (s[:max_len].rstrip("_")) or "run"


def _snapshot(outcome: "BuildOutcome") -> "ckpt.BuildOutcomeSnapshot":
    proc = outcome.procedure
    return ckpt.BuildOutcomeSnapshot(
        deploy_script=outcome.deploy_script or "",
        procedure=proc.model_dump(mode="json") if proc is not None else None,
        outgoing_values=outcome.outgoing_values,
        attempts=outcome.result.attempts,
    )


def _outcome_from_snapshot(entity_id: str, snap: "ckpt.BuildOutcomeSnapshot") -> "BuildOutcome":
    """Rebuild a BuildOutcome from a checkpoint snapshot (for packaging on resume)."""
    from goe.models.procedure import Procedure
    from goe.models.report import BuildOutcome, EntityResult, EntityStatus

    procedure = Procedure.model_validate(snap.procedure) if snap.procedure else None
    return BuildOutcome(
        result=EntityResult(
            id=entity_id,
            status=EntityStatus.PASSED,
            attempts=snap.attempts,
        ),
        deploy_script=snap.deploy_script,
        procedure=procedure,
        outgoing_values=snap.outgoing_values,
    )


def run(
    request: str = "",
    *,
    resume_dir: Path | None = None,
    verbose: bool = False,
    console=None,
) -> RunResult:
    """Drive the full single-system flow. Returns a RunResult."""
    from goe.build import build_entity
    from goe.models.report import EntityResult, EntityStatus
    from goe.packaging import package

    # ---- Phase 1: plan (or restore from checkpoint) -----------------------
    if resume_dir is not None:
        state = ckpt.load_state(Path(resume_dir))
        graph = state.graph
        request = state.request
    else:
        from goe.planner.pipeline import plan

        if console:
            console.planning()
        plan_result = plan(request, verbose=verbose, console=console)
        if not plan_result.success or plan_result.graph is None:
            if console:
                console.plan_failed(plan_result.final_violations)
            return RunResult(
                graph=None,
                results=[],
                output_dir=None,
                success=False,
                final_violations=plan_result.final_violations,
            )
        graph = plan_result.graph
        run_id = f"{datetime.now():%Y%m%dT%H%M%S}_{_slug(request)}"
        state = ckpt.RunState(run_id=run_id, request=request, graph=graph)
        ckpt.save_state(state, OUTPUT_ROOT)

    if console:
        console.header(request, len(graph.entities))

    # ---- Phase 2: schedule + build ----------------------------------------
    # Materialize generable edge secrets (SSH keypairs) deterministically before any
    # entity builds, so the producer and consumer embed the SAME key. Idempotent on resume.
    from goe.graph.secrets import materialize_secrets
    materialized = materialize_secrets(graph)
    if materialized:
        import sys
        print(f"[ORCH] Materialized secrets for edges: {', '.join(materialized)}", file=sys.stderr)

    sched = BuildScheduler(graph)
    built: dict[str, object] = {}  # entity_id → BuildOutcome
    results: list = []

    # Set up progressive environments. ProgressiveEnvironment runs an ubuntu:22.04
    # target and never installs a web runtime, so it only applies to all-ubuntu
    # systems. Web/mixed systems keep per-entity TestEnvironment isolation.
    from goe.container.progressive import ProgressiveEnvironment

    progressive_envs: dict[str, ProgressiveEnvironment] = {}  # system_id → env

    # Replay terminal entities from checkpoint (restores value propagation).
    for eid, snap in state.completed.items():
        sched.report_complete(eid, snap.outgoing_values)
        outcome = _outcome_from_snapshot(eid, snap)
        built[eid] = outcome
        results.append(outcome.result)
    for eid, fail in state.failed.items():
        skipped = sched.report_failed(eid)
        results.append(EntityResult(
            id=eid, status=EntityStatus.FAILED,
            failure_reason=fail.reason, failure_category=fail.category,
        ))
        for sid in skipped:
            results.append(EntityResult(
                id=sid, status=EntityStatus.SKIPPED,
                skip_reason=f"upstream {eid} failed",
            ))

    try:
        for system in graph.systems:
            system_entities = [e for e in graph.entities if e.system_id == system.id]
            if len(system_entities) > 1 or system.services:
                penv = ProgressiveEnvironment(system_id=system.id, scope=f"run_{state.run_id[:8]}_{system.id}")
                penv.setup()
                # Register before provision() so the finally tears it down even
                # if service provisioning fails partway through.
                progressive_envs[system.id] = penv
                penv.provision(system)

        while not sched.is_complete():
            nxt = sched.next_buildable()
            if nxt is None:
                break  # defensive — nothing buildable but not complete
            entity, incoming = nxt
            edge_schemas = _edge_schemas_for(graph, entity)
            system_context = _system_chain_context(graph, entity, built=built)
            provided_values = _provided_values_for(graph, entity)

            import sys
            print(f"[ORCH] Building entity {entity.id} on system {entity.system_id}", file=sys.stderr)

            if console:
                console.entity_start(entity.id)

            # Use progressive environment if available for this system
            penv = progressive_envs.get(entity.system_id)
            if penv is not None:
                # Handle fan-out: restore to correct parent snapshot
                parent_id = _same_system_parent(graph, entity)
                if parent_id and penv.current_snapshot != parent_id:
                    penv.restore(parent_id)

                outcome = build_entity(
                    entity,
                    incoming_edges=incoming,
                    scope=f"run_{entity.id[:12]}",
                    verbose=verbose,
                    env=penv,
                    edge_schemas=edge_schemas,
                    system_context=system_context,
                    provided_values=provided_values,
                    console=console,
                )
            else:
                # Single-entity systems (no services): per-entity isolation
                outcome = build_entity(
                    entity,
                    incoming_edges=incoming,
                    scope=f"run_{entity.id[:12]}",
                    verbose=verbose,
                    edge_schemas=edge_schemas,
                    system_context=system_context,
                    provided_values=provided_values,
                    console=console,
                )

            if outcome.result.status == EntityStatus.PASSED:
                sched.report_complete(entity.id, outcome.outgoing_values)
                _populate_edge_concrete(graph, entity.id, outcome.outgoing_values)
                built[entity.id] = outcome
                results.append(outcome.result)
                state.completed[entity.id] = _snapshot(outcome)
                ckpt.save_state(state, OUTPUT_ROOT)

                # Snapshot progressive environment after successful entity
                if penv is not None:
                    penv.snapshot(entity.id, outcome.deploy_script or "")

                if console:
                    console.entity_done(entity.id, outcome.result.attempts)
            else:
                skipped = sched.report_failed(entity.id)
                reason = outcome.result.failure_reason or "build failed"
                results.append(outcome.result)
                for sid in skipped:
                    results.append(EntityResult(
                        id=sid, status=EntityStatus.SKIPPED,
                        skip_reason=f"upstream {entity.id} failed",
                    ))
                state.failed[entity.id] = ckpt.FailureSnapshot(
                    reason=reason, category=outcome.result.failure_category,
                )
                ckpt.save_state(state, OUTPUT_ROOT)
                if console:
                    console.entity_failed(entity.id, reason, skipped)

    except Exception as e:
        import sys
        print(f"[ORCH] Exception in build loop: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        raise
    finally:
        # Teardown progressive environments
        import sys
        print(f"[ORCH] Tearing down {len(progressive_envs)} progressive environment(s)", file=sys.stderr)
        for penv in progressive_envs.values():
            penv.teardown()

    # ---- Edge completeness guard ------------------------------------------
    # Every declared param of a consumed edge must carry a concrete value by now.
    # A gap means partial information was lost between entities (e.g. a username that
    # never propagated) — fail loudly rather than letting chain_test silently inject the
    # structural placeholder text into commands.
    edge_gaps = _incomplete_consumed_edges(graph, built)
    if edge_gaps:
        import sys
        print(
            f"[ORCH] Incomplete edge params after build: {', '.join(edge_gaps)}",
            file=sys.stderr,
        )

    # ---- Phase 3: chain test (any run with > 1 entity built) ---------------
    from goe.models.report import ChainTestResult, ChainTestStatus

    chain_test: ChainTestResult | None = None
    chain_procedure = None
    if len(built) > 1:
        from goe.flow.chain_test import run_chain_test

        if console:
            console.chain_test_start(len(built))

        outcome = run_chain_test(graph, built, console=console)
        chain_test = outcome.result
        chain_procedure = outcome.procedure

        if console:
            console.chain_test_result(
                passed=(chain_test.status == ChainTestStatus.PASSED),
                reason=chain_test.reason,
            )

        # Persist chain test result into checkpoint
        state.chain_test = chain_test.model_dump(mode="json")
        ckpt.save_state(state, OUTPUT_ROOT)

    # ---- Phase 4: package --------------------------------------------------
    output_dir: Path | None = None
    if built:
        output_dir = OUTPUT_ROOT / state.run_id
        package(graph, built, output_dir, request=request, chain_procedure=chain_procedure)

    # Gating: chain test failure OR incomplete edge params make the overall run fail
    chain_passed = chain_test is None or chain_test.status == ChainTestStatus.PASSED
    success = bool(built) and not state.failed and chain_passed and not edge_gaps

    if console:
        console.summary(
            built=len(built),
            total=len(graph.entities),
            skipped=sum(1 for r in results if r.status == EntityStatus.SKIPPED),
            out_dir=output_dir,
            chain_test=chain_test,
        )

    return RunResult(
        graph=graph,
        results=results,
        output_dir=output_dir,
        success=success,
        failed={eid: fail.reason for eid, fail in state.failed.items()},
        chain_test=chain_test,
        edge_gaps=edge_gaps,
    )


def _edge_schemas_for(graph: "EntityGraph", entity: "Entity") -> dict:
    """Declared param schema for the edges this entity provides/requires.

    Returns {edge_id: {"type", "direction", "params": [declared param names]}}. The param
    keys come straight from the plan-time edge definitions, so the developer fills values
    for fixed keys and never invents structure.
    """
    provided = set(entity.provides)
    required = {r.edge_id for r in entity.requires}
    schemas: dict = {}
    for edge in graph.edges:
        if edge.id in provided:
            direction = "provides"
            # The developer only fills params not already resolved at plan time
            # (host/port come from resolve.py); this also stops it overwriting them.
            params = sorted(p for p, pv in edge.params.items() if pv.concrete is None)
        elif edge.id in required:
            direction = "requires"
            params = sorted(edge.params.keys())
        else:
            continue
        schemas[edge.id] = {
            "type": edge.type.value,
            "direction": direction,
            "params": params,
        }
    return schemas


def _provided_values_for(graph: "EntityGraph", entity: "Entity") -> dict:
    """Concrete values already determined for the edges this entity *provides*.

    Returns ``{edge_id: {param: concrete}}`` for provided-edge params that carry a concrete
    value at build time — host/port resolved by ``resolve.py`` and secrets materialized by
    ``graph.secrets``. The producing developer is told to use these EXACT values in its
    implementation (e.g. write the base64-decoded SSH key to the file it serves) rather than
    generating fresh material that would not match the consumer.
    """
    provided = set(entity.provides)
    out: dict[str, dict[str, str]] = {}
    for edge in graph.edges:
        if edge.id not in provided:
            continue
        concretes = {p: pv.concrete for p, pv in edge.params.items() if pv.concrete is not None}
        if concretes:
            out[edge.id] = concretes
    return out


def _extract_build_summary(entity: "Entity", outcome: "BuildOutcome") -> dict:
    """Parse a completed BuildOutcome into a lightweight summary of concrete facts.

    Extracts from the deploy script: which users were created, which paths were written,
    which services were restarted, and the app_dir. These facts constrain what co-located
    downstream entities can assume about the filesystem and user database.
    """
    import re as _re

    summary: dict = {
        "entity_id": entity.id,
        "runtime": entity.runtime.value,
        "app_dir": None,
        "users_created": [],
        "paths_written": [],
        "services_configured": [],
        "description": entity.description,
    }

    script = getattr(outcome, "deploy_script", None) or ""

    # app_dir: find the mkdir -p that sets up the app directory.
    # The deploy script always has `mkdir -p <app_dir>` right before writing source files
    # via `echo '...' | base64 -d > <app_dir>/...`. Find the mkdir whose path is a parent
    # of a base64 write — that's the app_dir.
    b64_dest = _re.findall(r"base64 -d > ([/][\w./-]+)", script)
    if b64_dest:
        # Pick the deepest common ancestor of the first few written paths
        import posixpath
        first_dest = b64_dest[0]
        candidate = posixpath.dirname(first_dest)
        summary["app_dir"] = candidate
    else:
        # Fall back: first mkdir -p with a non-trivial path (not /root/.ssh etc.)
        for m in _re.finditer(r"mkdir -p ([/][\w.-]+)", script):
            path = m.group(1)
            if path not in ("/root/.ssh", "/tmp", "/var/run", "/etc"):
                summary["app_dir"] = path
                break

    # Users created: capture the username that immediately follows -m/-s/flags or is the last word
    # useradd [-m] [-s /bin/bash] username  — username is the last non-flag token on the line
    summary["users_created"] = [
        m.group(1) for line in script.splitlines()
        if (m := _re.search(r"useradd\b.*?(\b(?!-)\w[\w-]*)\s*(?:#|$|2>)", line))
        and not m.group(1).startswith("-")
    ]

    # Paths written: echo ... > /path and heredoc redirections
    summary["paths_written"] = list(dict.fromkeys(
        _re.findall(r"(?:>|cat >)\s*([/][\w./-]+)", script)
    ))

    # Services configured: both `service <name> <action>` and `systemctl <action> <name>`
    svc = _re.findall(r"service\s+([\w-]+)\s+(?:restart|reload|start)", script)
    svc += _re.findall(r"systemctl\s+(?:restart|reload|start)\s+([\w-]+)", script)
    summary["services_configured"] = list(dict.fromkeys(svc))

    return summary


def _system_chain_context(
    graph: "EntityGraph",
    entity: "Entity",
    built: dict | None = None,
) -> str:
    """Render the system + killchain context for an entity's build prompts.

    Gives the engineer/developer what they were previously missing: the system this entity
    runs on (and the services the platform already provides there), plus a summary of every
    sibling entity. For already-built co-system entities, we include concrete facts (app_dir,
    users created, paths written) so downstream entities can reference the actual filesystem
    state rather than guessing.
    """
    built = built or {}
    system = graph.system_by_id(entity.system_id)
    lines: list[str] = ["## System & Chain Context", ""]

    if system is not None:
        services = ", ".join(s.id for s in system.services) or "(none)"
        lines += [
            f"This entity runs on system **{system.id}** (hostname `{system.network.hostname}`).",
            f"- Services already installed and running on this system (provided by the platform): **{services}**",
            "  Configure these services for your vulnerability — do NOT install or restart them, "
            "and do NOT add services this system does not declare.",
            "",
        ]

    siblings = [e for e in graph.entities if e.id != entity.id]
    if siblings:
        # Split siblings into: already-built on same system vs everything else
        built_cosystem = []
        other = []
        for sib in siblings:
            if sib.id in built and sib.system_id == entity.system_id:
                built_cosystem.append(sib)
            else:
                other.append(sib)

        if built_cosystem:
            lines.append("**Already deployed on this system** (their files/users are live in the container):")
            for sib in built_cosystem:
                summary = _extract_build_summary(sib, built[sib.id])
                lines.append(f"- `{sib.id}`: {sib.description}")
                if summary["app_dir"]:
                    lines.append(f"  - app_dir: `{summary['app_dir']}`")
                if summary["users_created"]:
                    lines.append(f"  - users created: {', '.join(summary['users_created'])}")
                if summary["paths_written"]:
                    # Only show first 6 paths to avoid overwhelming the prompt
                    paths = summary["paths_written"][:6]
                    lines.append(f"  - paths written: {', '.join(paths)}")
                if summary["services_configured"]:
                    lines.append(f"  - services configured: {', '.join(summary['services_configured'])}")
            lines.append("")

        if other:
            lines.append("Other entities in this scenario stay deployed and handle their own links — "
                         "do NOT re-create their setup:")
            for sib in other:
                sib_sys = graph.system_by_id(sib.system_id)
                host = f"/{sib_sys.network.hostname}" if sib_sys is not None else ""
                lines.append(f"- `{sib.id}` (on system `{sib.system_id}`{host}): {sib.description}")
            lines.append("")

    # The edges this entity consumes/provides describe exactly which link it owns.
    reqs = [r.edge_id for r in entity.requires]
    if reqs:
        lines.append(f"You consume (require) these edges from upstream: {', '.join(reqs)}.")
    if entity.provides:
        lines.append(f"You hand off (provide) these edges to downstream: {', '.join(entity.provides)}.")
    lines.append(
        "Build ONLY your own link. The end-to-end attack across systems is verified separately "
        "by the chain test — your success indicator must be observable on THIS system alone."
    )
    return "\n".join(lines) + "\n"


def _incomplete_consumed_edges(graph: "EntityGraph", built: dict) -> list[str]:
    """Find edges consumed by a built entity that still have unfilled (partial) params.

    After build + propagation every declared param of a consumed edge must have a concrete
    value (resolve.py fills host/port; the producing developer fills the rest). A declared
    param left with concrete=None means information was lost in transit — return a list of
    human-readable "edge_id.param" identifiers so the caller can fail loudly instead of
    silently substituting the structural description text into executed commands.
    """
    required_ids: set[str] = set()
    for eid in built:
        entity = graph.entity_by_id(eid)
        if entity is not None:
            required_ids.update(r.edge_id for r in entity.requires)

    missing: list[str] = []
    for edge in graph.edges:
        if edge.id not in required_ids:
            continue
        # Only check edges whose producer actually built (operator edges always count).
        if edge.from_entity != "operator" and edge.from_entity not in built:
            continue
        for param_name, pv in edge.params.items():
            if pv.concrete is None:
                missing.append(f"{edge.id}.{param_name}")
            elif param_name == "secret" and _looks_like_path_secret(edge, pv.concrete):
                # A creds_for ssh secret must be key *material* (base64 PEM), not a file path.
                # This was the original bug: the producer leaked "/srv/.../id_rsa" and the
                # consumer regenerated its own key. Fail loudly rather than ship a broken chain.
                missing.append(f"{edge.id}.{param_name} (path, not key material)")
    return missing


def _looks_like_path_secret(edge, value: str) -> bool:
    """True if a creds_for SSH ``secret`` carries a filesystem path instead of key material."""
    from goe.graph.secrets import _is_ssh_key

    if not _is_ssh_key(edge.params.get("cred_type")):
        return False
    v = (value or "").strip()
    return v.startswith("/") and "\n" not in v and "BEGIN" not in v


def _same_system_parent(graph: "EntityGraph", entity: "Entity") -> str | None:
    """Find the immediate upstream entity on the same system (via edge dependencies).

    Used for fan-out handling: when building entity B that requires an edge from entity A,
    and both are on the same system, return A's ID so the progressive environment can
    restore to A's snapshot before building B.
    """
    for req in entity.requires:
        edge = graph.edge_by_id(req.edge_id)
        if edge and edge.from_entity != "operator":
            parent = graph.entity_by_id(edge.from_entity)
            if parent and parent.system_id == entity.system_id:
                return parent.id
    return None
