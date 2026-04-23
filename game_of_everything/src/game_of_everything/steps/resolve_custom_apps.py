"""Step: resolve_custom_apps — run CustomAppFlow for each CustomVector in the scenario.

Reads state.synthesized_scenario.custom_vectors and runs a CustomAppFlow for each one.
Results are stored in state.resolved_custom_apps.

This step is a no-op when no custom web apps were requested (custom_vectors is empty).
"""

import time
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
        label = f"Building app: {vector.display_name}"
        if ui:
            ui.log(f"\nCustom App {i}/{len(vectors)}: {vector.display_name} / {'+'.join(vector.attack_chain_goals)} / {vector.runtime_id}")
            ui.status(label)

        t0 = time.monotonic()

        # Backfill synthesis_context if empty
        if not vector.synthesis_context and state.synthesized_scenario and state.synthesized_scenario.custom_app_scope:
            vector = vector.model_copy(
                update={"synthesis_context": state.synthesized_scenario.custom_app_scope}
            )

        try:
            flow = CustomAppFlow(vector=vector, ui=ui)
            flow.kickoff()

            elapsed = time.monotonic() - t0
            if flow.state.resolved:
                resolved.append(flow.state.resolved)
                if ui:
                    status = "PASS" if flow.state.resolved.validation_passed else "PARTIAL"
                    ui.log(f"  {status} — {vector.display_name}")
                    l1_pass = flow.state.layer1_verdict.passed if flow.state.layer1_verdict else False
                    l2_pass = flow.state.layer2_verdict.passed if flow.state.layer2_verdict else None
                    retries = max(0, flow.state.generate_attempts - 1)
                    testing_snip = flow.state.generated_app.testing_snippet if flow.state.generated_app else None
                    attack_snip = flow.state.generated_app.attack_snippet if flow.state.generated_app else None
                    all_pass = l1_pass and (l2_pass is not False)
                    if all_pass:
                        ui.step_done(label, elapsed)
                    else:
                        ui.step_fail(label)
                    ui.test_result(
                        atom=vector.display_name,
                        l1_pass=l1_pass,
                        l2_pass=l2_pass,
                        retries=retries,
                        is_app=True,
                        testing_snippet=testing_snip,
                        attack_snippet=attack_snip,
                    )
            else:
                if ui:
                    ui.log(f"  FAIL — no resolved output for {vector.display_name}")
                    ui.step_fail(label, "no output produced")
                    ui.test_skipped(vector.display_name, "no output")

        except AppGenerationError as e:
            elapsed = time.monotonic() - t0
            if ui:
                ui.log(f"  AppGenerationError — {e}")
                ui.step_fail(label, str(e)[:80])
                ui.test_skipped(vector.display_name, str(e)[:60])
            else:
                print(f"AppGenerationError: {e}")
        except Exception as e:
            elapsed = time.monotonic() - t0
            if ui:
                ui.log(f"  Unexpected error — {e}")
                ui.step_fail(label, str(e)[:80])
                ui.test_skipped(vector.display_name, str(e)[:60])
            else:
                print(f"Unexpected error: {e}")

    state.resolved_custom_apps = resolved
    if ui:
        ui.log(f"resolve_custom_apps complete: {len(resolved)}/{len(vectors)} app(s) resolved.")
