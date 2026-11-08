"""Shared atom content utilities for the construction crew.

Provides section-level extraction from atom markdown files to inject guidance
at different stages of the build pipeline, plus the atom->service bridge that
lets an atom declare which service recipe must install its dependencies.
"""

from pathlib import Path

_ATOMS_DIR = Path(__file__).resolve().parent.parent.parent / "atoms"


def _frontmatter(atom_id: str) -> dict:
    """Parse the YAML frontmatter block of an atom, or {} if absent/not found."""
    import yaml

    text = load_atom(atom_id)
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}


def required_services(atom_id: str) -> list[str]:
    """Static service recipe IDs this atom always needs pre-installed.

    Declared in the atom's frontmatter as ``requires_service`` (a single id or a
    list of ids) for atoms welded to one service (e.g. ``postgres_rce``). Returns
    ``[]`` when the atom declares none.

    This is the atom->service bridge: the planner unions these into
    ``System.services`` so the deterministic, distro-aware service layer installs
    the package (apt on Debian, dnf on a future Red Hat profile), instead of the
    atom hardcoding a package-manager command in its prose.

    Generic, parameterized atoms (whose service depends on a plan-time choice)
    declare ``requires_service_by`` instead — see ``resolve_required_services``.
    """
    value = _frontmatter(atom_id).get("requires_service")
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def resolve_required_services(atom_id: str, config: dict | None = None) -> list[str]:
    """All service recipes this atom needs, given the entity's pinned ``config``.

    Combines two sources, in order, de-duplicated:

    * static ``requires_service`` — atoms welded to a single service; and
    * dynamic ``requires_service_by`` — parameterized atoms whose service depends
      on a plan-time choice. Shape::

          requires_service_by:
            var: db_type            # which entity.config key selects the service
            map: {mysql: mysql, postgres: postgres, redis: redis}

      The chosen value (``config[var]``) is looked up in ``map`` to get the recipe
      id. A value with no mapping (e.g. a service we have no recipe for yet, or one
      that needs no recipe) contributes nothing — the bridge degrades gracefully
      rather than erroring.

    ``config`` is ``entity.config``; an empty/absent config yields only the static
    services, so fixed-service atoms behave exactly as before.
    """
    services = list(required_services(atom_id))

    spec = _frontmatter(atom_id).get("requires_service_by")
    if isinstance(spec, dict):
        var = spec.get("var")
        mapping = spec.get("map") or {}
        chosen = (config or {}).get(var) if var else None
        recipe = mapping.get(chosen) if chosen is not None else None
        if recipe and recipe not in services:
            services.append(str(recipe))

    return services


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
