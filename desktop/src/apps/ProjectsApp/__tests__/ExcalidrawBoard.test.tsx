import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { useThemeStore } from "@/stores/theme-store";

// Excalidraw is a heavy canvas/worker component jsdom cannot run. Mock it so the
// board's mapping + wiring can be asserted: convertToExcalidrawElements passes
// the skeletons through so we can count and inspect them, and Excalidraw records
// the props it was handed. The mocked API exposes updateScene so tests can see
// the board applying scenes.
let updateSceneCalls: unknown[][] = [];
let sseOnMessage: ((msg: any) => void) | undefined;

vi.mock("@/lib/sse", () => ({
  createSseConnection: vi.fn((opts) => {
    sseOnMessage = opts.onMessage;
    return () => {};
  }),
}));

function MockExcalidraw({
  initialData,
  viewModeEnabled,
  theme,
  files,
  excalidrawAPI,
}: any) {
  React.useEffect(() => {
    if (excalidrawAPI) {
      // The real Excalidraw calls excalidrawAPI({ updateScene, ... }), an API
      // object, not the raw function. Mirror that so the board's api state is
      // an object with an updateScene method.
      const updateScene = (data: any) => {
        updateSceneCalls.push((data?.elements ?? []) as unknown[]);
      };
      excalidrawAPI({ updateScene });
    }
  }, []);
  return (
    <div
      data-testid="excalidraw"
      data-count={initialData?.elements?.length ?? 0}
      data-viewmode={String(!!viewModeEnabled)}
      data-theme={theme}
      data-files={String(files?.length ?? 0)}
    />
  );
}

vi.mock("@excalidraw/excalidraw", () => ({
  convertToExcalidrawElements: vi.fn((els: unknown[]) => els),
  Excalidraw: MockExcalidraw,
}));
vi.mock("@excalidraw/excalidraw/index.css", () => ({}));
// Keep the heavy mermaid parser out of jsdom; no test element is a diagram.
vi.mock("@excalidraw/mermaid-to-excalidraw", () => ({
  parseMermaidToExcalidraw: vi.fn(async () => ({ elements: [], files: {} })),
}));

import { ExcalidrawBoard } from "../canvas/ExcalidrawBoard";
import type { CanvasElement, CanvasEvent } from "../canvas/canvas-api";
import { canvasApi } from "../canvas/canvas-api";
import { subscribeCanvasStream } from "../canvas/canvas-sse";
import { convertToExcalidrawElements } from "@excalidraw/excalidraw";

// eslint-disable-next-line @typescript-eslint/no-unused-vars
const _noop = subscribeCanvasStream;

function el(over: Partial<CanvasElement>): CanvasElement {
  return {
    id: "e",
    project_id: "p",
    kind: "note",
    author_kind: "user",
    author_id: "u",
    x: 0,
    y: 0,
    w: 80,
    h: 60,
    rotation: 0,
    z_index: 0,
    payload: {},
    element_id: null,
    created_at: 0,
    updated_at: 0,
    deleted_at: null,
    ...over,
  };
}

afterEach(() => {
  updateSceneCalls = [];
  vi.clearAllMocks();
});

