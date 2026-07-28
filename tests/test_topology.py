"""Tests for topology utilities — topological sort, reachability, dependency map."""

from pathlib import Path

import pytest

from goe.graph.models import EntityGraph
from goe.graph.topology import CycleError, dependency_map, reachable_from_operator, topological_sort

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


def test_topological_sort_2entity():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    order = topological_sort(graph)
    assert order.index("sqli_entity") < order.index("ssh_entity")


def test_topological_sort_4entity():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_4entity_chain.yaml")
    order = topological_sort(graph)
    assert order.index("entity_a") < order.index("entity_b")
    assert order.index("entity_b") < order.index("entity_c")
    assert order.index("entity_c") < order.index("entity_d")


def test_topological_sort_cycle():
    graph = EntityGraph.from_yaml(FIXTURES / "cycle.yaml")
    with pytest.raises(CycleError) as exc_info:
        topological_sort(graph)
    assert "entity_a" in exc_info.value.cycle_members or "entity_b" in exc_info.value.cycle_members


def test_topological_sort_multisystem():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_multisystem.yaml")
    order = topological_sort(graph)
    assert order.index("sqli_webapp") < order.index("ssh_pivot")


def test_reachable_from_operator_2entity():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    reachable = reachable_from_operator(graph)
    assert "sqli_entity" in reachable
    assert "ssh_entity" in reachable


def test_reachable_from_operator_orphan():
    graph = EntityGraph.from_yaml(FIXTURES / "orphan_entity.yaml")
    reachable = reachable_from_operator(graph)
    assert "reachable_entity" in reachable
    assert "orphan_entity" not in reachable


def test_reachable_from_operator_4entity():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_4entity_chain.yaml")
    reachable = reachable_from_operator(graph)
    assert reachable == {"entity_a", "entity_b", "entity_c", "entity_d"}


def test_dependency_map_2entity():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    deps = dependency_map(graph)
    assert deps["sqli_entity"] == set()  # depends only on operator
    assert deps["ssh_entity"] == {"sqli_entity"}


def test_dependency_map_4entity():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_4entity_chain.yaml")
    deps = dependency_map(graph)
    assert deps["entity_a"] == set()
    assert deps["entity_b"] == {"entity_a"}
    assert deps["entity_c"] == {"entity_b"}
    assert deps["entity_d"] == {"entity_c"}
