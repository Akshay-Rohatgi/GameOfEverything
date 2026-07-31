"""Tests for EntityGraph container model — construction, lookups, YAML round-trip."""

from pathlib import Path

import pytest
import yaml

from goe.graph.models import EntityGraph, ValidationResult, Violation

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


def test_load_valid_2entity_chain():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    assert len(graph.entities) == 2
    assert len(graph.edges) == 2
    assert len(graph.systems) == 1


def test_entity_by_id():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    e = graph.entity_by_id("sqli_entity")
    assert e is not None
    assert e.id == "sqli_entity"


def test_entity_by_id_missing():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    assert graph.entity_by_id("does_not_exist") is None


def test_edge_by_id():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    e = graph.edge_by_id("operator_to_sqli")
    assert e is not None
    assert e.from_entity == "operator"


def test_system_by_id():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    s = graph.system_by_id("target_system")
    assert s is not None
    assert s.network.hostname == "target"


def test_edges_to():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    edges = graph.edges_to("sqli_entity")
    assert len(edges) == 1
    assert edges[0].id == "operator_to_sqli"


def test_edges_from():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    edges = graph.edges_from("sqli_entity")
    assert len(edges) == 1
    assert edges[0].id == "sqli_to_ssh"


def test_yaml_round_trip(tmp_path):
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    out = tmp_path / "round_trip.yaml"
    graph.to_yaml(out)
    reloaded = EntityGraph.from_yaml(out)
    assert reloaded.model_dump() == graph.model_dump()


def test_yaml_str_round_trip():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    yaml_str = graph.to_yaml_str()
    data = yaml.safe_load(yaml_str)
    reloaded = EntityGraph.model_validate(data, strict=False)
    assert reloaded.model_dump() == graph.model_dump()


def test_load_multisystem():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_multisystem.yaml")
    assert len(graph.systems) == 2
    assert graph.system_by_id("web_system") is not None
    assert graph.system_by_id("db_system") is not None


def test_violation_model():
    v = Violation(check="edge_coverage", entity_id="e1", message="test")
    assert v.check == "edge_coverage"
    assert v.edge_id is None


def test_validation_result_valid():
    r = ValidationResult(valid=True)
    assert r.valid
    assert r.violations == []


def test_validation_result_invalid():
    v = Violation(check="no_cycles", message="cycle detected")
    r = ValidationResult(valid=False, violations=[v])
    assert not r.valid
    assert len(r.violations) == 1
