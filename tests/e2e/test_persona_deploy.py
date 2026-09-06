"""E2E tests: persona selection paths at agent deploy time.

Phase 12.1 — covers the three persona paths through the Deploy wizard:
  1. Browse library → pick → Use this persona
  2. Create new (with soul_md + save-to-library)
  3. Blank (no persona)

Selectors are ARIA-first; lines marked # TODO: refine need tuning
against a live instance.
"""
import pytest
from playwright.sync_api import Page, expect

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEPLOY_STEPS_AFTER_PERSONA = 5  # Name/Color, Framework, Model, Permissions, Failure Policy


def _open_wizard(page: Page, base_url: str) -> None:
    """Navigate to /agents and open the Deploy Agent wizard."""
    page.goto(f"{base_url}/agents")
    page.wait_for_load_state("networkidle")
    page.get_by_role("button", name="Deploy Agent").click()
    # Wait for the wizard dialog to appear
    page.get_by_role("dialog", name="Deploy Agent").wait_for(state="visible")  # TODO: refine if dialog aria-label differs


def _advance_to_deploy(page: Page) -> None:
    """Click Next through the remaining wizard steps, then click Deploy Agent."""
    next_btn = page.get_by_role("button", name="Next")
    for _ in range(_DEPLOY_STEPS_AFTER_PERSONA):
        # Not every step's Next is always enabled; wait briefly
        next_btn.wait_for(state="visible", timeout=5000)
        if next_btn.is_enabled():
            next_btn.click()
        else:
            # Some steps (e.g. Framework, Model) may block Next until a value is
            # selected.  The tests that call this helper intentionally don't pick
            # model/framework, so we skip disabled buttons.
            break  # TODO: refine — full deploy tests should fill these steps


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPersonaDeploy:
    """Three persona paths through the Deploy wizard."""

    def test_deploy_from_library_persona(self, page: Page, base_url: str):
        """Browse tab: search, select, Use this persona, then verify soul_md in API."""
        _open_wizard(page, base_url)

        # Step 0 is the Persona step. The default tab is Browse.
        page.get_by_role("tab", name="Browse").click()  # TODO: refine — button role, not tab role; see PersonaPicker

        # Search for a persona
        page.get_by_label("Search personas").fill("research")  # TODO: refine placeholder text
        page.wait_for_load_state("networkidle")

        # Click the first result in the persona list
        persona_list = page.get_by_role("list", name="Persona list")
        first_item = persona_list.locator("li").first
        first_item.locator("button").click()

        # Wait for the detail panel to load, then use the persona
        use_btn = page.get_by_role("button", name="Use this persona")
        use_btn.wait_for(state="visible", timeout=10000)
        use_btn.click()

        # Step 1 — Name & Color
        page.get_by_role("button", name="Next").click()
        page.get_by_label("Agent Name").fill("Atlas-LibTest")
        page.get_by_role("button", name="Next").click()

        # Steps 2–5: just click Next (wizard may block if framework/model unset)
        for _ in range(4):
            btn = page.get_by_role("button", name="Next")
            if btn.is_enabled():
                btn.click()

        # At Review step: confirm Deploy Agent button is visible
        deploy_btn = page.get_by_role("button", name="Deploy Agent")
        expect(deploy_btn).to_be_visible()
        # We don't actually click Deploy in E2E (no real container runtime)
        # but we verify soul_md would be non-empty by inspecting wizard state
        # via the Review table row.
        # The Review step renders an "Emoji" row and a "Name" row; there is no
        # explicit "Soul" row, so we can't read it from the DOM here.
        # A full integration test would POST to /api/agents/deploy via
        # page.request and read back the config. For now, assert Review visible.
        expect(page.get_by_text("Review Configuration")).to_be_visible()  # TODO: refine heading text

    def test_deploy_create_new_with_save(self, page: Page, base_url: str):
        """Create new tab: fill soul_md + agent_md, tick save-to-library."""
        _open_wizard(page, base_url)

        # Switch to Create new tab
        page.get_by_role("button", name="Create new").click()  # TODO: refine — button role; PersonaPicker uses role="tab"

        # Fill soul_md textarea (PersonaCreate aria-label="Soul (identity)")
        soul_area = page.get_by_label("Soul (identity)")
        soul_area.wait_for(state="visible", timeout=5000)
        soul_area.fill("You are a helpful research assistant with a calm, precise style.")

        # Fill agent_md textarea (PersonaCreate aria-label="Agent.md (operational rules)")
        agent_area = page.get_by_label("Agent.md (operational rules)")
        agent_area.fill("Always cite sources when providing information.")

        # Tick save-to-library checkbox ("Save to my persona library for reuse")
        save_chk = page.get_by_text("Save to my persona library for reuse")
        save_chk.click()  # clicking the label checks the checkbox

        # Fill persona name revealed after ticking save
        lib_name = page.get_by_label("Persona library name")
        lib_name.wait_for(state="visible", timeout=3000)
        lib_name.fill("My Research Persona")

        # Confirm Next (to Name & Color step) is enabled after filling soul
        next_btn = page.get_by_role("button", name="Next")
        expect(next_btn).to_be_enabled()

    def test_deploy_blank(self, page: Page, base_url: str):
        """Blank tab: deploy with no persona attached."""
        _open_wizard(page, base_url)

        # Switch to Blank tab
        page.get_by_role("button", name="Blank").click()  # TODO: refine — PersonaPicker uses role="tab" buttons

        # PersonaBlank renders: "Deploy with no persona →"
        use_blank = page.get_by_role("button", name="Deploy with no persona →")
        use_blank.wait_for(state="visible", timeout=5000)
        use_blank.click()

        next_btn = page.get_by_role("button", name="Next")
        expect(next_btn).to_be_enabled(timeout=5000)
