import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { GameStudioApp } from "./GameStudioApp";
import { TEMPLATES } from "./gamestudio/templates";
import { GENRES, OFFLINE_MODELS } from "./gamestudio/types";

// PlayView mounts a real three.js WebGLRenderer via useGameScene. JSDOM has no
// WebGL context, so the hook is replaced with an inert stand-in — the same
// approach sibling suites use to stub out heavy children (see
// ProjectWorkspace.mobile.test.tsx mocking FilesApp/MessagesApp). This keeps
// the test on the render contract (does selecting a template route to Play
// and show it) without ever touching a canvas/WebGL context.
vi.mock("./gamestudio/useGameScene", () => ({
  useGameScene: () => ({
    hostRef: { current: null },
    playing: false,
    setPlaying: vi.fn(),
    togglePlay: vi.fn(),
    fps: 60,
    reset: vi.fn(),
    supported: true,
  }),
}));

function renderApp(windowId = "test-window") {
  return render(<GameStudioApp windowId={windowId} />);
}

describe("GameStudioApp", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({ ok: true, json: () => Promise.resolve([]) }),
      ) as unknown as typeof fetch,
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("navigation rail", () => {
    it("renders Create, Play and Share", () => {
      renderApp();
      const nav = screen.getByRole("navigation", { name: "Game Studio views" });
      expect(nav).toBeDefined();
      expect(screen.getByRole("button", { name: "Create" })).toBeDefined();
      expect(screen.getByRole("button", { name: "Play" })).toBeDefined();
      expect(screen.getByRole("button", { name: "Share" })).toBeDefined();
    });

    it("shows Create view by default with Create rail item active", () => {
      renderApp();
      const nav = screen.getByRole("navigation", { name: "Game Studio views" });
      const createBtn = nav.querySelector('[aria-label="Create"]') as HTMLElement;
      expect(createBtn).toBeTruthy();
      expect(createBtn.getAttribute("aria-current")).toBe("page");
    });

    it("switches to Play view on rail click", () => {
      renderApp();
      fireEvent.click(screen.getByRole("button", { name: "Play" }));
      const nav = screen.getByRole("navigation", { name: "Game Studio views" });
      const playBtn = nav.querySelector('[aria-label="Play"]') as HTMLElement;
      expect(playBtn.getAttribute("aria-current")).toBe("page");
      // The Play stage renders the default template's heading once active.
      expect(screen.getByRole("heading", { name: TEMPLATES[0]!.title })).toBeDefined();
    });

    it("switches to Share view on rail click", () => {
      renderApp();
      fireEvent.click(screen.getByRole("button", { name: "Share" }));
      const nav = screen.getByRole("navigation", { name: "Game Studio views" });
      const shareBtn = nav.querySelector('[aria-label="Share"]') as HTMLElement;
      expect(shareBtn.getAttribute("aria-current")).toBe("page");
      expect(screen.getByRole("heading", { name: "Publish to the Store" })).toBeDefined();
    });
  });

  describe("Create view", () => {
    it("shows the prompt textarea", () => {
      renderApp();
      expect(screen.getByLabelText("Game idea")).toBeDefined();
    });

    it("renders all genre chips", () => {
      renderApp();
      for (const g of GENRES) {
        expect(screen.getByRole("button", { name: g })).toBeDefined();
      }
    });

    it("shows the offline model select with all model options", () => {
      renderApp();
      expect(screen.getByText("Model (offline)")).toBeDefined();
      for (const m of OFFLINE_MODELS) {
        expect(screen.getByText(m)).toBeDefined();
      }
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

  it("selecting a template from the gallery routes to Play and loads its scene", () => {
    renderApp("gamestudio-template-select-test");
    const template = TEMPLATES[2]!; // pick a non-default template so the routing is provable
    // Each card's Use button carries a unique accessible name
    // (`Use <title> template`), so we can target this template's button by role
    // and name alone. That decouples the test from the card's DOM shape and
    // Tailwind classes, and the per-template name is unambiguous, so the earlier
    // getByRole ambiguity (many identical "Use" buttons) no longer applies.
    const useButton = screen.getByRole("button", {
      name: `Use ${template.title} template`,
    });
    fireEvent.click(useButton);

    const nav = screen.getByRole("navigation", { name: "Game Studio views" });
    const playBtn = nav.querySelector('[aria-label="Play"]') as HTMLElement;
    expect(playBtn.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("heading", { name: template.title })).toBeDefined();
  });

  describe("Share view", () => {
    it("renders the publish form with the template preloaded", () => {
      renderApp();
      fireEvent.click(screen.getByRole("button", { name: "Share" }));
      expect(screen.getByRole("heading", { name: "Publish to the Store" })).toBeDefined();
      expect(screen.getByText("Game title")).toBeDefined();
      // The title field defaults to the active template's title.
      expect(screen.getByDisplayValue(TEMPLATES[0]!.title)).toBeDefined();
    });

    it("keeps publish inert and pins the coming-soon note after Share to Store", () => {
      renderApp();
      fireEvent.click(screen.getByRole("button", { name: "Share" }));

      // The publish flow never actually calls out to a Store backend in
      // this phase: clicking only reveals an honest "coming soon" notice,
      // it never fetches or reports success.
      expect(screen.queryByText(/Publishing flows through the Store, coming soon\./)).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Share to Store" }));

      expect(screen.getByText(/Publishing flows through the Store, coming soon\./)).toBeDefined();
      expect(
        screen.getByText(/A later phase packages the game and submits it to your taOS Store/),
      ).toBeDefined();
      expect(fetch).not.toHaveBeenCalled();
    });
  });
});
