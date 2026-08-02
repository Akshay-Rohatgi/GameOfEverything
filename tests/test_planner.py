"""Tests for planning pipeline — mocked bedrock calls."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from goe.graph.models import EntityGraph
from goe.graph.validator import validate
from goe.planner.pipeline import plan

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


def _load_graph_as_parts(path: Path):
    """Load a valid graph fixture and return its parts as JSON strings for mocking."""
    graph = EntityGraph.from_yaml(path)
    systems_json = json.dumps([s.model_dump(mode="json") for s in graph.systems])
    stubs_json = json.dumps([
        {"id": e.id, "description": e.description, "system_id": e.system_id}
        for e in graph.entities
    ])
    # Each entity as individual JSON (for specify step)
    entities_json = [json.dumps(e.model_dump(mode="json")) for e in graph.entities]
    edges_json = json.dumps([e.model_dump(mode="json") for e in graph.edges])

    return {
        "systems": systems_json,
        "stubs": stubs_json,
        "entities_json": entities_json,
        "edges": edges_json,
        "graph": graph,
    }


class TestPlannerHappyPath:
    """Full pipeline with mocked bedrock — verifies graph is produced and validated."""

    def test_2entity_chain(self):
        parts = _load_graph_as_parts(FIXTURES / "valid_2entity_chain.yaml")

        def mock_call(model_id, system, messages, **kwargs):
            content = messages[0]["content"]
            if "infrastructure systems" in content:
                return parts["systems"]
            elif "Decompose this scenario" in content:
                return parts["stubs"]
            elif "Fully specify ALL entities" in content:
                return json.dumps([json.loads(e) for e in parts["entities_json"]])
            elif "wire these entities" in content or "Create the Edge" in content:
                return parts["edges"]
            return "{}"

        with patch("goe.bedrock.call", side_effect=mock_call):
            result = plan("simple sqli to ssh pivot", verbose=False)

        assert result.success, f"Planning failed: {[v.message for v in result.final_violations]}"
        assert result.graph is not None
        assert len(result.graph.entities) == 2
        assert len(result.graph.edges) == 2

    def test_output_graph_passes_validator(self):
        parts = _load_graph_as_parts(FIXTURES / "valid_2entity_chain.yaml")

        def mock_call(model_id, system, messages, **kwargs):
            content = messages[0]["content"]
            if "infrastructure systems" in content:
                return parts["systems"]
            elif "Decompose this scenario" in content:
                return parts["stubs"]
            elif "Fully specify ALL entities" in content:
                return json.dumps([json.loads(e) for e in parts["entities_json"]])
            elif "wire these entities" in content or "Create the Edge" in content:
                return parts["edges"]
            return "{}"

        with patch("goe.bedrock.call", side_effect=mock_call):
            result = plan("simple sqli to ssh pivot")

        assert result.success
        validation = validate(result.graph)
        assert validation.valid, [v.message for v in validation.violations]


class TestPlannerRetry:
    """Test retry logic — validator failure triggers edge reconnection retry."""

    def test_edge_retry_on_validation_failure(self):
        """First connect_edges call returns bad edges; second call returns good ones."""
        parts = _load_graph_as_parts(FIXTURES / "valid_2entity_chain.yaml")
        graph = parts["graph"]

        # Bad edges: wrong to_entity on the first edge (makes edge_coverage fail)
        bad_edges = [e.model_dump(mode="json") for e in graph.edges]
        bad_edges[0]["to_entity"] = "nonexistent_entity"
        bad_edges_json = json.dumps(bad_edges)

        good_edges_json = parts["edges"]
        entity_call_count = 0
        edge_call_count = 0

        def mock_call(model_id, system, messages, **kwargs):
            nonlocal entity_call_count, edge_call_count
            content = messages[0]["content"]
            if "infrastructure systems" in content:
                return parts["systems"]
            elif "Decompose this scenario" in content:
                return parts["stubs"]
            elif "Fully specify ALL entities" in content:
                return json.dumps([json.loads(e) for e in parts["entities_json"]])
            elif "wire these entities" in content or "Create the Edge" in content:
                if edge_call_count == 0:
                    edge_call_count += 1
                    return bad_edges_json  # first attempt: bad
                else:
                    edge_call_count += 1
                    return good_edges_json  # retry: good
            return "{}"

        with patch("goe.bedrock.call", side_effect=mock_call):
            result = plan("sqli scenario", verbose=False)

        assert result.success
        assert edge_call_count == 2  # one failure + one retry


class TestPlannerFailure:
    """Test failure path — all retries exhausted."""

    def test_failure_when_always_bad_edges(self):
        parts = _load_graph_as_parts(FIXTURES / "valid_2entity_chain.yaml")
        graph = parts["graph"]

        bad_edges = [e.model_dump(mode="json") for e in graph.edges]
        bad_edges[0]["to_entity"] = "nonexistent_entity"
        bad_edges_json = json.dumps(bad_edges)

        def mock_call(model_id, system, messages, **kwargs):
            content = messages[0]["content"]
            if "infrastructure systems" in content:
                return parts["systems"]
            elif "Decompose this scenario" in content:
                return parts["stubs"]
            elif "Fully specify ALL entities" in content:
                return json.dumps([json.loads(e) for e in parts["entities_json"]])
            elif "wire these entities" in content or "Create the Edge" in content:
                return bad_edges_json  # always bad
            return "{}"

        with patch("goe.bedrock.call", side_effect=mock_call):
            result = plan("bad scenario")

        assert not result.success
        assert len(result.final_violations) > 0
