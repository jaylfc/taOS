import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";

// Excalidraw is a heavy canvas/worker component jsdom cannot run. Mock it so the
// board's mapping + wiring can be asserted: convertToExcalidrawElements passes
// the skeletons through so we can count them, and Excalidraw records the props
// it was handed.
vi.mock("@excalidraw/excalidraw", () => ({
  convertToExcalidrawElements: (els: unknown[]) => els,
  Excalidraw: ({
    initialData,
    viewModeEnabled,
    theme,
  }: {
    initialData?: { elements?: unknown[] };
    viewModeEnabled?: boolean;
    theme?: string;
  }) => (
    <div
      data-testid="excalidraw"
      data-count={initialData?.elements?.length ?? 0}
      data-viewmode={String(!!viewModeEnabled)}
      data-theme={theme}
    />
  ),
}));
vi.mock("@excalidraw/excalidraw/index.css", () => ({}));

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
});
