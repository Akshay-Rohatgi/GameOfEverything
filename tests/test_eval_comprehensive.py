"""Comprehensive eval system tests covering various scenarios."""

import pytest


@pytest.mark.eval
@pytest.mark.docker
@pytest.mark.llm
def test_multi_entity_build_eval():
    """Test build eval with multiple entities to verify per-entity breakdown."""
    from goe.eval.runner import run_build_eval

    fixtures = [
        "tests/fixtures/entities/sqli_express.yaml",
        "tests/fixtures/entities/cmdi_flask.yaml",
    ]
    result = run_build_eval(fixtures)

    # Should have 2 entities
    assert len(result["results"]) == 2
    assert len(result["entities"]) == 2
    assert len(result["entity_metrics"]) == 2

    # Check entity details captured
    for metrics in result["entity_metrics"]:
        assert "entity_id" in metrics
        assert "calls" in metrics
        assert len(metrics["calls"]) > 0  # Should have LLM calls

    # Verify all passed (these are confirmed fixtures)
    for entity_result in result["results"]:
        assert entity_result.status.value == "PASSED"


def test_planning_eval_golden_comparison():
    """Test planning eval golden comparison logic."""
    from goe.eval.golden import GoldenPlan, ExpectedEntity, compare_plan
    from goe.graph.models import EntityGraph
    from goe.models.entity import Entity
    from goe.models.system import System, NetworkConfig

    # Create a golden plan expecting SQLi
    golden = GoldenPlan(
        request="test",
        expected_systems=["web"],
        expected_entities=[
            ExpectedEntity(
                description="Web app with SQLi",
                runtime="express",
                atoms=["sqli_union"],
            )
        ],
        expected_edges=[],
    )

    # Create an actual graph with different naming but same atoms
    actual = EntityGraph(
        entities=[
            Entity(
                id="different_id",  # Different ID
                description="Different description",  # Different description
                system_id="web",
                runtime="express",
                atoms=["sqli_union"],  # SAME atom
                requires=[],
                provides=[],
            )
        ],
        edges=[],
        systems=[
            System(
                id="web",
                os="ubuntu",
                services=["nginx"],
                network=NetworkConfig(hostname="web", exposed_ports=[80], internal_ports=[]),
            )
        ],
    )

    # Should match on atoms, not ID/description
    score = compare_plan(actual, golden)
    assert score.entity_coverage == 1.0, "Should match based on atom set"
    assert score.structural_match is True


def test_planning_eval_missing_entity():
    """Test that missing entities are detected."""
    from goe.eval.golden import GoldenPlan, ExpectedEntity, compare_plan
    from goe.graph.models import EntityGraph
    from goe.models.entity import Entity
    from goe.models.system import System, NetworkConfig

    # Golden expects TWO entities
    golden = GoldenPlan(
        request="test",
        expected_systems=["web"],
        expected_entities=[
            ExpectedEntity(description="Entity 1", runtime="express", atoms=["sqli_union"]),
            ExpectedEntity(description="Entity 2", runtime="flask", atoms=["xss_stored"]),
        ],
        expected_edges=[],
    )

    # Actual only has ONE entity
    actual = EntityGraph(
        entities=[
            Entity(
                id="e1",
                description="Only one",
                system_id="web",
                runtime="express",
                atoms=["sqli_union"],
                requires=[],
                provides=[],
            )
        ],
        edges=[],
        systems=[
            System(
                id="web",
                os="ubuntu",
                services=["nginx"],
                network=NetworkConfig(hostname="web", exposed_ports=[80], internal_ports=[]),
            )
        ],
    )

    score = compare_plan(actual, golden)
    assert score.entity_coverage == 0.5, "Should detect 1 out of 2 entities"
    assert len(score.missing_entities) == 1
    assert "xss_stored" in score.missing_entities[0]


