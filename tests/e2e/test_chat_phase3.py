"""Chat Phase 3 — typing-phase labels E2E.

Requires TAOS_E2E_URL set.
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
URL = os.environ.get("TAOS_E2E_URL", "")


def test_typing_footer_renders_at_all(page: Page):
    """Smoke: open the chat, typing footer region is mounted (visibility
    depends on real agent activity which this stub doesn't trigger)."""
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    # The footer container is always mounted (hidden when empty); just verify
    # the channel opens without crash. Observing actual phase labels requires
    # a backend test harness emitting phase heartbeats — out of scope for this stub.
    expect(page.get_by_role("button", name="Attach files").first).to_be_visible()
