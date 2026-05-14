"""
BoundBrowserTool — crewAI tool for browser-based attacks via Playwright CDP.

Wraps a headless Chrome browser controlled via Chrome DevTools Protocol. Pre-bound to a
specific CDP endpoint at construction time so the LLM only needs to provide the task.

Used by the Attack Orchestrator to execute any attack step that requires JavaScript
execution, clicking, form submission, or cookie inspection.

Note: This is a Phase 1 implementation using Playwright directly. Phase 2 will integrate
with an LLM-powered browser agent (browser-use or similar) once dependency conflicts are resolved.
"""

import asyncio
import logging
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class BrowserTaskInput(BaseModel):
    """Input schema for browser_task tool."""
    task: str = Field(
        ...,
        description="Natural language instruction for the browser to execute. "
                    "Can include concrete steps like 'navigate to http://target:3000/login, "
                    "fill username field with admin, click submit button'."
    )


class BoundBrowserTool(BaseTool):
    """Execute a browser task via Playwright CDP connection.

    Use this for any action that requires clicking, filling forms, reading page content,
    or triggering JavaScript execution. Returns the browser operation result as a string.
    """

    name: str = "browser_task"
    description: str = (
        "Execute a browser task via headless Chrome. "
        "Use this for actions requiring clicking, filling forms, reading page content, "
        "or triggering JavaScript execution. Returns the operation result as a string."
    )
    args_schema: Type[BaseModel] = BrowserTaskInput

    cdp_url: str  # ws://localhost:{host_port}
    target_base_url: str  # http://target:{port} — base URL for the target app

    def _run(self, task: str) -> str:
        """Execute the browser task synchronously (handles async internally)."""
        try:
            from playwright.sync_api import sync_playwright
            import urllib.request
            import json
        except ImportError as e:
            return f"ERROR: Failed to import playwright: {e}. " \
                   "Run `crewai install` and `playwright install chromium` to install dependencies."

        try:
            # Get the full WebSocket debugger URL from the CDP HTTP endpoint
            # cdp_url is ws://localhost:port, we need to query http://localhost:port/json/version
            http_url = self.cdp_url.replace('ws://', 'http://').replace('wss://', 'https://')
            with urllib.request.urlopen(f"{http_url}/json/version", timeout=5) as response:
                version_data = json.loads(response.read().decode())
                ws_debugger_url = version_data.get('webSocketDebuggerUrl')
                if not ws_debugger_url:
                    return f"ERROR: No webSocketDebuggerUrl in CDP response: {version_data}"

            # For Phase 1, we'll do a simple navigation and page title retrieval
            # to prove the CDP connection works. Phase 2 will add LLM-powered automation.
            with sync_playwright() as p:
                # Connect to the existing browser via CDP
                browser = p.chromium.connect_over_cdp(ws_debugger_url)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page() if not context.pages else context.pages[0]

                # Parse the task for simple operations (Phase 1 implementation)
                result = self._execute_simple_task(page, task)

                # Don't close browser/context — they're shared
                return result

        except Exception as e:
            logger.error(f"Browser task failed: {e}", exc_info=True)
            return f"ERROR: Browser task failed: {e}"

    def _execute_simple_task(self, page, task: str) -> str:
        """Execute a browser task by parsing common patterns.

        Handles:
        - One or more "navigate to <url>" instructions (executed in order)
        - "fill <field> with <value>" → page.fill()
        - "click <selector or text>" → page.click()
        - "submit" → press Enter or click submit button
        - "get content" / "get html" → page.content()
        """
        import re
        task_lower = task.lower()
        results = []

        try:
            # Extract all "navigate to <url>" occurrences in order
            nav_urls = re.findall(r'navigate to\s+(https?://[^\s,]+)', task, re.IGNORECASE)

            if nav_urls:
                for url in nav_urls:
                    try:
                        page.goto(url, wait_until='networkidle', timeout=30000)
                        # Wait for JS/XSS to execute after page load
                        page.wait_for_timeout(5000)
                        title = page.title()
                        results.append(f"Navigated to {url}. Title: {title}")
                    except Exception as nav_err:
                        results.append(f"Navigation to {url} failed: {nav_err}")
                return "\n".join(results)

            # Fill pattern: "fill <field> with <value>"
            fill_match = re.search(r'fill\s+(.+?)\s+with\s+(.+?)(?:\s*,|\s*$)', task, re.IGNORECASE)
            if fill_match:
                selector, value = fill_match.group(1).strip(), fill_match.group(2).strip()
                page.fill(selector, value)
                results.append(f"Filled '{selector}' with '{value}'")

            # Click pattern: "click <selector>"
            click_match = re.search(r'click\s+(.+?)(?:\s*,|\s*$)', task, re.IGNORECASE)
            if click_match:
                selector = click_match.group(1).strip()
                page.click(selector)
                results.append(f"Clicked '{selector}'")

            # Submit pattern
            if "submit" in task_lower:
                page.keyboard.press("Enter")
                page.wait_for_timeout(1000)
                results.append("Submitted form")

            # Content pattern
            if "content" in task_lower or "html" in task_lower:
                content = page.content()
                results.append(f"Page HTML ({len(content)} bytes):\n{content[:500]}")

            if results:
                return "\n".join(results)

            # Default: navigate to base URL
            page.goto(self.target_base_url, wait_until='networkidle', timeout=30000)
            return f"Navigated to {self.target_base_url}. Title: {page.title()}"

        except Exception as e:
            error_msg = str(e)
            try:
                return f"Error: {error_msg}\nCurrent URL: {page.url}\nTitle: {page.title()}"
            except Exception:
                return f"Error: {error_msg}"
