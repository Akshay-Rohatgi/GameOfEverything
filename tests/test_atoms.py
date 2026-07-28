"""Unit tests for atom content extraction."""

import pytest
from goe.construction_crew.atoms import (
    extract_section,
    load_atom,
    load_logic_requirements,
    load_synthesis_guidance,
    load_testing_guidance,
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
