"""Atom catalog — structured atom inventory for planner prompts.

Parses `atoms/web_vulnerabilities/*.md` to extract:
- ID and description from YAML frontmatter
- Compatible runtimes from Synthesis Guidance code block headers
- What the atom provides (capability granted to attacker)

Also parses `atoms/*.md` (misconfig/system atoms) to extract:
- ID and description
- Edge contract (what edge type/params the atom requires producers/consumers to model)

Used to ground planner prompts in the actual atom inventory rather than
having the LLM invent atoms or guess compatibility.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_ATOMS_DIR = Path(__file__).resolve().parent.parent.parent / "atoms" / "web_vulnerabilities"
_MISCONFIG_ATOMS_DIR = Path(__file__).resolve().parent.parent.parent / "atoms"

# Runtime name mapping: code block header → runtime ID
_RUNTIME_PATTERNS = {
    "express": re.compile(r"\*\*.*(?:Node\.?js|Express).*\(", re.IGNORECASE),
    "flask": re.compile(r"\*\*.*(?:Python|Flask).*(?:\(|:)", re.IGNORECASE),
    "apache_php": re.compile(r"\*\*.*PHP.*\(", re.IGNORECASE),
}


def _extract_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from an atom markdown file."""
    import yaml
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    return yaml.safe_load(parts[1]) or {}


def _detect_runtimes(text: str) -> list[str]:
    """Scan Synthesis Guidance for runtime code block headers."""
    runtimes = set()
    for runtime_id, pattern in _RUNTIME_PATTERNS.items():
        if pattern.search(text):
            runtimes.add(runtime_id)
    return sorted(runtimes)


def _derive_capability(text: str, description: str) -> str:
    """Heuristic: extract what the attacker gains from the atom.

    Searches Logic Requirements for keywords, or falls back to description.
    """
    text_lower = text.lower()

    # Specific atoms with known capabilities
    if "admin bot" in text_lower:
        return "Admin session token"

    if "jwt" in text_lower:
        return "Token forgery"

    if "race condition" in text_lower or "toctou" in text_lower:
        return "Bypassed validation"

    # SQL injection → credentials
    if "sql" in text_lower and "injection" in text_lower:
        if "tautology" in text_lower:
            return "Auth bypass"
        return "Leaked credentials"

    # Command/template injection → RCE
    if any(k in text_lower for k in ["command injection", "ssti", "template injection"]):
        return "RCE as web server user"

    if "deserializ" in text_lower:
        return "RCE via deserialization"

    # File operations
    if "file upload" in text_lower:
        return "Uploaded webshell"

    if "path traversal" in text_lower or "lfi" in text_lower:
        return "File read access"

    # XSS
    if "xss" in text_lower or "cross-site scripting" in text_lower:
        if "admin bot" not in text_lower:  # already handled above
            return "Session token (via XSS)"

    # Generic data exfil
    if any(k in text_lower for k in ["exfiltrat", "extract", "dump", "leak"]):
        return "Leaked data"

    # Fallback: truncate description to 60 chars
    fallback = description.split(".")[0].strip()
    return fallback if len(fallback) <= 60 else fallback[:57] + "..."


@lru_cache(maxsize=1)
def _load_atom_entries() -> list[dict]:
    """Load and parse all atoms once."""
    entries = []
    for path in sorted(_ATOMS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fm = _extract_frontmatter(text)

        atom_id = fm.get("id", path.stem)
        description = fm.get("description", "")

        # Detect runtimes from Synthesis Guidance
        runtimes = _detect_runtimes(text)
        if not runtimes:
            # Default: assume all web runtimes unless proven otherwise
            runtimes = ["express", "flask", "apache_php"]

        capability = _derive_capability(text, description)

        entries.append({
            "id": atom_id,
            "description": description,
            "runtimes": runtimes,
            "capability": capability,
        })

    return entries


@lru_cache(maxsize=1)
def _load_misconfig_atom_entries() -> list[dict]:
    """Load and parse all misconfig/system atoms from atoms/*.md (not web_vulnerabilities)."""
    import yaml as _yaml

    entries = []
    for path in sorted(_MISCONFIG_ATOMS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        fm = _yaml.safe_load(parts[1]) or {}
        atom_id = fm.get("id", path.stem)
        description = fm.get("description", "")

        # Extract edge contract from "Edge Contract" section if present
        edge_contract = ""
        m = re.search(r"### Edge Contract.*?(?=###|\Z)", text, re.DOTALL)
        if m:
            # Pull out the first meaningful line (the creds_for / shell_as type mention)
            block = m.group(0).strip()
            for line in block.splitlines()[1:]:
                line = line.strip()
                if line and not line.startswith("#"):
                    edge_contract = line[:80]
                    break

        entries.append({
            "id": atom_id,
            "description": description,
            "edge_contract": edge_contract,
        })
    return entries


@lru_cache(maxsize=1)
def misconfig_atom_catalog() -> str:
    """Return a markdown table of misconfig/system atoms (ubuntu runtime only).

    Used to inform the planner about ubuntu-entity atoms and the edge contracts
    they impose on producers/consumers.
    """
    entries = _load_misconfig_atom_entries()
    if not entries:
        return "(none)"

    lines = [
        "| Atom | Description | Edge Contract |",
        "|------|-------------|---------------|",
    ]
    for e in entries:
        desc = e["description"].replace("|", "\\|")[:80]
        contract = (e["edge_contract"] or "—").replace("|", "\\|")
        lines.append(f"| `{e['id']}` | {desc} | {contract} |")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def atom_catalog() -> str:
    """Return a markdown table of all atoms with descriptions, runtimes, and capabilities.

    Used in planner prompts to ground the LLM in the actual atom inventory.
    """
    entries = _load_atom_entries()

    lines = [
        "| Atom | Description | Compatible Runtimes | Provides |",
        "|------|-------------|---------------------|----------|",
    ]

    for e in entries:
        runtimes_str = ", ".join(e["runtimes"])
        # Escape pipe characters in descriptions
        desc = e["description"].replace("|", "\\|")
        cap = e["capability"].replace("|", "\\|")
        lines.append(f"| `{e['id']}` | {desc} | {runtimes_str} | {cap} |")

    return "\n".join(lines)


def atom_catalog_for_ids(atom_ids: list[str]) -> str:
    """Return a filtered atom catalog table containing only the specified atom IDs.

    Used when RAG returns a subset of relevant atoms.
    """
    entries = _load_atom_entries()
    id_set = set(atom_ids)
    filtered = [e for e in entries if e["id"] in id_set]

    if not filtered:
        # Fallback to full catalog if no matches
        return atom_catalog()

    lines = [
        "| Atom | Description | Compatible Runtimes | Provides |",
        "|------|-------------|---------------------|----------|",
    ]

    for e in filtered:
        runtimes_str = ", ".join(e["runtimes"])
        desc = e["description"].replace("|", "\\|")
        cap = e["capability"].replace("|", "\\|")
        lines.append(f"| `{e['id']}` | {desc} | {runtimes_str} | {cap} |")

    return "\n".join(lines)
