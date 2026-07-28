"""Tests for ScriptGrader validation and conflict resolution."""

import pytest


class TestBashValidation:
    def test_valid_script_passes(self):
        """Test that a valid bash script passes validation."""
        from goe.packaging.grader import _is_valid_bash

        script = """#!/bin/bash
set -e
apt-get update -qq
apt-get install -y openssh-server
useradd -m alice
echo 'alice:password123' | chpasswd
"""
        is_valid, error = _is_valid_bash(script)
        assert is_valid
        assert error == ""

    def test_truncated_echo_fails(self):
        """Test that truncated echo statement is caught."""
        from goe.packaging.grader import _is_valid_bash

        # This is the actual bug pattern from the failed run
        script = """#!/bin/bash
echo 'PD9waHAKaWYgKCRfU0VSVkVSWydSRVFVRVNUX01FVEhPRCddICE9PS
useradd alice
"""
        is_valid, error = _is_valid_bash(script)
        assert not is_valid
        assert error  # Should have an error message

    def test_unclosed_quote_fails(self):
        """Test that unclosed quotes are caught."""
        from goe.packaging.grader import _is_valid_bash

        script = """#!/bin/bash
echo "hello world
useradd alice
"""
        is_valid, error = _is_valid_bash(script)
        assert not is_valid

    def test_valid_base64_echo_passes(self):
        """Test that complete base64 echo statements pass."""
        from goe.packaging.grader import _is_valid_bash

        script = """#!/bin/bash
echo 'VGhpc0lzQVZlcnlMb25nQmFzZTY0U3RyaW5nVGhhdEdvZXNPbkFuZE9uQW5kT24=' | base64 -d > /tmp/file
useradd alice
"""
        is_valid, error = _is_valid_bash(script)
        assert is_valid
        assert error == ""


class TestTruncationDetection:
    def test_no_truncation_when_valid_syntax(self):
        """Test that size loss with valid syntax doesn't trigger truncation."""
        from goe.packaging.grader import _detect_suspicious_truncation

        original = "#!/bin/bash\n" + "useradd alice\n" * 10
        fixed = "#!/bin/bash\n" + "useradd alice\n"  # 90% loss but valid
        # Should not trigger because syntax is valid
        assert not _detect_suspicious_truncation(original, fixed)

    def test_truncation_detected_with_syntax_error(self):
        """Test that >30% loss WITH syntax error is detected."""
        from goe.packaging.grader import _detect_suspicious_truncation

        original = "#!/bin/bash\n" + "useradd alice\n" * 10
        fixed = "#!/bin/bash\necho 'incomplete"  # Syntax error + big loss
        # Should trigger because both size loss AND syntax error
        assert _detect_suspicious_truncation(original, fixed)

    def test_no_truncation_when_less_than_30_percent_loss(self):
        """Test that <30% loss is never considered truncation."""
        from goe.packaging.grader import _detect_suspicious_truncation

        original = "#!/bin/bash\n" + "x" * 1000
        fixed = "#!/bin/bash\n" + "x" * 750  # 25% loss
        # Should never trigger even with syntax error, because < 30% loss
        assert not _detect_suspicious_truncation(original, fixed)


