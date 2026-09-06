"""Shell cross-app drag-drop E2E — Files → Messages.

Requires TAOS_E2E_URL set and at least one file in the user workspace.
"""
import os
import re

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


def test_drag_file_from_files_to_messages_composer(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Files").click()
    file_row = page.locator("[data-file-row]").first
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    composer_drop_target = page.locator(".message-list-drop-target").first
    file_row.drag_to(composer_drop_target)
    expect(page.locator("[aria-label^='Remove']")).to_be_visible()
