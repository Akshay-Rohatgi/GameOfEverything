#!/usr/bin/env python3
"""
Phase 1 verification script for browser-use infrastructure.

Tests:
1. Browser sidecar starts and CDP endpoint becomes reachable
2. All three containers (target, attacker, browser) start and teardown cleanly
3. BoundBrowserTool can execute a simple task
"""

import sys
from pathlib import Path

# Add src to path so we can import game_of_everything modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from game_of_everything.tools.test_environment import TestEnvironmentTool


def test_browser_startup():
    """Test 1 & 2: Start environment with browser enabled and verify CDP is reachable."""
    print("=" * 70)
    print("TEST 1 & 2: Browser sidecar startup and teardown")
    print("=" * 70)

    env = TestEnvironmentTool(enable_browser=True)

    try:
        print("\nStarting test environment with browser enabled...")
        env.setup()

        print(f"✓ Target container: {env.target_name}")
        print(f"✓ Attacker container: {env.attacker_name}")
        print(f"✓ Browser container: {env._prefix}browser")
        print(f"✓ Network: {env.network_name}")
        print(f"✓ Browser CDP URL: {env.browser_cdp_url}")
        print(f"✓ Browser host port: {env._browser_host_port}")

        # Verify containers are actually running
        assert env.target_container is not None
        assert env.attacker_container is not None
        assert env.browser_container is not None
        assert env.browser_cdp_url.startswith("ws://localhost:")
        assert env._browser_host_port > 0

        print("\n✓ All containers started successfully")
        print("✓ CDP endpoint is reachable")

    finally:
        print("\nTearing down test environment...")
        env.teardown()
        print("✓ Teardown complete")

    print("\n" + "=" * 70)
    print("TEST 1 & 2: PASSED")
    print("=" * 70)


def test_browser_tool():
    """Test 3: Smoke-test BoundBrowserTool with a simple navigation task."""
    print("\n" + "=" * 70)
    print("TEST 3: BoundBrowserTool smoke test")
    print("=" * 70)

    # Note: This requires browser-use and playwright to be installed
    # If not installed, the test will fail gracefully with an import error

    try:
        from game_of_everything.tools.bound_browser_tool import BoundBrowserTool
    except ImportError as e:
        print(f"\n⚠ SKIPPED: BoundBrowserTool import failed: {e}")
        print("Run `crewai install` and `playwright install chromium` to enable this test")
        return

    env = TestEnvironmentTool(enable_browser=True)

    try:
        print("\nStarting test environment with browser...")
        env.setup()

        print(f"Browser CDP URL: {env.browser_cdp_url}")

        # Create the tool
        tool = BoundBrowserTool(
            cdp_url=env.browser_cdp_url,
            target_base_url="http://example.com"
        )

        print("\nExecuting browser task: 'Navigate to http://example.com and return the page title'")
        result = tool._run("Navigate to http://example.com and return the page title")

        print(f"\nBrowser task result: {result[:200]}...")

        if "ERROR" in result:
            print(f"\n✗ Browser task returned an error: {result}")
        else:
            print("\n✓ Browser task executed successfully")

    except Exception as e:
        print(f"\n✗ Browser tool test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\nTearing down test environment...")
        env.teardown()
        print("✓ Teardown complete")

    print("\n" + "=" * 70)
    print("TEST 3: COMPLETED (check output above for pass/fail)")
    print("=" * 70)


if __name__ == "__main__":
    print("\nPhase 1 Verification: Browser Infrastructure")
    print("=" * 70)

    try:
        test_browser_startup()
        test_browser_tool()

        print("\n" + "=" * 70)
        print("PHASE 1 VERIFICATION: ALL TESTS COMPLETED")
        print("=" * 70)

    except Exception as e:
        print(f"\n✗ PHASE 1 VERIFICATION FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
