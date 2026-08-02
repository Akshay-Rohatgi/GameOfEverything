"""Browser action handlers using Playwright sync API."""

from __future__ import annotations
from typing import TYPE_CHECKING
from goe.executor.actions import ActionResult

if TYPE_CHECKING:
    from playwright.sync_api import Page
    from goe.models.procedure import (
        NavigateAction, ClickAction, FillAction, FillAndSubmitAction,
        EvaluateAction, WaitForAction, UploadAction, ExtractAction,
    )


def navigate(page: "Page", action: "NavigateAction", base_url: str) -> ActionResult:
    url = base_url.rstrip("/") + action.path if not action.path.startswith("http") else action.path
    try:
        response = page.goto(url)
        status = response.status if response else None
        return ActionResult(
            exit_code=0,
            status_code=status,
            current_url=page.url,
        )
    except Exception as e:
        return ActionResult(exit_code=1, error=str(e), current_url=page.url)


def click(page: "Page", action: "ClickAction") -> ActionResult:
    try:
        page.click(action.selector)
        return ActionResult(exit_code=0, current_url=page.url)
    except Exception as e:
        return ActionResult(exit_code=1, error=str(e), current_url=page.url)


def fill(page: "Page", action: "FillAction") -> ActionResult:
    try:
        for selector, value in action.fields.items():
            page.fill(selector, value)
        return ActionResult(exit_code=0, current_url=page.url)
    except Exception as e:
        return ActionResult(exit_code=1, error=str(e), current_url=page.url)


def fill_and_submit(page: "Page", action: "FillAndSubmitAction") -> ActionResult:
    try:
        for selector, value in action.fields.items():
            page.fill(selector, value)
        if action.submit:
            page.click(action.submit)
        else:
            # Press Enter in the last field as fallback
            last_selector = list(action.fields.keys())[-1]
            page.press(last_selector, "Enter")
        page.wait_for_load_state("networkidle", timeout=5000)
        return ActionResult(exit_code=0, current_url=page.url)
    except Exception as e:
        return ActionResult(exit_code=1, error=str(e), current_url=page.url)


def evaluate(page: "Page", action: "EvaluateAction") -> ActionResult:
    try:
        script = action.script
        if not script.strip().startswith("return ") and "\n" not in script:
            script = "return " + script
        result = page.evaluate(script)
        return ActionResult(exit_code=0, evaluate_return=str(result) if result is not None else "", current_url=page.url)
    except Exception as e:
        return ActionResult(exit_code=1, error=str(e), current_url=page.url)


def wait_for(page: "Page", action: "WaitForAction") -> ActionResult:
    try:
        cond = action.condition
        if cond.type == "selector":
            page.wait_for_selector(cond.value, state="visible")
        elif cond.type == "url":
            page.wait_for_url(f"**{cond.value}**")
        elif cond.type == "network_idle":
            page.wait_for_load_state("networkidle")
        elif cond.type == "text_visible":
            page.wait_for_function(
                f"() => document.body.innerText.includes({repr(cond.value)})"
            )
        return ActionResult(exit_code=0, current_url=page.url)
    except Exception as e:
        return ActionResult(exit_code=1, error=str(e), current_url=page.url)


def upload(page: "Page", action: "UploadAction") -> ActionResult:
    import tempfile, os
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="_" + action.filename, delete=False
        ) as f:
            f.write(action.file_content)
            tmp_path = f.name
        try:
            page.set_input_files(action.selector, tmp_path)
        finally:
            os.unlink(tmp_path)
        return ActionResult(exit_code=0, current_url=page.url)
    except Exception as e:
        return ActionResult(exit_code=1, error=str(e), current_url=page.url)


def extract(page: "Page", action: "ExtractAction") -> ActionResult:
    try:
        el = page.locator(action.selector).first
        attr = action.attribute
        if attr == "textContent":
            value = el.text_content()
        elif attr == "innerHTML":
            value = el.inner_html()
        elif attr == "value":
            value = el.input_value()
        else:
            value = el.get_attribute(attr)
        return ActionResult(
            exit_code=0,
            extracted_value=value or "",
            current_url=page.url,
        )
    except Exception as e:
        return ActionResult(exit_code=1, error=str(e), current_url=page.url)


def check_selector_visible(page: "Page", selector: str) -> ActionResult:
    """Used by SelectorVisibleAssertion — returns extracted_value='1' if visible."""
    try:
        page.wait_for_selector(selector, state="visible", timeout=3000)
        return ActionResult(exit_code=0, extracted_value="1", current_url=page.url)
    except Exception:
        return ActionResult(exit_code=0, extracted_value=None, current_url=page.url)


def get_title(page: "Page") -> ActionResult:
    """Used by TitleContainsAssertion."""
    try:
        title = page.title()
        return ActionResult(exit_code=0, extracted_value=title, current_url=page.url)
    except Exception as e:
        return ActionResult(exit_code=1, error=str(e), current_url=page.url)


def get_cookie(page: "Page", name: str) -> ActionResult:
    """Used by CookieExistsAssertion / CookieValueAssertion."""
    try:
        cookies = page.context.cookies()
        for c in cookies:
            if c["name"] == name:
                return ActionResult(exit_code=0, extracted_value=c["value"], current_url=page.url)
        return ActionResult(exit_code=0, extracted_value=None, current_url=page.url)
    except Exception as e:
        return ActionResult(exit_code=1, error=str(e), current_url=page.url)
