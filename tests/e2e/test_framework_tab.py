"""E2E: Framework tab renders state + performs an update.

Selectors marked TODO need tuning against live DOM on first run.
Tests follow the pattern established by tests/e2e/test_pages.py.
"""
import pytest
from playwright.sync_api import Page, expect


class TestFrameworkTab:
    def test_out_of_date_agent_shows_update_pill(self, page: Page, base_url: str):
        # TODO seed an out-of-date agent via API + direct config patch before this runs
        page.goto(f"{base_url}/agents?slug=atlas")
        page.get_by_role("tab", name="Framework").click()
        expect(page.get_by_text("Update available")).to_be_visible()
        expect(page.get_by_role("button", name="Update Framework")).to_be_enabled()

    def test_update_confirmation_and_progress(self, page: Page, base_url: str):
        page.goto(f"{base_url}/agents?slug=atlas")
        page.get_by_role("tab", name="Framework").click()
        page.get_by_role("button", name="Update Framework").click()
        # Confirmation dialog opens
        expect(page.get_by_text("to")).to_be_visible()
        page.get_by_role("button", name="Update").click()
        # Updating banner appears
        expect(page.get_by_text("started")).to_be_visible()

    def test_up_to_date_agent_shows_tick(self, page: Page, base_url: str):
        page.goto(f"{base_url}/agents?slug=uptodate")
        page.get_by_role("tab", name="Framework").click()
        expect(page.get_by_text("You're on the latest version")).to_be_visible()
