/**
 * E2E: service worker + reconnect UX.
 *
 * Covers:
 *  - SW registers and precaches the shell on first visit
 *  - With the backend "down" (we abort all matching requests), reload
 *    serves the cached shell and the BackendBanner appears
 *  - When the backend "comes back" the banner clears
 *  - When the backend reports a different X-Taos-Version than the
 *    build version, the update toast appears
 *  - Same scenarios run against /chat-pwa (parameterized)
 *
 * The backend is assumed running by the test harness (playwright.config.ts
 * webServer or external). Network conditions are simulated via
 * page.route() rather than actually restarting the backend.
 */
import { test, expect, type Page } from "@playwright/test";

const PWA_PATHS = [
  { name: "desktop", url: "/desktop/" },
  { name: "chat-pwa", url: "/chat-pwa" },
];

async function waitForSWReady(page: Page) {
  await page.waitForFunction(async () => {
    if (!("serviceWorker" in navigator)) return false;
    const reg = await navigator.serviceWorker.getRegistration();
    return Boolean(reg && reg.active);
  }, { timeout: 15_000 });
}

for (const pwa of PWA_PATHS) {
  test.describe(`${pwa.name} fast-boot UX`, () => {
    test(`registers SW and precaches the shell (${pwa.url})`, async ({ page }) => {
      await page.goto(pwa.url);
      await waitForSWReady(page);
      const cacheNames = await page.evaluate(() => caches.keys());
      expect(cacheNames.some((n) => n.startsWith("taos-static-"))).toBe(true);
    });

    test(`shows BackendBanner when /api/health is unreachable (${pwa.url})`, async ({ page }) => {
      await page.goto(pwa.url);
      await waitForSWReady(page);
      // Block all /api/* traffic to simulate the backend being down.
      await page.route("**/api/**", (route) => route.abort("connectionrefused"));
      // Trigger a reconnect by waiting longer than the first poll.
      await page.waitForTimeout(3_500);
      await expect(page.getByText(/taOS is restarting/i)).toBeVisible();
    });

    test(`banner clears when backend recovers (${pwa.url})`, async ({ page }) => {
      await page.goto(pwa.url);
      await waitForSWReady(page);
      // First make backend "fail"...
      await page.route("**/api/health", (route) => route.abort("connectionrefused"));
      await page.waitForTimeout(3_500);
      await expect(page.getByText(/taOS is restarting/i)).toBeVisible();
      // ...then "recover"
      await page.unroute("**/api/health");
      await page.waitForTimeout(5_500);
      await expect(page.getByText(/taOS is restarting/i)).not.toBeVisible();
    });

    test(`update toast appears on version mismatch (${pwa.url})`, async ({ page }) => {
      // Force the backend to claim a different version than the build.
      await page.route("**/api/health", async (route) => {
        const r = await route.fetch();
        const body = await r.text();
        await route.fulfill({
          status: r.status(),
          headers: { ...r.headers(), "x-taos-version": "99.99.99" },
          body,
        });
      });
      await page.goto(pwa.url);
      await waitForSWReady(page);
      await page.waitForTimeout(3_000);
      await expect(page.getByText(/new taOS version available/i)).toBeVisible();
    });
  });
}
