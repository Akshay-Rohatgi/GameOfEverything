"""Variable interpolation for procedure YAML.

Supported syntax:
  ${target_host}                    — built-in variable
  ${attacker_host}                  — built-in variable
  ${target_port}                    — built-in variable
  ${edge.<edge_id>.<param>}         — concrete value from a resolved edge
  ${steps.<step_id>.<output_name>}  — captured output from a previous step
"""

import re

_VAR_RE = re.compile(r'\$\{([^}]+)\}')


def interpolate(template: str, ctx: dict) -> str:
    """Replace all ${...} references in template using ctx."""
    def _replace(m: re.Match) -> str:
        key = m.group(1)
        parts = key.split(".")

        if len(parts) == 1:
            val = ctx.get(parts[0])
        elif parts[0] == "edge" and len(parts) == 3:
            val = ctx.get("edges", {}).get(parts[1], {}).get(parts[2])
        elif parts[0] == "steps" and len(parts) == 3:
            val = ctx.get("steps", {}).get(parts[1], {}).get(parts[2])
        else:
            val = None

        if val is None:
            return m.group(0)  # leave unresolved references as-is
        return str(val)

    return _VAR_RE.sub(_replace, template)