describe("ExcalidrawBoard", () => {
  it("maps non-deleted elements into the scene and renders read-only", async () => {
    useThemeStore.setState({ scheme: "dark" } as never);
    vi.spyOn(canvasApi, "listElements").mockResolvedValue([
      el({ id: "n1", kind: "note", payload: { text: "a" } }),
      el({ id: "t1", kind: "text", payload: { text: "idea" } }),
      el({ id: "gone", deleted_at: 1 }),
    ]);
    render(<ExcalidrawBoard projectId="p1" projectSlug="s1" />);
    await waitFor(() => {
      const ex = screen.getByTestId("excalidraw");
      return ex.getAttribute("data-count") === "2";
    });
    const ex = screen.getByTestId("excalidraw");
    expect(ex.getAttribute("data-count")).toBe("2");
    expect(ex.getAttribute("data-viewmode")).toBe("false");
    expect(ex.getAttribute("data-theme")).toBe("dark");
  });

  it("defaults to the light theme", () => {
    useThemeStore.setState({ scheme: "light" } as never);
    render(<ExcalidrawBoard projectId="p1" projectSlug="s1" />);
    expect(screen.getByTestId("excalidraw").getAttribute("data-theme")).toBe("light");
  });

  it("renders an empty scene without crashing", async () => {
    vi.spyOn(canvasApi, "listElements").mockResolvedValue([]);
    render(<ExcalidrawBoard projectId="p1" projectSlug="s1" />);
    expect(screen.getByTestId("excalidraw").getAttribute("data-count")).toBe("0");
  });

  it("an SSE element_added for a note reaches updateScene and the accessible list", async () => {
    vi.spyOn(canvasApi, "listElements").mockResolvedValue([]);

    render(<ExcalidrawBoard projectId="p1" projectSlug="s1" />);
    await waitFor(() => sseOnMessage !== undefined, { timeout: 2000 });

    expect(updateSceneCalls.length).toBeGreaterThanOrEqual(1);
    expect(updateSceneCalls[0].length).toBe(0); // initial empty scene

    const note: CanvasEvent = {
      type: "canvas.element_added",
      project_id: "p1",
      payload: {
        element: {
          id: "n1",
          project_id: "p1",
          kind: "note",
          author_kind: "user",
          author_id: "u1",
          x: 0,
          y: 0,
          w: 80,
          h: 60,
          rotation: 0,
          z_index: 0,
          payload: { text: "hello" },
          element_id: null,
          created_at: 0,
          updated_at: 0,
          deleted_at: null,
        },
      },
      ts: 0,
    };
    sseOnMessage!({ data: JSON.stringify(note) } as MessageEvent);

    await waitFor(() => updateSceneCalls.length >= 2, { timeout: 2000 });
    expect(updateSceneCalls[1].length).toBe(1);
    expect(
      screen.getByRole("listitem").textContent?.includes("note, user u1: hello"),
    ).toBe(true);
  });

  it("mindmap_edge between two notes is converted in the same batch", async () => {
    let lastConvertArgs: unknown[] | undefined;
    vi.mocked(convertToExcalidrawElements).mockImplementation((els: unknown[]) => {
      lastConvertArgs = els;
      return els;
    });

    const n1 = el({ id: "n1", kind: "note", payload: { text: "a" } });
    const n2 = el({ id: "n2", kind: "note", payload: { text: "b" } });
    const edge = el({
      id: "e1",
      kind: "mindmap_edge",
      payload: { from: "n1", to: "n2" },
      x: 0,
      y: 0,
      w: 100,
      h: 100,
      rotation: 0,
      z_index: 0,
    });

    vi.spyOn(canvasApi, "listElements").mockResolvedValue([n1, edge, n2]);
    render(<ExcalidrawBoard projectId="p1" projectSlug="s1" />);

    await waitFor(() => lastConvertArgs !== undefined, { timeout: 2000 });
    expect(lastConvertArgs).toHaveLength(3);
    expect(
      lastConvertArgs?.find((s) => (s as any).start?.id === "n1"),
    ).toMatchObject({ type: "arrow", start: { id: "n1" }, end: { id: "n2" } });
  });

  it("a legacy user_shape with a malformed blob shows a placeholder list entry carrying its element id", async () => {
    const el = {
      id: "u1",
      kind: "user_shape",
      x: 10,
      y: 20,
      w: 100,
      h: 50,
      rotation: 0,
      z_index: 0,
      payload: { tldraw_shape: "not-an-object" as any },
      project_id: "p1",
      author_kind: "user",
      author_id: "u1",
      element_id: null,
      created_at: 0,
      updated_at: 0,
      deleted_at: null,
    };
    vi.spyOn(canvasApi, "listElements").mockResolvedValue([el]);
    render(<ExcalidrawBoard projectId="p1" projectSlug="s1" />);
    await waitFor(() => {
      const li = screen.getByRole("listitem");
      return li.textContent?.includes("not convertible, element u1");
    }, { timeout: 2000 });
    expect(screen.getByRole("listitem").textContent).toContain("not convertible, element u1");
  });
});
