"""Phase 2b-2b/c desktop E2E stubs.

Requires TAOS_E2E_URL and a test channel named 'roundtable'.
"""
import os

import pytest
from playwright.sync_api import Page, expect

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("TAOS_E2E_URL"),
        reason="TAOS_E2E_URL required",
    ),
]
URL = os.environ.get("TAOS_E2E_URL", "")


def test_help_panel_opens(page: Page):
    """? button opens in-app HelpPanel dialog."""
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    page.get_by_role("button", name="Open chat guide").click()
    expect(page.get_by_role("dialog", name="Chat guide")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog", name="Chat guide")).to_be_hidden()


def test_all_threads_panel_opens(page: Page):
    """💬 button opens AllThreadsList panel."""
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    page.get_by_role("button", name="All threads").click()
    expect(page.get_by_role("complementary", name="All threads")).to_be_visible()
    page.get_by_role("complementary", name="All threads").get_by_role("button", name="Close").click()
    expect(page.get_by_role("complementary", name="All threads")).to_be_hidden()
