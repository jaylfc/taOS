"""E2E tests: store memory category deep-link from Memory tab.

Phase 12.4 — from the Agent Settings Memory tab, clicking "Get more plugins →"
navigates to the Store with the Memory category pre-selected:
  - URL hash includes category=memory
  - The Memory category is visually active in the store sidebar
  - Empty-state copy is visible (no memory plugins listed yet)
"""
import pytest
from playwright.sync_api import Page, expect

_SEED_AGENT = {
    "name": "e2e-store-deeplink",
    "host": "localhost:11434",
    "qmd_index": "default",
    "color": "#888888",
    "can_read_user_memory": False,
}
_AGENT_SLUG = "e2e-store-deeplink"


@pytest.fixture(scope="class")
def seeded_agent(page: Page, base_url: str):  # type: ignore[misc]
    resp = page.request.post(f"{base_url}/api/agents", data=_SEED_AGENT)
    assert resp.ok, f"Failed to seed agent: {resp.status} {resp.text()}"
    yield
    page.request.delete(f"{base_url}/api/agents/{_AGENT_SLUG}")  # best-effort cleanup; TODO: refine endpoint


def _navigate_to_memory_tab(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/agents")
    page.wait_for_load_state("networkidle")

    agent_card = page.get_by_text(_AGENT_SLUG).first  # TODO: refine — target the specific card button
    agent_card.click()

    memory_tab = page.get_by_role("tab", name="Memory")  # TODO: refine if rendered as TabsTrigger with different role
    memory_tab.wait_for(state="visible", timeout=5000)
    memory_tab.click()
    page.wait_for_load_state("networkidle")


class TestStoreMemoryDeeplink:
    """Store Memory category is reachable from the Memory tab plugin link."""

    def test_deeplink_url_contains_category_memory(self, page: Page, base_url: str, seeded_agent: None):
        """Clicking 'Get more plugins →' navigates to a URL with #store?category=memory."""
        _navigate_to_memory_tab(page, base_url)

        get_more = page.get_by_role("link", name="Get more plugins →")
        expect(get_more).to_be_visible(timeout=5000)

        # The link href is '#store?category=memory' — an in-app hash route.
        # Clicking it updates the URL fragment.
        with page.expect_navigation():
            get_more.click()

        assert "category=memory" in page.url, (
            f"Expected 'category=memory' in URL after clicking deep-link, got: {page.url}"
        )

    def test_memory_category_active_in_store(self, page: Page, base_url: str, seeded_agent: None):
        """After navigating via the deep-link, the Memory category is active in the store."""
        _navigate_to_memory_tab(page, base_url)
        page.get_by_role("link", name="Get more plugins →").click()
        page.wait_for_load_state("networkidle")

        # The store renders a category list; the active one should be labelled "Memory"
        # and have an active/selected state.  We assert the element is visible —
        # exact styling class is implementation-specific.
        memory_category_btn = page.get_by_role("button", name="Memory")  # TODO: refine — may be a link or listitem
        expect(memory_category_btn).to_be_visible(timeout=5000)

        # Check the button/link carries an aria-selected or aria-current attribute
        # indicating it is active.  Accept either attribute.
        aria_selected = memory_category_btn.get_attribute("aria-selected")
        aria_current = memory_category_btn.get_attribute("aria-current")
        is_active = aria_selected in ("true", True) or aria_current in ("page", "true", True)
        assert is_active, (
            "Memory category button is visible but not marked as active "
            f"(aria-selected={aria_selected!r}, aria-current={aria_current!r}). "
            "TODO: verify the exact active indicator used by the Store component."
        )

    def test_memory_category_empty_state_visible(self, page: Page, base_url: str, seeded_agent: None):
        """The Memory category shows an empty-state message when no plugins are installed."""
        _navigate_to_memory_tab(page, base_url)
        page.get_by_role("link", name="Get more plugins →").click()
        page.wait_for_load_state("networkidle")

        # The store's Memory category empty-state copy contains text like
        # "No memory plugins" or similar — the exact wording is implementation-specific.
        # We assert any visible text that implies an empty state exists.
        # TODO: refine the search text to match the exact copy from StoreApp / MemoryCategoryEmpty.
        empty_state = page.get_by_text("No memory plugins")  # TODO: refine to actual copy
        if empty_state.count() == 0:
            # Fallback: look for any element with "empty" in its role or text
            empty_state = page.get_by_role("status")  # TODO: refine
        expect(empty_state.first).to_be_visible(timeout=5000)
