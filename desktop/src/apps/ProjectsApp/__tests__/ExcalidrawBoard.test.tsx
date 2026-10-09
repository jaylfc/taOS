import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";

// Excalidraw is a heavy canvas/worker component jsdom cannot run. Mock it so the
// board's mapping + wiring can be asserted: convertToExcalidrawElements passes
// the skeletons through so we can count them, and Excalidraw records the props
// it was handed.
const mockRef = {
  convertToExcalidrawElementsMock: null as ReturnType<typeof vi.fn> | null,
  capturedElements: [] as unknown[],
};
vi.mock("@excalidraw/excalidraw", () => {
  mockRef.convertToExcalidrawElementsMock = vi.fn((els: unknown[]) => els);
  return {
    convertToExcalidrawElements: mockRef.convertToExcalidrawElementsMock,
    Excalidraw: ({
      initialData,
      viewModeEnabled,
      theme,
    }: {
      initialData?: { elements?: unknown[] };
      viewModeEnabled?: boolean;
      theme?: string;
    }) => {
      mockRef.capturedElements = initialData?.elements ?? [];
      return (
        <div
          data-testid="excalidraw"
          data-count={mockRef.capturedElements.length}
          data-viewmode={String(!!viewModeEnabled)}
          data-theme={theme}
        />
      );
    },
  };
});
vi.mock("@excalidraw/excalidraw/index.css", () => ({}));
// Keep the heavy mermaid parser out of jsdom; no test element is a diagram.
vi.mock("@excalidraw/mermaid-to-excalidraw", () => ({
  parseMermaidToExcalidraw: vi.fn(async () => ({ elements: [], files: {} })),
}));

import { ExcalidrawBoard } from "../canvas/ExcalidrawBoard";
import type { CanvasElement } from "../canvas/canvas-api";

function el(over: Partial<CanvasElement>): CanvasElement {
  return {
    id: "e", project_id: "p", kind: "note", author_kind: "user", author_id: "u",
    x: 0, y: 0, w: 80, h: 60, rotation: 0, z_index: 0, payload: {},
    created_at: 0, updated_at: 0, deleted_at: null, ...over,
  };
}

describe("ExcalidrawBoard", () => {
  beforeEach(() => {
    mockRef.capturedElements = [];
    mockRef.convertToExcalidrawElementsMock?.mockClear();
    // Reset the mermaid mock to its default implementation
    vi.mocked(require("@excalidraw/mermaid-to-excalidraw").parseMermaidToExcalidraw).mockImplementation(
      async () => ({ elements: [], files: {} })
    );
  });

  it("maps non-deleted elements into the scene and renders read-only", () => {
    const { getByTestId } = render(
      <ExcalidrawBoard
        theme="dark"
        elements={[
          el({ id: "n1", kind: "note", payload: { text: "a" } }),
          el({ id: "t1", kind: "text", payload: { text: "idea" } }),
          el({ id: "gone", deleted_at: 1 }),
        ]}
      />,
    );
    const ex = getByTestId("excalidraw");
    expect(ex.getAttribute("data-count")).toBe("2");
    expect(ex.getAttribute("data-viewmode")).toBe("true");
    expect(ex.getAttribute("data-theme")).toBe("dark");
  });

  it("defaults to the light theme", () => {
    const { getByTestId } = render(<ExcalidrawBoard elements={[el({})]} />);
    expect(getByTestId("excalidraw").getAttribute("data-theme")).toBe("light");
  });

  it("renders an empty scene without crashing", () => {
    const { getByTestId } = render(<ExcalidrawBoard elements={[]} />);
    expect(getByTestId("excalidraw").getAttribute("data-count")).toBe("0");
  });

  it("mindmap_edge between two notes is converted in the same batch", () => {
    const { getByTestId } = render(
      <ExcalidrawBoard
        elements={[
          el({ id: "n1", kind: "note" }),
          el({ id: "n2", kind: "note" }),
          el({ id: "e1", kind: "mindmap_edge", payload: { start: "n1", end: "n2" } }),
        ]}
      />,
    );
    const ex = getByTestId("excalidraw");
    // Expect three elements in the scene (two notes + one edge)
    expect(ex.getAttribute("data-count")).toBe("3");
    // Expect convertToExcalidrawElements to have been called exactly once
    expect(mockRef.convertToExcalidrawElementsMock).toHaveBeenCalledTimes(1);
    // The call should have received three skeletons
    expect(mockRef.convertToExcalidrawElementsMock).toHaveBeenLastCalledWith(
      expect.arrayContaining([expect.anything(), expect.anything(), expect.anything()]),
    );
    // More precisely, check the length of the argument array
    expect(mockRef.convertToExcalidrawElementsMock.mock.calls[0][0]).toHaveLength(3);
  });

  it("a ready diagram keeps its z_index position", async () => {
    // Override the mermaid mock to return a known element for this test
    const mockDiagramElement = { id: "diagram-el", type: "rectangle", x: 0, y: 0, width: 100, height: 100 };
    vi.mocked(require("@excalidraw/mermaid-to-excalidraw").parseMermaidToExcalidraw).mockResolvedValueOnce({
      elements: [mockDiagramElement],
      files: {},
    });

    const { getByTestId } = render(
      <ExcalidrawBoard
        elements={[
          el({ id: "n1", kind: "note", z_index: 0 }),
          el({ id: "d1", kind: "mermaid", z_index: 1, payload: { source: "graph LR; A-->B;" } }),
          el({ id: "n2", kind: "note", z_index: 2 }),
        ]}
      />,
    );

    // Wait for the diagram conversion to complete
    // We can wait for the data-count to be 3 (note + diagram element + note)
    // Initially, we have two notes and one diagram placeholder (each as a skeleton) -> 3 skeletons.
    // After conversion, the diagram placeholder is replaced by the converted element(s) -> still 3 elements.
    // So we wait for the capturedElements to have length 3 and to contain our known diagram element.
    await waitFor(() => {
      expect(getByTestId("excalidraw").getAttribute("data-count")).toBe("3");
    });

    // Now check that the capturedElements array has the diagram element at index 1 (z_index order: note0, diagram1, note2)
    expect(mockRef.capturedElements).toHaveLength(3);
    // The first element should be the note with z_index 0
    expect(mockRef.capturedElements[0]).toHaveProperty("id", "n1");
    // The second element should be our diagram element
    expect(mockRef.capturedElements[1]).toHaveProperty("id", "diagram-el");
    // The third element should be the note with z_index 2
    expect(mockRef.capturedElements[2]).toHaveProperty("id", "n2");
  });
});

// Helper function to wait for a condition (since we don't have waitFor from testing-library)
// We'll implement a simple polling wait.
function waitFor(condition: () => void) {
  return new Promise<void>((resolve, reject) => {
    const start = Date.now();
    const interval = setInterval(() => {
      try {
        condition();
        clearInterval(interval);
        resolve();
      } catch (e) {
        if (Date.now() - start > 1000) {
          clearInterval(interval);
          reject(e);
        }
      }
    }, 50);
  });
}