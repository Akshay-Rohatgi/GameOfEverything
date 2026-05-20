"""Main procedure executor."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from goe.executor.actions import ActionResult
from goe.executor.interpolation import interpolate
from goe.executor.assertions import check as check_assertion
from goe.executor.outputs import capture as capture_output

if TYPE_CHECKING:
    from goe.models.procedure import Procedure, Step
    from goe.container.environment import TestEnvironment


@dataclass
class StepOutcome:
    step_id: str
    passed: bool
    reason: str
    outputs: dict[str, str]
    raw: ActionResult


@dataclass
class ProcedureResult:
    passed: bool
    steps: list[StepOutcome] = field(default_factory=list)
    failed_step: str | None = None
    error: str | None = None


def run(procedure: "Procedure", env: "TestEnvironment", ctx: dict) -> ProcedureResult:
    """Execute a procedure against the given environment.

    ctx must contain at least:
      target_host, attacker_host, target_port
    Optionally:
      edges: {edge_id: {param: concrete_value}}
    """
    from goe.executor.sessions import SessionManager
    from goe.models.procedure import (
        HttpRequestAction, ExecAttackerAction, ExecTargetAction,
        ListenAction, SleepAction,
        NavigateAction, ClickAction, FillAction, FillAndSubmitAction,
        EvaluateAction, WaitForAction, UploadAction, ExtractAction,
        SelectorVisibleAssertion, SelectorNotVisibleAssertion,
        SelectorTextAssertion, SelectorCountAssertion,
        UrlContainsAssertion, UrlEqualsAssertion,
        CookieExistsAssertion, CookieValueAssertion,
        LocalStorageContainsAssertion, TitleContainsAssertion,
        EvaluateResultContainsAssertion, EvaluateResultRegexAssertion,
    )
    from goe.executor.actions import shell, http, listen, sleep as sleep_mod
    from goe.executor.actions import browser as browser_mod

    # Build mutable interpolation context
    run_ctx: dict = {**ctx, "steps": {}}

    # Initialise browser sessions if any
    sessions: SessionManager | None = None
    cdp_url = env.get_cdp_url() if procedure.sessions else ""
    if procedure.sessions:
        sessions = SessionManager(cdp_url)
        for sess in procedure.sessions:
            sessions.init_session(sess)
            if sess.auth:
                sessions.login(sess.id, sess.auth, run_ctx)

    outcomes: list[StepOutcome] = []

    try:
        for step in procedure.procedure:
            result = _execute_step(step, env, sessions, run_ctx,
                                   browser_mod, shell, http, listen, sleep_mod)

            # Handle browser assertions that need extra page queries
            if step.expect is not None and step.session and sessions:
                page = sessions.get_page(step.session)
                result = _augment_for_browser_assertions(step.expect, result, page, browser_mod)

            # Check assertion
            if step.expect is not None:
                passed, reason = check_assertion(step.expect, result)
            else:
                passed, reason = True, "no assertion"

            # Capture outputs
            captured: dict[str, str] = {}
            for name, spec in step.outputs.items():
                val = capture_output(interpolate(spec, run_ctx), result)
                if val is not None:
                    captured[name] = val

            # Update step context for downstream interpolation
            run_ctx["steps"][step.step_id] = captured

            outcomes.append(StepOutcome(
                step_id=step.step_id,
                passed=passed,
                reason=reason,
                outputs=captured,
                raw=result,
            ))

            if not passed:
                return ProcedureResult(passed=False, steps=outcomes, failed_step=step.step_id)

    except Exception as e:
        return ProcedureResult(passed=False, steps=outcomes, error=str(e))
    finally:
        if sessions is not None:
            sessions.close_all()

    return ProcedureResult(passed=True, steps=outcomes)


def _execute_step(
    step: "Step",
    env: "TestEnvironment",
    sessions,
    ctx: dict,
    browser_mod,
    shell,
    http,
    listen,
    sleep_mod,
) -> ActionResult:
    from goe.models.procedure import (
        HttpRequestAction, ExecAttackerAction, ExecTargetAction,
        ListenAction, SleepAction,
        NavigateAction, ClickAction, FillAction, FillAndSubmitAction,
        EvaluateAction, WaitForAction, UploadAction, ExtractAction,
    )

    action = step.action

    # Interpolate all string fields in the action
    action = _interpolate_action(action, ctx)

    if isinstance(action, ExecAttackerAction):
        return shell.exec_attacker(env, action.command)

    if isinstance(action, ExecTargetAction):
        return shell.exec_target(env, action.command)

    if isinstance(action, HttpRequestAction):
        return http.http_request(env, action.method, action.url, action.headers, action.body)

    if isinstance(action, ListenAction):
        return listen.listen(env, action.port, action.duration)

    if isinstance(action, SleepAction):
        return sleep_mod.sleep_action(action.seconds)

    # Browser actions
    if step.session is None:
        raise ValueError(f"Step '{step.step_id}' has browser action but no session declared")
    page = sessions.get_page(step.session)
    base_url = sessions.get_base_url(step.session)

    if isinstance(action, NavigateAction):
        return browser_mod.navigate(page, action, base_url)
    if isinstance(action, ClickAction):
        return browser_mod.click(page, action)
    if isinstance(action, FillAction):
        return browser_mod.fill(page, action)
    if isinstance(action, FillAndSubmitAction):
        return browser_mod.fill_and_submit(page, action)
    if isinstance(action, EvaluateAction):
        return browser_mod.evaluate(page, action)
    if isinstance(action, WaitForAction):
        return browser_mod.wait_for(page, action)
    if isinstance(action, UploadAction):
        return browser_mod.upload(page, action)
    if isinstance(action, ExtractAction):
        return browser_mod.extract(page, action)

    raise ValueError(f"Unknown action type: {type(action).__name__}")


def _interpolate_action(action, ctx: dict):
    """Return a copy of the action with all string fields interpolated."""
    data = action.model_dump()
    data = _deep_interpolate(data, ctx)
    return type(action).model_validate(data)


def _deep_interpolate(obj, ctx: dict):
    if isinstance(obj, str):
        return interpolate(obj, ctx)
    if isinstance(obj, dict):
        return {k: _deep_interpolate(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_interpolate(v, ctx) for v in obj]
    return obj


def _augment_for_browser_assertions(
    assertion,
    result: ActionResult,
    page,
    browser_mod,
) -> ActionResult:
    """Run extra page queries needed by certain assertion types."""
    from goe.models.procedure import (
        AllAssertion,
        SelectorVisibleAssertion, SelectorNotVisibleAssertion,
        SelectorTextAssertion, SelectorCountAssertion,
        UrlContainsAssertion, UrlEqualsAssertion,
        CookieExistsAssertion, CookieValueAssertion,
        LocalStorageContainsAssertion, TitleContainsAssertion,
        EvaluateResultContainsAssertion, EvaluateResultRegexAssertion,
    )

    if isinstance(assertion, AllAssertion):
        for sub in assertion.all:
            result = _augment_for_browser_assertions(sub, result, page, browser_mod)
        return result

    if isinstance(assertion, SelectorVisibleAssertion):
        r = browser_mod.check_selector_visible(page, assertion.selector_visible)
        return ActionResult(**{**result.__dict__, "extracted_value": r.extracted_value,
                               "current_url": page.url, "error": r.error})

    if isinstance(assertion, SelectorNotVisibleAssertion):
        r = browser_mod.check_selector_visible(page, assertion.selector_not_visible)
        # Invert: if visible returns "1", we want None for "not visible"
        val = None if r.extracted_value == "1" else "not_visible"
        return ActionResult(**{**result.__dict__, "extracted_value": val, "current_url": page.url})

    if isinstance(assertion, (SelectorTextAssertion, SelectorCountAssertion)):
        try:
            selector = assertion.selector
            if isinstance(assertion, SelectorTextAssertion):
                el = page.locator(selector).first
                val = el.text_content() or ""
            else:
                val = str(page.locator(selector).count())
            return ActionResult(**{**result.__dict__, "extracted_value": val, "current_url": page.url})
        except Exception:
            return result

    if isinstance(assertion, (UrlContainsAssertion, UrlEqualsAssertion)):
        return ActionResult(**{**result.__dict__, "current_url": page.url})

    if isinstance(assertion, CookieExistsAssertion):
        r = browser_mod.get_cookie(page, assertion.cookie_exists)
        exists = "1" if r.extracted_value is not None else None
        return ActionResult(**{**result.__dict__, "extracted_value": exists, "current_url": page.url})

    if isinstance(assertion, CookieValueAssertion):
        r = browser_mod.get_cookie(page, assertion.name)
        return ActionResult(**{**result.__dict__, "extracted_value": r.extracted_value,
                               "current_url": page.url})

    if isinstance(assertion, LocalStorageContainsAssertion):
        try:
            val = page.evaluate(f"() => localStorage.getItem({repr(assertion.key)})")
            return ActionResult(**{**result.__dict__, "extracted_value": str(val) if val else "",
                                   "current_url": page.url})
        except Exception:
            return result

    if isinstance(assertion, TitleContainsAssertion):
        r = browser_mod.get_title(page)
        return ActionResult(**{**result.__dict__, "extracted_value": r.extracted_value,
                               "current_url": page.url})

    # URL is always updated for browser steps
    return ActionResult(**{**result.__dict__, "current_url": page.url})
