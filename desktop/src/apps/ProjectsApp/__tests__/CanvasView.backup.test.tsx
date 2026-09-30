import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";

// The drawing engine (tldraw today, Excalidraw next) cannot run in jsdom, and the
// backup control must not depend on it at all: it has to survive the engine swap
// and the tldraw removal. Stub the engine to a plain div.
vi.mock("../canvas/CanvasBoard", () => ({
  CanvasBoard: () => <div data-testid="engine-stub" />,
}));

import { CanvasView } from "../canvas/CanvasView";
import { canvasApi } from "../canvas/canvas-api";

afterEach(cleanup);

function openBackupMenu() {
  fireEvent.click(screen.getByRole("button", { name: /canvas backup/i }));
}

describe("CanvasView backup downloads", () => {
  it("offers the .tldr recovery file as a download", () => {
    render(<CanvasView projectId="p1" projectSlug="s1" />);
    openBackupMenu();
    const tldr = screen.getByRole("link", { name: /download \.tldr/i });
    expect(tldr.getAttribute("href")).toBe("/api/projects/p1/canvas/snapshot.tldr");
    expect(tldr.hasAttribute("download")).toBe(true);
  });

  it("offers the raw element rows as a .json download named after the project", () => {
    render(<CanvasView projectId="p1" projectSlug="s1" />);
    openBackupMenu();
    const raw = screen.getByRole("link", { name: /download raw elements/i });
    expect(raw.getAttribute("href")).toBe("/api/projects/p1/canvas/elements?include_deleted=true");
    expect(raw.getAttribute("download")).toBe("s1-canvas.json");
  });

  it("lives outside the engine, so it renders even when the engine is replaced", () => {
    render(<CanvasView projectId="p1" projectSlug="s1" />);
    const engine = screen.getByTestId("engine-stub");
    const button = screen.getByRole("button", { name: /canvas backup/i });
    expect(engine.contains(button)).toBe(false);
  });

  it("is a keyboard-operable disclosure that Escape closes", () => {
    render(<CanvasView projectId="p1" projectSlug="s1" />);
    const button = screen.getByRole("button", { name: /canvas backup/i });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("link", { name: /download \.tldr/i })).toBeNull();

    openBackupMenu();
    expect(button.getAttribute("aria-expanded")).toBe("true");
    const tldr = screen.getByRole("link", { name: /download \.tldr/i });

    fireEvent.keyDown(tldr, { key: "Escape" });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("link", { name: /download \.tldr/i })).toBeNull();
    expect(document.activeElement).toBe(button);
  });

  it("builds the download URLs in canvas-api", () => {
    expect(canvasApi.snapshotTldrUrl("prj 1")).toBe("/api/projects/prj%201/canvas/snapshot.tldr");
    expect(canvasApi.elementsJsonUrl("prj 1")).toBe("/api/projects/prj%201/canvas/elements?include_deleted=true");
  });
});
