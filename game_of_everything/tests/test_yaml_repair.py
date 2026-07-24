"""Tests for goe/construction_crew/_yaml_repair.py."""

import pytest
import yaml

from goe.construction_crew._yaml_repair import (
    ProcedureParseError,
    repair_yaml,
    safe_parse_yaml,
)


class TestRepairYaml:
    """Tests for repair_yaml() function."""

    def test_fixes_unquoted_colon_in_shell_command(self):
        """Unquoted colons in values get quoted."""
        text = """procedure:
  - step_id: test
    action:
      type: exec_attacker
      command: cut -d: -f3,4"""

        fixed = repair_yaml(text)
        # Should quote the command value
        assert 'command: "cut -d: -f3,4"' in fixed
        # Should parse successfully
        yaml.safe_load(fixed)

    def test_fixes_grep_with_colon(self):
        """Grep patterns with colons get quoted."""
        text = """procedure:
  - step_id: check_passwd
    action:
      type: exec_target
      command: grep "^hacker:" /etc/passwd | cut -d: -f1"""

        fixed = repair_yaml(text)
        assert 'command: "grep' in fixed
        yaml.safe_load(fixed)

    def test_preserves_already_quoted_values(self):
        """Already-quoted values are left unchanged."""
        text = '''procedure:
  - step_id: test
    action:
      command: "cut -d: -f3,4"'''

        fixed = repair_yaml(text)
        assert fixed == text

    def test_preserves_single_quoted_values(self):
        """Single-quoted values are left unchanged."""
        text = """procedure:
  - step_id: test
    action:
      command: 'grep "test:" file.txt'"""

        fixed = repair_yaml(text)
        assert fixed == text

    def test_preserves_block_scalars(self):
        """Block scalars (|, >) are not modified."""
        text = """procedure:
  - step_id: test
    script: |
      echo test: value
      cut -d: -f1"""

        fixed = repair_yaml(text)
        assert "script: |" in fixed

    def test_preserves_collections(self):
        """Collection starts ([, {) are not modified."""
        text = """procedure:
  - step_id: test
    action: {type: exec, command: test}
    list: [a, b, c]"""

        fixed = repair_yaml(text)
        assert "action: {" in fixed
        assert "list: [" in fixed

    def test_preserves_booleans_and_null(self):
        """Boolean and null values are not quoted."""
        text = """procedure:
  - step_id: test
    enabled: true
    disabled: false
    optional: null"""

        fixed = repair_yaml(text)
        assert "enabled: true" in fixed
        assert "disabled: false" in fixed
        assert "optional: null" in fixed

    def test_preserves_numbers(self):
        """Numeric values are not quoted."""
        text = """procedure:
  - step_id: test
    timeout: 30
    ratio: 1.5"""

        fixed = repair_yaml(text)
        assert "timeout: 30" in fixed
        assert "ratio: 1.5" in fixed

    def test_fixes_value_with_colon_space(self):
        """Values containing ': ' pattern get quoted."""
        text = """procedure:
  - step_id: test
    message: error: file not found"""

        fixed = repair_yaml(text)
        assert 'message: "error: file not found"' in fixed
        yaml.safe_load(fixed)

    def test_handles_list_item_mappings(self):
        """List items with colons in values get fixed."""
        text = """procedure:
  - step_id: extract
    command: awk -F: '{print $1}'"""

        fixed = repair_yaml(text)
        assert 'command: "awk -F:' in fixed


class TestSafeParseYaml:
    """Tests for safe_parse_yaml() function."""

    def test_parses_valid_yaml(self):
        """Valid YAML parses on first attempt."""
        text = """procedure:
  - step_id: test
    action:
      type: exec_attacker
      command: echo hello"""

        data = safe_parse_yaml(text)
        assert "procedure" in data
        assert len(data["procedure"]) == 1

    def test_strips_markdown_fences(self):
        """Markdown code fences are removed."""
        text = """```yaml
procedure:
  - step_id: test
```"""

        data = safe_parse_yaml(text)
        assert "procedure" in data

    def test_repairs_invalid_yaml(self):
        """Invalid YAML gets repaired automatically."""
        text = """procedure:
  - step_id: test
    command: cut -d: -f3,4"""

        data = safe_parse_yaml(text)
        assert "procedure" in data
        assert data["procedure"][0]["command"] == "cut -d: -f3,4"

    def test_raises_on_unrepairable_yaml(self):
        """Unparseable YAML raises ProcedureParseError."""
        text = """procedure:
  - step_id: test
    : : : invalid syntax"""

        with pytest.raises(ProcedureParseError) as exc_info:
            safe_parse_yaml(text)

        assert "YAML parse failed even after repair" in str(exc_info.value)
        assert exc_info.value.raw == text.strip()
        assert exc_info.value.cause is not None

    def test_raises_on_non_dict_result(self):
        """YAML that parses to non-dict raises ProcedureParseError."""
        text = "just a string"

        with pytest.raises(ProcedureParseError) as exc_info:
            safe_parse_yaml(text)

        assert "not a mapping" in str(exc_info.value)

    def test_escapes_quotes_in_repaired_values(self):
        """Embedded quotes are escaped when repairing."""
        text = '''procedure:
  - step_id: test
    command: echo "test:" value'''

        data = safe_parse_yaml(text)
        assert "procedure" in data
        # The repaired value should have escaped quotes
        assert 'test:' in data["procedure"][0]["command"]


class TestProcedureParseError:
    """Tests for ProcedureParseError exception."""

    def test_stores_raw_text(self):
        """Error carries the raw text."""
        raw = "invalid: yaml: syntax"
        err = ProcedureParseError("test error", raw=raw)
        assert err.raw == raw

    def test_stores_cause(self):
        """Error carries the underlying exception."""
        cause = ValueError("test cause")
        err = ProcedureParseError("test error", raw="", cause=cause)
        assert err.cause is cause

    def test_message(self):
        """Error message is accessible."""
        err = ProcedureParseError("custom message", raw="")
        assert str(err) == "custom message"
