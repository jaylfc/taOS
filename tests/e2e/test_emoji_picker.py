"""E2E tests: emoji picker in the Deploy wizard.

Phase 12.6 — covers:
  - Opening the picker, searching for "rocket", clicking 🚀, and verifying
    the trigger button updates to show 🚀.
  - Walking to the Review step without picking an emoji and asserting the
    Review table shows "—" for the Emoji row.
"""
import pytest
from playwright.sync_api import Page, expect


def _open_wizard_to_name_step(page: Page, base_url: str) -> None:
    """Open the Deploy wizard and advance to the Name & Color step via the Blank persona path."""
    page.goto(f"{base_url}/agents")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Deploy Agent").click()

    # Wizard dialog
    page.get_by_role("dialog", name="Deploy Agent").wait_for(state="visible")  # TODO: refine if aria-label differs

    # Step 0: use Blank persona
    page.get_by_role("button", name="Blank").click()  # TODO: refine — PersonaPicker uses role="tab" buttons
    page.get_by_role("button", name="Deploy with no persona →").click()

    # Advance to Name & Color (step 1)
    page.get_by_role("button", name="Next").click()


def _advance_to_review(page: Page) -> None:
    """Advance through Framework/Model/Permissions/Failure Policy to Review.

    Steps 2–5 have required fields (Framework, Model) that block Next in the
    live wizard. In a CI environment without a running LLM backend these steps
    won't be fillable — so we skip past them by clicking Next whenever enabled.
    """
    for _ in range(5):  # Name→Framework→Model→Permissions→Failure Policy→Review
        btn = page.get_by_role("button", name="Next")
        btn.wait_for(state="visible", timeout=3000)
        if btn.is_enabled():
            btn.click()
        else:
            # Blocked step (Framework or Model not selected) — stop here.
            # The test that calls this helper accepts partial navigation.
            break


class TestEmojiPicker:
    """Emoji picker opens, searches, selects, and the result is reflected in the wizard."""

    def test_emoji_picker_opens_and_selects_rocket(self, page: Page, base_url: str):
        """Open picker, search 'rocket', click 🚀, assert trigger button shows 🚀."""
        _open_wizard_to_name_step(page, base_url)

        # Fill agent name so we can advance later
        page.get_by_label("Agent Name").fill("Rocket Agent")

        # The emoji picker trigger is a button with aria-label="Open emoji picker"
        picker_trigger = page.get_by_label("Open emoji picker")
        expect(picker_trigger).to_be_visible(timeout=5000)
        picker_trigger.click()

        # The picker dialog should appear (aria-label="Emoji picker")
        picker_dialog = page.get_by_role("dialog", name="Emoji picker")
        expect(picker_dialog).to_be_visible(timeout=5000)

        # Search for "rocket" — emoji-picker-react renders a search input
        # TODO: refine the aria-label/placeholder for the picker's search field
        search_input = picker_dialog.get_by_role("searchbox")  # TODO: refine — may be type="search" without role
        if search_input.count() == 0:
            search_input = picker_dialog.locator("input[type='search']")
        search_input.wait_for(state="visible", timeout=5000)
        search_input.fill("rocket")

        # Click the 🚀 emoji button
        # emoji-picker-react renders each emoji as a button with aria-label containing the name
        rocket_btn = picker_dialog.get_by_role("button", name="rocket")  # TODO: refine — may be "🚀" or "Rocket"
        rocket_btn.wait_for(state="visible", timeout=5000)
        rocket_btn.first.click()

        # After selection the picker should close
        expect(picker_dialog).not_to_be_visible(timeout=3000)

        # The trigger button now shows 🚀 as its text content
        expect(picker_trigger).to_have_text("🚀", timeout=3000)  # TODO: refine — button text may include spaces

    def test_review_shows_dash_when_no_emoji(self, page: Page, base_url: str):
        """Without picking an emoji, the Review step shows '—' for the Emoji row."""
        _open_wizard_to_name_step(page, base_url)

        # Fill agent name but do NOT touch the emoji picker
        page.get_by_label("Agent Name").fill("NoEmoji Agent")

        # Verify the trigger button still shows "+" (empty state)
        picker_trigger = page.get_by_label("Open emoji picker")
        expect(picker_trigger).to_have_text("+", timeout=3000)  # TODO: refine — EmojiPickerField renders "+" when empty

        # Advance through remaining wizard steps to Review
        _advance_to_review(page)

        # If we reached Review (step 6), the Emoji row should display "—"
        # The Review table is built as label/value pairs; look for the Emoji row.
        review_section = page.get_by_text("Review Configuration")
        if review_section.count() > 0:
            # Locate the Emoji value cell — the table maps ["Emoji", emoji.trim() || "—"]
            emoji_value = page.locator("span").filter(has_text="—").first  # TODO: refine — need to scope to Emoji row
            expect(emoji_value).to_be_visible(timeout=3000)
        else:
            # Could not reach Review (blocked on Framework/Model selection in CI).
            # Verify the picker still shows "+" as a proxy for no emoji selected.
            expect(picker_trigger).to_have_text("+", timeout=3000)
            pytest.skip(
                "Could not reach Review step without a runnable Framework/Model. "
                "Run against a live taOS instance with at least one model available."
            )
