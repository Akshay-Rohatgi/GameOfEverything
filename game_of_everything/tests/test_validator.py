"""Tests for static graph validator — all 7 checks."""

from pathlib import Path

import pytest

from goe.graph.models import EntityGraph
from goe.graph.validator import validate

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


# --- Valid graphs pass ---

def test_valid_2entity_chain():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    result = validate(graph)
    assert result.valid, [v.message for v in result.violations]


def test_valid_4entity_chain():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_4entity_chain.yaml")
    result = validate(graph)
    assert result.valid, [v.message for v in result.violations]


def test_valid_multisystem():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_multisystem.yaml")
    result = validate(graph)
    assert result.valid, [v.message for v in result.violations]


# --- Check 1: Edge coverage ---

def test_missing_edge_fails():
    graph = EntityGraph.from_yaml(FIXTURES / "missing_edge.yaml")
    result = validate(graph)
    assert not result.valid
    checks = {v.check for v in result.violations}
    assert "edge_coverage" in checks


def test_missing_edge_violation_names_entity():
    graph = EntityGraph.from_yaml(FIXTURES / "missing_edge.yaml")
    result = validate(graph)
    edge_coverage_violations = [v for v in result.violations if v.check == "edge_coverage"]
    assert any(v.entity_id == "entity_b" for v in edge_coverage_violations)


# --- Check 2: Type compatibility ---

def test_type_mismatch_fails():
    graph = EntityGraph.from_yaml(FIXTURES / "type_mismatch.yaml")
    result = validate(graph)
    assert not result.valid
    checks = {v.check for v in result.violations}
    assert "type_compatibility" in checks


def test_type_mismatch_names_edge():
    graph = EntityGraph.from_yaml(FIXTURES / "type_mismatch.yaml")
    result = validate(graph)
    tc_violations = [v for v in result.violations if v.check == "type_compatibility"]
    assert any(v.edge_id == "op_to_a" for v in tc_violations)


# --- Check 3: Reachability ---

def test_orphan_entity_fails():
    graph = EntityGraph.from_yaml(FIXTURES / "orphan_entity.yaml")
    result = validate(graph)
    assert not result.valid
    checks = {v.check for v in result.violations}
    assert "reachability" in checks


def test_orphan_entity_violation_names_entity():
    graph = EntityGraph.from_yaml(FIXTURES / "orphan_entity.yaml")
    result = validate(graph)
    reachability_violations = [v for v in result.violations if v.check == "reachability"]
    assert any(v.entity_id == "orphan_entity" for v in reachability_violations)


# --- Check 4: Fan-out consistency ---

def test_fan_out_duplicate_edge_fails():
    """Build a minimal graph where two entities require the same edge_id."""
    from goe.graph.models import EntityGraph
    from goe.models.entity import Entity, Requirement
    from goe.models.edge import Edge, EdgeType, ParamValue
    from goe.models.system import System, NetworkConfig

    system = System(
        id="s",
        os="ubuntu_22_04",
        services=["web"],
        network=NetworkConfig(hostname="target", exposed_ports=[3000], internal_ports=[]),
    )
    shared_edge = Edge(
        id="op_to_both",
        from_entity="operator",
        to_entity="entity_a",
        type=EdgeType.network_reach,
        params={
            "host": ParamValue(structural="target"),
            "port": ParamValue(structural="3000"),
        },
    )
    entity_a = Entity(
        id="entity_a",
        description="a",
        system_id="s",
        requires=[Requirement(edge_id="op_to_both")],
        provides=[],
    )
    entity_b = Entity(
        id="entity_b",
        description="b",
        system_id="s",
        requires=[Requirement(edge_id="op_to_both")],  # same edge_id as entity_a
        provides=[],
    )
    graph = EntityGraph(systems=[system], entities=[entity_a, entity_b], edges=[shared_edge])
    result = validate(graph)
    assert not result.valid
    checks = {v.check for v in result.violations}
    assert "fan_out_consistency" in checks


# --- Check 5: System reference validity ---

def test_invalid_system_ref_fails():
    graph = EntityGraph.from_yaml(FIXTURES / "invalid_system_ref.yaml")
    result = validate(graph)
    assert not result.valid
    checks = {v.check for v in result.violations}
    assert "system_reference_validity" in checks


def test_invalid_system_ref_names_entity():
    graph = EntityGraph.from_yaml(FIXTURES / "invalid_system_ref.yaml")
    result = validate(graph)
    sys_violations = [v for v in result.violations if v.check == "system_reference_validity"]
    assert any(v.entity_id == "entity_a" for v in sys_violations)


# --- Check 6: No cycles ---

def test_cycle_fails():
    graph = EntityGraph.from_yaml(FIXTURES / "cycle.yaml")
    result = validate(graph)
    assert not result.valid
    checks = {v.check for v in result.violations}
    assert "no_cycles" in checks


# --- Check 7: Initial access ---

def test_no_initial_access_fails():
    graph = EntityGraph.from_yaml(FIXTURES / "no_initial_access.yaml")
    result = validate(graph)
    assert not result.valid
    checks = {v.check for v in result.violations}
    assert "initial_access_exists" in checks


# --- Multiple violations ---

def test_multiple_violations_reported():
    """A bad graph can produce violations from multiple checks simultaneously."""
    graph = EntityGraph.from_yaml(FIXTURES / "missing_edge.yaml")
    result = validate(graph)
    assert not result.valid
    assert len(result.violations) >= 1
