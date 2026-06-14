"""Top-level single-system orchestrator: plan → schedule → build → package.

Wires the existing Phase 0-2 pieces together:
  planner.pipeline.plan → graph.BuildScheduler → build.build_entity → packaging.package

build_entity manages its own per-entity Docker container lifecycle, so the
orchestrator never touches Docker directly. Per-entity isolation is the existing
design; the full-topology chain test is Phase 4.
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


def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return (s[:max_len].rstrip("_")) or "run"


def _snapshot(outcome) -> "ckpt.BuildOutcomeSnapshot":
    proc = outcome.procedure
    return ckpt.BuildOutcomeSnapshot(
        deploy_script=outcome.deploy_script or "",
        procedure=proc.model_dump(mode="json") if proc is not None else None,
        outgoing_values=outcome.outgoing_values,
        attempts=outcome.result.attempts,
    )


def _outcome_from_snapshot(entity_id: str, snap: "ckpt.BuildOutcomeSnapshot"):
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
        plan_result = plan(request, verbose=verbose)
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
    sched = BuildScheduler(graph)
    built: dict[str, object] = {}  # entity_id → BuildOutcome
    results: list = []

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

    while not sched.is_complete():
        nxt = sched.next_buildable()
        if nxt is None:
            break  # defensive — nothing buildable but not complete
        entity, incoming = nxt

        if console:
            console.entity_start(entity.id)
        outcome = build_entity(
            entity,
            incoming_edges=incoming,
            scope=f"run_{entity.id[:12]}",
            verbose=verbose,
        )

        if outcome.result.status == EntityStatus.PASSED:
            sched.report_complete(entity.id, outcome.outgoing_values)
            built[entity.id] = outcome
            results.append(outcome.result)
            state.completed[entity.id] = _snapshot(outcome)
            ckpt.save_state(state, OUTPUT_ROOT)
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

    # ---- Phase 3: package --------------------------------------------------
    output_dir: Path | None = None
    if built:
        output_dir = OUTPUT_ROOT / state.run_id
        package(graph, built, output_dir, request=request)

    success = bool(built) and not state.failed
    if console:
        console.summary(
            built=len(built),
            total=len(graph.entities),
            skipped=sum(1 for r in results if r.status == EntityStatus.SKIPPED),
            out_dir=output_dir,
        )

    return RunResult(
        graph=graph,
        results=results,
        output_dir=output_dir,
        success=success,
        failed={eid: fail.reason for eid, fail in state.failed.items()},
    )
