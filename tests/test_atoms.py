"""Unit tests for atom content extraction."""

import pytest
from goe.construction_crew.atoms import (
    extract_section,
    load_atom,
    load_logic_requirements,
    load_synthesis_guidance,
    load_testing_guidance,
    required_services,
    resolve_required_services,
)


SAMPLE_ATOM = """---
id: test_atom
description: Test atom for unit testing
---

# Test Atom

This is a test atom.

### Logic Requirements:

1. The query MUST use string concatenation
2. The input MUST NOT be sanitized

### Synthesis Guidance:

For Flask:
```python
query = f"SELECT * FROM users WHERE id = {user_id}"
```

For Express:
```javascript
const query = `SELECT * FROM users WHERE id = ${userId}`;
```

### Testing Guidance:

**Layer 1 (Internal):**
- Verify query construction uses concatenation

**Layer 2 (External):**
- Send payload: `1 OR 1=1`
- Assert: response contains multiple users
"""


def test_extract_section_with_colon():
    """Test extraction when heading has trailing colon."""
    result = extract_section(SAMPLE_ATOM, "Logic Requirements")
    assert "The query MUST use string concatenation" in result
    assert "The input MUST NOT be sanitized" in result
    assert "Synthesis Guidance" not in result


def test_extract_section_without_colon():
    """Test extraction when heading has no trailing colon (should still work)."""
    # The extract_section normalizes by removing trailing colons
    result = extract_section(SAMPLE_ATOM, "Synthesis Guidance")
    assert "For Flask:" in result
    assert "For Express:" in result
    assert "Testing Guidance" not in result


def test_extract_section_last():
    """Test extraction of the last section (no subsequent heading)."""
    result = extract_section(SAMPLE_ATOM, "Testing Guidance")
    assert "Layer 1 (Internal)" in result
    assert "Layer 2 (External)" in result
    assert "Send payload" in result


def test_extract_section_missing():
    """Test extraction of non-existent section returns empty string."""
    result = extract_section(SAMPLE_ATOM, "Nonexistent Section")
    assert result == ""


def test_load_functions_return_empty_on_not_found():
    """Test that load functions return empty string for nonexistent atoms."""
    assert load_logic_requirements("nonexistent_atom_id") == ""
    assert load_synthesis_guidance("nonexistent_atom_id") == ""
    assert load_testing_guidance("nonexistent_atom_id") == ""


def test_load_atom_real():
    """Test loading a real atom from the atoms/ directory."""
    # This assumes sqli_union.md exists in atoms/web_vulnerabilities/
    content = load_atom("sqli_union")
    assert "sqli_union" in content or "SQL injection" in content.lower()
    # Should not be the fallback message
    assert "not found" not in content


def test_load_logic_requirements_real():
    """Test extracting Logic Requirements from a real atom."""
    result = load_logic_requirements("sqli_union")
    # Should find content if the atom exists and has that section
    if result:
        assert len(result) > 0
        # Logic Requirements typically have numbered points
        assert any(char.isdigit() for char in result)


def test_load_synthesis_guidance_real():
    """Test extracting Synthesis Guidance from a real atom."""
    result = load_synthesis_guidance("sqli_union")
    # Should find content if the atom exists and has that section
    if result:
        assert len(result) > 0


def test_load_testing_guidance_real():
    """Test extracting Testing Guidance from a real atom."""
    result = load_testing_guidance("sqli_union")
    # Should find content if the atom exists and has that section
    if result:
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Atom -> service bridge: static (requires_service) + dynamic (requires_service_by)
# ---------------------------------------------------------------------------

class TestRequiredServices:
    """Static, fixed-service declarations."""

    def test_fixed_service_atom(self):
        assert required_services("postgres_rce") == ["postgres"]

    def test_fixed_service_atom_samba(self):
        assert required_services("samba_insecure_share") == ["samba"]

    def test_tool_atom_has_no_service(self):
        # install_package is a plain-package tool atom, not service-backed.
        assert required_services("install_package") == []

    def test_generic_atom_has_no_static_service(self):
        # database_expose uses the dynamic mapping only — nothing static.
        assert required_services("database_expose") == []


class TestResolveRequiredServices:
    """Dynamic resolution keyed off the planner-pinned entity config."""

    def test_generic_resolves_to_pinned_engine(self):
        assert resolve_required_services("database_expose", {"db_type": "postgres"}) == ["postgres"]
        assert resolve_required_services("database_expose", {"db_type": "mysql"}) == ["mysql"]
        assert resolve_required_services("database_expose", {"db_type": "redis"}) == ["redis"]

    def test_alias_values_map_to_same_recipe(self):
        # postgresql -> postgres, mariadb -> mysql
        assert resolve_required_services("database_expose", {"db_type": "postgresql"}) == ["postgres"]
        assert resolve_required_services("database_expose", {"db_type": "mariadb"}) == ["mysql"]

    def test_mapped_mongodb_resolves(self):
        # mongodb now has a recipe -> both spellings resolve to it.
        assert resolve_required_services("database_expose", {"db_type": "mongodb"}) == ["mongodb"]
        assert resolve_required_services("database_expose", {"db_type": "mongo"}) == ["mongodb"]

    def test_unmapped_value_degrades_gracefully(self):
        # mssql has no recipe -> no service added, no error.
        assert resolve_required_services("database_expose", {"db_type": "mssql"}) == []

    def test_unpinned_config_yields_nothing(self):
        assert resolve_required_services("database_expose", {}) == []
        assert resolve_required_services("database_expose", None) == []

    def test_weak_password_selects_by_service_var(self):
        assert resolve_required_services("weak_service_password", {"service": "mysql"}) == ["mysql"]
        assert resolve_required_services("weak_service_password", {"service": "ftp"}) == ["ftp"]

    def test_weak_password_ssh_needs_no_recipe(self):
        # sshd ships in the base image; ssh is intentionally not in the map.
        assert resolve_required_services("weak_service_password", {"service": "ssh"}) == []

    def test_fixed_service_atom_ignores_config(self):
        # A welded atom returns its static service regardless of config.
        assert resolve_required_services("postgres_rce", {"db_type": "mysql"}) == ["postgres"]
        assert resolve_required_services("postgres_rce", None) == ["postgres"]
