"""Browser session lifecycle management via Playwright CDP."""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page, Playwright
    from goe.models.procedure import Session, SessionAuth


class SessionManager:
    """Manages persistent browser contexts connected via CDP."""

    def __init__(self, cdp_url: str):
        self._cdp_url = cdp_url
        self._playwright: "Playwright | None" = None
        self._browser = None
        self._contexts: dict[str, "BrowserContext"] = {}
        self._pages: dict[str, "Page"] = {}
        self._base_urls: dict[str, str] = {}

    def _ensure_browser(self) -> None:
        if self._browser is None:
            import json, urllib.request
            from playwright.sync_api import sync_playwright

            # cdp_url is ws://localhost:PORT — we need the actual webSocketDebuggerUrl
            http_url = self._cdp_url.replace("ws://", "http://").replace("wss://", "https://")
            with urllib.request.urlopen(f"{http_url}/json/version", timeout=5) as resp:
                data = json.loads(resp.read().decode())
            ws_url = data.get("webSocketDebuggerUrl")
            if not ws_url:
                raise RuntimeError(f"No webSocketDebuggerUrl in CDP response: {data}")

            # Playwright sync API cannot be used inside a running asyncio loop
            # (pytest-anyio installs one). Run it in a fresh thread with its own loop.
            import asyncio, threading
            try:
                loop = asyncio.get_running_loop()
                loop_running = True
            except RuntimeError:
                loop_running = False

            if loop_running:
                result: list = []
                error: list = []

                def _init():
                    try:
                        pw = sync_playwright().__enter__()
                        br = pw.chromium.connect_over_cdp(ws_url)
                        result.append((pw, br))
                    except Exception as e:
                        error.append(e)

                t = threading.Thread(target=_init, daemon=True)
                t.start()
                t.join(timeout=15)
                if error:
                    raise error[0]
                if not result:
                    raise RuntimeError("Playwright browser init timed out")
                self._playwright, self._browser = result[0]
            else:
                self._playwright = sync_playwright().__enter__()
                self._browser = self._playwright.chromium.connect_over_cdp(ws_url)

    def init_session(self, session: "Session") -> None:
        """Create a BrowserContext for the session (called before first use)."""
        self._ensure_browser()
        self._base_urls[session.id] = session.base_url
        context = self._browser.new_context()
        page = context.new_page()
        self._contexts[session.id] = context
        self._pages[session.id] = page

    def login(self, session_id: str, auth: "SessionAuth", ctx: dict) -> None:
        """Pre-authenticate a session."""
        from goe.executor.interpolation import interpolate

        page = self._pages[session_id]
        base_url = self._base_urls[session_id]

        login_url = interpolate(auth.login_url, ctx)
        if not login_url.startswith("http"):
            login_url = base_url.rstrip("/") + login_url

        page.goto(login_url)
        page.fill(auth.username_field, interpolate(auth.username, ctx))
        page.fill(auth.password_field, interpolate(auth.password, ctx))

        # Submit: try clicking submit button, fallback to Enter
        try:
            page.click("button[type=submit]")
        except Exception:
            page.keyboard.press("Enter")

        # Wait for success indicator (URL fragment or CSS selector)
        indicator = interpolate(auth.success_indicator, ctx)
        if indicator.startswith("/") or indicator.startswith("http"):
            page.wait_for_url(f"**{indicator}**", timeout=10000)
        else:
            page.wait_for_selector(indicator, timeout=10000)

    def get_page(self, session_id: str) -> "Page":
        return self._pages[session_id]

    def get_base_url(self, session_id: str) -> str:
        return self._base_urls.get(session_id, "")

    def close_all(self) -> None:
        for ctx in self._contexts.values():
            try:
                ctx.close()
            except Exception:
                pass
        self._contexts.clear()
        self._pages.clear()
        if self._playwright is not None:
            try:
                self._playwright.__exit__(None, None, None)
            except Exception:
                pass
            self._playwright = None
        self._browser = None
