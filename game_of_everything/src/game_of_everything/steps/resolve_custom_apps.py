"""Step: resolve_custom_apps — run CustomAppFlow for each CustomVector in the scenario.

Reads state.synthesized_scenario.custom_vectors and runs a CustomAppFlow for each one.
Results are stored in state.resolved_custom_apps.

This step is a no-op when no custom web apps were requested (custom_vectors is empty).
"""

import rich

from game_of_everything.state import GoEState
from game_of_everything.steps.custom_app_flow import CustomAppFlow, AppGenerationError


def run_resolve_custom_apps(state: GoEState) -> None:
    """Run CustomAppFlow for each CustomVector produced by the synthesis step.

    Args:
        state: Flow state to mutate in-place.
    """
    vectors = (
        state.synthesized_scenario.custom_vectors
        if state.synthesized_scenario
        else []
    )

    if not vectors:
        rich.print("[dim]resolve_custom_apps: no custom vectors — skipping.[/dim]")
        return

    rich.print(f"\n[bold cyan]=== resolve_custom_apps: {len(vectors)} custom app(s) to build ===[/bold cyan]")

    resolved = []
    for i, vector in enumerate(vectors, 1):
        rich.print(
            f"\n[bold cyan]--- Custom App {i}/{len(vectors)}: "
            f"{vector.vuln_atom_id} / {vector.attack_chain_goal} / {vector.runtime_id} ---[/bold cyan]"
        )

        # If synthesis_context is empty, backfill from the human-readable custom_app_scope
        if not vector.synthesis_context and state.synthesized_scenario and state.synthesized_scenario.custom_app_scope:
            vector = vector.model_copy(
                update={"synthesis_context": state.synthesized_scenario.custom_app_scope}
            )

        try:
            flow = CustomAppFlow(vector=vector)
            flow.kickoff()

            if flow.state.resolved:
                resolved.append(flow.state.resolved)
                status = (
                    "[green]PASS[/green]"
                    if flow.state.resolved.validation_passed
                    else "[yellow]PARTIAL (validation failed)[/yellow]"
                )
                rich.print(f"  {status} — {vector.vuln_atom_id}")
            else:
                rich.print(f"  [red]FAIL[/red] — no resolved output for {vector.vuln_atom_id}")

        except AppGenerationError as e:
            rich.print(f"  [red]AppGenerationError[/red] — {e}")
            rich.print("  [yellow]Skipping this custom app and continuing.[/yellow]")
        except Exception as e:
            rich.print(f"  [red]Unexpected error[/red] — {e}")
            rich.print("  [yellow]Skipping this custom app and continuing.[/yellow]")

    state.resolved_custom_apps = resolved
    rich.print(
        f"\n[bold green]resolve_custom_apps complete: "
        f"{len(resolved)}/{len(vectors)} app(s) resolved.[/bold green]"
    )
