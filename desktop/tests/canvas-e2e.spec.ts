// Playwright is not yet scaffolded in this repo (no @playwright/test dep,
// no playwright.config). To run this suite, first wire up Playwright:
//   npm install -D @playwright/test
//   npx playwright install
//   npx playwright init  # generate playwright.config.ts
// Then start the FastAPI dev server + `npm run dev`, and:
//   npx playwright test canvas-e2e.spec.ts
//
// Until then this file documents the canonical end-to-end coverage we
// want for the per-project canvas board: a user-side hydration path
// (REST seeded → canvas tab renders) and a live SSE path (REST POST
// from outside the page → element appears without reload).

import { test, expect } from "@playwright/test";

// Playwright retries each spec twice in CI against a backend that keeps the
// projects created by the previous attempt, so a fixed slug makes the retry
// fail on a duplicate rather than on whatever it is actually testing.
const uniq = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

test.describe("Project canvas board", () => {
  test("user adds note via API, sees it on canvas tab after reload", async ({
    page, request,
  }) => {
    const created = await request.post("/api/projects", {
      data: { name: `E2E Canvas ${uniq()}`, slug: `e2e-canvas-${uniq()}`, description: "" },
    });
    // Report the status and body on failure: `toBeTruthy()` on its own says
    // only "Received: false", which costs a whole CI round to turn into a cause.
    expect(
      created.ok(),
      `POST /api/projects -> ${created.status()} ${await created.text()}`,
    ).toBeTruthy();
    const project = await created.json();

    await request.post(
      `/api/projects/${project.id}/canvas/elements`,
      { data: { kind: "note", x: 100, y: 100, w: 200, h: 100,
                payload: { text: "hello-from-test", color: "yellow", font_size: 14 } } },
    );

    await page.goto("/");
    await page.click(`text=${project.name}`);
    await page.click("role=tab[name=/canvas/i]");

    await expect(page.locator(".tl-container")).toBeVisible({ timeout: 5000 });
    await expect(page.getByText("hello-from-test")).toBeVisible({ timeout: 5000 });
  });

  test("agent adds note via REST → user sees it without reload (SSE)", async ({
    page, request,
  }) => {
    const created = await request.post("/api/projects", {
      data: { name: `E2E SSE ${uniq()}`, slug: `e2e-sse-${uniq()}`, description: "" },
    });
    // Same guard as the spec above: without it a 403/422 surfaces as an opaque
    // JSON parse error from created.json() instead of naming the status.
    expect(
      created.ok(),
      `POST /api/projects -> ${created.status()} ${await created.text()}`,
    ).toBeTruthy();
    const project = await created.json();

    await page.goto("/");
    await page.click(`text=${project.name}`);
    await page.click("role=tab[name=/canvas/i]");
    await expect(page.locator(".tl-container")).toBeVisible({ timeout: 5000 });

    await request.post(
      `/api/projects/${project.id}/canvas/elements`,
      { data: { kind: "note", x: 50, y: 50, w: 150, h: 80,
                payload: { text: "live-from-agent", color: "blue", font_size: 14 } } },
    );

    await expect(page.getByText("live-from-agent")).toBeVisible({ timeout: 3000 });
  });
});