@pytest.mark.llm
def test_llm_judge_evaluation():
    """Test LLM judge can evaluate a simple graph."""
    from goe.eval.llm_judge import judge_graph_quality
    from goe.graph.models import EntityGraph
    from goe.models.entity import Entity
    from goe.models.edge import Edge, ParamValue
    from goe.models.system import System, NetworkConfig

    # Create a simple valid graph (SQLi scenario)
    graph = EntityGraph(
        entities=[
            Entity(
                id="sqli_app",
                description="Web app with SQL injection",
                system_id="web",
                runtime="express",
                atoms=["sqli_union"],
                requires=[],
                provides=["creds"],
            )
        ],
        edges=[
            Edge(
                id="op_to_app",
                from_entity="operator",
                to_entity="sqli_app",
                type="network_reach",
                params={
                    "host": ParamValue(structural="target", concrete=None),
                    "port": ParamValue(structural="80", concrete=None),
                },
            ),
            Edge(
                id="sqli_to_creds",
                from_entity="sqli_app",
                to_entity=None,  # Terminal edge
                type="creds_for",
                params={
                    "user": ParamValue(structural="admin", concrete=None),
                },
            ),
        ],
        systems=[
            System(
                id="web",
                os="ubuntu",
                services=["web"],
                network=NetworkConfig(hostname="target", exposed_ports=[80], internal_ports=[]),
            )
        ],
    )

    result = judge_graph_quality(graph, "web app with SQL injection leading to credential theft")

    # Should pass basic checks
    assert isinstance(result, dict)
    assert "logical_path" in result
    assert "overall_quality" in result

    # For a valid SQLi graph, should be positive
    assert result["logical_path"] is True, "Should recognize valid attack path"
    assert result["overall_quality"] in ["good", "acceptable"], "Should rate valid graph positively"


def test_metrics_session_per_entity_tracking():
    """Test that metrics are correctly tracked per entity."""
    from goe.metrics import start_session, end_session, LLMCallRecord
    import time

    session = start_session()

    # Simulate LLM calls for first entity
    session.record(LLMCallRecord(
        call_id="e1_1",
        timestamp=time.time(),
        caller="architect",
        model_id="test",
        input_tokens=100,
        output_tokens=50,
        latency_ms=1000,
    ))

    # Track position
    first_entity_end = len(session.records)

    # Simulate LLM calls for second entity
    session.record(LLMCallRecord(
        call_id="e2_1",
        timestamp=time.time(),
        caller="architect",
        model_id="test",
        input_tokens=120,
        output_tokens=60,
        latency_ms=1200,
    ))

    session = end_session()

    # Verify we can slice metrics per entity
    first_entity_calls = session.records[:first_entity_end]
    second_entity_calls = session.records[first_entity_end:]

    assert len(first_entity_calls) == 1
    assert len(second_entity_calls) == 1
    assert first_entity_calls[0].call_id == "e1_1"
    assert second_entity_calls[0].call_id == "e2_1"


def test_eval_report_serialization():
    """Test that EvalReport can be serialized to JSON."""
    from goe.eval.report import EvalReport, EntityDetail
    from datetime import datetime
    import json

    report = EvalReport(
        timestamp=datetime.now().isoformat(),
        suite="build",
        total_llm_calls=10,
        total_input_tokens=5000,
        total_output_tokens=1000,
        total_tokens=6000,
        total_latency_ms=30000,
        avg_latency_ms=3000,
        calls_by_caller={
            "architect": {"count": 2, "input_tokens": 1000, "output_tokens": 200, "latency_ms": 6000}
        },
        entities_tested=2,
        entities_passed=2,
        entities_failed=0,
        mean_attempts=1.0,
        median_attempts=1.0,
        max_attempts=1,
        entity_details=[
            EntityDetail(
                id="test_entity",
                runtime="express",
                atoms=["sqli_union"],
                status="PASSED",
                attempts=1,
                llm_calls=5,
                total_tokens=3000,
                latency_ms=15000,
            )
        ],
        plan_adherence={"entity_coverage": 1.0, "edge_coverage": 1.0, "structural_match": True},
    )

    # Should serialize to JSON without errors
    json_str = report.model_dump_json()
    assert json_str

    # Should deserialize back
    data = json.loads(json_str)
    assert data["suite"] == "build"
    assert data["entities_tested"] == 2
    assert len(data["entity_details"]) == 1
