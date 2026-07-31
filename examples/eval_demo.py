#!/usr/bin/env python3
"""
Demonstration of the GoE v2 evaluation system.

This script shows how to:
1. Start a metrics session
2. Run a build pipeline
3. Collect and display metrics
"""

import sys
from pathlib import Path

# Add parent directory to path so we can import goe
sys.path.insert(0, str(Path(__file__).parent.parent))


def demo_basic_metrics():
    """Basic example: instrument a single build run."""
    from goe.metrics import start_session, end_session
    from goe.build import build_entity
    from goe.models.entity import Entity
    import yaml

    print("=" * 60)
    print("Demo: Basic Metrics Collection")
    print("=" * 60)

    # Load an entity fixture
    fixture_path = Path(__file__).parent.parent / "tests" / "fixtures" / "entities" / "sqli_express.yaml"
    with open(fixture_path) as f:
        entity = Entity.model_validate(yaml.safe_load(f))

    print(f"\nBuilding entity: {entity.id}")
    print(f"Runtime: {entity.runtime.value}")
    print(f"Atoms: {entity.atoms}\n")

    # Start metrics collection
    session = start_session()

    # Run the build
    result = build_entity(entity, verbose=False)

    # End metrics collection
    session = end_session()

    # Display results
    print(f"\n{'─' * 60}")
    print(f"Build Result: {result.status.value}")
    print(f"Attempts: {result.attempts}")
    if result.failure_reason:
        print(f"Failure: {result.failure_reason}")

    # Display metrics
    summary = session.summary()
    print(f"\n{'─' * 60}")
    print("Metrics Summary:")
    print(f"{'─' * 60}")
    print(f"  Total LLM calls: {summary['total_calls']}")
    print(f"  Total tokens: {summary['total_tokens']:,}")
    print(f"    Input: {summary['total_input_tokens']:,}")
    print(f"    Output: {summary['total_output_tokens']:,}")
    print(f"  Total latency: {summary['total_latency_ms'] / 1000:.1f}s")
    print(f"  Avg latency per call: {summary['avg_latency_ms']:.0f}ms")

    print(f"\n  Calls by agent:")
    for caller, stats in sorted(summary['calls_by_caller'].items()):
        tokens = stats['input_tokens'] + stats['output_tokens']
        print(f"    {caller:30s}: {stats['count']:2d} calls, {tokens:8,} tokens, {stats['latency_ms']/1000:5.1f}s")

    print(f"\n{'=' * 60}\n")


def demo_eval_runner():
    """Show how to use the eval runner for multiple fixtures."""
    from goe.eval.runner import run_build_eval
    from goe.eval.report import print_summary, EvalReport

    print("=" * 60)
    print("Demo: Eval Runner (Multiple Fixtures)")
    print("=" * 60)

    # Run eval on multiple fixtures
    fixtures = [
        "tests/fixtures/entities/sqli_express.yaml",
        # Add more fixtures here as they become available
    ]

    print(f"\nRunning build eval on {len(fixtures)} fixture(s)...")
    result = run_build_eval(fixtures)

    # Build report
    from datetime import datetime
    results = result["results"]
    summary = result["metrics_session"].summary()

    failure_breakdown = {}
    for r in results:
        if r.failure_category:
            failure_breakdown[r.failure_category] = failure_breakdown.get(r.failure_category, 0) + 1

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
        mean_attempts=sum(r.attempts for r in results) / len(results) if results else 0,
        failure_breakdown=failure_breakdown,
    )

    print_summary(report)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GoE v2 eval system demo")
    parser.add_argument(
        "--mode",
        choices=["basic", "runner"],
        default="basic",
        help="Which demo to run (default: basic)",
    )

    args = parser.parse_args()

    if args.mode == "basic":
        demo_basic_metrics()
    elif args.mode == "runner":
        demo_eval_runner()
