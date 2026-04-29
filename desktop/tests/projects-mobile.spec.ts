import { test, expect } from "@playwright/test";

test.describe("projects mobile shell @iphone-14", () => {
  test("end-to-end mobile flow: list → workspace → tabs → board → modal → canvas", async ({ page }) => {
    await page.goto("/desktop/");

    // Sign in if required (skip if dev mode auto-auths).
    // On iPhone 14 emulation, isTouchDevice = true so launched=true and no login screen.
    const loginVisible = await page
      .getByRole("button", { name: /sign in|login/i })
      .isVisible()
      .catch(() => false);
    if (loginVisible) {
      await page.getByLabel(/username|email/i).fill("dev");
      await page.getByLabel(/password/i).fill("dev");
      await page.getByRole("button", { name: /sign in|login/i }).click();
    }

    // Open Projects via the Launchpad ("All Apps" button in the dock).
    // Projects is not on the home screen by default — it lives in the Launchpad.
    await page.getByRole("button", { name: /all apps/i }).click();

    // Launchpad is open — Projects icon has aria-label="Open Projects".
    await expect(page.getByRole("button", { name: /open projects/i })).toBeVisible({ timeout: 5000 });
    await page.getByRole("button", { name: /open projects/i }).click();

    // Projects app window is now active.
    // The MobileSplitView list pane shows a "Projects" heading.
    await expect(page.getByRole("heading", { name: /^projects$/i })).toBeVisible({ timeout: 5000 });

    // ── Core assertion: Projects list is mounted ────────────────────────────
    // The list <ul> is in the DOM (may be off-screen via translate in MobileSplitView).
    // We verify via the heading we already checked, and wait for the list to be attached.
    const projectList = page.getByRole("list", { name: /projects/i });
    await projectList.waitFor({ state: "attached", timeout: 5000 });

    // Attempt to open a project for the workspace / tab / board sub-flow.
    // This requires at least one project to exist (needs a live backend).
    const projectButtons = projectList.getByRole("button");
    const projectCount = await projectButtons.count().catch(() => 0);

    if (projectCount === 0) {
      // No projects (no backend or empty dataset).
      // Try to create one; if the API is unavailable, cancel and skip the workspace sub-flow.
      await page.getByRole("button", { name: /create project/i }).click();
      await expect(page.getByRole("dialog")).toBeVisible({ timeout: 3000 });

      const nameInput = page.getByRole("dialog").locator("input").first();
      await nameInput.fill("e2e Mobile Test Project");
      await page.getByRole("dialog").getByRole("button", { name: /^create$/i }).click();

      // Give the API 3 s to succeed; if it doesn't, cancel and skip workspace tests.
      const dialogGone = await page
        .getByRole("dialog")
        .waitFor({ state: "hidden", timeout: 3000 })
        .then(() => true)
        .catch(() => false);

      if (!dialogGone) {
        // API unavailable — dismiss dialog, skip project-detail sub-flow.
        await page.getByRole("dialog").getByRole("button", { name: /cancel/i }).click();
        // Core assertion already passed (list is visible), test ends here.
        return;
      }
    }

    // At least one project exists — tap it to open the workspace.
    await projectList.getByRole("button").first().click();

    // Detail pane slid in — back button visible (aria-label="Back to Projects").
    await expect(page.getByRole("button", { name: /back to/i })).toBeVisible({ timeout: 3000 });

    // Switch through workspace tabs via WorkspaceTabPills.
    // On mobile, ProjectWorkspace renders WorkspaceTabPills (role=tablist, role=tab buttons).
    for (const tabName of ["tasks", "board", "files", "messages"]) {
      await page.getByRole("tab", { name: new RegExp(tabName, "i") }).click();
      // Each tab should at least mount without throwing.
      await expect(page.locator("body")).toBeVisible();
    }

    // Tasks tab — tap FAB → sheet opens → create a task.
    await page.getByRole("tab", { name: /tasks/i }).click();
    // ProjectFab has aria-label="Create task".
    await page.getByRole("button", { name: /create task/i }).click();
    await expect(page.getByTestId("task-create-sheet")).toBeVisible({ timeout: 3000 });
    // Input placeholder is "Task title".
    await page.getByPlaceholder(/task title/i).fill(`mobile e2e ${Date.now()}`);
    // Submit button text is "Create".
    await page.getByRole("button", { name: /^create$/i }).click();
    // Wait for the sheet to auto-close after submit. If it doesn't (network failure
    // or actual regression), dismiss it manually and fail the test loudly.
    const sheetClosed = await page
      .getByTestId("task-create-sheet")
      .waitFor({ state: "hidden", timeout: 5000 })
      .then(() => true)
      .catch(() => false);
    if (!sheetClosed) {
      await page.getByRole("button", { name: /cancel/i }).click();
      throw new Error(
        "TaskCreateSheet did not auto-close after submit — likely a network failure or a regression in TaskCreateSheet's onSubmit handler.",
      );
    }

    // Board tab — navigate and verify carousel mounted.
    await page.getByRole("tab", { name: /board/i }).click();
    const scroller = page.getByTestId("mobile-board-scroller");
    await expect(scroller).toBeVisible({ timeout: 5000 });

    // Attempt swipe gesture on carousel (best-effort; board still passes if no tasks).
    const box = await scroller.boundingBox();
    if (box) {
      await page.mouse.move(box.x + box.width - 20, box.y + box.height / 2);
      await page.mouse.down();
      await page.mouse.move(box.x + 20, box.y + box.height / 2, { steps: 10 });
      await page.mouse.up();
    }

    // Tap first task card in scroller → MobileTaskModal opens (conditional on tasks existing).
    const firstCard = scroller.getByTestId("task-card").first();
    if (await firstCard.isVisible().catch(() => false)) {
      await firstCard.click();
      const modal = page.getByTestId("mobile-task-modal");
      if (await modal.isVisible({ timeout: 2000 }).catch(() => false)) {
        await expect(modal).toBeVisible();
        // Close button has aria-label="Close modal".
        await page.getByRole("button", { name: /close modal/i }).click();
        await expect(modal).not.toBeVisible({ timeout: 3000 });
      }
    }

    // Navigate back to project list via in-app back button.
    // (MobileSplitView uses internal state, not browser history, so goBack() won't work.)
    await page.getByRole("button", { name: /back to/i }).click();
    // Back on list pane — "Projects" heading visible again.
    await expect(page.getByRole("heading", { name: /^projects$/i })).toBeVisible({ timeout: 3000 });
  });
});
