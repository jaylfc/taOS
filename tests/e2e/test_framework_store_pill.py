"""E2E: Store pill + sidebar dot surface framework updates.

Selectors marked TODO need tuning against live DOM on first run.
"""
import pytest
from playwright.sync_api import Page, expect


class TestFrameworkIndicators:
    def test_store_pill_counts_affected_agents(self, page: Page, base_url: str):
        # TODO seed an out-of-date agent before this runs
        page.goto(f"{base_url}/store")
        expect(page.get_by_text("Update available")).to_be_visible()

    def test_sidebar_dot_on_out_of_date_agent(self, page: Page, base_url: str):
        # TODO seed an out-of-date agent before this runs
        page.goto(f"{base_url}/agents")
        expect(page.locator('[aria-label="framework update available"]').first).to_be_visible()
