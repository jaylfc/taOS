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

// ---- TEMPORARY DIAGNOSTIC (tsk-vg54cy) - removed before the PR leaves draft ----
const PRECACHE_PROBE = [
  "/desktop/",
  "/desktop/index.html",
  "/chat-pwa",
  "/static/manifest-desktop.json",
  "/static/manifest-chat.json",
  "/static/favicon.ico",
  "/static/icon-16.png",
  "/static/icon-32.png",
  "/static/icon-180.png",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

async function dumpDiag(page: Page, label: string) {
  const info = await page.evaluate(async (urls) => {
    const out: Record<string, unknown> = {};
    try {
      out.cacheNames = await caches.keys();
      const names = out.cacheNames as string[];
      const entries: Record<string, number> = {};
      for (const n of names) {
        const c = await caches.open(n);
        entries[n] = (await c.keys()).length;
      }
      out.cacheEntryCounts = entries;
    } catch (e) {
      out.cacheError = String(e);
    }
    try {
      const reg = await navigator.serviceWorker.getRegistration();
      out.reg = reg
        ? {
            scope: reg.scope,
            scriptURL: reg.active?.scriptURL ?? reg.installing?.scriptURL ?? reg.waiting?.scriptURL ?? null,
            activeState: reg.active?.state ?? null,
            hasInstalling: Boolean(reg.installing),
            hasWaiting: Boolean(reg.waiting),
          }
        : null;
      out.controller = navigator.serviceWorker.controller?.scriptURL ?? null;
      out.regCount = (await navigator.serviceWorker.getRegistrations()).length;
    } catch (e) {
      out.regError = String(e);
    }
    try {
      const r = await fetch("/sw.js", { cache: "no-store" });
      const body = await r.text();
      out.swJs = {
        status: r.status,
        ctype: r.headers.get("content-type"),
        swAllowed: r.headers.get("service-worker-allowed"),
        bytes: body.length,
        head: body.slice(0, 160).replace(/\s+/g, " "),
      };
    } catch (e) {
      out.swJsError = String(e);
    }
    const probes: Record<string, number | string> = {};
    for (const u of urls as string[]) {
      try {
        const r = await fetch(u, { cache: "no-store" });
        probes[u] = r.status;
      } catch (e) {
        probes[u] = String(e);
      }
    }
    out.precacheProbe = probes;
    try {
      const h = await fetch("/api/health", { cache: "no-store" });
      out.health = { status: h.status, version: h.headers.get("x-taos-version") };
    } catch (e) {
      out.healthError = String(e);
    }
    out.bodyText = (document.body?.innerText ?? "").slice(0, 400).replace(/\s+/g, " ");
    out.href = location.href;
    return out;
  }, PRECACHE_PROBE);
  console.log(`DIAG ${label} ${JSON.stringify(info)}`);
}

function wireConsole(page: Page, label: string) {
  page.on("console", (m) => console.log(`CONSOLE ${label} [${m.type()}] ${m.text().slice(0, 300)}`));
  page.on("pageerror", (e) => console.log(`PAGEERROR ${label} ${String(e).slice(0, 300)}`));
}
// ---- END TEMPORARY DIAGNOSTIC ----

for (const pwa of PWA_PATHS) {
  test.describe(`${pwa.name} fast-boot UX`, () => {
    test(`registers SW and precaches the shell (${pwa.url})`, async ({ page }) => {
      wireConsole(page, `precache:${pwa.name}`);
      await page.goto(pwa.url);
      await waitForSWReady(page).catch((e) => console.log(`DIAG waitForSWReady-threw precache:${pwa.name} ${String(e).slice(0, 200)}`));
      await dumpDiag(page, `precache:${pwa.name}`);
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
      wireConsole(page, `toast:${pwa.name}`);
      await page.goto(pwa.url);
      await waitForSWReady(page).catch((e) => console.log(`DIAG waitForSWReady-threw toast:${pwa.name} ${String(e).slice(0, 200)}`));
      await page.waitForTimeout(3_000);
      await dumpDiag(page, `toast:${pwa.name}`);
      console.log(`DIAG toast-html:${pwa.name} ${JSON.stringify((await page.content()).slice(0, 1200))}`);
      await expect(page.getByText(/new taOS version available/i)).toBeVisible();
    });
  });
}
