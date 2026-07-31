"""Shared atom content utilities for the construction crew.

Provides section-level extraction from atom markdown files to inject guidance
at different stages of the build pipeline.
"""

from pathlib import Path

_ATOMS_DIR = Path(__file__).resolve().parent.parent.parent / "atoms"


def load_atom(atom_id: str) -> str:
    """Read full atom markdown content by ID.

    Searches atoms/ directory recursively for {atom_id}.md.
    Returns the full file content, or a fallback string if not found.
    """
    for path in _ATOMS_DIR.rglob(f"{atom_id}.md"):
        return path.read_text()
    return f"(atom {atom_id!r} not found)"


def extract_section(markdown: str, heading: str) -> str:
    """Extract content between a heading and the next heading (or EOF).

    Atom markdown uses ### Heading: or ### Heading format.
    This function is tolerant of trailing colon presence/absence.

    Args:
        markdown: Full atom markdown content
        heading: Section name (e.g., "Logic Requirements", "Synthesis Guidance")

    Returns:
        Section content (stripped), or empty string if not found.
    """
    lines = markdown.split("\n")
    section_lines = []
    in_section = False

    # Normalize heading for comparison (remove trailing colon if present)
    target = heading.rstrip(":")

    for line in lines:
        # Check if this is a section header
        if line.startswith("###"):
            header_text = line[3:].strip().rstrip(":")

            if header_text == target:
                in_section = True
                continue
            elif in_section:
                # Hit the next section, stop collecting
                break

        if in_section:
            section_lines.append(line)

    return "\n".join(section_lines).strip()


def load_logic_requirements(atom_id: str) -> str:
    """Load the Logic Requirements section from an atom.

    This section contains numbered constraints that MUST be true for the
    vulnerability to exist (e.g., "query MUST use string concatenation").
    Used as constraints before generation to prevent fundamental mistakes.
    """
    atom_content = load_atom(atom_id)
    if "(atom" in atom_content and "not found)" in atom_content:
        return ""
    return extract_section(atom_content, "Logic Requirements")


def load_synthesis_guidance(atom_id: str) -> str:
    """Load the Synthesis Guidance section from an atom.

    This section contains runtime-specific code patterns for implementing
    the vulnerability. Used during self-review as a verification checklist.
    """
    atom_content = load_atom(atom_id)
    if "(atom" in atom_content and "not found)" in atom_content:
        return ""
    return extract_section(atom_content, "Synthesis Guidance")


def load_testing_guidance(atom_id: str) -> str:
    """Load the Testing Guidance section from an atom.

    This section contains L1/L2 verification steps and example exploitation
    commands. Used during attacker self-review as a verification checklist.
    """
    atom_content = load_atom(atom_id)
    if "(atom" in atom_content and "not found)" in atom_content:
        return ""
    return extract_section(atom_content, "Testing Guidance")
