"""Tests for the Haiku OS-assignment planner step — mocked bedrock calls."""

import json
from unittest.mock import patch

from goe.models.system import System
from goe.planner.assign_os import assign_os


def _system(id_: str, hostname: str, os_: str = "ubuntu") -> System:
    return System.model_validate(
        {
            "id": id_,
            "os": os_,
            "services": ["ssh"],
            "network": {"hostname": hostname, "exposed_ports": [22], "internal_ports": []},
        },
        strict=False,
    )


def test_multihost_applies_returned_mapping():
    systems = [_system("web", "webserver"), _system("db", "dbserver")]

    def mock_call(model_id, system, messages, **kwargs):
        return json.dumps({"web": "ubuntu", "db": "debian"})

    with patch("goe.bedrock.call", side_effect=mock_call):
        result = assign_os("web pivots to db", systems, model="haiku")

    by_id = {s.id: s.os for s in result}
    assert by_id == {"web": "ubuntu", "db": "debian"}


def test_invalid_or_missing_entry_keeps_inferred_os():
    systems = [_system("web", "webserver", os_="ubuntu"), _system("db", "dbserver", os_="ubuntu")]

    def mock_call(model_id, system, messages, **kwargs):
        # db gets a bogus distro; web is absent from the mapping entirely.
        return json.dumps({"db": "plan9"})

    with patch("goe.bedrock.call", side_effect=mock_call):
        result = assign_os("web pivots to db", systems, model="haiku")

    by_id = {s.id: s.os for s in result}
    # Both fall back to the design_systems-inferred OS; the run is never broken.
    assert by_id == {"web": "ubuntu", "db": "ubuntu"}


def test_single_host_is_noop_without_llm_call():
    systems = [_system("target", "target", os_="debian")]

    with patch("goe.bedrock.call", side_effect=AssertionError("must not call bedrock")) as m:
        result = assign_os("single box", systems, model="haiku")

    m.assert_not_called()
    assert [s.os for s in result] == ["debian"]
    assert result is systems  # returned unchanged
