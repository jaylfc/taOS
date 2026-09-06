"""End-to-end tests for the Projects board UI (Kanban / swimlane).

Skipped unless TAOS_E2E_URL is set; matches the pattern used by
test_chat_phase2a.py and other e2e tests in this directory. The board UI
lives inside the desktop SPA at the "board" tab of a project workspace.

Required server-side setup before running:
  1. uvicorn tinyagentos.app:create_app --factory --port 6969
  2. A signed-in browser session (cookies preserved by Playwright)
  3. At least one project named "board-e2e" exists (or fixture creates one)

These tests are scaffolds — they exercise the UI surface but rely on the
dev environment having an authenticated session. They will fail loudly if
the server is unauthenticated; that is expected — run them locally only.
"""
import os
import re
import uuid
import pytest
from playwright.sync_api import Page, BrowserContext, expect

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("TAOS_E2E_URL"),
        reason="TAOS_E2E_URL required",
    ),
]

_URL = os.environ.get("TAOS_E2E_URL", "")


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _create_project_and_tasks(page: Page, slug: str, n_tasks: int = 0) -> str:
    """Create a project and N open tasks via the JSON API.

    Uses page.evaluate so we inherit the browser session cookie. Returns
    the project id.
    """
    project = page.evaluate(
        """async ({ slug }) => {
            const r = await fetch('/api/projects', {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: slug, slug }),
            });
            if (!r.ok) throw new Error('create project: ' + r.status);
            return r.json();
        }""",
        {"slug": slug},
    )
    pid = project["id"]
    for i in range(n_tasks):
        page.evaluate(
            """async ({ pid, title }) => {
                const r = await fetch(`/api/projects/${pid}/tasks`, {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ title }),
                });
                if (!r.ok) throw new Error('create task: ' + r.status);
                return r.json();
            }""",
            {"pid": pid, "title": f"Test card {i + 1}"},
        )
    return pid


def _open_board(page: Page, pid: str) -> None:
    """Open the project workspace at the board tab.

    The desktop SPA opens projects via the Projects app. We click into the
    project by id once it appears in the projects list.
    """
    page.goto(_URL)
    page.get_by_role("button", name="Projects").click()
    # Project tiles include the project id as data-project-id; fallback to
    # text match if attribute selectors aren't present.
    page.locator(f'[data-project-id="{pid}"]').first.click()
    page.get_by_role("tab", name="board").click()


def test_board_renders_three_columns_in_kanban(page: Page):
    pid = _create_project_and_tasks(page, _uniq("board-e2e"))
    _open_board(page, pid)
    page.get_by_role("tab", name="Kanban").click()
    expect(page.get_by_role("region", name="Ready")).to_be_visible()
    expect(page.get_by_role("region", name="Claimed")).to_be_visible()
    expect(page.get_by_role("region", name=re.compile("Closed"))).to_be_visible()


def test_drag_card_ready_to_claimed(page: Page):
    pid = _create_project_and_tasks(page, _uniq("board-e2e"), n_tasks=1)
    _open_board(page, pid)
    page.get_by_role("tab", name="Kanban").click()

    card = page.get_by_role("button", name="Test card 1")
    target = page.get_by_role("region", name="Claimed")
    card.drag_to(target)

    expect(target.get_by_text("Test card 1")).to_be_visible()


def test_modal_open_via_card_click_and_keyboard_nav(page: Page):
    pid = _create_project_and_tasks(page, _uniq("board-e2e"), n_tasks=3)
    _open_board(page, pid)

    page.get_by_role("button", name="Test card 1").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    expect(page.get_by_text("Test card 1")).to_be_visible()

    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).not_to_be_visible()


def test_sse_live_update_across_contexts(browser):
    """Two browser contexts on the same project — claim in A appears in B."""
    ctx_a: BrowserContext = browser.new_context()
    ctx_b: BrowserContext = browser.new_context()
    try:
        page_a = ctx_a.new_page()
        page_b = ctx_b.new_page()
        pid = _create_project_and_tasks(page_a, _uniq("board-e2e"), n_tasks=1)
        _open_board(page_a, pid)
        _open_board(page_b, pid)
        page_a.get_by_role("tab", name="Kanban").click()
        page_b.get_by_role("tab", name="Kanban").click()

        card = page_a.get_by_role("button", name="Test card 1")
        target = page_a.get_by_role("region", name="Claimed")
        card.drag_to(target)

        # SSE should propagate within ~2s; Playwright auto-waits up to 5s.
        b_target = page_b.get_by_role("region", name="Claimed")
        expect(b_target.get_by_text("Test card 1")).to_be_visible(timeout=5000)
    finally:
        ctx_a.close()
        ctx_b.close()
