"""Messages PWA mobile-viewport E2E.

Requires TAOS_E2E_URL set; skipped locally.
"""
import os
import re

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


MOBILE_VIEWPORT = {"width": 375, "height": 667}


@pytest.fixture
def mobile_page(page: Page):
    page.set_viewport_size(MOBILE_VIEWPORT)
    return page


def test_mobile_thread_takeover(mobile_page: Page):
    mobile_page.goto(f"{URL}/chat-pwa")
    mobile_page.get_by_text("roundtable").first.click()
    first = mobile_page.locator("[data-message-id]").first
    first.tap()
    mobile_page.get_by_role("button", name=re.compile("Reply in thread", re.I)).click()
    panel = mobile_page.get_by_role("complementary", name=re.compile("Thread", re.I))
    expect(panel).to_be_visible()
    box = panel.bounding_box()
    assert box is not None
    assert box["width"] >= 300
    mobile_page.get_by_role("button", name=re.compile("Back", re.I)).click()
    expect(panel).not_to_be_visible()


def test_mobile_overflow_bottom_sheet(mobile_page: Page):
    mobile_page.goto(f"{URL}/chat-pwa")
    mobile_page.get_by_text("roundtable").first.click()
    first = mobile_page.locator("[data-message-id]").first
    first.tap()
    mobile_page.get_by_role("button", name="More").click()
    sheet_backdrop = mobile_page.get_by_test_id("bottom-sheet-backdrop")
    expect(sheet_backdrop).to_be_visible()
    expect(mobile_page.get_by_role("menuitem", name=re.compile("Copy link", re.I))).to_be_visible()


def test_install_banner_hidden_when_no_event(mobile_page: Page):
    mobile_page.goto(f"{URL}/chat-pwa")
    expect(mobile_page.get_by_role("region", name=re.compile("Install", re.I))).not_to_be_visible()
