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

test.describe("Project canvas board", () => {
  test("user adds note via API, sees it on canvas tab after reload", async ({
    page, request,
  }) => {
    const created = await request.post("/api/projects", {
      data: { name: "E2E Canvas", slug: "e2e-canvas", description: "" },
    });
    expect(created.ok()).toBeTruthy();
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
      data: { name: "E2E SSE", slug: "e2e-sse", description: "" },
    });
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
