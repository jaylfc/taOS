"""E2E tests: migration banner appears for legacy agents and dismisses permanently.

Phase 12.5 — the MigrationBanner component is only shown when
`agent.migrated_to_v2_personas` is False (falsy / absent).

Seeding a legacy agent via the /api/agents POST endpoint creates an agent with
migrated_to_v2_personas=True (set by the deploy path).  There is currently no
test-only endpoint to force it to False.

Options evaluated:
1. POST /api/agents/deploy → always sets migrated_to_v2_personas=True ✗
2. POST /api/agents        → also sets True on new agents ✗
3. Direct config mutation  → not exposed via HTTP ✗
4. PATCH /api/agents/{slug}/persona → does not touch migrated_to_v2_personas ✗

Until a test-helper endpoint exists (e.g. POST /api/agents/{slug}/test-reset-migration),
the banner test is skipped.  The test structure is complete so it can be
un-skipped once the endpoint is available.
"""
import pytest
from playwright.sync_api import Page, expect

_AGENT_SLUG = "e2e-banner-test"

# TODO: Add a test-only endpoint such as
#   POST /api/agents/{slug}/test-reset-migration
# that sets migrated_to_v2_personas=False so this test can run without
# direct config file access.
_MISSING_RESET_ENDPOINT = True


@pytest.fixture(scope="class")
def legacy_agent(page: Page, base_url: str):  # type: ignore[misc]
    """Seed an agent and attempt to force it into legacy (unmigrated) state."""
    resp = page.request.post(
        f"{base_url}/api/agents",
        data={
            "name": _AGENT_SLUG,
            "host": "localhost:11434",
            "qmd_index": "default",
            "color": "#888888",
            "can_read_user_memory": False,
        },
    )
    assert resp.ok, f"Failed to seed banner-test agent: {resp.status} {resp.text()}"

    # Attempt to reset migration flag via test helper (may not exist yet).
    reset_resp = page.request.post(
        f"{base_url}/api/agents/{_AGENT_SLUG}/test-reset-migration"  # TODO: implement this endpoint
    )
    if not reset_resp.ok:
        # Cleanup and skip — the banner cannot be triggered without a way to
        # set migrated_to_v2_personas=False.
        page.request.delete(f"{base_url}/api/agents/{_AGENT_SLUG}")
        pytest.skip(
            "No test-reset-migration endpoint available; cannot seed a legacy "
            "agent with migrated_to_v2_personas=False. Implement "
            "POST /api/agents/{slug}/test-reset-migration to un-skip this test."
        )

    yield

    page.request.delete(f"{base_url}/api/agents/{_AGENT_SLUG}")  # best-effort cleanup


def _navigate_to_agent_persona_tab(page: Page, base_url: str) -> None:
    """Open the agent detail view and click the Persona tab where the banner lives."""
    page.goto(f"{base_url}/agents")
    page.wait_for_load_state("networkidle")

    agent_card = page.get_by_text(_AGENT_SLUG).first  # TODO: refine selector
    agent_card.click()

    persona_tab = page.get_by_role("tab", name="Persona")  # TODO: refine if role differs
    persona_tab.wait_for(state="visible", timeout=5000)
    persona_tab.click()
    page.wait_for_load_state("networkidle")


class TestMigrationBanner:
    """Migration banner appears once and stays dismissed after reload."""

    def test_banner_visible_for_legacy_agent(self, page: Page, base_url: str, legacy_agent: None):
        """MigrationBanner is shown when migrated_to_v2_personas is False."""
        _navigate_to_agent_persona_tab(page, base_url)

        # The banner contains the upgrade message
        banner = page.get_by_text("Memory upgraded")  # partial match
        expect(banner).to_be_visible(timeout=5000)

        # Dismiss button is also visible
        dismiss_btn = page.get_by_role("button", name="Dismiss")
        expect(dismiss_btn).to_be_visible()

    def test_banner_dismisses_on_click(self, page: Page, base_url: str, legacy_agent: None):
        """Clicking Dismiss hides the banner immediately."""
        _navigate_to_agent_persona_tab(page, base_url)

        dismiss_btn = page.get_by_role("button", name="Dismiss")
        dismiss_btn.wait_for(state="visible", timeout=5000)
        dismiss_btn.click()

        # Banner should no longer be visible
        banner = page.get_by_text("Memory upgraded")
        expect(banner).not_to_be_visible(timeout=5000)

    def test_banner_stays_dismissed_after_reload(self, page: Page, base_url: str, legacy_agent: None):
        """After dismissing and reloading, the banner does not reappear."""
        _navigate_to_agent_persona_tab(page, base_url)

        # Dismiss the banner
        dismiss_btn = page.get_by_role("button", name="Dismiss")
        dismiss_btn.wait_for(state="visible", timeout=5000)
        dismiss_btn.click()

        # Reload the page
        page.reload()
        page.wait_for_load_state("networkidle")
        _navigate_to_agent_persona_tab(page, base_url)

        # Banner must not reappear
        banner = page.get_by_text("Memory upgraded")
        expect(banner).not_to_be_visible(timeout=3000)
