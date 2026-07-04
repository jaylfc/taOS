import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createElement, useEffect, useRef } from "react";
import type { ReactNode } from "react";

// Captures what the last export call saw, so tests can assert the crop is
// independent of the stage's pan/zoom at the moment of capture. Declared via
// vi.hoisted because vi.mock's factory below is hoisted above normal
// top-level statements.
const exportCapture = vi.hoisted(() => ({
  args: undefined as Record<string, unknown> | undefined,
  scaleAtCapture: undefined as { x: number; y: number } | undefined,
  posAtCapture: undefined as { x: number; y: number } | undefined,
}));

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
    // Mirrors the live x/y/scaleX/scaleY props onto the fake node's transform
    // state, the way a real Konva stage reflects prop changes. Runs on every
    // render (no dep array) so DesignView's zoom/pan state stays in sync.
    const transformRef = useRef({ scale: { x: 1, y: 1 }, pos: { x: 0, y: 0 } });
    useEffect(() => {
      transformRef.current = {
        scale: { x: Number(props.scaleX ?? 1), y: Number(props.scaleY ?? 1) },
        pos: { x: Number(props.x ?? 0), y: Number(props.y ?? 0) },
      };
    });

    useEffect(() => {
      const fakeLayer = {
        toDataURL: (opts?: Record<string, unknown>) => {
          exportCapture.args = opts;
          exportCapture.scaleAtCapture = { ...transformRef.current.scale };
          exportCapture.posAtCapture = { ...transformRef.current.pos };
          return "data:image/png;base64,FAKE";
        },
      };
      const fakeStage = {
        findOne: () => fakeLayer,
        container: () => ({ getBoundingClientRect: () => ({ top: 0, left: 0, width: 0, height: 0 }) }),
        getPointerPosition: () => ({ x: 0, y: 0 }),
        position: (value?: { x: number; y: number }) => {
          if (value !== undefined) {
            transformRef.current.pos = value;
            return fakeStage;
          }
          return transformRef.current.pos;
        },
        scale: (value?: { x: number; y: number }) => {
          if (value !== undefined) {
            transformRef.current.scale = value;
            return fakeStage;
          }
          return transformRef.current.scale;
        },
        scaleX: (value?: number) => {
          if (value !== undefined) {
            transformRef.current.scale = { ...transformRef.current.scale, x: value };
            return fakeStage;
          }
          return transformRef.current.scale.x;
        },
        scaleY: (value?: number) => {
          if (value !== undefined) {
            transformRef.current.scale = { ...transformRef.current.scale, y: value };
            return fakeStage;
          }
          return transformRef.current.scale.y;
        },
        batchDraw: () => {},
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
    expect(screen.getByRole("button", { name: "Library" })).toBeDefined();
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

  it("exports the full artboard at a fixed crop regardless of the current zoom/pan", () => {
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Text" }));

    // Zoom to 200% and pan (setZoomCentered re-centers around the stage
    // size, which is a non-zero, non-default fallback in tests), so the
    // artboard is neither at 1:1 scale nor at the stage's origin when we
    // export.
    fireEvent.change(screen.getByLabelText("Zoom level"), { target: { value: "200" } });

    fireEvent.click(screen.getByRole("button", { name: "Export as PNG" }));

    // The crop handed to toDataURL is the artboard's true size at a fixed
    // origin, independent of the zoom/pan that was active on screen.
    expect(exportCapture.args).toEqual({ x: 0, y: 0, width: 1080, height: 1350, pixelRatio: 2 });
    // The stage's transform was reset to identity at the moment of capture.
    expect(exportCapture.scaleAtCapture).toEqual({ x: 1, y: 1 });
    expect(exportCapture.posAtCapture).toEqual({ x: 0, y: 0 });

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

  it("does not delete the selection when Delete/Backspace is pressed while a form control is focused", () => {
    const { container } = renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Text" }));
    const node = container.querySelector('[data-testid="konva-text"][data-id]') as HTMLElement;
    fireEvent.click(node);
    expect(countCanvasElements(container)).toBe(1);

    // The zoom picker is a <select>; Delete/Backspace typed into it must not
    // hijack the canvas selection.
    const zoomSelect = screen.getByLabelText("Zoom level");
    fireEvent.keyDown(zoomSelect, { key: "Delete" });
    fireEvent.keyDown(zoomSelect, { key: "Backspace" });
    expect(countCanvasElements(container)).toBe(1);

    // Delete still works once focus is no longer on a form control.
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

describe("DesignStudioApp persistence", () => {
  const originalResizeObserver = globalThis.ResizeObserver;

  beforeEach(() => {
    globalThis.ResizeObserver = MockResizeObserver as unknown as typeof ResizeObserver;
  });

  afterEach(() => {
    globalThis.ResizeObserver = originalResizeObserver;
    vi.unstubAllGlobals();
  });

  /** Routes fetch by URL/method so a single mock can stand in for the
   *  models probe and the /api/designs persistence endpoints. */
  function stubFetch(overrides: {
    listDesigns?: unknown[];
    getDesignContent?: string;
    onCreate?: (body: { name: string; content: string }) => void;
    onUpdate?: (id: string, body: { name?: string; content?: string }) => void;
  }) {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";

      if (url === "/api/models") {
        return { ok: true, json: async () => ({ models: [] }) } as Response;
      }
      if (url === "/api/designs" && method === "GET") {
        return { ok: true, json: async () => overrides.listDesigns ?? [] } as Response;
      }
      if (url === "/api/designs" && method === "POST") {
        const body = JSON.parse(String(init?.body)) as { name: string; content: string };
        overrides.onCreate?.(body);
        return {
          ok: true,
          json: async () => ({
            id: "design-created",
            name: body.name,
            content: body.content,
            created_at: 1000,
            updated_at: 1000,
          }),
        } as Response;
      }
      if (url.startsWith("/api/designs/") && method === "GET") {
        const id = url.split("/").pop()!;
        return {
          ok: true,
          json: async () => ({
            id,
            name: "My Poster",
            content: overrides.getDesignContent ?? "{}",
            created_at: 1000,
            updated_at: 1000,
          }),
        } as Response;
      }
      if (url.startsWith("/api/designs/") && method === "PUT") {
        const id = url.split("/").pop()!;
        const body = JSON.parse(String(init?.body)) as { name?: string; content?: string };
        overrides.onUpdate?.(id, body);
        return {
          ok: true,
          json: async () => ({
            id,
            name: body.name ?? "My Poster",
            content: body.content ?? "{}",
            created_at: 1000,
            updated_at: 1000,
          }),
        } as Response;
      }
      return { ok: true, json: async () => ({}) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("adding an element marks the design dirty, and Save persists the current canvas", async () => {
    let created: { name: string; content: string } | null = null;
    stubFetch({ onCreate: (body) => (created = body) });

    const { container } = renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Text" }));
    expect(countCanvasElements(container)).toBe(1);
    expect(screen.getByText("Unsaved changes")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: /Save/ }));

    await waitFor(() => expect(screen.queryByText("Unsaved changes")).toBeNull());
    expect(created).not.toBeNull();
    const savedContent = JSON.parse(created!.content) as { elements: unknown[] };
    expect(savedContent.elements.length).toBe(1);
  });

  it("New confirms discard when there are unsaved changes", () => {
    stubFetch({});
    const { container } = renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Text" }));
    expect(countCanvasElements(container)).toBe(1);

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(countCanvasElements(container)).toBe(1);

    confirmSpy.mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "New" }));
    expect(countCanvasElements(container)).toBe(0);
    expect(screen.queryByText("Unsaved changes")).toBeNull();
    confirmSpy.mockRestore();
  });

  it("Library lists saved designs, and opening one hydrates the canvas and name", async () => {
    stubFetch({
      listDesigns: [{ id: "design-1", name: "My Poster", updated_at: 1700000000 }],
      getDesignContent: JSON.stringify({
        artboard: { name: "My Poster", width: 800, height: 600 },
        elements: [
          {
            id: "text-1",
            type: "text",
            x: 0,
            y: 0,
            width: 100,
            height: 40,
            rotation: 0,
            zIndex: 0,
            visible: true,
            text: "Hi",
            fontSize: 20,
            fontFamily: "Inter",
            fill: "#ffffff",
            align: "left",
          },
        ],
      }),
    });

    const { container } = renderApp();
    fireEvent.click(screen.getByRole("button", { name: "Library" }));
    expect(await screen.findByText("My Poster")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Open My Poster" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Design" }).getAttribute("aria-current")).toBe(
        "page",
      ),
    );
    expect(countCanvasElements(container)).toBe(1);
    expect(screen.getByLabelText("Design name")).toHaveValue("My Poster");
  });
});
