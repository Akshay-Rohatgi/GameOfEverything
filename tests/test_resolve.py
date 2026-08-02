"""Tests for deterministic structural param resolver."""

from pathlib import Path

from goe.graph.models import EntityGraph
from goe.planner.resolve import resolve

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


def test_resolve_fills_host_concrete():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    # Before resolve, concrete is None
    op_edge = graph.edge_by_id("operator_to_sqli")
    assert op_edge.params["host"].concrete is None

    resolved = resolve(graph)

    op_edge_resolved = resolved.edge_by_id("operator_to_sqli")
    assert op_edge_resolved.params["host"].concrete == "target"


def test_resolve_fills_port_for_network_reach():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    op_edge = graph.edge_by_id("operator_to_sqli")
    assert op_edge.params["port"].concrete is None

    resolved = resolve(graph)

    op_edge_resolved = resolved.edge_by_id("operator_to_sqli")
    assert op_edge_resolved.params["port"].concrete == "3000"


def test_resolve_does_not_overwrite_existing_concrete():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    op_edge = graph.edge_by_id("operator_to_sqli")
    op_edge.params["host"].concrete = "already_set"

    resolved = resolve(graph)

    op_edge_resolved = resolved.edge_by_id("operator_to_sqli")
    assert op_edge_resolved.params["host"].concrete == "already_set"


def test_resolve_non_network_reach_edge_no_port():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    # sqli_to_ssh is creds_for, not network_reach — should not get port resolved
    creds_edge = graph.edge_by_id("sqli_to_ssh")
    assert "port" not in creds_edge.params

    resolve(graph)

    creds_edge_after = graph.edge_by_id("sqli_to_ssh")
    assert "port" not in creds_edge_after.params


def test_resolve_multisystem_host():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_multisystem.yaml")
    resolved = resolve(graph)

    # op_to_webapp targets sqli_webapp on web_system with hostname webserver
    op_edge = resolved.edge_by_id("op_to_webapp")
    assert op_edge.params["host"].concrete == "webserver"

    # webapp_to_db_creds targets ssh_pivot on db_system with hostname dbserver
    creds_edge = resolved.edge_by_id("webapp_to_db_creds")
    assert creds_edge.params["host"].concrete == "dbserver"
