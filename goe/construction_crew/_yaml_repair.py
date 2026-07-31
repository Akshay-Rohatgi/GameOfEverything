"""YAML repair utilities for LLM-generated procedure YAML.

LLMs frequently produce YAML with unquoted shell commands containing colons
(e.g., `cut -d: -f3,4`), which YAML interprets as key-value separators.
This module provides preprocessing that fixes the most common issues before
calling yaml.safe_load.
"""

from __future__ import annotations

import logging
import re

import yaml

logger = logging.getLogger(__name__)


class ProcedureParseError(Exception):
    """Raised when YAML cannot be parsed even after repair attempts."""

    def __init__(self, message: str, raw: str, cause: Exception | None = None):
        super().__init__(message)
        self.raw = raw
        self.cause = cause


def _strip_fences(raw: str) -> str:
    """Remove markdown code fences wrapping YAML output."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        raw = raw.rsplit("```", 1)[0]
    return raw


def repair_yaml(text: str) -> str:
    """Fix unquoted values containing ': ' in YAML lines.

    Detects mapping lines where the plain-scalar value contains a colon
    followed by a space (which YAML treats as a nested mapping indicator)
    and wraps the value in double quotes.
    """
    lines = text.split("\n")
    fixed_lines = []
    for line in lines:
        # Match key: value lines, including list-item mappings (- key: value)
        m = re.match(r"^(\s*(?:-\s+)?)([\w_-]+):\s+(.+)$", line)
        if m:
            prefix, key, value = m.groups()
            # Skip already-quoted or block/collection values
            if value.startswith(('"', "'", "|", ">", "[", "{")):
                fixed_lines.append(line)
                continue
            # Skip pure scalar types (booleans, null, numbers)
            if re.match(r"^(true|false|null|~|\d+(\.\d+)?)$", value, re.IGNORECASE):
                fixed_lines.append(line)
                continue
            # If value contains ': ' or ends with ':', it needs quoting
            if re.search(r":\s", value) or (value.endswith(":") and len(value) > 1):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                fixed_lines.append(f'{prefix}{key}: "{escaped}"')
                continue
        fixed_lines.append(line)
    return "\n".join(fixed_lines)


def safe_parse_yaml(raw: str) -> dict:
    """Parse LLM-generated YAML with automatic repair on failure.

    Steps:
      1. Strip markdown fences
      2. Try yaml.safe_load directly
      3. On failure, apply repair_yaml and retry
      4. On second failure, raise ProcedureParseError

    Returns:
        Parsed dict suitable for Pydantic model_validate.

    Raises:
        ProcedureParseError: When YAML cannot be parsed even after repair.
    """
    raw = _strip_fences(raw)

    # Attempt 1: parse as-is
    try:
        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError:
        pass

    # Attempt 2: repair and retry
    repaired = repair_yaml(raw)
    try:
        data = yaml.safe_load(repaired)
        if isinstance(data, dict):
            logger.debug("YAML repair succeeded (fixed colon quoting)")
            return data
    except yaml.YAMLError as e:
        raise ProcedureParseError(
            f"YAML parse failed even after repair: {e}",
            raw=raw,
            cause=e,
        ) from e

    # If safe_load returned non-dict (e.g., a string or None)
    raise ProcedureParseError(
        f"YAML parsed but result is not a mapping (got {type(data).__name__})",
        raw=raw,
    )
