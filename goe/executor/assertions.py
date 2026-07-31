"""Assertion checking for procedure steps. Pure functions, no I/O."""

import re
from typing import TYPE_CHECKING
from goe.executor.actions import ActionResult

if TYPE_CHECKING:
    from goe.models.procedure import Assertion


def check(assertion: "Assertion", result: ActionResult) -> tuple[bool, str]:
    """Return (passed, reason). Dispatches on assertion type by class name."""
    from goe.models.procedure import (
        AllAssertion,
        StatusAssertion, ExitCodeAssertion,
        StdoutContainsAssertion, StdoutRegexAssertion,
        ReceivedContainsAssertion, ReceivedRegexAssertion,
        BodyContainsAssertion, BodyRegexAssertion,
        SelectorVisibleAssertion, SelectorNotVisibleAssertion,
        SelectorTextAssertion, SelectorCountAssertion,
        UrlContainsAssertion, UrlEqualsAssertion,
        CookieExistsAssertion, CookieValueAssertion,
        LocalStorageContainsAssertion,
        EvaluateResultContainsAssertion, EvaluateResultRegexAssertion,
        ExtractedContainsAssertion, ExtractedRegexAssertion,
        TitleContainsAssertion,
    )

    if isinstance(assertion, AllAssertion):
        for sub in assertion.all:
            passed, reason = check(sub, result)
            if not passed:
                return False, reason
        return True, "all assertions passed"

    if isinstance(assertion, StatusAssertion):
        actual = result.status_code
        if actual == assertion.status:
            return True, f"status {actual}"
        return False, f"expected status {assertion.status}, got {actual}"

    if isinstance(assertion, ExitCodeAssertion):
        if result.exit_code == assertion.exit_code:
            return True, f"exit_code {result.exit_code}"
        return False, f"expected exit_code {assertion.exit_code}, got {result.exit_code}"

    if isinstance(assertion, StdoutContainsAssertion):
        if assertion.stdout_contains in result.stdout:
            return True, f"stdout contains '{assertion.stdout_contains}'"
        return False, f"stdout does not contain '{assertion.stdout_contains}'\nstdout: {result.stdout[:500]}"

    if isinstance(assertion, StdoutRegexAssertion):
        if re.search(assertion.stdout_regex, result.stdout):
            return True, f"stdout matches regex"
        return False, f"stdout does not match '{assertion.stdout_regex}'\nstdout: {result.stdout[:500]}"

    if isinstance(assertion, ReceivedContainsAssertion):
        if assertion.received_contains in result.stdout:
            return True, f"received contains '{assertion.received_contains}'"
        return False, f"received does not contain '{assertion.received_contains}'\nreceived: {result.stdout[:500]}"

    if isinstance(assertion, ReceivedRegexAssertion):
        if re.search(assertion.received_regex, result.stdout):
            return True, "received matches regex"
        return False, f"received does not match '{assertion.received_regex}'"

    if isinstance(assertion, BodyContainsAssertion):
        if assertion.body_contains in result.body:
            return True, f"body contains '{assertion.body_contains}'"
        return False, f"body does not contain '{assertion.body_contains}'\nbody: {result.body[:500]}"

    if isinstance(assertion, BodyRegexAssertion):
        if re.search(assertion.body_regex, result.body):
            return True, "body matches regex"
        return False, f"body does not match '{assertion.body_regex}'"

    if isinstance(assertion, SelectorVisibleAssertion):
        if result.extracted_value is not None:
            return True, f"selector '{assertion.selector_visible}' visible"
        return False, f"selector '{assertion.selector_visible}' not visible\nerror: {result.error}"

    if isinstance(assertion, SelectorNotVisibleAssertion):
        if result.extracted_value is None:
            return True, f"selector '{assertion.selector_not_visible}' not visible"
        return False, f"selector '{assertion.selector_not_visible}' is visible but should not be"

    if isinstance(assertion, SelectorTextAssertion):
        text = result.extracted_value or ""
        if assertion.contains in text:
            return True, f"selector text contains '{assertion.contains}'"
        return False, f"selector text '{text[:200]}' does not contain '{assertion.contains}'"

    if isinstance(assertion, SelectorCountAssertion):
        try:
            count = int(result.extracted_value or "0")
        except ValueError:
            count = 0
        if count == assertion.count:
            return True, f"selector count is {count}"
        return False, f"expected {assertion.count} elements, found {count}"

    if isinstance(assertion, UrlContainsAssertion):
        if assertion.url_contains in result.current_url:
            return True, f"url contains '{assertion.url_contains}'"
        return False, f"url '{result.current_url}' does not contain '{assertion.url_contains}'"

    if isinstance(assertion, UrlEqualsAssertion):
        if result.current_url == assertion.url_equals:
            return True, f"url equals '{assertion.url_equals}'"
        return False, f"url '{result.current_url}' != '{assertion.url_equals}'"

    if isinstance(assertion, CookieExistsAssertion):
        # Cookie check encoded in extracted_value as "1" or "0"
        if result.extracted_value == "1":
            return True, f"cookie '{assertion.cookie_exists}' exists"
        return False, f"cookie '{assertion.cookie_exists}' not found"

    if isinstance(assertion, CookieValueAssertion):
        val = result.extracted_value or ""
        if assertion.contains in val:
            return True, f"cookie value contains '{assertion.contains}'"
        return False, f"cookie value '{val[:200]}' does not contain '{assertion.contains}'"

    if isinstance(assertion, LocalStorageContainsAssertion):
        val = result.extracted_value or ""
        if assertion.contains in val:
            return True, f"localStorage[{assertion.key}] contains '{assertion.contains}'"
        return False, f"localStorage[{assertion.key}]='{val[:200]}' does not contain '{assertion.contains}'"

    if isinstance(assertion, EvaluateResultContainsAssertion):
        if assertion.evaluate_result_contains is None:
            return True, "no assertion on evaluate result"
        val = result.evaluate_return or ""
        if assertion.evaluate_result_contains in val:
            return True, f"evaluate result contains '{assertion.evaluate_result_contains}'"
        return False, f"evaluate result '{val[:200]}' does not contain '{assertion.evaluate_result_contains}'"

    if isinstance(assertion, EvaluateResultRegexAssertion):
        val = result.evaluate_return or ""
        if re.search(assertion.evaluate_result_regex, val):
            return True, "evaluate result matches regex"
        return False, f"evaluate result does not match '{assertion.evaluate_result_regex}'"

    if isinstance(assertion, ExtractedContainsAssertion):
        val = result.extracted_value or ""
        if assertion.extracted_contains in val:
            return True, f"extracted value contains '{assertion.extracted_contains}'"
        return False, f"extracted value '{val[:200]}' does not contain '{assertion.extracted_contains}'"

    if isinstance(assertion, ExtractedRegexAssertion):
        val = result.extracted_value or ""
        if re.search(assertion.extracted_regex, val):
            return True, "extracted value matches regex"
        return False, f"extracted value does not match '{assertion.extracted_regex}'"

    if isinstance(assertion, TitleContainsAssertion):
        title = result.extracted_value or ""
        if assertion.title_contains in title:
            return True, f"title contains '{assertion.title_contains}'"
        return False, f"title '{title[:200]}' does not contain '{assertion.title_contains}'"

    return False, f"unknown assertion type: {type(assertion).__name__}"
