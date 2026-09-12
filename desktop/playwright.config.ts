import { defineConfig, devices } from "@playwright/test";

// The suite is pointed at a REAL taOS backend when E2E_BASE_URL is set (that is
// what the desktop-e2e CI job does). Only the backend serves the three routes
// the specs actually need: /sw.js at the ROOT (src/lib/sw-register.ts:45
// registers "/sw.js" so the worker gets root scope and can control /chat-pwa,
// which is in the SW's PRECACHE_URLS), /chat-pwa, and /api/*. vite serves the
// built worker only at /desktop/sw.js because base is "/desktop/" -- measured
// 404 at the root against both `vite dev` and `vite preview`, which is why no
// service worker registered at all and every fast-boot spec failed on an empty
// caches.keys(). tinyagentos/routes/desktop.py:185 is the route that fixes it.
//
// With E2E_BASE_URL unset, `npm run test:e2e` behaves as before and drives its
// own vite server, so the local loop does not need a backend for the specs that
// do not talk to one.
const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:5173";

export default defineConfig({
  testDir: "./tests",
  testMatch: "**/*.spec.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "iphone-14",
      use: { ...devices["iPhone 14"] },
    },
  ],
  // When E2E_BASE_URL is set the server is already up and is not ours to manage.
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: "npm run build && npm run preview -- --port 5173 --strictPort",
        url: "http://localhost:5173/desktop/",
        reuseExistingServer: !process.env.CI,
        timeout: 180_000,
      },
});
