"""E2E tests: Agent Settings → Memory tab, Librarian controls.

Phase 12.3 — seeds an agent via API, navigates to its Memory tab, and
asserts that each UI control fires a PATCH /api/agents/{slug}/librarian
request when changed.

Each interaction is wrapped in page.expect_request() so the test fails if
the network call doesn't fire.
"""
import pytest
from playwright.sync_api import Page, expect, Request

# Minimal agent payload for seeding (no container needed — the E2E suite
# does not spin up real LXC/Docker).  The Memory tab reads from
# /api/agents/{slug}/librarian, which works on any registered agent.
_SEED_AGENT = {
    "name": "e2e-memory-test",
    "host": "localhost:11434",
    "qmd_index": "default",
    "color": "#888888",
    "can_read_user_memory": False,
}

_AGENT_SLUG = "e2e-memory-test"


@pytest.fixture(scope="class")
def seeded_agent(page: Page, base_url: str):  # type: ignore[misc]
    """Create the test agent via API before tests run."""
    resp = page.request.post(
        f"{base_url}/api/agents",
        data=_SEED_AGENT,
    )
    assert resp.ok, f"Failed to seed agent: {resp.status} {resp.text()}"
    yield
    # Best-effort cleanup: archive the agent so it doesn't pollute the instance.
    page.request.delete(f"{base_url}/api/agents/{_AGENT_SLUG}")  # TODO: refine — DELETE may not exist; check archive endpoint


def _navigate_to_memory_tab(page: Page, base_url: str) -> None:
    """Open the agent detail view and click the Memory tab."""
    page.goto(f"{base_url}/agents")
    page.wait_for_load_state("networkidle")

    # Click the agent card to open its detail panel
    agent_card = page.get_by_text(_AGENT_SLUG)  # TODO: refine — may need to target the card button specifically
    agent_card.first.click()

    # Click the Memory tab
    memory_tab = page.get_by_role("tab", name="Memory")  # TODO: refine if tab role differs (TabsTrigger)
    memory_tab.wait_for(state="visible", timeout=5000)
    memory_tab.click()

    # Ensure the Memory tab content is visible
    page.wait_for_load_state("networkidle")


def _librarian_url_pattern(slug: str) -> str:
    return f"**/api/agents/{slug}/librarian"


class TestMemoryTab:
    """Librarian controls in the Agent Settings Memory tab."""

    def test_toggle_librarian_enable(self, page: Page, base_url: str, seeded_agent: None):
        """Toggling 'Enable Librarian' fires a PATCH to /api/agents/{slug}/librarian."""
        _navigate_to_memory_tab(page, base_url)

        enable_chk = page.get_by_label("Enable Librarian")
        enable_chk.wait_for(state="visible", timeout=5000)

        with page.expect_request(
            lambda r: r.method == "PATCH" and f"/api/agents/{_AGENT_SLUG}/librarian" in r.url
        ) as req_info:
            enable_chk.click()

        req = req_info.value
        assert req.method == "PATCH"
        assert f"/api/agents/{_AGENT_SLUG}/librarian" in req.url

    def test_change_librarian_model(self, page: Page, base_url: str, seeded_agent: None):
        """Changing the Librarian model select fires a PATCH."""
        _navigate_to_memory_tab(page, base_url)

        model_select = page.get_by_label("Librarian model")
        model_select.wait_for(state="visible", timeout=5000)

        with page.expect_request(
            lambda r: r.method == "PATCH" and f"/api/agents/{_AGENT_SLUG}/librarian" in r.url
        ):
            model_select.select_option("ollama:qwen3:4b")

    def test_expand_advanced_and_toggle_task(self, page: Page, base_url: str, seeded_agent: None):
        """Clicking 'Show advanced…' reveals task toggles; toggling one fires a PATCH."""
        _navigate_to_memory_tab(page, base_url)

        # Expand advanced section
        advanced_btn = page.get_by_role("button", name="Show advanced…")  # TODO: refine — button text from MemoryTab
        advanced_btn.wait_for(state="visible", timeout=5000)
        advanced_btn.click()

        # Task checkboxes are rendered as aria-label="Task: <task_name>"
        # Wait for at least one task checkbox to appear
        first_task = page.get_by_role("checkbox").filter(has=page.locator('[aria-label^="Task:"]')).first  # TODO: refine
        first_task.wait_for(state="visible", timeout=5000)

        with page.expect_request(
            lambda r: r.method == "PATCH" and f"/api/agents/{_AGENT_SLUG}/librarian" in r.url
        ):
            first_task.click()

    def test_change_fanout(self, page: Page, base_url: str, seeded_agent: None):
        """Changing the fanout select fires a PATCH."""
        _navigate_to_memory_tab(page, base_url)

        # Expand advanced first
        advanced_btn = page.get_by_role("button", name="Show advanced…")
        advanced_btn.wait_for(state="visible", timeout=5000)
        advanced_btn.click()

        fanout_select = page.get_by_label("Librarian fanout level")
        fanout_select.wait_for(state="visible", timeout=5000)

        with page.expect_request(
            lambda r: r.method == "PATCH" and f"/api/agents/{_AGENT_SLUG}/librarian" in r.url
        ):
            fanout_select.select_option("high")
