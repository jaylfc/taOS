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

// The CI job onboards the first user against the backend it just started and
// writes that session cookie out as a Playwright storage state. Without it
// /desktop/ renders the zero-user setup wizard rather than the desktop shell,
// and POST /api/projects answers 401. Unset locally, so `npm run test:e2e`
// still runs signed out.
const STORAGE_STATE = process.env.E2E_STORAGE_STATE || undefined;

// The `request` fixture inherits storageState, so it carries taos_session -- but
// nothing attaches X-CSRF-Token. The SPA adds that header from JavaScript
// (src/lib/csrf.ts) and an APIRequestContext runs no page script, so every
// mutating call a spec makes through `request` reaches verify_csrf with a
// cookie and no header and is refused 403. Most routers are registered with
// `dependencies=_csrf` (tinyagentos/routes/__init__.py), so this is not
// specific to /api/projects. The CI job exports the token whose pair it already
// proved with a curl probe before starting Playwright. Unset locally, where the
// suite runs signed out and never reaches the check.
const CSRF_TOKEN = process.env.E2E_CSRF_TOKEN || undefined;

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
    storageState: STORAGE_STATE,
    trace: "on-first-retry",
    ...(CSRF_TOKEN ? { extraHTTPHeaders: { "X-CSRF-Token": CSRF_TOKEN } } : {}),
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
