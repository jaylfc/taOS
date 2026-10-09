import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { CanvasView } from "../canvas/CanvasView";

// Mock the canvas-engine module to control getCanvasEngine
vi.mock("../canvas/canvas-engine", () => ({
  getCanvasEngine: vi.fn(),
}));

// Stub the engine components to plain divs with data-testid
vi.mock("../canvas/CanvasBoard", () => ({
  CanvasBoard: () => <div data-testid="canvas-board-stub" />,
}));
vi.mock("../canvas/ExcalidrawCanvas", () => ({
  default: () => <div data-testid="excalidraw-canvas-stub" />,
}));

import { getCanvasEngine } from "../canvas/canvas-engine";

afterEach(() => {
  vi.resetAllMocks();
});

describe("CanvasView engine switching", () => {
  beforeEach(() => {
    // Clear URL search params before each test
    window.history.replaceState({}, "", "/");
  });

   it("renders CanvasBoard by default", async () => {
     (getCanvasEngine as vi.Mock).mockReturnValue("tldraw");
     
     render(<CanvasView projectId="p1" projectSlug="s1" />);
     
     // Should render CanvasBoard stub
     expect(await screen.findByTestId("canvas-board-stub")).toBeInTheDocument();
     // Should not render ExcalidrawCanvas stub
     expect(await screen.queryByTestId("excalidraw-canvas-stub")).not.toBeInTheDocument();
   });

   it("renders ExcalidrawCanvas when ?canvas=excalidraw", async () => {
     (getCanvasEngine as vi.Mock).mockReturnValue("excalidraw");
     
     render(<CanvasView projectId="p1" projectSlug="s1" />);
     
     // Should render ExcalidrawCanvas stub
     expect(await screen.findByTestId("excalidraw-canvas-stub")).toBeInTheDocument();
     // Should not render CanvasBoard stub
     expect(await screen.queryByTestId("canvas-board-stub")).not.toBeInTheDocument();
   });
});
