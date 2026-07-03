import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createElement, useEffect } from "react";
import type { ReactNode } from "react";

// react-konva renders to a real <canvas> 2D context, which jsdom does not
// implement. Following the project's established pattern for canvas-heavy
// libraries (see ProjectsApp/__tests__/ExcalidrawBoard.test.tsx), mock the
// library with lightweight stand-ins so the surrounding wiring (add/select/
// undo/redo/export) can be exercised without a real canvas.
vi.mock("react-konva", () => {
  type MockProps = Record<string, unknown> & {
    ref?: ((node: unknown) => void) | { current: unknown };
    id?: string;
    name?: string;
    text?: string;
    onClick?: () => void;
    onDblClick?: () => void;
    children?: ReactNode;
  };

  function attachRef(ref: MockProps["ref"], node: unknown) {
    if (!ref) return;
    if (typeof ref === "function") ref(node);
    else ref.current = node;
  }

  function makeShape(testId: string) {
    return function MockShape(props: MockProps) {
      useEffect(() => {
        let shadowEnabled = false;
        const node = {
          x: () => Number(props.x ?? 0),
          y: () => Number(props.y ?? 0),
          width: () => Number(props.width ?? 0),
          height: () => Number(props.height ?? 0),
          rotation: () => Number(props.rotation ?? 0),
          scaleX: () => 1,
          scaleY: () => 1,
          position: () => ({ x: Number(props.x ?? 0), y: Number(props.y ?? 0) }),
          getAbsolutePosition: () => ({ x: Number(props.x ?? 0), y: Number(props.y ?? 0) }),
          getAbsoluteScale: () => ({ x: 1, y: 1 }),
          shadowEnabled: (value?: boolean) => {
            if (value !== undefined) shadowEnabled = value;
            return shadowEnabled;
          },
        };
        attachRef(props.ref, node);
        return () => attachRef(props.ref, null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, []);
      return createElement("div", {
        "data-testid": testId,
        "data-id": props.id,
        "data-name": props.name,
        onClick: props.onClick,
        onDoubleClick: props.onDblClick,
      });
    };
  }

  function Stage(props: MockProps) {
    useEffect(() => {
      const fakeLayer = { toDataURL: () => "data:image/png;base64,FAKE" };
      const fakeStage = {
        findOne: () => fakeLayer,
        container: () => ({ getBoundingClientRect: () => ({ top: 0, left: 0, width: 0, height: 0 }) }),
        getPointerPosition: () => ({ x: 0, y: 0 }),
        position: () => ({ x: 0, y: 0 }),
        getStage: () => fakeStage,
      };
      attachRef(props.ref, fakeStage);
      return () => attachRef(props.ref, null);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return createElement("div", { "data-testid": "konva-stage" }, props.children);
  }

  function Layer(props: MockProps) {
    return createElement("div", { "data-testid": "konva-layer" }, props.children);
  }

  function Transformer(props: MockProps) {
    useEffect(() => {
      const fakeTransformer = {
        nodes: () => {},
        enabledAnchors: () => {},
        getLayer: () => ({ batchDraw: () => {} }),
      };
      attachRef(props.ref, fakeTransformer);
      return () => attachRef(props.ref, null);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return null;
  }

  return {
    Stage,
    Layer,
    Rect: makeShape("konva-rect"),
    Text: makeShape("konva-text"),
    Ellipse: makeShape("konva-ellipse"),
    Line: makeShape("konva-line"),
    Image: makeShape("konva-image"),
    Transformer,
  };
});

// jsdom has no layout engine, so containers always report a 0 clientWidth /
// clientHeight and never trigger a ResizeObserver callback. DesignView
// tolerates that (it keeps a non-zero fallback stage size), but jsdom also
// has no ResizeObserver at all, so stub one out.
class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

import { DesignStudioApp } from "./DesignStudioApp";

function renderApp() {
  return render(<DesignStudioApp windowId="test-window" />);
}

/** Real canvas elements carry a data-id; the artboard background rect does not. */
function countCanvasElements(container: HTMLElement): number {
  return container.querySelectorAll('[data-testid^="konva-"][data-id]').length;
}

describe("DesignStudioApp", () => {
  const originalResizeObserver = globalThis.ResizeObserver;

  beforeEach(() => {
    globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver;
  });

  afterEach(() => {
    globalThis.ResizeObserver = originalResizeObserver;
  });

  it("renders all rail items", () => {
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Design Studio views" });
    expect(nav).toBeDefined();
    expect(screen.getByRole("button", { name: "Design" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Templates" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Elements" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Magic" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Brand" })).toBeDefined();
  });

  it("shows Design view by default with Design rail item active", () => {
    renderApp();
    const nav = screen.getByRole("navigation", { name: "Design Studio views" });
    const designBtn = nav.querySelector('[aria-label="Design"]') as HTMLElement;
    expect(designBtn).toBeTruthy();
    expect(designBtn.getAttribute("aria-current")).toBe("page");
  });

  it("default Design view renders the canvas artboard, empty", () => {
    renderApp();
    expect(screen.getByText("Untitled poster")).toBeDefined();
    expect(screen.getByText("Add an element or generate with Magic")).toBeDefined();
  });

  it("Undo and Export are disabled when the canvas is empty", () => {
    renderApp();
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Export as PNG" })).toBeDisabled();
  });

  it("adds a text element from the toolbar", () => {
    const { container } = renderApp();
    expect(countCanvasElements(container)).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Text" }));
    expect(countCanvasElements(container)).toBe(1);
    expect(screen.queryByText("Add an element or generate with Magic")).toBeNull();
    expect(screen.getByRole("button", { name: "Undo" })).not.toBeDisabled();
  });

  it("selecting an element shows its properties panel", () => {
    const { container } = renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Shape" }));
    const node = container.querySelector('[data-testid="konva-rect"][data-id]') as HTMLElement;
    expect(node).toBeTruthy();
    fireEvent.click(node);
    expect(screen.getByText("Selection")).toBeDefined();
    expect(screen.getByLabelText("Fill color")).toBeDefined();
  });

  it("undo and redo change the element count", () => {
    const { container } = renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Text" }));
    fireEvent.click(screen.getByRole("button", { name: "Shape" }));
    expect(countCanvasElements(container)).toBe(2);

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(countCanvasElements(container)).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(countCanvasElements(container)).toBe(0);
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Redo" }));
    expect(countCanvasElements(container)).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "Redo" }));
    expect(countCanvasElements(container)).toBe(2);
    expect(screen.getByRole("button", { name: "Redo" })).toBeDisabled();
  });

  it("exports a PNG by driving the stage's toDataURL through an anchor download", () => {
    let capturedHref = "";
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        capturedHref = this.href;
      });

    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Text" }));
    const exportBtn = screen.getByRole("button", { name: "Export as PNG" });
    expect(exportBtn).not.toBeDisabled();
    fireEvent.click(exportBtn);

    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(capturedHref.startsWith("data:image/png")).toBe(true);
    clickSpy.mockRestore();
  });

  it("duplicates the selected element with Cmd/Ctrl+D", () => {
    const { container } = renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Text" }));
    const node = container.querySelector('[data-testid="konva-text"][data-id]') as HTMLElement;
    fireEvent.click(node);
    expect(countCanvasElements(container)).toBe(1);
    fireEvent.keyDown(window, { key: "d", ctrlKey: true });
    expect(countCanvasElements(container)).toBe(2);
  });

  it("deletes the selected element with the Delete key", () => {
    const { container } = renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Text" }));
    const node = container.querySelector('[data-testid="konva-text"][data-id]') as HTMLElement;
    fireEvent.click(node);
    fireEvent.keyDown(window, { key: "Delete" });
    expect(countCanvasElements(container)).toBe(0);
  });

  it("switches to Templates view and shows template cards", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Templates" }));
    expect(screen.getByRole("button", { name: "Templates" }).getAttribute("aria-current")).toBe(
      "page",
    );
    const expectedCards = [
      "Instagram Post",
      "Story",
      "Poster",
      "Presentation",
      "Logo",
      "Flyer",
      "Banner",
      "Business Card",
    ];
    for (const name of expectedCards) {
      expect(screen.getByText(name)).toBeDefined();
    }
  });

  it("picking a template switches to Design view with a clean canvas at the template size", () => {
    // The canvas is non-empty, so picking a template asks to confirm the reset.
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { container } = renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Text" }));
    expect(countCanvasElements(container)).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "Templates" }));
    fireEvent.click(screen.getByText("Instagram Post"));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    const nav = screen.getByRole("navigation", { name: "Design Studio views" });
    const designBtn = nav.querySelector('[aria-label="Design"]') as HTMLElement;
    expect(designBtn.getAttribute("aria-current")).toBe("page");
    expect(screen.getByText("Instagram Post")).toBeDefined();
    expect(screen.getByText("1080 x 1080")).toBeDefined();
    expect(countCanvasElements(container)).toBe(0);
    confirmSpy.mockRestore();
  });

  it("keeps the current design when a template reset is cancelled", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { container } = renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Text" }));
    expect(countCanvasElements(container)).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "Templates" }));
    fireEvent.click(screen.getByText("Instagram Post"));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    // Still on Templates, and the design is untouched.
    expect(
      screen.getByRole("button", { name: "Templates" }).getAttribute("aria-current"),
    ).toBe("page");
    fireEvent.click(screen.getByRole("button", { name: "Design" }));
    expect(countCanvasElements(container)).toBe(1);
    confirmSpy.mockRestore();
  });

  it("switches to Magic view and shows prompt bar and style chips", () => {
    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Magic" }));
    expect(screen.getByRole("button", { name: "Magic" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByText("Describe the design you need.")).toBeDefined();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDefined();
    expect(screen.getByPlaceholderText(/launch poster/i)).toBeDefined();
    expect(screen.getAllByText("Editorial").length).toBeGreaterThan(0);
  });
});
