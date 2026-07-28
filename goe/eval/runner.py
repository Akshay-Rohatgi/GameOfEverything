"""Eval orchestrator — runs planning and build evaluations."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from goe.eval.golden import GoldenPlan
    from goe.eval.report import EvalReport
    from goe.graph.models import EntityGraph


def run_planning_eval(
    request: str,
    golden: "GoldenPlan",
    repeats: int = 1,
    capture_artifacts: bool = False,
    run_dir: Path | None = None,
) -> dict:
    """Run the planner against a request and compare to golden baseline.

    Args:
        request: Natural language attack scenario request.
        golden: Golden plan for comparison.
        repeats: Number of planning attempts (currently only 1 is run).
        capture_artifacts: Whether to capture full LLM conversation history.
        run_dir: Where to write artifact files (required when capture_artifacts=True).

    Returns:
        Dict with keys: graph, adherence_score, metrics_session, plan_result
    """
    from goe.metrics import start_session, end_session
    from goe.eval.golden import compare_plan
    from goe.planner import plan as run_planner

    started_at = datetime.now().isoformat(timespec="seconds")

    # Start metrics (and optionally artifact) collection
    session = start_session(capture_artifacts=capture_artifacts, run_dir=run_dir)

    # Run planner (returns PlanResult)
    plan_result = run_planner(request, verbose=True)

    # End metrics collection
    session = end_session()

    # Flush artifact files into run_dir if capturing
    if capture_artifacts and run_dir is not None:
        from goe.artifacts.run import flush_run
        flush_run(
            run_dir=run_dir,
            session=session,
            entry_point="eval:planning",
            command=f"planning_eval request={request!r}",
            entities=None,
            started_at=started_at,
            ended_at=datetime.now().isoformat(timespec="seconds"),
        )
        # Also write the planned graph if available
        if plan_result.graph:
            (run_dir / "graph.yaml").write_text(
                plan_result.graph.to_yaml_str(), encoding="utf-8"
            )

    # Compare against golden (use plan_result.graph)
    if plan_result.graph:
        adherence = compare_plan(plan_result.graph, golden)
    else:
        # Planning failed - return zero scores
        adherence = None

    return {
        "graph": plan_result.graph,
        "adherence_score": adherence,
        "metrics_session": session,
        "plan_result": plan_result,
    }


def run_build_eval(
    fixtures: list[str],
    output_dir: Path | None = None,
    capture_artifacts: bool = False,
    run_dir: Path | None = None,
) -> dict:
    """Run build_entity on a list of fixture paths.

    Args:
        fixtures: List of fixture file paths (e.g., ["tests/fixtures/entities/sqli_express.yaml"])
        output_dir: Optional output directory for results (legacy param, kept for compat).
        capture_artifacts: Whether to capture full LLM conversation history and persist
            generated scripts. When True, run_dir must be provided.
        run_dir: Where to write artifact files (required when capture_artifacts=True).

    Returns:
        Dict with keys: results (list[EntityResult]), metrics_session, entities (list[Entity])
    """
    from goe.build import build_entity
    from goe.metrics import start_session, end_session, get_session
    from goe.models.entity import Entity

    started_at = datetime.now().isoformat(timespec="seconds")

    session = start_session(capture_artifacts=capture_artifacts, run_dir=run_dir)

    results = []
    entities = []
    entity_metrics = []  # Track per-entity LLM calls

    total = len(fixtures)
    print(f"\n{'='*60}")
    print(f"Building {total} entities...")
    print(f"{'='*60}\n")

    for idx, fixture_path in enumerate(fixtures, 1):
        with open(fixture_path) as f:
            entity = Entity.model_validate(yaml.safe_load(f))
        entities.append(entity)

        print(f"[{idx}/{total}] Building {entity.id} ({entity.runtime.value})...")

        # Track LLM calls before this entity
        calls_before = len(session.records)

        result = build_entity(entity, verbose=False).result
        results.append(result)

        # Track LLM calls for this entity
        calls_after = len(session.records)
        entity_calls = session.records[calls_before:calls_after]
        entity_metrics.append({
            "entity_id": entity.id,
            "calls": entity_calls,
        })

        # Show result
        status_icon = "✓" if result.status.value == "PASSED" else "✗"
        tokens = sum(c.input_tokens + c.output_tokens for c in entity_calls)
        latency = sum(c.latency_ms for c in entity_calls) / 1000
        print(f"[{idx}/{total}] {status_icon} {result.status.value} - {entity.id}")
        print(f"         {len(entity_calls)} LLM calls, {tokens:,} tokens, {latency:.1f}s, {result.attempts} attempt(s)")
        print()

    session = end_session()

    # Flush artifact files into run_dir if capturing
    if capture_artifacts and run_dir is not None:
        from goe.artifacts.run import flush_run
        entity_summaries = [
            {"id": r.id, "status": r.status.value, "attempts": r.attempts}
            for r in results
        ]
        flush_run(
            run_dir=run_dir,
            session=session,
            entry_point="eval:build",
            command=f"build_eval fixtures={fixtures!r}",
            entities=entity_summaries,
            started_at=started_at,
            ended_at=datetime.now().isoformat(timespec="seconds"),
        )

    return {
        "results": results,
        "metrics_session": session,
        "entities": entities,
        "entity_metrics": entity_metrics,
    }


def run_full_eval(
    build_fixtures: list[str] | None = None,
    planning_golden: str | None = None,
    planning_request: str | None = None,
    output_dir: Path = Path("eval_results"),
    capture_artifacts: bool = False,
) -> "EvalReport":
    """Run a full evaluation suite (planning + build).

    Args:
        build_fixtures: List of entity fixture paths for build eval
        planning_golden: Name of golden plan for planning eval
        planning_request: User request for planning eval
        output_dir: Output directory for results
        capture_artifacts: Whether to capture full LLM conversation history and
            generated scripts. When True, conversations/ and entities/ are written
            inside the same eval run_dir alongside summary.json.

    Returns:
        EvalReport
    """
    import statistics
    from goe.eval.golden import load_golden
    from goe.eval.report import EvalReport, EntityDetail, CallerStats, PlanAdherenceScore

    timestamp = datetime.now().isoformat(timespec="seconds")
    run_dir = output_dir / timestamp.replace(":", "-")
    run_dir.mkdir(parents=True, exist_ok=True)

    report_data = {
        "timestamp": timestamp,
        "suite": "full",
        "total_llm_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_tokens": 0,
        "total_latency_ms": 0.0,
        "avg_latency_ms": 0.0,
        "calls_by_caller": {},
        "entities_tested": 0,
        "entities_passed": 0,
        "entities_failed": 0,
        "mean_attempts": 0.0,
        "median_attempts": 0.0,
        "max_attempts": 0,
        "failure_breakdown": {},
        "entity_details": [],
        "plan_adherence": None,
    }

    all_llm_calls = []

    # Planning eval
    if planning_golden and planning_request:
        golden = load_golden(planning_golden)
        plan_result = run_planning_eval(
            planning_request,
            golden,
            capture_artifacts=capture_artifacts,
            run_dir=run_dir if capture_artifacts else None,
        )
        # Convert adherence_score to dict (it might be None if planning failed)
        if plan_result["adherence_score"]:
            report_data["plan_adherence"] = plan_result["adherence_score"].model_dump()
        all_llm_calls.extend(plan_result["metrics_session"].records)

        # Write plan adherence (only if it exists)
        if report_data["plan_adherence"]:
            with open(run_dir / "plan_adherence.json", "w") as f:
                json.dump(report_data["plan_adherence"], f, indent=2)

    # Build eval
    if build_fixtures:
        build_result = run_build_eval(
            build_fixtures,
            capture_artifacts=capture_artifacts,
            run_dir=run_dir if capture_artifacts else None,
        )
        results = build_result["results"]
        entities = build_result["entities"]
        entity_metrics = build_result["entity_metrics"]
        all_llm_calls.extend(build_result["metrics_session"].records)

        attempts = [r.attempts for r in results]
        report_data["entities_tested"] = len(results)
        report_data["entities_passed"] = sum(1 for r in results if r.status.value == "PASSED")
        report_data["entities_failed"] = sum(1 for r in results if r.status.value == "FAILED")
        report_data["mean_attempts"] = sum(attempts) / len(attempts) if attempts else 0
        report_data["median_attempts"] = statistics.median(attempts) if attempts else 0
        report_data["max_attempts"] = max(attempts) if attempts else 0

        # Failure breakdown
        failure_breakdown = {}
        for r in results:
            if r.failure_category:
                failure_breakdown[r.failure_category] = failure_breakdown.get(r.failure_category, 0) + 1
        report_data["failure_breakdown"] = failure_breakdown

        # Build entity details
        entity_details = []
        for i, (entity, entity_result) in enumerate(zip(entities, results)):
            metrics = entity_metrics[i]
            calls = metrics["calls"]
            entity_details.append(EntityDetail(
                id=entity.id,
                runtime=entity.runtime.value,
                atoms=entity.atoms or [],
                status=entity_result.status.value,
                attempts=entity_result.attempts,
                failure_category=entity_result.failure_category,
                failure_reason=entity_result.failure_reason,
                llm_calls=len(calls),
                total_tokens=sum(c.input_tokens + c.output_tokens for c in calls),
                latency_ms=sum(c.latency_ms for c in calls),
            ))
        report_data["entity_details"] = [e.model_dump() for e in entity_details]

        # Write entity results
        with open(run_dir / "entity_results.json", "w") as f:
            json.dump([r.model_dump() for r in results], f, indent=2)

    # Aggregate LLM metrics
    if all_llm_calls:
        report_data["total_llm_calls"] = len(all_llm_calls)
        report_data["total_input_tokens"] = sum(r.input_tokens for r in all_llm_calls)
        report_data["total_output_tokens"] = sum(r.output_tokens for r in all_llm_calls)
        report_data["total_tokens"] = report_data["total_input_tokens"] + report_data["total_output_tokens"]
        report_data["total_latency_ms"] = sum(r.latency_ms for r in all_llm_calls)
        report_data["avg_latency_ms"] = report_data["total_latency_ms"] / len(all_llm_calls)

        # Group by caller
        by_caller = {}
        for rec in all_llm_calls:
            if rec.caller not in by_caller:
                by_caller[rec.caller] = {
                    "count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "latency_ms": 0.0,
                }
            by_caller[rec.caller]["count"] += 1
            by_caller[rec.caller]["input_tokens"] += rec.input_tokens
            by_caller[rec.caller]["output_tokens"] += rec.output_tokens
            by_caller[rec.caller]["latency_ms"] += rec.latency_ms
        report_data["calls_by_caller"] = by_caller

        # Write LLM calls as JSONL
        with open(run_dir / "llm_calls.jsonl", "w") as f:
            for rec in all_llm_calls:
                f.write(json.dumps(asdict(rec)) + "\n")

    # Write summary report
    report = EvalReport.model_validate(report_data)
    with open(run_dir / "summary.json", "w") as f:
        f.write(report.model_dump_json(indent=2))

    return report
