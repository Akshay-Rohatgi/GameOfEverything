"""Unit tests for the developer's outgoing edge-value validation (no LLM/Docker)."""

import pytest

from goe.construction_crew.developer import validate_outgoing_edge_values

_SCHEMA = {
    "sqli_to_ssh": {
        "type": "creds_for",
        "direction": "provides",
        "params": ["user", "secret", "cred_type"],
    }
}


def test_accepts_declared_params():
    validate_outgoing_edge_values(
        {"sqli_to_ssh": {"user": "admin", "secret": "hunter2", "cred_type": "password"}},
        _SCHEMA,
    )


def test_accepts_subset_of_declared_params():
    # Emitting only some declared params is allowed at parse time (the completeness guard
    # catches genuinely missing values later, with the full graph context).
    validate_outgoing_edge_values({"sqli_to_ssh": {"user": "admin"}}, _SCHEMA)


def test_rejects_undeclared_param_key():
    with pytest.raises(ValueError, match="undeclared params"):
        validate_outgoing_edge_values(
            {"sqli_to_ssh": {"user": "admin", "password": "hunter2"}}, _SCHEMA
        )


def test_rejects_unknown_edge_id():
    with pytest.raises(ValueError, match="unknown edge"):
        validate_outgoing_edge_values({"made_up_edge": {"user": "admin"}}, _SCHEMA)


def test_rejects_flat_string_payload():
    # The old single-scalar contract must no longer be accepted.
    with pytest.raises(ValueError, match="must be a dict"):
        validate_outgoing_edge_values({"sqli_to_ssh": "hunter2"}, _SCHEMA)


def test_no_schema_is_noop():
    # Standalone single-entity builds pass no schema — anything goes.
    validate_outgoing_edge_values({"whatever": {"x": "y"}}, {})


def test_rejects_empty_value():
    # A blank/placeholder value is never a real built credential.
    with pytest.raises(ValueError, match="is empty"):
        validate_outgoing_edge_values(
            {"sqli_to_ssh": {"user": "admin", "secret": "  "}}, _SCHEMA
        )