class TestGradeAndFixScript:
    def test_invalid_output_reverts_to_original(self, monkeypatch):
        """Test that invalid grader output causes revert to original."""
        from goe.packaging.grader import grade_and_fix_script

        original = """#!/bin/bash
set -e
useradd alice
echo 'alice:pass1' | chpasswd
"""

        # Mock the LLM to return truncated output
        def mock_call(*args, **kwargs):
            return """#!/bin/bash
set -e
echo 'PD9waHAKaWYgKCRfU0VS
"""

        monkeypatch.setattr("goe.bedrock.call", mock_call)

        fixed, warnings = grade_and_fix_script(original, {"e1": original})

        # Should have reverted to original
        assert fixed == original
        assert len(warnings) == 1
        assert "syntax error" in warnings[0] or "rejected" in warnings[0]

    def test_truncated_output_reverts_to_original(self, monkeypatch):
        """Test that suspiciously short output causes revert."""
        from goe.packaging.grader import grade_and_fix_script

        original = "#!/bin/bash\n" + "useradd alice\n" * 100  # 1000+ chars

        # Mock the LLM to return drastically shortened AND invalid output
        def mock_call(*args, **kwargs):
            return "#!/bin/bash\necho 'incomplete"  # Truncated + syntax error

        monkeypatch.setattr("goe.bedrock.call", mock_call)

        fixed, warnings = grade_and_fix_script(original, {"e1": original})

        # Should have reverted to original
        assert fixed.strip() == original.strip()
        assert len(warnings) == 1
        assert "rejected" in warnings[0].lower()

    def test_valid_fix_is_accepted(self, monkeypatch):
        """Test that valid grader output is accepted."""
        from goe.packaging.grader import grade_and_fix_script

        original = """#!/bin/bash
set -e
useradd alice
echo 'alice:pass1' | chpasswd
useradd alice
echo 'alice:pass2' | chpasswd
"""

        expected_fix = """#!/bin/bash
set -e
useradd alice
echo 'alice:pass1' | chpasswd
"""

        # Mock the LLM to return valid fixed output
        def mock_call(*args, **kwargs):
            return expected_fix

        monkeypatch.setattr("goe.bedrock.call", mock_call)

        fixed, warnings = grade_and_fix_script(original, {"e1": original})

        # Should have accepted the fix (compare stripped to avoid newline issues)
        assert fixed.strip() == expected_fix.strip()
        assert len(warnings) > 0  # Should detect removed lines
        assert any("duplicate" in w.lower() or "password" in w.lower() for w in warnings)

    def test_unchanged_script_returns_original(self, monkeypatch):
        """Test that if grader returns unchanged script, it's preserved."""
        from goe.packaging.grader import grade_and_fix_script

        original = """#!/bin/bash
set -e
useradd alice"""  # No trailing newline to avoid whitespace issues

        # Mock the LLM to return the same script
        def mock_call(*args, **kwargs):
            return original

        monkeypatch.setattr("goe.bedrock.call", mock_call)

        fixed, warnings = grade_and_fix_script(original, {"e1": original})

        assert fixed.strip() == original.strip()  # Compare stripped to avoid newline issues
        assert len(warnings) == 0  # No changes detected


class TestDiffSummary:
    def test_detects_removed_chpasswd(self):
        """Test that removed chpasswd lines are detected."""
        from goe.packaging.grader import _diff_summary

        original = """useradd alice
echo 'alice:pass1' | chpasswd
echo 'alice:pass2' | chpasswd
"""
        fixed = """useradd alice
echo 'alice:pass1' | chpasswd
"""
        warnings = _diff_summary(original, fixed)
        assert len(warnings) > 0
        assert any("password" in w.lower() for w in warnings)

    def test_detects_removed_useradd(self):
        """Test that removed useradd lines are detected.

        Note: _diff_summary uses set difference, so duplicate lines on
        the same line won't be detected. It needs different user names.
        """
        from goe.packaging.grader import _diff_summary

        original = """useradd alice
useradd bob
"""
        fixed = """useradd alice
"""
        warnings = _diff_summary(original, fixed)
        assert len(warnings) > 0
        # Check for either "user creation" or "duplicate user"
        assert any("user" in w.lower() for w in warnings)

    def test_detects_removed_file_overwrite(self):
        """Test that removed file overwrites are detected."""
        from goe.packaging.grader import _diff_summary

        original = """echo 'content1' > /tmp/file
echo 'content2' > /tmp/file
"""
        fixed = """echo 'content1' > /tmp/file
"""
        warnings = _diff_summary(original, fixed)
        assert len(warnings) > 0
        assert any("file overwrite" in w.lower() for w in warnings)

    def test_no_changes_returns_empty_list(self):
        """Test that identical scripts return no warnings."""
        from goe.packaging.grader import _diff_summary

        script = """#!/bin/bash
useradd alice
"""
        warnings = _diff_summary(script, script)
        assert len(warnings) == 0
