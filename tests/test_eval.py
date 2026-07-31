"""Tests for evaluation system."""

import pytest


@pytest.mark.eval
@pytest.mark.docker
@pytest.mark.llm
def test_build_eval_sqli_express():
    """Test build eval on sqli_express fixture."""
    from goe.eval.runner import run_build_eval
    from pathlib import Path

    fixtures = ["tests/fixtures/entities/sqli_express.yaml"]
    result = run_build_eval(fixtures)

    assert len(result["results"]) == 1
    entity_result = result["results"][0]
    assert entity_result.id == "sqli_entity"
    # We expect this fixture to pass
    assert entity_result.status.value == "PASSED"

    # Check metrics were collected
    session = result["metrics_session"]
    summary = session.summary()
    assert summary["total_calls"] > 0
    assert summary["total_tokens"] > 0


def test_golden_plan_comparison():
    """Test comparing an actual plan against golden baseline."""
    from goe.eval.golden import GoldenPlan, ExpectedEntity, compare_plan
    from goe.graph.models import EntityGraph
    from goe.models.entity import Entity
    from goe.models.edge import Edge
    from goe.models.system import System, NetworkConfig

    # Create a golden plan
    golden = GoldenPlan(
        request="test",
        expected_systems=["web"],
        expected_entities=[
            ExpectedEntity(
                description="Test entity",
                runtime="express",
                atoms=["sqli_union"],
            )
        ],
        expected_edges=[],
    )

    # Create an actual graph that matches
    actual = EntityGraph(
        entities=[
            Entity(
                id="e1",
                description="Web app",
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
    assert score.entity_coverage == 1.0
    assert score.structural_match is True


def test_golden_plan_mismatch():
    """Test comparing a plan with missing entities."""
    from goe.eval.golden import GoldenPlan, ExpectedEntity, compare_plan
    from goe.graph.models import EntityGraph
    from goe.models.entity import Entity
    from goe.models.system import System, NetworkConfig

    golden = GoldenPlan(
        request="test",
        expected_systems=["web"],
        expected_entities=[
            ExpectedEntity(
                description="Entity 1",
                runtime="express",
                atoms=["sqli_union"],
            ),
            ExpectedEntity(
                description="Entity 2",
                runtime="flask",
                atoms=["xss_stored"],
            ),
        ],
        expected_edges=[],
    )

    # Actual only has one entity
    actual = EntityGraph(
        entities=[
            Entity(
                id="e1",
                description="Web app",
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
    assert score.entity_coverage == 0.5  # 1 out of 2
    assert score.structural_match is False
    assert len(score.missing_entities) == 1
