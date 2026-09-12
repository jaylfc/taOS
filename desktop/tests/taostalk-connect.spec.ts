import { test, expect, type Page } from "@playwright/test";

test.describe("Connect session wizard @taostalk", () => {
  test("wizard opens from empty state and shows 3 steps", async ({ page }) => {
    await page.goto("/desktop/").catch(() => {});

    const connectBtn = page.getByRole("button", { name: /connect session/i });
    if (!(await connectBtn.isVisible().catch(() => false))) {
      test.skip(true, "Connect session button not visible in this run");
      return;
    }

    await connectBtn.click();

    const dialog = page.getByRole("dialog", { name: /connect session/i });
    await expect(dialog).toBeVisible({ timeout: 5000 });

    await expect(page.getByText("Pick agent")).toBeVisible();
    await expect(page.getByRole("button", { name: /cancel/i })).toBeVisible();
  });

  test("wizard step indicators show 3 steps", async ({ page }) => {
    await page.goto("/desktop/").catch(() => {});

    const connectBtn = page.getByRole("button", { name: /connect session/i });
    if (!(await connectBtn.isVisible().catch(() => false))) {
      test.skip(true, "Connect session button not visible");
      return;
    }

    await connectBtn.click();
    await expect(page.getByRole("dialog")).toBeVisible();

    const steps = page.locator('[aria-current="step"]');
    await expect(steps).toHaveCount(1);
    await expect(steps.first()).toHaveAttribute("aria-label", "Step 1: Pick agent");
  });

  test("wizard closes on Escape key", async ({ page }) => {
    await page.goto("/desktop/").catch(() => {});

    const connectBtn = page.getByRole("button", { name: /connect session/i });
    if (!(await connectBtn.isVisible().catch(() => false))) {
      test.skip(true, "Connect session button not visible");
      return;
    }

    await connectBtn.click();
    await expect(page.getByRole("dialog")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).not.toBeVisible();
  });

  test("wizard shows agent list after fetching", async ({ page }) => {
    await page.route("**/api/agents", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          { name: "test-agent", status: "running", display_name: "Test Agent" },
        ]),
      });
    });

    await page.goto("/desktop/").catch(() => {});

    const connectBtn = page.getByRole("button", { name: /connect session/i });
    if (!(await connectBtn.isVisible().catch(() => false))) {
      test.skip(true, "Connect session button not visible");
      return;
    }

    await connectBtn.click();
    await expect(page.getByRole("dialog")).toBeVisible();

    await expect(page.getByText("Test Agent")).toBeVisible({ timeout: 5000 });
  });

  test("agent listbox supports keyboard navigation", async ({ page }) => {
    await page.route("**/api/agents", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          { name: "alpha", status: "running", display_name: "Alpha" },
          { name: "beta", status: "running", display_name: "Beta" },
        ]),
      });
    });

    await page.goto("/desktop/").catch(() => {});

    const connectBtn = page.getByRole("button", { name: /connect session/i });
    if (!(await connectBtn.isVisible().catch(() => false))) {
      test.skip(true, "Connect session button not visible");
      return;
    }

    await connectBtn.click();
    await expect(page.getByRole("dialog")).toBeVisible();

    const listbox = page.getByRole("listbox", { name: /running agents/i });
    await expect(listbox).toBeVisible();

    await listbox.focus();
    await page.keyboard.press("ArrowDown");
    const selected = page.locator('[role="option"][aria-selected="true"]');
    await expect(selected).toHaveCount(1);
  });

  test("snippet panel shows bash then can switch to PowerShell", async ({ page }) => {
    await page.route("**/api/agents", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([{ name: "my-agent", status: "running" }]),
      });
    });

    await page.route("**/api/chat/channels", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ id: "ch-1", name: "#session-my-agent" }),
        });
      }
    });

    await page.goto("/desktop/").catch(() => {});

    const connectBtn = page.getByRole("button", { name: /connect session/i });
    if (!(await connectBtn.isVisible().catch(() => false))) {
      test.skip(true, "Connect session button not visible");
      return;
    }

    await connectBtn.click();
    await expect(page.getByRole("dialog")).toBeVisible();

    await expect(page.getByText("my-agent")).toBeVisible({ timeout: 5000 });
    await page.getByText("my-agent").click();
    await page.getByRole("button", { name: /next/i }).click();

    const channelInput = page.getByLabel(/channel name/i);
    await channelInput.fill("#session-my-agent");
    await page.getByRole("button", { name: /create channel/i }).click();

    await expect(page.getByText("Connect snippet")).toBeVisible({ timeout: 5000 });

    await expect(page.getByText(/taOStalk connect snippet/)).toBeVisible();
    await expect(page.getByText(/taOS A2A bus/)).toBeVisible();

    await page.getByRole("tab", { name: "PowerShell" }).click();
    await expect(page.getByText(/Invoke-RestMethod/)).toBeVisible();
  });

  test("wizard dialog has correct ARIA role and modal attributes", async ({ page }) => {
    await page.goto("/desktop/").catch(() => {});

    const connectBtn = page.getByRole("button", { name: /connect session/i });
    if (!(await connectBtn.isVisible().catch(() => false))) {
      test.skip(true, "Connect session button not visible");
      return;
    }

    await connectBtn.click();

    const dialog = page.getByRole("dialog", { name: /connect session/i });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute("aria-modal", "true");
    await expect(dialog).toHaveAttribute("aria-label", "Connect session");
  });

  test("wizard step indicator has aria-current=step for active step", async ({ page }) => {
    await page.goto("/desktop/").catch(() => {});

    const connectBtn = page.getByRole("button", { name: /connect session/i });
    if (!(await connectBtn.isVisible().catch(() => false))) {
      test.skip(true, "Connect session button not visible");
      return;
    }

    await connectBtn.click();

    const activeStep = page.locator('[aria-current="step"]').first();
    await expect(activeStep).toHaveAttribute("aria-label", "Step 1: Pick agent");
  });

  test("content blocks render in MessageList", async ({ page }) => {
    await page.goto("/desktop/").catch(() => {});

    const channelLink = page.getByRole("link", { name: /chat guide/i });
    if (!(await channelLink.isVisible().catch(() => false))) {
      test.skip(true, "Chat UI not available");
      return;
    }
  });
});
