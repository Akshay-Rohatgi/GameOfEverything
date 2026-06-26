"""Deploy script grader — validates and fixes conflicts in concatenated entity scripts.

When multiple entities' deploy scripts are stitched together, conflicts can arise:
- Password overwrites (entity 2 runs chpasswd after entity 1 set the password)
- Duplicate user creation
- Service conflicts (multiple entities starting the same daemon)
- Port conflicts

This LLM-based grader detects and fixes these issues before deployment.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def assemble_deploy_script(sections: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """Concatenate ordered (entity_id, section) deploy scripts into one final script.

    This is the single entry point every assembly path uses (single-system packaging,
    multi-system packaging, and the chain test) so the script that gets tested is
    byte-identical to the script that gets packaged.

    Steps:
      1. Concatenate sections with ``# --- <entity_id> ---`` headers.
      2. When there is more than one section, run the LLM grader to resolve
         cross-entity conflicts (duplicate ``useradd``, password overwrites). A single
         section has no cross-entity conflicts, so the grader is skipped.
      3. Post-process (shebang, ``set -e``, blank-line normalisation).

    Args:
        sections: Ordered ``(entity_id, deploy_script_section)`` pairs. Order matters —
            the grader keeps the FIRST occurrence on conflict, so the credential-providing
            entity must come first (callers pass topo build order).

    Returns:
        ``(final_script, warnings)`` where ``warnings`` lists conflicts the grader fixed.
    """
    from goe.packaging.postprocessor import apply_post_processors

    entity_sections: dict[str, str] = {}
    blocks: list[str] = []
    for eid, script in sections:
        stripped = (script or "").strip()
        entity_sections[eid] = stripped
        blocks.append(f"# --- {eid} ---\n{stripped}\n")

    combined = "\n".join(blocks)
    warnings: list[str] = []

    # Conflicts only arise when multiple entities are stitched onto one system.
    if len(sections) > 1:
        combined, warnings = grade_and_fix_script(combined, entity_sections, verbose=False)

    return apply_post_processors(combined) + "\n", warnings


def grade_and_fix_script(
    concatenated_script: str,
    entity_sections: dict[str, str],
    verbose: bool = False,
) -> tuple[str, list[str]]:
    """Grade a concatenated deploy script and fix conflicts.

    Args:
        concatenated_script: The full stitched script from all entities
        entity_sections: Dict mapping entity_id → its script section (for context)
        verbose: Log detailed grading output

    Returns:
        (fixed_script, warnings) tuple
        - fixed_script: The corrected script with conflicts resolved
        - warnings: List of issues found and fixed
    """
    from goe.bedrock import call
    from goe.config import GoEConfig

    cfg = GoEConfig.get()
    model = cfg.model_for("developer")  # Use developer model tier

    system_prompt = """You are a deployment script validator for multi-entity cybersecurity challenges.

Your job: Review a concatenated bash deployment script from multiple entities and fix conflicts.

## Common Conflicts to Fix

1. **Password overwrites**: If user "alice" is created with password "pass1" in section A, and section B runs `chpasswd` to set "pass2", DELETE the second chpasswd line. The first password must be preserved for the attack chain to work.

2. **Duplicate user creation**: If `useradd alice` appears multiple times, keep only the FIRST occurrence. Delete subsequent ones.

3. **Duplicate package installs**: If `apt-get install -y sudo` appears in multiple sections, keep all of them (idempotent, safe).

4. **Service restarts**: If multiple sections restart the same service (sshd, apache2), keep all restarts (later entities may need to reload config).

5. **File overwrites**: If section A writes `/etc/config` and section B overwrites it completely (not appends), this is likely a bug — keep the first write, DELETE the second.

## Output Format

Return ONLY the fixed bash script. No explanation, no markdown fences, just the corrected script text.

## Critical Rules

- Preserve ALL apt-get installs (idempotent, safe to repeat)
- For user management: first occurrence wins (useradd, chpasswd)
- For config files: first write wins unless second is clearly an append (>>)
- Preserve all service start/restart commands
- Keep all `set -e`, `#!/bin/bash` directives from the original
- If no issues found, return the original script unchanged"""

    # Build context showing entity boundaries
    entity_context = "## Entity Sections\n\n"
    for eid, section in entity_sections.items():
        lines = section.strip().split("\n")[:5]  # First 5 lines as sample
        entity_context += f"### {eid}\n```bash\n" + "\n".join(lines) + "\n...\n```\n\n"

    user_msg = f"""{entity_context}## Full Concatenated Script

```bash
{concatenated_script}
```

Grade and fix this script. Remove conflicting commands (especially duplicate user creation and password overwrites). Return ONLY the fixed script."""

    if verbose:
        logger.info("[ScriptGrader] Grading concatenated deploy script...")

    fixed_script = call(
        model_id=model,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
        caller="packaging.grader",
    )

    # Strip markdown fences if LLM added them
    fixed_script = fixed_script.strip()
    if fixed_script.startswith("```"):
        lines = fixed_script.split("\n")
        fixed_script = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

    # Detect what changed
    warnings = _diff_summary(concatenated_script, fixed_script)

    if warnings:
        logger.warning(f"[ScriptGrader] Fixed {len(warnings)} conflict(s): {warnings}")
    elif verbose:
        logger.info("[ScriptGrader] No conflicts detected")

    return fixed_script, warnings


def _diff_summary(original: str, fixed: str) -> list[str]:
    """Generate a summary of what changed between original and fixed scripts."""
    if original.strip() == fixed.strip():
        return []

    warnings = []
    orig_lines = set(original.strip().split("\n"))
    fixed_lines = set(fixed.strip().split("\n"))

    removed = orig_lines - fixed_lines
    for line in removed:
        line = line.strip()
        if "chpasswd" in line:
            warnings.append(f"Removed duplicate password set: {line[:60]}")
        elif "useradd" in line:
            warnings.append(f"Removed duplicate user creation: {line[:60]}")
        elif line.startswith("echo") and ">" in line and ">>" not in line:
            warnings.append(f"Removed file overwrite: {line[:60]}")

    if not warnings:
        warnings.append("Script modified (see deploy.sh for details)")

    return warnings
