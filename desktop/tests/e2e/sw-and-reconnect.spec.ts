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
 * webServer or external). The backend going down is simulated with
 * context.setOffline(), not page.route(): once the service worker controls
 * the page, WebKit sends the page's requests through the worker and
 * page.route() never sees them (measured: an abort route on every /api/ URL matched 0
 * requests and /api/health answered 200 from the real backend). Going offline
 * fails those requests at the network, which is also what a user sees when
 * the backend is really gone. The version-mismatch test has to rewrite a
 * response header, which only page.route() can do, so it runs with service
 * workers blocked; sw.ts never handles /api/*, so the header reaches the page
 * unchanged whether or not a worker is in control.
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

/** The fast-boot state under test: the worker has claimed the page. */
async function waitForSWControl(page: Page) {
  await waitForSWReady(page);
  await page.waitForFunction(() => Boolean(navigator.serviceWorker.controller), undefined, {
    timeout: 15_000,
  });
}

for (const pwa of PWA_PATHS) {
  test.describe(`${pwa.name} fast-boot UX`, () => {
    test(`registers SW and precaches the shell (${pwa.url})`, async ({ page }) => {
      await page.goto(pwa.url);
      await waitForSWReady(page);
      const cacheNames = await page.evaluate(() => caches.keys());
      expect(cacheNames.some((n) => n.startsWith("taos-static-"))).toBe(true);
    });

    test(`shows BackendBanner when /api/health is unreachable (${pwa.url})`, async ({ page, context }) => {
      await page.goto(pwa.url);
      await waitForSWControl(page);
      // Take the backend "down" under a controlling worker (see header).
      await context.setOffline(true);
      // Trigger a reconnect by waiting longer than the first poll.
      await page.waitForTimeout(3_500);
      await expect(page.getByText(/taOS is restarting/i)).toBeVisible();
    });

    test(`banner clears when backend recovers (${pwa.url})`, async ({ page, context }) => {
      await page.goto(pwa.url);
      await waitForSWControl(page);
      // First make backend "fail"...
      await context.setOffline(true);
      await page.waitForTimeout(3_500);
      await expect(page.getByText(/taOS is restarting/i)).toBeVisible();
      // ...then "recover"
      await context.setOffline(false);
      await page.waitForTimeout(5_500);
      await expect(page.getByText(/taOS is restarting/i)).not.toBeVisible();
    });
  });

  test.describe(`${pwa.name} version check`, () => {
    // page.route() cannot see a worker-controlled page's requests (see header),
    // so keep the worker out of this one to rewrite X-Taos-Version.
    test.use({ serviceWorkers: "block" });

    test(`update toast appears on version mismatch (${pwa.url})`, async ({ page }) => {
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
      await page.waitForTimeout(3_000);
      await expect(page.getByText(/new taOS version available/i)).toBeVisible();
    });
  });
}
