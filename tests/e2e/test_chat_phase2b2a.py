"""Phase 2b-2a desktop E2E.

Requires TAOS_E2E_URL and a test channel named 'roundtable' with at least
one message authored by the test user.
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


def test_edit_own_message(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    first = page.locator("[data-message-id]").first
    first.hover()
    page.get_by_role("button", name="More").click()
    page.get_by_role("menuitem", name=re.compile("Edit", re.I)).click()
    editor = page.get_by_role("textbox", name=re.compile("Edit message", re.I))
    editor.fill("edited content")
    editor.press("Enter")
    expect(page.get_by_text("edited content")).to_be_visible()
    expect(page.get_by_text("(edited)")).to_be_visible()


def test_delete_own_message_shows_tombstone(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    first = page.locator("[data-message-id]").first
    first.hover()
    page.get_by_role("button", name="More").click()
    page.on("dialog", lambda d: d.accept())
    page.get_by_role("menuitem", name=re.compile("Delete", re.I)).click()
    expect(page.get_by_text("This message was deleted")).to_be_visible()


def test_pin_badge_and_popover(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    first = page.locator("[data-message-id]").first
    first.hover()
    page.get_by_role("button", name="More").click()
    page.get_by_role("menuitem", name=re.compile("Pin", re.I)).click()
    expect(page.get_by_role("button", name=re.compile("Pinned messages", re.I))).to_be_visible()
    page.get_by_role("button", name=re.compile("Pinned messages", re.I)).click()
    expect(page.get_by_role("dialog", name=re.compile("Pinned messages", re.I))).to_be_visible()


def test_deep_link_scroll(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    msg = page.locator("[data-message-id]").first
    msg_id = msg.get_attribute("data-message-id")
    page.goto(f"{URL}?msg={msg_id}")
    expect(page.locator(f"[data-message-id='{msg_id}']")).to_be_visible()
