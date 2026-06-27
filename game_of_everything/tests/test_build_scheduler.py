"""Tests for BuildScheduler — build ordering, value propagation, failure cascading."""

from pathlib import Path

import pytest

from goe.graph.models import EntityGraph
from goe.graph.build_scheduler import BuildScheduler, EntityState

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


def test_initial_state_2entity():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    sched = BuildScheduler(graph)
    states = sched.states()
    # sqli_entity has no non-operator deps → buildable
    assert states["sqli_entity"] == EntityState.buildable
    # ssh_entity depends on sqli_entity → pending
    assert states["ssh_entity"] == EntityState.pending


def test_next_buildable_returns_first_entity():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    sched = BuildScheduler(graph)
    result = sched.next_buildable()
    assert result is not None
    entity, incoming = result
    assert entity.id == "sqli_entity"
    assert incoming == {}  # no concrete values yet


def test_building_state_after_next():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    sched = BuildScheduler(graph)
    sched.next_buildable()
    assert sched.states()["sqli_entity"] == EntityState.building


def test_report_complete_propagates_values():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    sched = BuildScheduler(graph)
    sched.next_buildable()  # get sqli_entity
    sched.report_complete("sqli_entity", {"sqli_to_ssh": {"user": "admin", "secret": "hunter2"}})

    result = sched.next_buildable()
    assert result is not None
    entity, incoming = result
    assert entity.id == "ssh_entity"
    assert incoming == {"sqli_to_ssh": {"user": "admin", "secret": "hunter2"}}


def test_report_complete_marks_complete():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    sched = BuildScheduler(graph)
    sched.next_buildable()
    sched.report_complete("sqli_entity", {})
    assert sched.states()["sqli_entity"] == EntityState.complete


def test_is_complete_false_initially():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    sched = BuildScheduler(graph)
    assert not sched.is_complete()


def test_is_complete_after_all_done():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    sched = BuildScheduler(graph)
    e1, _ = sched.next_buildable()
    sched.report_complete(e1.id, {"sqli_to_ssh": {"secret": "creds"}})
    e2, _ = sched.next_buildable()
    sched.report_complete(e2.id, {})
    assert sched.is_complete()


def test_4entity_chain_builds_in_order():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_4entity_chain.yaml")
    sched = BuildScheduler(graph)
    build_order = []

    while not sched.is_complete():
        result = sched.next_buildable()
        if result is None:
            break
        entity, _ = result
        build_order.append(entity.id)
        # Simulate providing the outgoing edge value
        outgoing = {e.id: {"v": f"value_from_{entity.id}"} for e in graph.edges_from(entity.id)}
        sched.report_complete(entity.id, outgoing)

    assert build_order == ["entity_a", "entity_b", "entity_c", "entity_d"]


def test_failure_skips_downstream():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_4entity_chain.yaml")
    sched = BuildScheduler(graph)

    # Build entity_a successfully
    e, _ = sched.next_buildable()
    assert e.id == "entity_a"
    outgoing = {e_out.id: {"v": "val"} for e_out in graph.edges_from(e.id)}
    sched.report_complete(e.id, outgoing)

    # Fail entity_b
    e, _ = sched.next_buildable()
    assert e.id == "entity_b"
    skipped = sched.report_failed(e.id)

    # entity_c and entity_d should be skipped
    assert set(skipped) == {"entity_c", "entity_d"}
    assert sched.states()["entity_c"] == EntityState.skipped
    assert sched.states()["entity_d"] == EntityState.skipped
    assert sched.is_complete()


def test_failure_does_not_skip_independent_entities():
    """In a graph with parallel branches, failing one branch shouldn't affect the other."""
    from goe.graph.models import EntityGraph
    from goe.models.entity import Entity, Requirement
    from goe.models.edge import Edge, EdgeType, ParamValue
    from goe.models.system import System, NetworkConfig

    system = System(
        id="s",
        os="ubuntu_22_04",
        services=[],
        network=NetworkConfig(hostname="target", exposed_ports=[3000], internal_ports=[]),
    )
    # operator → branch_a (independent of branch_b)
    # operator → branch_b (independent of branch_a)
    op_to_a = Edge(id="op_to_a", from_entity="operator", to_entity="branch_a",
                   type=EdgeType.network_reach,
                   params={"host": ParamValue(structural="target"), "port": ParamValue(structural="3000")})
    op_to_b = Edge(id="op_to_b", from_entity="operator", to_entity="branch_b",
                   type=EdgeType.network_reach,
                   params={"host": ParamValue(structural="target"), "port": ParamValue(structural="3000")})
    branch_a = Entity(id="branch_a", description="a", system_id="s",
                      requires=[Requirement(edge_id="op_to_a")], provides=[])
    branch_b = Entity(id="branch_b", description="b", system_id="s",
                      requires=[Requirement(edge_id="op_to_b")], provides=[])

    graph = EntityGraph(systems=[system], entities=[branch_a, branch_b], edges=[op_to_a, op_to_b])
    sched = BuildScheduler(graph)

    e, _ = sched.next_buildable()
    skipped = sched.report_failed(e.id)
    assert skipped == []  # other branch unaffected

    # Other branch still buildable
    result = sched.next_buildable()
    assert result is not None


def test_no_buildable_returns_none_when_all_building():
    graph = EntityGraph.from_yaml(FIXTURES / "valid_2entity_chain.yaml")
    sched = BuildScheduler(graph)
    sched.next_buildable()  # takes sqli_entity, marks it building
    # ssh_entity still pending (depends on sqli_entity which is building, not complete)
    assert sched.next_buildable() is None
