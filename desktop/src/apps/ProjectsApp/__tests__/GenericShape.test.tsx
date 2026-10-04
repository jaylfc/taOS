import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { elementToShape, genericPlaceholder } from "../canvas/element-to-shape";
import { TaosGenericShapeUtil } from "../canvas/shapes/GenericShape";
import { CanvasElement } from "../canvas/canvas-api";

function makeElement(over: Partial<CanvasElement>): CanvasElement {
  return {
    id: "cve-1",
    project_id: "p",
    kind: "mermaid",
    author_kind: "agent",
    author_id: "a",
    x: 0, y: 0, w: 200, h: 120, rotation: 0, z_index: 0,
    payload: {},
    created_at: 0, updated_at: 0, deleted_at: null,
    ...over,
  };
}

// component() does not touch the editor, so render it unbound from a
// shape built by the real elementToShape mapping.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function renderShape(shape: any) {
  const el = TaosGenericShapeUtil.prototype.component.call({} as never, shape);
  return render(el);
}

describe("genericPlaceholder", () => {
  it("labels a mermaid element with the first non-empty source line", () => {
    expect(genericPlaceholder("mermaid", { source: "\n  graph TD\n  A-->B" })).toEqual({
      label: "graph TD",
      badge: "mermaid",
    });
  });
  it("falls back to the kind name when the source is missing or blank", () => {
    expect(genericPlaceholder("mermaid", {})).toEqual({ label: "mermaid", badge: "mermaid" });
    expect(genericPlaceholder("flowchart", { source: "  \n " })).toEqual({
      label: "flowchart",
      badge: "flowchart",
    });
  });
  it("returns null for non-diagram kinds", () => {
    expect(genericPlaceholder("user_shape", { source: "graph TD" })).toBeNull();
    expect(genericPlaceholder(undefined, undefined)).toBeNull();
  });
});

describe("TaosGenericShapeUtil placeholder", () => {
  it("renders a mermaid element's label and a mermaid badge, not a blank box", () => {
    const shape = elementToShape(
      makeElement({ payload: { source: "sequenceDiagram\n  A->>B: hi" } }),
      "slug",
    );
    const { getByTestId } = renderShape(shape);
    expect(getByTestId("generic-shape-label").textContent).toBe("sequenceDiagram");
    expect(getByTestId("generic-shape-badge").textContent).toBe("mermaid");
  });

  it("stays an unlabelled box for other generic kinds", () => {
    const shape = elementToShape(makeElement({ kind: "user_shape", payload: {} }), "slug");
    const { queryByTestId } = renderShape(shape);
    expect(queryByTestId("generic-shape-label")).toBeNull();
    expect(queryByTestId("generic-shape-badge")).toBeNull();
  });
});
