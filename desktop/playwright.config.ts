import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: "**/*.spec.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "iphone-14",
      use: { ...devices["iPhone 14"] },
    },
  ],
  // The suite must run against a BUILT SPA, not the dev server. src/sw.ts is a
  // rollup input (see vite.config.ts build.rollupOptions.input.sw, emitted as
  // sw.js); `vite dev` never produces it, so /desktop/sw.js 404s, no service
  // worker registers, no `taos-static-*` cache is ever created and every
  // fast-boot spec in tests/e2e/sw-and-reconnect.spec.ts fails on
  // `expect(cacheNames.some(n => n.startsWith("taos-static-"))).toBe(true)`.
  // Measured: against `npm run dev` the suite is 10 failed / 13 skipped /
  // 1 passed, and eight of those ten are that one missing file.
  //
  // `vite preview` serves build.outDir (../static/desktop) at base /desktop/ on
  // 4173 by default, so the port is pinned to keep baseURL above correct and
  // --strictPort makes a port clash fail loudly instead of silently serving the
  // suite from somewhere else. The timeout covers the production build.
  webServer: {
    command: "npm run build && npm run preview -- --port 5173 --strictPort",
    url: "http://localhost:5173/desktop/",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
