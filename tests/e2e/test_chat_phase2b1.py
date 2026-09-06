"""Phase 2b-1 desktop E2E.

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


def test_thread_panel_opens_from_hover_and_persists_reply(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    first_msg = page.locator("[data-message-id]").first
    first_msg.hover()
    page.get_by_role("button", name="Reply in thread").click()
    expect(page.get_by_role("complementary", name="Thread")).to_be_visible()
    composer = page.get_by_placeholder("Reply in thread…")
    composer.fill("hello thread")
    composer.press("Enter")
    expect(page.get_by_text("hello thread")).to_be_visible()


def test_paperclip_opens_file_picker(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    page.get_by_role("button", name="Attach files").click()
    expect(page.get_by_role("dialog", name="Pick a file")).to_be_visible()
    page.get_by_role("button", name="Cancel").click()
    expect(page.get_by_role("dialog", name="Pick a file")).not_to_be_visible()


def test_help_posts_system_message(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    composer = page.get_by_placeholder(re.compile("Message", re.I))
    composer.fill("/help threads")
    composer.press("Enter")
    expect(page.get_by_text(re.compile("narrow routing|threads", re.I))).to_be_visible()
