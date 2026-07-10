import { describe, it, expect, vi, beforeAll, afterEach } from "vitest";
import { useState } from "react";
import { render, screen, fireEvent } from "@testing-library/react";

/* react-konva needs a real <canvas> (the `canvas` npm package), which is not
 * installed in the jsdom test env. Mock the primitives to inert passthroughs so
 * DesignView's toolbar / layers / properties logic can be exercised without a
 * Konva stage. Because the Stage/Transformer refs are never populated, the
 * canvas-only effects (transformer wiring, PNG export) safely no-op. */
vi.mock("react-konva", () => ({
  Stage: ({ children }: { children?: React.ReactNode }) => <div data-konva="stage">{children}</div>,
  Layer: ({ children }: { children?: React.ReactNode }) => <div data-konva="layer">{children}</div>,
  Rect: () => null,
  Text: () => null,
  Image: () => null,
  Ellipse: () => null,
  Line: () => null,
  Transformer: () => null,
}));

import { DesignView } from "./DesignView";
import { DEFAULT_ARTBOARD, type CanvasElement } from "./types";

/** DesignView is a controlled component: it hands the next elements array back
 * through onElementsChange and re-reads it from props. This host mirrors how
 * DesignStudioApp owns that state, and exposes the latest array for assertions. */
let latest: CanvasElement[] = [];
function Host() {
  const [els, setEls] = useState<CanvasElement[]>([]);
  latest = els;
  return <DesignView elements={els} onElementsChange={setEls} artboard={DEFAULT_ARTBOARD} />;
}

function renderView() {
  return render(<Host />);
}

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  latest = [];
  vi.clearAllMocks();
});

describe("DesignView", () => {
  it("renders the empty state with export/undo/redo disabled", () => {
    renderView();
    expect(screen.getByText(/Add an element or generate with Magic/i)).toBeDefined();
    expect((screen.getByLabelText("Export as PNG") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Undo") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Redo") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/No layers yet/i)).toBeDefined();
  });

  it("keeps the coming-soon Star tile disabled", () => {
    renderView();
    expect((screen.getByLabelText("Star") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Text") as HTMLButtonElement).disabled).toBe(false);
  });

  it("adding a text element updates state and adds a layer, enabling export/undo", () => {
    renderView();
    fireEvent.click(screen.getByLabelText("Text"));

    expect(latest).toHaveLength(1);
    expect(latest[0].type).toBe("text");
    expect(screen.getByLabelText(/Select layer/i)).toBeDefined();
    expect((screen.getByLabelText("Export as PNG") as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByLabelText("Undo") as HTMLButtonElement).disabled).toBe(false);
  });

  it("adding a shape creates a rect and auto-selects it (properties panel appears)", () => {
    renderView();
    fireEvent.click(screen.getByLabelText("Shape"));

    expect(latest[0].type).toBe("rect");
    // Auto-selection surfaces the shape's fill/stroke controls.
    expect(screen.getByLabelText("Fill color")).toBeDefined();
    expect(screen.getByText("Rectangle")).toBeDefined();
  });

  it("adding a circle creates an ellipse", () => {
    renderView();
    fireEvent.click(screen.getByLabelText("Circle"));
    expect(latest[0].type).toBe("ellipse");
  });

  it("adding a line creates a line element", () => {
    renderView();
    fireEvent.click(screen.getByLabelText("Line"));
    expect(latest[0].type).toBe("line");
  });

  it("undo removes a just-added element and returns to the empty state", () => {
    renderView();
    fireEvent.click(screen.getByLabelText("Text"));
    expect(latest).toHaveLength(1);
    fireEvent.click(screen.getByLabelText("Undo"));
    expect(latest).toHaveLength(0);
    expect(screen.getByText(/Add an element or generate with Magic/i)).toBeDefined();
  });

  it("brand swatches are disabled until a fillable element is selected", () => {
    renderView();
    expect((screen.getByLabelText("Apply #6b7689") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByLabelText("Shape"));
    expect((screen.getByLabelText("Apply #6b7689") as HTMLButtonElement).disabled).toBe(false);
  });

  it("exposes zoom controls that fit the artboard to the stage", () => {
    renderView();
    const zoom = screen.getByLabelText("Zoom level") as HTMLSelectElement;
    // The view fits the artboard on mount, so the zoom is a positive percentage.
    expect(Number(zoom.value)).toBeGreaterThan(0);
    expect(screen.getByLabelText("Zoom in")).toBeDefined();
    expect(screen.getByLabelText("Zoom out")).toBeDefined();
    expect(screen.getByLabelText("Fit to screen")).toBeDefined();
  });

  it("shows the artboard name and dimensions in the header", () => {
    renderView();
    expect(screen.getByText(DEFAULT_ARTBOARD.name)).toBeDefined();
    expect(
      screen.getByText(`${DEFAULT_ARTBOARD.width} x ${DEFAULT_ARTBOARD.height}`),
    ).toBeDefined();
  });
});
