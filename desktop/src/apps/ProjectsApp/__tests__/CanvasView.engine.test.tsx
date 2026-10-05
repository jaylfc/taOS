import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// Both engines are stubbed to plain divs so the engine swap decision is asserted
// without loading tldraw or Excalidraw. The stub divs carry testids used by the
// assertions below.
vi.mock("../canvas/ExcalidrawBoard", () => ({
  default: () => <div data-testid="excalidraw-board-mock" />,
  ExcalidrawBoard: () => <div data-testid="excalidraw-board-mock" />,
}));
vi.mock("../canvas/CanvasBoard", () => ({
  default: () => <div data-testid="canvas-board-mock" />,
  CanvasBoard: () => <div data-testid="canvas-board-mock" />,
}));

import { CanvasView } from "../canvas/CanvasView";

describe("CanvasView engine switching", () => {
  afterEach(() => {
    localStorage.removeItem("taos.canvas.engine");
    Object.defineProperty(window, "location", {
      value: { href: "http://localhost:5173/" },
      writable: true,
      configurable: true,
    });
  });

  it("renders ExcalidrawBoard when ?canvas=excalidraw", () => {
    const original = window.location;
    Object.defineProperty(window, "location", {
      value: new URL("http://localhost:5173/?canvas=excalidraw"),
      writable: true,
      configurable: true,
    });
    render(<CanvasView projectId="p1" projectSlug="s1" />);
    expect(screen.getByTestId("excalidraw-board-mock")).toBeInTheDocument();
    expect(screen.queryByTestId("canvas-board-mock")).toBeNull();
    window.location = original;
  });

  it("renders CanvasBoard by default", () => {
    render(<CanvasView projectId="p1" projectSlug="s1" />);
    expect(screen.getByTestId("canvas-board-mock")).toBeInTheDocument();
  });
});
