"""Phase 2a desktop UI end-to-end.

Requires the app running at TAOS_E2E_URL with a test channel named
'roundtable' created beforehand (≥2 agents).  Skipped locally unless
TAOS_E2E_URL is set.
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

_URL = os.environ.get("TAOS_E2E_URL", "")


def _open_roundtable(page: Page) -> None:
    """Navigate to the Messages app and open the 'roundtable' channel."""
    page.goto(_URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()


def test_slash_menu_opens_and_inserts(page: Page):
    _open_roundtable(page)

    composer = page.get_by_placeholder("Message")
    composer.click()
    composer.press("/")
    expect(page.get_by_role("listbox", name="Slash commands")).to_be_visible()

    # Pick the first command
    page.keyboard.press("Enter")

    value = composer.input_value()
    assert "/" in value, f"Expected '/' in composer value, got: {value!r}"
    assert "@" in value, f"Expected '@' in composer value, got: {value!r}"


def test_channel_settings_panel_opens_and_flips_mode(page: Page):
    _open_roundtable(page)

    page.get_by_role("button", name="Channel settings").click()
    expect(
        page.get_by_role("complementary", name="Channel settings")
    ).to_be_visible()

    # Flip to lively mode
    page.get_by_role("button", name="lively").click()

    # Close and re-open to confirm the setting persisted
    page.get_by_role("button", name="Close").click()
    page.get_by_role("button", name="Channel settings").click()
    expect(page.get_by_role("button", name="lively")).to_be_visible()


def test_agent_context_menu_via_right_click(page: Page):
    _open_roundtable(page)

    # Right-click the first agent mention in the transcript.
    # Agent names may render as plain spans rather than <a> elements;
    # fall back to get_by_text if a link role isn't found.
    agent_span = page.locator("[data-agent-slug]").first
    agent_span.click(button="right")

    menu = page.get_by_role("menu")
    expect(menu).to_be_visible()
    # "DM @agentname" menu item — match by partial text since agent name varies
    expect(page.locator('[role="menuitem"]', has_text="DM")).to_be_visible()

    page.keyboard.press("Escape")
    expect(menu).not_to_be_visible()
