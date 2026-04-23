"""Step: resolve_preset_apps — run PresetAppFlow for each PresetVector in the scenario.

Reads state.synthesized_scenario.preset_vectors and runs a PresetAppFlow for each one.
Results are stored in state.resolved_preset_apps.

This step is a no-op when no preset apps were requested (preset_vectors is empty).
"""

from typing import Optional, TYPE_CHECKING

from game_of_everything.state import GoEState
from game_of_everything.steps.preset_app_flow import PresetAppFlow, PresetDeployError

if TYPE_CHECKING:
    from game_of_everything.ui import GoEConsole


def run_resolve_preset_apps(
    state: GoEState,
    ui: Optional["GoEConsole"] = None,
) -> None:
    """Run PresetAppFlow for each PresetVector produced by the synthesis step."""
    vectors = (
        state.synthesized_scenario.preset_vectors
        if state.synthesized_scenario
        else []
    )

    if not vectors:
        if ui:
            ui.log("resolve_preset_apps: no preset vectors — skipping.")
        return

    if ui:
        ui.log(f"resolve_preset_apps: {len(vectors)} preset app(s) to deploy")
        ui.info("  [dim]apps:[/dim]")

    resolved = []
    for i, vector in enumerate(vectors, 1):
        if ui:
            ui.log(
                f"\nPreset App {i}/{len(vectors)}: "
                f"{vector.preset_id} / {vector.vuln_profile_ids}"
            )

        try:
            flow = PresetAppFlow(vector=vector, ui=ui)
            flow.kickoff()

            if flow.state.resolved:
                resolved.append(flow.state.resolved)
                if ui:
                    status = "PASS" if flow.state.resolved.validation_passed else "PARTIAL"
                    ui.log(f"  {status} — {vector.preset_id}")
                    l1_pass = flow.state.layer1_verdict.passed if flow.state.layer1_verdict else False
                    l2_pass = flow.state.layer2_verdict.passed if flow.state.layer2_verdict else None
                    retries = max(0, flow.state.validate_attempts - 1)
                    ui.test_result(
                        atom=vector.preset_id,
                        l1_pass=l1_pass,
                        l2_pass=l2_pass,
                        retries=retries,
                        is_app=True,
                        testing_snippet=flow.state.testing_snippet,
                        attack_snippet=flow.state.attack_snippet,
                    )
            else:
                if ui:
                    ui.log(f"  FAIL — no resolved output for {vector.preset_id}")
                    ui.test_skipped(vector.preset_id, "no output")

        except PresetDeployError as e:
            if ui:
                ui.log(f"  PresetDeployError — {e}")
                ui.test_skipped(vector.preset_id, str(e)[:80])
            else:
                print(f"PresetDeployError: {e}")
        except Exception as e:
            if ui:
                ui.log(f"  Unexpected error — {e}")
                ui.test_skipped(vector.preset_id, str(e)[:80])
            else:
                print(f"Unexpected error: {e}")

    state.resolved_preset_apps = resolved
    if ui:
        ui.log(f"resolve_preset_apps complete: {len(resolved)}/{len(vectors)} app(s) resolved.")
