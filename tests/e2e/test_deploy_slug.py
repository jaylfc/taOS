"""E2E tests: slug live-preview, edit flow, and validation in the Deploy wizard.

Phase 12.2 — covers:
  - Typing a display name causes the slug caption to update (live preview)
  - Clicking "edit" makes an editable input appear
  - Typing an invalid slug shows a validation error
  - Correcting the slug clears the error
"""
import pytest
from playwright.sync_api import Page, expect


def _open_wizard_to_name_step(page: Page, base_url: str) -> None:
    """Open the Deploy wizard and advance past the Persona step (Blank path)."""
    page.goto(f"{base_url}/agents")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Deploy Agent").click()
    # Wait for wizard dialog
    page.get_by_role("dialog", name="Deploy Agent").wait_for(state="visible")  # TODO: refine if aria-label differs

    # Step 0: pick Blank persona so we can advance
    page.get_by_role("button", name="Blank").click()  # TODO: refine role — PersonaPicker uses role="tab"
    page.get_by_role("button", name="Deploy with no persona →").click()

    # Advance to Name & Color step
    page.get_by_role("button", name="Next").click()


class TestDeploySlug:
    """Slug live-preview and validation in the Deploy wizard."""

    def test_slug_caption_updates_on_name_input(self, page: Page, base_url: str):
        """Typing a display name causes the Slug caption to reflect the slugified value."""
        _open_wizard_to_name_step(page, base_url)

        name_input = page.get_by_label("Agent Name")
        expect(name_input).to_be_visible()

        name_input.fill("My Research Bot")

        # The slug caption is a <code> element that shows the live-derived slug.
        # slugifyClient("My Research Bot") → "my-research-bot"
        slug_caption = page.locator("code").filter(has_text="my-research-bot")
        expect(slug_caption).to_be_visible(timeout=5000)

    def test_edit_slug_shows_input(self, page: Page, base_url: str):
        """Clicking 'edit' next to the slug caption reveals an editable input."""
        _open_wizard_to_name_step(page, base_url)

        name_input = page.get_by_label("Agent Name")
        name_input.fill("Atlas")

        # Click the "edit" link/button next to the slug caption
        page.get_by_role("button", name="edit").click()  # TODO: refine — rendered as <button class="text-blue-400...">edit</button>

        # After clicking edit, a secondary input with aria-label="Edit slug" appears
        slug_input = page.get_by_label("Edit slug")
        expect(slug_input).to_be_visible(timeout=3000)

    def test_invalid_slug_shows_error(self, page: Page, base_url: str):
        """Typing an invalid slug shows a validation error message."""
        _open_wizard_to_name_step(page, base_url)

        page.get_by_label("Agent Name").fill("Atlas")
        page.get_by_role("button", name="edit").click()

        slug_input = page.get_by_label("Edit slug")
        slug_input.wait_for(state="visible", timeout=3000)

        # Enter an invalid slug (starts with hyphen, contains uppercase)
        slug_input.fill("-Bad-Slug!")

        # Expect the error message to appear
        error_msg = page.get_by_text("Slug must match")
        expect(error_msg).to_be_visible(timeout=3000)

        # Next button should be disabled while slug is invalid
        next_btn = page.get_by_role("button", name="Next")
        expect(next_btn).to_be_disabled(timeout=3000)

    def test_valid_slug_clears_error(self, page: Page, base_url: str):
        """After correcting an invalid slug the error disappears and Next re-enables."""
        _open_wizard_to_name_step(page, base_url)

        page.get_by_label("Agent Name").fill("Atlas")
        page.get_by_role("button", name="edit").click()

        slug_input = page.get_by_label("Edit slug")
        slug_input.wait_for(state="visible", timeout=3000)

        # First type an invalid value to trigger the error
        slug_input.fill("--invalid")
        expect(page.get_by_text("Slug must match")).to_be_visible(timeout=3000)

        # Now fix it to a valid slug
        slug_input.fill("atlas-fixed")

        # Error should be gone
        error_msg = page.get_by_text("Slug must match")
        expect(error_msg).not_to_be_visible(timeout=3000)

        # Next should be re-enabled
        next_btn = page.get_by_role("button", name="Next")
        expect(next_btn).to_be_enabled(timeout=3000)
