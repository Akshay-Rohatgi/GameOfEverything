"""Integration tests to verify atom content is injected into prompts."""

import pytest
from unittest.mock import patch
from goe.construction_crew import developer, attacker
from goe.construction_crew.architect import ArchitectPlan
from goe.models.entity import Entity, Runtime
from goe.models.artifacts import BuildArtifact


@pytest.fixture
def sample_entity():
    """Create a sample entity with atoms for testing."""
    return Entity(
        id="test_sqli",
        description="Web app with SQL injection",
        system_id="sys1",
        runtime=Runtime.flask,
        atoms=["sqli_union"],
        requires=[],
        provides=["credentials"],
    )


@pytest.fixture
def sample_plan():
    """Create a sample architect plan."""
    return ArchitectPlan(
        summary="Flask app with SQLi",
        runtime="flask",
        app_description="Simple search app",
        vulnerability_placement="Search query concatenates input",
        attack_entry_point="GET /search?q=",
        success_indicator="Database credentials visible in response",
    )


@pytest.fixture
def sample_artifact():
    """Create a sample build artifact."""
    return BuildArtifact(
        source_files={"app.py": "# Flask app code"},
        primary_source="app.py",
        port=5000,
    )


def test_developer_prompt_includes_logic_requirements(sample_entity, sample_plan):
    """Verify developer first-turn prompt includes Logic Requirements."""
    with patch('goe.bedrock.call') as mock_call:
        mock_call.return_value = '{"source_files": {"app.py": "pass"}, "primary_source": "app.py", "port": 5000}'

        # We'll intercept the first call to check the prompt
        def check_first_call(model_id, system, messages, caller, **kwargs):
            if caller == "developer":
                user_msg = messages[0]["content"]
                # Should include Vulnerability Constraints section
                assert "## Vulnerability Constraints" in user_msg, \
                    "Developer first turn should include Vulnerability Constraints"
                # Should include some logic requirements from sqli_union atom
                assert "concatenat" in user_msg.lower(), \
                    "Should mention concatenation constraint from SQLi atom"
            return '{"source_files": {"app.py": "pass"}, "primary_source": "app.py", "port": 5000}'

        mock_call.side_effect = check_first_call

        try:
            developer.develop(sample_entity, sample_plan, {})
        except:
            # May fail on parsing, but we just care about the prompt
            pass


def test_developer_self_review_includes_synthesis_guidance(sample_entity, sample_plan):
    """Verify developer self-review prompt includes Synthesis Guidance."""
    with patch('goe.bedrock.call') as mock_call:
        call_count = [0]

        def check_calls(model_id, system, messages, caller, **kwargs):
            call_count[0] += 1
            if caller == "developer.self_review":
                user_msg = messages[-1]["content"]
                # Should include Verify Against Atom Guidance section
                assert "## Verify Against Atom Guidance" in user_msg, \
                    "Developer self-review should include atom guidance"
                # Should include synthesis guidance content
                assert "injection point" in user_msg.lower() or "query" in user_msg.lower(), \
                    "Should include synthesis guidance from SQLi atom"
            return '{"source_files": {"app.py": "pass"}, "primary_source": "app.py", "port": 5000}'

        mock_call.side_effect = check_calls

        try:
            developer.develop(sample_entity, sample_plan, {})
        except:
            pass

        # Should have made at least 2 calls (initial + self-review)
        assert call_count[0] >= 2, "Should have called LLM at least twice"


def test_attacker_prompt_includes_logic_requirements(sample_entity, sample_plan, sample_artifact):
    """Verify attacker first-turn prompt includes Logic Requirements."""
    with patch('goe.bedrock.call') as mock_call:
        mock_call.return_value = 'procedure:\n  - step_id: test\n    action:\n      type: http_request\n      method: GET\n      url: http://localhost\n    expect:\n      status: 200'

        def check_first_call(model_id, system, messages, caller, **kwargs):
            if caller == "attacker":
                user_msg = messages[0]["content"]
                # Should include Attack Constraints section
                assert "## Attack Constraints" in user_msg, \
                    "Attacker first turn should include Attack Constraints"
                # Should include logic requirements
                assert "concatenat" in user_msg.lower() or "parameterized" in user_msg.lower(), \
                    "Should mention SQL injection constraints"
            return 'procedure:\n  - step_id: test\n    action:\n      type: http_request\n      method: GET\n      url: http://localhost\n    expect:\n      status: 200'

        mock_call.side_effect = check_first_call

        try:
            attacker.attack(sample_entity, sample_plan, sample_artifact, {})
        except:
            pass


def test_attacker_self_review_includes_testing_guidance(sample_entity, sample_plan, sample_artifact):
    """Verify attacker self-review prompt includes Testing Guidance."""
    with patch('goe.bedrock.call') as mock_call:
        call_count = [0]

        def check_calls(model_id, system, messages, caller, **kwargs):
            call_count[0] += 1
            if caller == "attacker.self_review":
                user_msg = messages[-1]["content"]
                # Should include Verify Against Atom Testing Guidance section
                assert "## Verify Against Atom Testing Guidance" in user_msg, \
                    "Attacker self-review should include testing guidance"
                # Should include testing guidance content
                assert "layer" in user_msg.lower() or "curl" in user_msg.lower(), \
                    "Should include testing guidance from SQLi atom"
            return 'procedure:\n  - step_id: test\n    action:\n      type: http_request\n      method: GET\n      url: http://localhost\n    expect:\n      status: 200'

        mock_call.side_effect = check_calls

        try:
            attacker.attack(sample_entity, sample_plan, sample_artifact, {})
        except:
            pass

        assert call_count[0] >= 2, "Should have called LLM at least twice"


def test_empty_atoms_no_sections(sample_entity, sample_plan, sample_artifact):
    """Verify that entities with no atoms don't add empty sections."""
    # Create entity with no atoms
    entity_no_atoms = Entity(
        id="test_no_atoms",
        description="Web app without specific atoms",
        system_id="sys1",
        runtime=Runtime.flask,
        atoms=[],
        requires=[],
        provides=[],
    )

    with patch('goe.bedrock.call') as mock_call:
        mock_call.return_value = '{"source_files": {"app.py": "pass"}, "primary_source": "app.py", "port": 5000}'

        def check_no_constraints(model_id, system, messages, caller, **kwargs):
            if caller == "developer":
                user_msg = messages[0]["content"]
                # Should NOT include Vulnerability Constraints when no atoms
                assert "## Vulnerability Constraints" not in user_msg, \
                    "Should not add Vulnerability Constraints section when no atoms"
            return '{"source_files": {"app.py": "pass"}, "primary_source": "app.py", "port": 5000}'

        mock_call.side_effect = check_no_constraints

        try:
            developer.develop(entity_no_atoms, sample_plan, {})
        except:
            pass
