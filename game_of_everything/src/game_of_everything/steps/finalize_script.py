"""Step 4: Concatenate validated snippets, post-process, and write the final deployment script."""

from datetime import datetime
from pathlib import Path

import rich

from game_of_everything.state import GoEState
from game_of_everything.script_postprocessor import apply_post_processors

# Project root: steps/ → game_of_everything/ → src/ → game_of_everything/ → (project root)
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def run_finalize_script(
    state: GoEState,
    agents_config: dict,
    tasks_config: dict,
) -> None:
    """Concatenate validated snippets through the post-processor pipeline and write the final script.

    Args:
        state: Flow state to mutate in-place.
        agents_config: Loaded agents.yaml dict (unused — included for interface consistency).
        tasks_config: Loaded tasks.yaml dict (unused — included for interface consistency).
    """
    if not state.generated_snippets:
        print("No generated snippets to finalize. Skipping.")
        return

    # Only include validated snippets in the final script
    validated = [s for s in state.generated_snippets if s.validated]
    skipped = [s for s in state.generated_snippets if not s.validated]

    if skipped:
        rich.print("\n[bold yellow]=== SKIPPED SNIPPETS (validation failed) ===[/bold yellow]")
        for s in skipped:
            rich.print(f"  [red]✗[/red] {s.atom_name}")
            if state.test_results:
                for tr in state.test_results:
                    if tr.atom_name == s.atom_name:
                        if not tr.layer1_verdict.passed:
                            rich.print(f"    Layer 1: {tr.layer1_verdict.reasoning}")
                        if tr.layer2_verdicts:
                            for v in tr.layer2_verdicts:
                                if not v.passed:
                                    rich.print(f"    Layer 2: {v.reasoning}")
                        if tr.error:
                            rich.print(f"    Error: {tr.error}")
                        if tr.diagnostic_results:
                            rich.print(f"    [bold cyan]Diagnostic History ({len(tr.diagnostic_results)} attempts):[/bold cyan]")
                            for idx, dr in enumerate(tr.diagnostic_results, 1):
                                rich.print(f"      #{idx} [confidence: {dr.confidence}]")
                                rich.print(f"         Diagnosis: {dr.diagnosis}")
                                if dr.fixed_code_snippet != s.code_snippet:
                                    rich.print(f"         [dim]Code was modified in this attempt[/dim]")
                                if dr.fixed_testing_snippet and dr.fixed_testing_snippet != getattr(s, 'testing_snippet', ''):
                                    rich.print(f"         [dim]Testing snippet was modified in this attempt[/dim]")

    if not validated:
        rich.print("[bold red]No snippets passed validation. No deployment script generated.[/bold red]")
        return

    # Concatenate validated snippets in order, separated by labelled section headers
    sections = []
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

    rich.print("\n[bold magenta]=== FINAL DEPLOYMENT SCRIPT ===[/bold magenta]")
    rich.print(final_script)
    rich.print(f"\n[bold green]Written to:[/bold green] {out_path}")
