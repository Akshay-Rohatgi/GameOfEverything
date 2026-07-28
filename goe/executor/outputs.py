"""Capture named output values from ActionResult."""

import json
import re
from goe.executor.actions import ActionResult


def capture(spec: str, result: ActionResult) -> str | None:
    """Extract a named output from an ActionResult according to spec string.

    Spec formats:
      regex("pattern")      — first capture group from stdout/body/received
      header("name")        — HTTP response header
      body                  — full HTTP response body
      stdout                — full stdout
      evaluate_return       — JS evaluation return value
      extracted_value       — DOM extraction result
      json(".path")         — JSONPath-style from response body (dots only, no arrays)
      url                   — current URL after action
      cookie                — stub (returns empty string, requires browser context)
    """
    spec = spec.strip()

    if spec == "body":
        return result.body or None
    if spec == "stdout":
        return result.stdout or None
    if spec == "evaluate_return":
        return result.evaluate_return
    if spec == "extracted_value":
        return result.extracted_value
    if spec == "url":
        return result.current_url or None

    m = re.match(r'^regex\("(.+)"\)$', spec)
    if m:
        pattern = m.group(1)
        source = result.stdout or result.body or ""
        match = re.search(pattern, source)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
        return None

    m = re.match(r'^header\("(.+)"\)$', spec)
    if m:
        name = m.group(1).lower()
        return result.headers.get(name) or result.headers.get(m.group(1))

    m = re.match(r'^json\("(.+)"\)$', spec)
    if m:
        path = m.group(1)
        try:
            data = json.loads(result.body)
            for key in path.strip(".").split("."):
                if isinstance(data, dict):
                    data = data[key]
                else:
                    return None
            return str(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    if spec == "cookie":
        return ""

    return None
