"""Step: resolve_custom_apps — run CustomAppFlow for each CustomVector in the scenario.

Reads state.synthesized_scenario.custom_vectors and runs a CustomAppFlow for each one.
Results are stored in state.resolved_custom_apps.

This step is a no-op when no custom web apps were requested (custom_vectors is empty).
"""

from typing import Optional, TYPE_CHECKING

from game_of_everything.state import GoEState
from game_of_everything.steps.custom_app_flow import CustomAppFlow, AppGenerationError

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole


def run_resolve_custom_apps(
    state: GoEState,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Run CustomAppFlow for each CustomVector produced by the synthesis step."""
    vectors = (
        state.synthesized_scenario.custom_vectors
        if state.synthesized_scenario
        else []
    )

    if not vectors:
        if ui:
            ui.log("resolve_custom_apps: no custom vectors — skipping.")
        return

    if ui:
        ui.log(f"resolve_custom_apps: {len(vectors)} custom app(s) to build")

    resolved = []
    for i, vector in enumerate(vectors, 1):
        if ui:
            ui.log(f"\nCustom App {i}/{len(vectors)}: {vector.vuln_atom_id} / {vector.attack_chain_goal} / {vector.runtime_id}")

        # Backfill synthesis_context if empty
        if not vector.synthesis_context and state.synthesized_scenario and state.synthesized_scenario.custom_app_scope:
            vector = vector.model_copy(
                update={"synthesis_context": state.synthesized_scenario.custom_app_scope}
            )

        try:
            flow = CustomAppFlow(vector=vector, ui=ui)
            flow.kickoff()

            if flow.state.resolved:
                resolved.append(flow.state.resolved)
                if ui:
                    status = "PASS" if flow.state.resolved.validation_passed else "PARTIAL"
                    ui.log(f"  {status} — {vector.vuln_atom_id}")
            else:
                if ui:
                    ui.log(f"  FAIL — no resolved output for {vector.vuln_atom_id}")

        except AppGenerationError as e:
            if ui:
                ui.log(f"  AppGenerationError — {e}")
            else:
                print(f"AppGenerationError: {e}")
        except Exception as e:
            if ui:
                ui.log(f"  Unexpected error — {e}")
            else:
                print(f"Unexpected error: {e}")

    state.resolved_custom_apps = resolved
    if ui:
        ui.log(f"resolve_custom_apps complete: {len(resolved)}/{len(vectors)} app(s) resolved.")
