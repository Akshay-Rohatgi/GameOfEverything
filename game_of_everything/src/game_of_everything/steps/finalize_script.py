"""Step 4: Concatenate validated snippets, post-process, and write the final deployment script."""

from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import rich

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
    skip_disk_write: bool = False,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Concatenate validated snippets through the post-processor pipeline and write the final script.

    Args:
        state: Flow state to mutate in-place.
        agents_config: Loaded agents.yaml dict (unused — included for interface consistency).
        tasks_config: Loaded tasks.yaml dict (unused — included for interface consistency).
        skip_disk_write: When True, populate state.final_script but do not write to disk.
            Used by run_box_pipelines so per-box scripts are collected into deploy_scripts
            and written once by finalize_topology rather than as stray timestamped files.
        ui: Optional GoEConsole for structured output.
    """
    if not state.generated_snippets and not state.resolved_custom_apps and not state.resolved_preset_apps:
        if ui:
            ui.log("No generated snippets to finalize. Skipping.")
        return

    # Only include validated snippets
    all_snippets = state.generated_snippets or []
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
            header = f"# --- custom_app/{app.vector.display_name} ---"
            custom_sections.append(f"{header}\n{app.deploy_snippet}")
        else:
            if ui:
                ui.log(f"  Skipping custom app '{app.vector.display_name}' (validation failed)")

    # Prepend validated preset app deploy snippets (with stack deduplication)
    preset_sections = []
    emitted_stacks: set = set()
    for app in state.resolved_preset_apps:
        if app.validation_passed:
            # The deploy_snippet includes the stack install. For dedup, we split
            # on the "# --- Preset:" marker: everything before it is stack setup,
            # everything from it onward is app-specific.
            stack_marker = f"# --- Preset: {app.vector.preset_id} ---"
            if stack_marker in app.deploy_snippet and app.stack_id not in emitted_stacks:
                # First app using this stack — emit full snippet
                header = f"# --- preset_app/{app.vector.preset_id} ({', '.join(app.vector.vuln_profile_ids)}) ---"
                preset_sections.append(f"{header}\n{app.deploy_snippet}")
                emitted_stacks.add(app.stack_id)
            elif stack_marker in app.deploy_snippet and app.stack_id in emitted_stacks:
                # Stack already emitted — only emit from the preset marker onward
                idx = app.deploy_snippet.index(stack_marker)
                app_only = app.deploy_snippet[idx:]
                header = f"# --- preset_app/{app.vector.preset_id} ({', '.join(app.vector.vuln_profile_ids)}) ---"
                preset_sections.append(f"{header}\n{app_only}")
            else:
                # No marker found — emit the whole snippet
                header = f"# --- preset_app/{app.vector.preset_id} ({', '.join(app.vector.vuln_profile_ids)}) ---"
                preset_sections.append(f"{header}\n{app.deploy_snippet}")
        else:
            if ui:
                ui.log(f"  Skipping preset app '{app.vector.preset_id}' (validation failed)")

    if not (state.generated_snippets or []) and not custom_sections and not preset_sections:
        if ui:
            ui.log("No snippets passed validation. No deployment script generated.")
        else:
            rich.print("[bold red]No snippets passed validation. No deployment script generated.[/bold red]")
        return

    # Build a lookup of failure reasons from test_results for commented-out stubs
    failure_reasons: dict = {}
    if state.test_results:
        for tr in state.test_results:
            if not tr.layer1_verdict.passed:
                failure_reasons[tr.atom_name] = tr.layer1_verdict.reasoning
            elif tr.layer2_verdicts and not all(v.passed for v in tr.layer2_verdicts):
                failed_l2 = [v.reasoning for v in tr.layer2_verdicts if not v.passed]
                failure_reasons[tr.atom_name] = "Layer 2: " + "; ".join(failed_l2)

    # Concatenate: custom apps first, then all snippets in sequenced order.
    # Failed snippets are included as commented-out stubs so the reader knows
    # what was attempted and why it was skipped.
    sections = preset_sections[:] + custom_sections[:]
    for snippet in state.generated_snippets or []:
        if snippet.validated:
            header = f"# --- {snippet.atom_name} ---"
            sections.append(f"{header}\n{snippet.code_snippet}")
        else:
            reason = failure_reasons.get(snippet.atom_name, "validation failed")
            commented_code = "\n".join(
                f"# {line}" for line in snippet.code_snippet.splitlines()
            )
            stub = (
                f"# --- {snippet.atom_name} (SKIPPED: {reason}) ---\n"
                f"{commented_code}"
            )
            sections.append(stub)
    raw_script = "\n\n".join(sections)

    # Run through the extensible post-processor pipeline
    final_script = apply_post_processors(raw_script)
    state.final_script = final_script

    if skip_disk_write:
        if ui:
            ui.log("finalize_script: skip_disk_write=True — script stored in state, not written to disk.")
        else:
            rich.print("[dim]finalize_script: skip_disk_write=True — script stored in state, not written to disk.[/dim]")
        return

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
