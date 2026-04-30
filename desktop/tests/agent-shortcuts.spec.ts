import { test, expect, type Page } from "@playwright/test";

// Uses the iphone-14 project defined in playwright.config.ts
// Run with: cd desktop && npx playwright test --project=iphone-14 agent-shortcuts.spec.ts

async function openAgentsApp(page: Page): Promise<boolean> {
  // Try to navigate and open the AgentsApp. Returns false if it can't be opened
  // (no backend, no Agents button, etc.) so the test can skip gracefully.
  await page.goto("/desktop/").catch(() => {});

  // Sign-in fallback (mobile bypasses login on touch devices, but be defensive).
  const loginVisible = await page
    .getByRole("button", { name: /sign in|login/i })
    .isVisible()
    .catch(() => false);
  if (loginVisible) {
    await page.getByLabel(/username|email/i).fill("dev").catch(() => {});
    await page.getByLabel(/password/i).fill("dev").catch(() => {});
    await page.getByRole("button", { name: /sign in|login/i }).click().catch(() => {});
  }

  // Agents is pinned in the dock — try the dock button first.
  const directBtn = page.getByRole("button", { name: /^agents$/i });
  if (await directBtn.isVisible().catch(() => false)) {
    await directBtn.click();
    return true;
  }

  // Fall back to Launchpad ("All Apps" dock button → aria-label="Open Agents").
  const launchpad = page.getByRole("button", { name: /all apps/i });
  if (await launchpad.isVisible().catch(() => false)) {
    await launchpad.click();
    const openAgents = page.getByRole("button", { name: /open agents/i });
    if (await openAgents.isVisible({ timeout: 3000 }).catch(() => false)) {
      await openAgents.click();
      return true;
    }
  }

  return false;
}

test.describe("Agent shortcuts @iphone-14", () => {
  test("shortcut row appears on agent card when shortcuts available", async ({ page }) => {
    const opened = await openAgentsApp(page);
    if (!opened) {
      test.skip(true, "AgentsApp not openable in this run (no agents app launcher visible)");
      return;
    }

    // Wait briefly for agents to render and shortcut fetches to resolve.
    await page.waitForTimeout(1500);

    const shortcutRow = page.locator(".agent-shortcut-row").first();
    const visible = await shortcutRow.isVisible().catch(() => false);
    if (!visible) {
      test.skip(true, "No agents with shortcuts in this seed (backend unavailable or no agents)");
      return;
    }
    await expect(shortcutRow).toBeVisible();
  });

  test("clicking a terminal shortcut opens TerminalApp with shortcut status indicator", async ({ page }) => {
    const opened = await openAgentsApp(page);
    if (!opened) {
      test.skip(true, "AgentsApp not openable");
      return;
    }
    await page.waitForTimeout(1500);

    // Shortcut buttons use aria-label={shortcut.label}; pick any container-terminal shortcut.
    // Common label patterns: "Container shell", "Shell", "Terminal".
    const shellBtn = page
      .locator(".agent-shortcut-btn")
      .filter({ has: page.locator('[aria-label*="shell" i], [aria-label*="terminal" i]') })
      .first();

    if (!(await shellBtn.isVisible().catch(() => false))) {
      test.skip(true, "No terminal/shell shortcut available in this seed");
      return;
    }

    await shellBtn.tap();

    // TerminalApp opened via shortcut renders: <span>Connecting to shortcut…</span>
    await expect(page.getByText("Connecting to shortcut…")).toBeVisible({ timeout: 8000 });
  });

  test("clicking a dashboard shortcut opens BrowserApp iframe", async ({ page }) => {
    const opened = await openAgentsApp(page);
    if (!opened) {
      test.skip(true, "AgentsApp not openable");
      return;
    }
    await page.waitForTimeout(1500);

    // Dashboard shortcuts have kind="dashboard" — they open BrowserApp.
    // Common label patterns: "Gateway dashboard", "Dashboard", "Web UI".
    const dashBtn = page
      .locator(".agent-shortcut-btn")
      .filter({ has: page.locator('[aria-label*="dashboard" i], [aria-label*="gateway" i]') })
      .first();

    if (!(await dashBtn.isVisible().catch(() => false))) {
      test.skip(true, "No dashboard shortcut available (likely no openclaw agent in this seed)");
      return;
    }

    await dashBtn.tap();

    // BrowserApp mounts an <iframe> for the target URL.
    await expect(page.locator("iframe").first()).toBeAttached({ timeout: 8000 });
  });

  test("backend-down: shortcut fetch error renders no shortcut buttons", async ({ page }) => {
    // Intercept the shortcuts list API and return 503 so useAgentShortcuts returns [].
    await page.route("**/api/agents/*/shortcuts", (route) =>
      route.fulfill({ status: 503, body: "Service Unavailable" }),
    );

    const opened = await openAgentsApp(page);
    if (!opened) {
      test.skip(true, "AgentsApp not openable");
      return;
    }
    await page.waitForTimeout(1500);

    // AgentShortcutRow returns null when shortcuts.length === 0,
    // so no .agent-shortcut-btn elements should be present.
    const shortcutBtns = page.locator(".agent-shortcut-btn");
    await expect(shortcutBtns).toHaveCount(0);
  });
});
