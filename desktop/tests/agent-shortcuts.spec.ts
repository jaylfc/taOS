import { test, expect, type Page } from "@playwright/test";

// Uses the iphone-14 project defined in playwright.config.ts
// Run with: cd desktop && npx playwright test --project=iphone-14 agent-shortcuts.spec.ts

async function openAgentsApp(page: Page): Promise<void> {
  await page.goto("/desktop/");

  // Sign-in fallback (mobile bypasses login on touch devices, but be defensive).
  const loginBtn = page.getByRole("button", { name: /sign in|login/i });
  if (await loginBtn.isVisible()) {
    await page.getByLabel(/username|email/i).fill("dev");
    await page.getByLabel(/password/i).fill("dev");
    await loginBtn.click();
  }

  // Agents is pinned in the dock — try the dock button first.
  const directBtn = page.getByRole("button", { name: /^agents$/i });
  if (await directBtn.isVisible()) {
    await directBtn.click();
    return;
  }

  // Fall back to Launchpad ("All Apps" dock button → aria-label="Open Agents").
  const launchpad = page.getByRole("button", { name: /all apps/i });
  await expect(launchpad).toBeVisible();
  await launchpad.click();
  const openAgents = page.getByRole("button", { name: /open agents/i });
  await expect(openAgents).toBeVisible({ timeout: 3000 });
  await openAgents.click();
}

test.describe("Agent shortcuts @iphone-14", () => {
  test("shortcut row appears on agent card when shortcuts available", async ({ page }) => {
    await openAgentsApp(page);

    // Wait briefly for agents to render and shortcut fetches to resolve.
    await page.waitForTimeout(1500);

    const shortcutRow = page.locator(".agent-shortcut-row").first();
    await expect(shortcutRow).toBeVisible();
  });

  test("clicking a terminal shortcut opens TerminalApp with shortcut status indicator", async ({ page }) => {
    await openAgentsApp(page);
    await page.waitForTimeout(1500);

    // Shortcut buttons use aria-label={shortcut.label}; pick any container-terminal shortcut.
    // Common label patterns: "Container shell", "Shell", "Terminal".
    // Use CSS attribute selector on the button itself: filter({has}) only matches descendants,
    // not the element's own aria-label attribute.
    const shellBtn = page
      .locator('.agent-shortcut-btn[aria-label*="shell" i], .agent-shortcut-btn[aria-label*="terminal" i]')
      .first();

    await expect(shellBtn).toBeVisible();

    await shellBtn.tap();

    // TerminalApp opened via shortcut renders: <span>Connecting to shortcut…</span>
    await expect(page.getByText("Connecting to shortcut…")).toBeVisible({ timeout: 8000 });
  });

  test("clicking a dashboard shortcut opens BrowserApp iframe", async ({ page }) => {
    await openAgentsApp(page);
    await page.waitForTimeout(1500);

    // Dashboard shortcuts have kind="dashboard" — they open BrowserApp.
    // Common label patterns: "Gateway dashboard", "Dashboard", "Web UI".
    const dashBtn = page
      .locator('.agent-shortcut-btn[aria-label*="dashboard" i], .agent-shortcut-btn[aria-label*="gateway" i]')
      .first();

    await expect(dashBtn).toBeVisible();

    await dashBtn.tap();

    // BrowserApp mounts an <iframe> for the target URL.
    await expect(page.locator("iframe").first()).toBeAttached({ timeout: 8000 });
  });

  test("backend-down: shortcut fetch error renders no shortcut buttons", async ({ page }) => {
    // Intercept the shortcuts list API and return 503 so useAgentShortcuts returns [].
    await page.route("**/api/agents/*/shortcuts", (route) =>
      route.fulfill({ status: 503, body: "Service Unavailable" }),
    );

    await openAgentsApp(page);
    await page.waitForTimeout(1500);

    // AgentShortcutRow returns null when shortcuts.length === 0,
    // so no .agent-shortcut-btn elements should be present.
    const shortcutBtns = page.locator(".agent-shortcut-btn");
    await expect(shortcutBtns).toHaveCount(0);
  });
});
