"""CLI entry point for GoE v2 evaluation system."""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Run GoE v2 evaluation suite")
    parser.add_argument(
        "--suite",
        choices=["planning", "build", "full"],
        default="full",
        help="Which eval suite to run (default: full)",
    )
    parser.add_argument(
        "--fixtures",
        help="Comma-separated list of entity fixture paths for build eval",
    )
    parser.add_argument(
        "--golden",
        help="Name of golden plan for planning eval (e.g., 'sqli_scenario')",
    )
    parser.add_argument(
        "--request",
        help="User request for planning eval",
    )
    parser.add_argument(
        "--output",
        default="eval_results",
        help="Output directory for results (default: eval_results)",
    )
    artifact_group = parser.add_mutually_exclusive_group()
    artifact_group.add_argument(
        "--artifacts",
        dest="artifacts",
        action="store_true",
        default=None,
        help="Save LLM conversations and generated scripts alongside eval results"
             " (overrides goe.toml [artifacts].enabled)",
    )
    artifact_group.add_argument(
        "--no-artifacts",
        dest="artifacts",
        action="store_false",
        help="Disable artifact saving for this eval run",
    )
    parser.add_argument(
        "--llm-judge",
        action="store_true",
        help="Use LLM-as-a-judge for semantic graph validation (planning eval only)",
    )

    args = parser.parse_args()

    # Resolve artifact capture flag: CLI → config
    from goe.config import GoEConfig
    cfg = GoEConfig.get()
    capture_artifacts = args.artifacts if args.artifacts is not None else cfg.save_artifacts

    if args.suite == "planning" and not (args.golden and args.request):
        print("Error: --golden and --request are required for planning eval", file=sys.stderr)
        sys.exit(1)

    if args.suite == "build" and not args.fixtures:
        print("Error: --fixtures is required for build eval", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output)

    if args.suite == "planning":
        from goe.eval.runner import run_planning_eval
        from goe.eval.golden import load_golden
        from goe.eval.report import EvalReport, print_summary
        from datetime import datetime

        golden = load_golden(args.golden)
        # For standalone planning eval, create run_dir only if capturing
        plan_run_dir = None
        if capture_artifacts:
            from goe.artifacts.run import _make_timestamp
            plan_run_dir = output_dir / _make_timestamp()
            plan_run_dir.mkdir(parents=True, exist_ok=True)

        result = run_planning_eval(
            args.request,
            golden,
            capture_artifacts=capture_artifacts,
            run_dir=plan_run_dir,
        )

        # Build minimal report
        summary = result["metrics_session"].summary()

        # Check if planning succeeded
        plan_result = result["plan_result"]
        if not plan_result.success:
            print(f"\n❌ Planning FAILED after {plan_result.attempts} attempts")
            if plan_result.final_violations:
                print(f"Violations: {len(plan_result.final_violations)}")
                for v in plan_result.final_violations[:3]:
                    print(f"  - [{v.check}] {v.message}")

        # Convert adherence_score to dict if it exists
        adherence_dict = None
        if result["adherence_score"]:
            adherence_dict = result["adherence_score"].model_dump()

        report = EvalReport(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            suite="planning",
            total_llm_calls=summary["total_calls"],
            total_input_tokens=summary["total_input_tokens"],
            total_output_tokens=summary["total_output_tokens"],
            total_tokens=summary["total_tokens"],
            total_latency_ms=summary["total_latency_ms"],
            avg_latency_ms=summary["avg_latency_ms"],
            calls_by_caller=summary["calls_by_caller"],
            plan_adherence=adherence_dict,
        )
        print_summary(report)

        # Optional: LLM-as-a-judge evaluation
        if args.llm_judge and plan_result.graph:
            from goe.eval.llm_judge import judge_graph_quality, print_judge_result
            print("\n[Running LLM judge evaluation...]")
            judge_result = judge_graph_quality(plan_result.graph, args.request)
            print_judge_result(judge_result)

        if capture_artifacts and plan_run_dir:
            print(f"Artifacts written to: {plan_run_dir}")

    elif args.suite == "build":
        from goe.eval.runner import run_build_eval
        from goe.eval.report import EvalReport, EntityDetail, print_summary
        from datetime import datetime
        import statistics

        fixtures = [f.strip() for f in args.fixtures.split(",")]
        # For standalone build eval, create run_dir only if capturing
        build_run_dir = None
        if capture_artifacts:
            from goe.artifacts.run import _make_timestamp
            build_run_dir = output_dir / _make_timestamp()
            build_run_dir.mkdir(parents=True, exist_ok=True)

        result = run_build_eval(
            fixtures,
            output_dir,
            capture_artifacts=capture_artifacts,
            run_dir=build_run_dir,
        )

        # Build report
        results = result["results"]
        entities = result["entities"]
        entity_metrics = result["entity_metrics"]
        summary = result["metrics_session"].summary()

        failure_breakdown = {}
        for r in results:
            if r.failure_category:
                failure_breakdown[r.failure_category] = failure_breakdown.get(r.failure_category, 0) + 1

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

        attempts = [r.attempts for r in results]

        report = EvalReport(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            suite="build",
            total_llm_calls=summary["total_calls"],
            total_input_tokens=summary["total_input_tokens"],
            total_output_tokens=summary["total_output_tokens"],
            total_tokens=summary["total_tokens"],
            total_latency_ms=summary["total_latency_ms"],
            avg_latency_ms=summary["avg_latency_ms"],
            calls_by_caller=summary["calls_by_caller"],
            entities_tested=len(results),
            entities_passed=sum(1 for r in results if r.status.value == "PASSED"),
            entities_failed=sum(1 for r in results if r.status.value == "FAILED"),
            mean_attempts=sum(attempts) / len(attempts) if attempts else 0,
            median_attempts=statistics.median(attempts) if attempts else 0,
            max_attempts=max(attempts) if attempts else 0,
            failure_breakdown=failure_breakdown,
            entity_details=entity_details,
        )
        print_summary(report)
        if capture_artifacts and build_run_dir:
            print(f"Artifacts written to: {build_run_dir}")

    elif args.suite == "full":
        from goe.eval.runner import run_full_eval
        from goe.eval.report import print_summary

        fixtures = [f.strip() for f in args.fixtures.split(",")] if args.fixtures else None
        report = run_full_eval(
            build_fixtures=fixtures,
            planning_golden=args.golden,
            planning_request=args.request,
            output_dir=output_dir,
            capture_artifacts=capture_artifacts,
        )
        print_summary(report)
        print(f"Results written to: {output_dir}")


if __name__ == "__main__":
    main()
