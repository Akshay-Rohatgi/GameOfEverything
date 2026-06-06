"""Dynamic context assembled from authoritative sources for use in planner prompts.

atom_list()      — full atom list, used only as RAG fallback
runtime_list()   — scanned from goe/runtimes/templates/*.yaml
edge_type_list() — derived from EDGE_TYPE_PARAMS in validator.py
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_WEB_ATOMS_DIR = Path(__file__).resolve().parent.parent.parent / "atoms" / "web_vulnerabilities"
_RUNTIMES_DIR = Path(__file__).resolve().parent.parent / "runtimes" / "templates"


@lru_cache(maxsize=1)
def atom_list() -> str:
    """Bulleted list of web vulnerability atom IDs + descriptions from frontmatter."""
    import yaml

    lines = []
    for path in sorted(_WEB_ATOMS_DIR.glob("*.md")):
        text = path.read_text()
        # Extract YAML frontmatter between --- delimiters
        if text.startswith("---"):
            fm_text = text.split("---", 2)[1]
            fm = yaml.safe_load(fm_text)
            atom_id = fm.get("id", path.stem)
            desc = fm.get("description", "")
            # Trim description to one short clause
            desc = desc.split(".")[0].rstrip()
            lines.append(f"- `{atom_id}` — {desc}")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def runtime_list() -> str:
    """Bulleted list of available runtimes with port from YAML templates."""
    import yaml

    lines = []
    for path in sorted(_RUNTIMES_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        rid = data["id"]
        port = data.get("port", "?")
        description = data.get("description", "")
        entry = f"- `{rid}` (port {port})"
        if description:
            entry += f" — {description}"
        lines.append(entry)
    return "\n".join(lines)


@lru_cache(maxsize=1)
def edge_type_list() -> str:
    """Edge types and their required params derived from EDGE_TYPE_PARAMS in validator."""
    from goe.graph.validator import EDGE_TYPE_PARAMS

    lines = []
    for edge_type, params in EDGE_TYPE_PARAMS.items():
        params_str = ", ".join(sorted(params))
        lines.append(f"- `{edge_type.value}`: {{{params_str}}}")
    return "\n".join(lines)
