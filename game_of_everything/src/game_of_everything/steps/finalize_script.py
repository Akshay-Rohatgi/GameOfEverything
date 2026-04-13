"""Step 4: Concatenate validated snippets, post-process, and write the final deployment script."""

from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from game_of_everything.state import GoEState
from game_of_everything.script_postprocessor import apply_post_processors

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole

# Project root: steps/ → game_of_everything/ → src/ → game_of_everything/ → (project root)
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def run_finalize_script(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Concatenate validated snippets through the post-processor pipeline and write the final script."""
    if not state.generated_snippets and not state.resolved_custom_apps:
        if ui:
            ui.log("No generated snippets to finalize. Skipping.")
        return

    # Only include validated snippets
    all_snippets = state.generated_snippets or []
    validated = [s for s in all_snippets if s.validated]
    skipped = [s for s in all_snippets if not s.validated]

    # Log skipped snippets
    if skipped and ui:
        ui.log("\n=== SKIPPED SNIPPETS (validation failed) ===")
        for s in skipped:
            ui.log(f"  ✗ {s.atom_name}")
            if state.test_results:
                for tr in state.test_results:
                    if tr.atom_name == s.atom_name:
                        if not tr.layer1_verdict.passed:
                            ui.log(f"    Layer 1: {tr.layer1_verdict.reasoning}")
                        if tr.layer2_verdicts:
                            for v in tr.layer2_verdicts:
                                if not v.passed:
                                    ui.log(f"    Layer 2: {v.reasoning}")
                        if tr.error:
                            ui.log(f"    Error: {tr.error}")
                        if tr.diagnostic_results:
                            ui.log(f"    Diagnostic History ({len(tr.diagnostic_results)} attempts):")
                            for idx, dr in enumerate(tr.diagnostic_results, 1):
                                ui.log(f"      #{idx} [confidence: {dr.confidence}]: {dr.diagnosis}")

    # Prepend validated custom app deploy snippets
    custom_sections = []
    for app in state.resolved_custom_apps:
        if app.validation_passed:
            header = f"# --- custom_app/{app.vector.vuln_atom_id} ---"
            custom_sections.append(f"{header}\n{app.deploy_snippet}")
        else:
            if ui:
                ui.log(f"  Skipping custom app '{app.vector.vuln_atom_id}' (validation failed)")

    if not validated and not custom_sections:
        if ui:
            ui.log("No snippets passed validation. No deployment script generated.")
        return

    # Concatenate: custom apps first, then misconfig snippets in sequenced order
    sections = custom_sections[:]
    for snippet in validated:
        header = f"# --- {snippet.atom_name} ---"
        sections.append(f"{header}\n{snippet.code_snippet}")
    raw_script = "\n\n".join(sections)

    # Run through the extensible post-processor pipeline
    final_script = apply_post_processors(raw_script)
    state.final_script = final_script

    # Write to output/<timestamp>_deploy.sh
    output_dir = _PROJECT_ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"{timestamp}_deploy.sh"
    out_path.write_text(final_script, encoding="utf-8")
    out_path.chmod(0o755)

    state.output_path = str(out_path)

    if ui:
        ui.log(f"\n=== FINAL DEPLOYMENT SCRIPT ===")
        ui.log(final_script)
        ui.log(f"\nWritten to: {out_path}")
