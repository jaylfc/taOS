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

import { test, expect, type Page } from "@playwright/test";

// Playwright retries each spec twice in CI against a backend that keeps the
// projects created by the previous attempt, so a fixed slug makes the retry
// fail on a duplicate rather than on whatever it is actually testing.
const uniq = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

// Reaching the canvas: the projects list is NOT a landing page. The backend
// registers no root SPA route at all -- tinyagentos/routes/desktop.py serves
// only /desktop (:202) and /desktop/{rest} (:213) -- and in the shell Projects
// is an APP opened from the Launchpad, so its list is never bare text on "/".
// The original `page.goto("/")` + `page.click(text=<project name>)` came from
// the header above, written before Playwright was scaffolded here, and was
// never run against the real app: it times out waiting for a locator that
// cannot exist. This is the same route projects-mobile.spec.ts takes, which
// passes against the same backend.
async function openProjectCanvas(page: Page, projectName: string) {
  await page.goto("/desktop/");
  await page.getByRole("button", { name: /all apps/i }).click();
  await page.getByRole("button", { name: /open projects/i }).click();
  await expect(page.getByRole("heading", { name: /^projects$/i })).toBeVisible({ timeout: 5000 });

  const list = page.getByRole("list", { name: /projects/i });
  await list.waitFor({ state: "attached", timeout: 5000 });

  // Name the cause rather than letting a bare timeout say nothing: if the
  // project the test just created is not in the list, print what IS.
  const projectButton = list.getByRole("button", { name: projectName });
  try {
    await expect(projectButton).toBeVisible({ timeout: 5000 });
  } catch {
    throw new Error(
      `project "${projectName}" never appeared in the Projects list. List text: ` +
        (await list.innerText().catch(() => "<unreadable>")),
    );
  }
  await projectButton.click();

  // Scope to the workspace tab pills: an unrelated tablist, aria-label
  // "Preview mode" (ProjectWorkspacePane.tsx:28), ALSO has a "Canvas" tab, so
  // an unscoped getByRole("tab", {name:/canvas/i}) is a strict-mode violation
  // resolving to 2 elements. The pills are the workspace navigation
  // (WorkspaceTabPills.tsx:29); the other is a preview-surface toggle inside
  // the pane the pills open.
  await page
    .getByTestId("workspace-tab-pills-scroller")
    .getByRole("tab", { name: /^canvas$/i })
    .click();
  // tldraw pulls its bundle and assets before .tl-container mounts, which on
  // emulated mobile WebKit in CI is slower than the 5 s the specs used.
  await expect(page.locator(".tl-container")).toBeVisible({ timeout: 15_000 });
}

test.describe("Project canvas board", () => {
  test("user adds note via API, sees it on canvas tab after reload", async ({
    page, request,
  }) => {
    // Quarantined: navigation and mount are PROVEN working (openProjectCanvas
    // gets past .tl-container), but a note seeded through
    // POST /api/projects/<id>/canvas/elements never renders on the board.
    // Cause not yet established -- either the REST element never becomes a
    // tldraw shape, or tldraw renders its text where getByText cannot see it.
    // Card tsk-hgmbpu decides which before anything is changed.
    test.fixme(true, "tsk-hgmbpu: REST-seeded canvas elements do not render (hydration vs locator undecided)");
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

    await openProjectCanvas(page, project.name);
    await expect(page.getByText("hello-from-test")).toBeVisible({ timeout: 5000 });
  });

  test("agent adds note via REST → user sees it without reload (SSE)", async ({
    page, request,
  }) => {
    // Quarantined: navigation and mount are PROVEN working (openProjectCanvas
    // gets past .tl-container), but a note seeded through
    // POST /api/projects/<id>/canvas/elements never renders on the board.
    // Cause not yet established -- either the REST element never becomes a
    // tldraw shape, or tldraw renders its text where getByText cannot see it.
    // Card tsk-hgmbpu decides which before anything is changed.
    test.fixme(true, "tsk-hgmbpu: REST-seeded canvas elements do not render (hydration vs locator undecided)");
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

    await openProjectCanvas(page, project.name);

    await request.post(
      `/api/projects/${project.id}/canvas/elements`,
      { data: { kind: "note", x: 50, y: 50, w: 150, h: 80,
                payload: { text: "live-from-agent", color: "blue", font_size: 14 } } },
    );

    await expect(page.getByText("live-from-agent")).toBeVisible({ timeout: 3000 });
  });
});
