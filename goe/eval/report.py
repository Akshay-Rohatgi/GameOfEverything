"""Evaluation report models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CallerStats(BaseModel):
    """Aggregated metrics for a specific caller."""

    model_config = ConfigDict(extra="allow")

    count: int
    input_tokens: int
    output_tokens: int
    latency_ms: float


class PlanAdherenceScore(BaseModel):
    """Result of comparing a plan against golden baseline."""

    model_config = ConfigDict(extra="allow")

    entity_coverage: float
    edge_coverage: float
    structural_match: bool
    missing_entities: list[str] = []
    missing_edges: list[str] = []
    extra_entities: int = 0
    extra_edges: int = 0


class EntityDetail(BaseModel):
    """Per-entity detail for quality report."""

    model_config = ConfigDict(extra="allow")

    id: str
    runtime: str
    atoms: list[str]
    status: str
    attempts: int
    failure_category: str | None = None
    failure_reason: str | None = None
    llm_calls: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0


class EvalReport(BaseModel):
    """Top-level evaluation report."""

    model_config = ConfigDict(extra="allow")

    timestamp: str
    suite: str  # "planning", "build", "full"

    # Efficiency metrics
    total_llm_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    calls_by_caller: dict[str, CallerStats] = {}

    # Quality metrics (build suite)
    entities_tested: int = 0
    entities_passed: int = 0
    entities_failed: int = 0
    mean_attempts: float = 0.0
    median_attempts: float = 0.0
    max_attempts: int = 0
    failure_breakdown: dict[str, int] = {}  # DiagnosisCategory -> count
    entity_details: list[EntityDetail] = []  # Per-entity breakdown

    # Quality metrics (planning suite)
    plan_adherence: dict | None = None  # PlanAdherenceScore serialized to dict


def print_summary(report: EvalReport) -> None:
    """Print a human-readable summary of the evaluation report."""
    print(f"\n{'='*60}")
    print(f"Eval Report: {report.suite}")
    print(f"Timestamp: {report.timestamp}")
    print(f"{'='*60}\n")

    print("## Efficiency Metrics")
    print(f"  Total LLM calls: {report.total_llm_calls}")
    print(f"  Total tokens: {report.total_tokens:,} (in: {report.total_input_tokens:,}, out: {report.total_output_tokens:,})")
    print(f"  Total latency: {report.total_latency_ms / 1000:.1f}s")
    print(f"  Avg latency per call: {report.avg_latency_ms:.0f}ms")

    if report.calls_by_caller:
        print("\n  Calls by agent:")
        for caller, stats in sorted(report.calls_by_caller.items()):
            print(f"    {caller:30s}: {stats.count:3d} calls, {stats.input_tokens + stats.output_tokens:8,} tokens, {stats.latency_ms/1000:5.1f}s")

    if report.suite in ("build", "full") and report.entities_tested > 0:
        print("\n## Quality Metrics (Build)")
        print(f"  Entities tested: {report.entities_tested}")
        print(f"  Passed: {report.entities_passed} ({100 * report.entities_passed / report.entities_tested if report.entities_tested else 0:.1f}%)")
        print(f"  Failed: {report.entities_failed}")
        print(f"\n  Retry statistics:")
        print(f"    Mean attempts: {report.mean_attempts:.2f}")
        print(f"    Median attempts: {report.median_attempts:.1f}")
        print(f"    Max attempts: {report.max_attempts}")

        if report.failure_breakdown:
            print("\n  Failure breakdown:")
            for category, count in sorted(report.failure_breakdown.items()):
                print(f"    {category}: {count}")

        if report.entity_details:
            print("\n  Per-entity details:")
            print(f"    {'Entity ID':<20s} {'Runtime':<12s} {'Status':<8s} {'Attempts':<8s} {'LLM Calls':<10s} {'Tokens':<10s} {'Latency':<10s}")
            print(f"    {'-'*20} {'-'*12} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*10}")
            for entity in report.entity_details:
                status_symbol = "✓" if entity.status == "PASSED" else "✗"
                print(f"    {entity.id:<20s} {entity.runtime:<12s} {status_symbol} {entity.status:<6s} {entity.attempts:<8d} {entity.llm_calls:<10d} {entity.total_tokens:<10,d} {entity.latency_ms/1000:>8.1f}s")

            # Show atoms for each entity
            print(f"\n  Vulnerability coverage:")
            for entity in report.entity_details:
                atoms_str = ", ".join(entity.atoms) if entity.atoms else "(none)"
                print(f"    {entity.id}: {atoms_str}")

    if report.plan_adherence:
        print("\n## Quality Metrics (Planning)")
        adh = report.plan_adherence
        print(f"  Entity coverage: {adh['entity_coverage']:.2%}")
        print(f"  Edge coverage: {adh['edge_coverage']:.2%}")
        print(f"  Structural match: {adh['structural_match']}")
        if adh.get('missing_entities'):
            print(f"\n  Missing entities ({len(adh['missing_entities'])}):")
            for e in adh['missing_entities'][:5]:
                print(f"    - {e}")
        if adh.get('missing_edges'):
            print(f"\n  Missing edges ({len(adh['missing_edges'])}):")
            for e in adh['missing_edges'][:5]:
                print(f"    - {e}")
        if adh.get('extra_entities', 0) > 0:
            print(f"\n  Extra entities (not in golden): {adh['extra_entities']}")
        if adh.get('extra_edges', 0) > 0:
            print(f"  Extra edges (not in golden): {adh['extra_edges']}")

    print(f"\n{'='*60}\n")
