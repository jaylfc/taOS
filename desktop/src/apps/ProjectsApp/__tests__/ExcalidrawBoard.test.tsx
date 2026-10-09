import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import { waitFor } from "@testing-library/react";

// Excalidraw is a heavy canvas/worker component jsdom cannot run. Mock it so the
// board's mapping + wiring can be asserted: convertToExcalidrawElements passes
// the skeletons through so we can count them, and Excalidraw records the props
// it was handed.
const mockRef = vi.hoisted(() => {
  return {
    convertToExcalidrawElementsMock: null as ReturnType<typeof vi.fn> | null,
    capturedElements: [] as unknown[],
  };
});

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
import { parseMermaidToExcalidraw } from "@excalidraw/mermaid-to-excalidraw";
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
    vi.mocked(parseMermaidToExcalidraw).mockImplementation(
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
    // Expect convertToExcalidrawElements to have been called at least once
    expect(mockRef.convertToExcalidrawElementsMock).toHaveBeenCalled();
    // Expect that there was a call with exactly three skeletons
    const threeSkeletonCall = mockRef.convertToExcalidrawElementsMock.mock.calls.find(
      call => call[0] && call[0].length === 3
    );
    expect(threeSkeletonCall).toBeDefined();
  });

  it("a ready diagram keeps its z_index position", async () => {
    // Override the mermaid mock to return a known element for this test
    const mockDiagramElement = { id: "diagram-el", type: "rectangle", x: 0, y: 0, width: 100, height: 100 };
    vi.mocked(parseMermaidToExcalidraw).mockResolvedValueOnce({
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
it('a bound label text follows its container', () => {
  // Set up the mock to return skeleton and text element for notes with label
  mockRef.convertToExcalidrawElementsMock.mockImplementation((skeletonList) => {
    console.log('MOCK CALLED with skeletonList:', skeletonList);
    const result = [];
    for (const skel of skeletonList) {
      const taosId = skel.customData?.taos_id;
      // Change the skeleton's id to 'c-'+taosId
      const newSkel = { ...skel, id: taosId ? `c-${taosId}` : skel.id };
      result.push(newSkel);
      // If the skeleton has a label with non-empty text, add a text element
      if (skel.label && skel.label.text && skel.label.text.trim() !== '') {
        const textEl = {
          id: `t-${taosId}`,
          type: 'text',
          containerId: taosId ? `c-${taosId}` : undefined,
        };
        result.push(textEl);
      }
    }
    console.log('MOCK RETURNING:', result);
    return result;
  });

  console.log('mock call count before render:', mockRef.convertToExcalidrawElementsMock.mock.calls.length);
  const { getByTestId } = render(
    <ExcalidrawBoard
      elements={[el({ id: 'n1', kind: 'note', payload: { text: 'hello' } })]}
    />,
  );
  console.log('mock call count after render:', mockRef.convertToExcalidrawElementsMock.mock.calls.length);
  const ex = getByTestId('excalidraw');
  console.log('data-count:', ex.getAttribute('data-count'));
  console.log('capturedElements:', mockRef.capturedElements);
  expect(ex.getAttribute('data-count')).toBe('2');
  // Expect the captured elements to be [container, text] in that order
  expect(mockRef.capturedElements).toHaveLength(2);
  // First element should be the container (note shape)
  expect(mockRef.capturedElements[0]).toHaveProperty('id', 'c-n1');
  expect(mockRef.capturedElements[0]).toHaveProperty('type', 'rectangle');
  // Second element should be the text label
  expect(mockRef.capturedElements[1]).toHaveProperty('id', 't-n1');
  expect(mockRef.capturedElements[1]).toHaveProperty('type', 'text');
  expect(mockRef.capturedElements[1]).toHaveProperty('containerId', 'c-n1');
});
});