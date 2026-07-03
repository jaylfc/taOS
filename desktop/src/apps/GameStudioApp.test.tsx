import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { GameStudioApp } from "./GameStudioApp";
import { TEMPLATES } from "./gamestudio/templates";

/* ------------------------------------------------------------------ */
/*  A tiny in-memory fake of the games backend + the seed static files  */
/*  + /api/taos-agent/settings, keyed by URL/method, so "Use template"   */
/*  can round-trip through the real save -> open -> load sequence       */
/*  without a live server. AI streaming (Create's "Generate with AI")   */
/*  is exercised separately in generate-game.test.ts.                   */
/* ------------------------------------------------------------------ */

function makeFetchMock() {
  const games = new Map<string, { id: string; name: string; prompt: string; template: string; created: number; updated: number; files: Record<string, string> }>();

  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();

    if (url === "/api/games" && method === "GET") {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ games: [...games.values()].map(({ files: _files, ...meta }) => meta) }),
      } as Response);
    }

    if (url === "/api/taos-agent/settings") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ model: "test-model" }) } as Response);
    }

    if (url.startsWith("/desktop/gamestudio-seeds/")) {
      return Promise.resolve({ ok: true, text: () => Promise.resolve("// seed content\n") } as Response);
    }

    const gameMatch = url.match(/^\/api\/games\/([^/]+)$/);
    if (gameMatch) {
      const id = gameMatch[1]!;
      if (method === "PUT") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        const now = Date.now() / 1000;
        const existing = games.get(id);
        const record = {
          id,
          name: body.name ?? existing?.name ?? id,
          prompt: body.prompt ?? existing?.prompt ?? "",
          template: body.template ?? existing?.template ?? "",
          created: existing?.created ?? now,
          updated: now,
          files: body.files,
        };
        games.set(id, record);
        return Promise.resolve({ ok: true, json: () => Promise.resolve(record) } as Response);
      }
      if (method === "GET") {
        const record = games.get(id);
        if (!record) return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ error: "not found" }) } as Response);
        return Promise.resolve({ ok: true, json: () => Promise.resolve(record) } as Response);
      }
    }

    return Promise.resolve({ ok: true, json: () => Promise.resolve([]) } as Response);
  });
}

function renderApp(windowId = "test-window") {
  return render(<GameStudioApp windowId={windowId} />);
}

describe("GameStudioApp", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", makeFetchMock() as unknown as typeof fetch);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("navigation rail", () => {
    it("renders Create, Editor, Library and Share", () => {
      renderApp();
      const nav = screen.getByRole("navigation", { name: "Game Studio views" });
      expect(nav).toBeDefined();
      expect(screen.getByRole("button", { name: "Create" })).toBeDefined();
      expect(screen.getByRole("button", { name: "Editor" })).toBeDefined();
      expect(screen.getByRole("button", { name: "Library" })).toBeDefined();
      expect(screen.getByRole("button", { name: "Share" })).toBeDefined();
    });

    it("shows Create view by default with Create rail item active", () => {
      renderApp();
      const nav = screen.getByRole("navigation", { name: "Game Studio views" });
      const createBtn = nav.querySelector('[aria-label="Create"]') as HTMLElement;
      expect(createBtn).toBeTruthy();
      expect(createBtn.getAttribute("aria-current")).toBe("page");
    });

    it("switches to Editor view on rail click, showing an empty state with no active game", () => {
      renderApp();
      fireEvent.click(screen.getByRole("button", { name: "Editor" }));
      const nav = screen.getByRole("navigation", { name: "Game Studio views" });
      const editorBtn = nav.querySelector('[aria-label="Editor"]') as HTMLElement;
      expect(editorBtn.getAttribute("aria-current")).toBe("page");
      expect(screen.getByText(/Create a game or open one from your Library/)).toBeDefined();
    });

    it("switches to Library view on rail click", async () => {
      renderApp();
      fireEvent.click(screen.getByRole("button", { name: "Library" }));
      const nav = screen.getByRole("navigation", { name: "Game Studio views" });
      const libBtn = nav.querySelector('[aria-label="Library"]') as HTMLElement;
      expect(libBtn.getAttribute("aria-current")).toBe("page");
      await waitFor(() => expect(screen.getByText(/No games yet/)).toBeDefined());
    });

    it("switches to Share view on rail click, showing an empty state with no active game", () => {
      renderApp();
      fireEvent.click(screen.getByRole("button", { name: "Share" }));
      const nav = screen.getByRole("navigation", { name: "Game Studio views" });
      const shareBtn = nav.querySelector('[aria-label="Share"]') as HTMLElement;
      expect(shareBtn.getAttribute("aria-current")).toBe("page");
      expect(screen.getByText("Open or create a game first.")).toBeDefined();
    });
  });

  describe("Create view", () => {
    it("shows the prompt textarea", () => {
      renderApp();
      expect(screen.getByLabelText("Game idea")).toBeDefined();
    });

    it("shows the Generate with AI button", () => {
      renderApp();
      expect(screen.getByRole("button", { name: "Generate with AI" })).toBeDefined();
    });

    it("shows the template gallery with every template name", () => {
      renderApp();
      for (const t of TEMPLATES) {
        expect(screen.getByText(t.title)).toBeDefined();
      }
    });
  });

  it("using a template saves it and opens the Editor with its name", async () => {
    renderApp("gamestudio-template-select-test");
    const template = TEMPLATES[2]!;
    const useButton = screen.getByRole("button", {
      name: `Use ${template.title} template`,
    });
    fireEvent.click(useButton);

    await waitFor(() => {
      const nav = screen.getByRole("navigation", { name: "Game Studio views" });
      const editorBtn = nav.querySelector('[aria-label="Editor"]') as HTMLElement;
      expect(editorBtn.getAttribute("aria-current")).toBe("page");
    });
    await waitFor(() => expect(screen.getByRole("heading", { name: template.title })).toBeDefined());
  });
});
