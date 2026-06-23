import { describe, it, expect } from "vitest";
import { elementToNode, elementsToNodes } from "../canvas/element-to-konva";
import type { CanvasElement } from "../canvas/canvas-api";

function el(over: Partial<CanvasElement>): CanvasElement {
  return {
    id: "el1",
    project_id: "prj",
    kind: "note",
    author_kind: "user",
    author_id: "u",
    x: 10,
    y: 20,
    w: 100,
    h: 50,
    rotation: 0,
    z_index: 0,
    payload: {},
    created_at: 0,
    updated_at: 0,
    deleted_at: null,
    ...over,
  };
}

describe("elementToNode", () => {
  it("maps a note with its payload + geometry", () => {
    const n = elementToNode(el({ kind: "note", payload: { text: "hi", color: "blue", font_size: 18 } }));
    expect(n).toMatchObject({ type: "note", x: 10, y: 20, w: 100, h: 50, text: "hi", color: "blue", fontSize: 18 });
  });

  it("maps a link", () => {
    const n = elementToNode(el({ kind: "link", payload: { url: "https://x.test", title: "X" } }));
    expect(n).toMatchObject({ type: "link", url: "https://x.test", title: "X" });
  });

  it("maps an image", () => {
    const n = elementToNode(el({ kind: "image", payload: { file_id: "f1", alt: "pic", mime: "image/jpeg" } }));
    expect(n).toMatchObject({ type: "image", fileId: "f1", alt: "pic", mime: "image/jpeg" });
  });

  it("maps user_shape and unknown kinds to a generic shape", () => {
    expect(elementToNode(el({ kind: "user_shape" })).type).toBe("shape");
    expect(elementToNode(el({ kind: "wat" as never })).type).toBe("shape");
  });

  it("maps a text node with its content and defaults", () => {
    const n = elementToNode(el({ kind: "text", payload: { text: "an idea", font_size: 20 } }));
    expect(n).toMatchObject({ type: "text", text: "an idea", fontSize: 20, color: "#1e293b" });
  });

  it("maps mermaid and flowchart to their source", () => {
    expect(elementToNode(el({ kind: "mermaid", payload: { source: "graph TD; A-->B" } })))
      .toMatchObject({ type: "mermaid", source: "graph TD; A-->B" });
    expect(elementToNode(el({ kind: "flowchart", payload: { source: "flowchart LR; a-->b" } })))
      .toMatchObject({ type: "flowchart", source: "flowchart LR; a-->b" });
  });

  it("maps a mindmap_edge to its endpoints", () => {
    const n = elementToNode(el({ kind: "mindmap_edge", payload: { from: "cve-a", to: "cve-b" } }));
    expect(n).toMatchObject({ type: "mindmap_edge", from: "cve-a", to: "cve-b" });
  });

  it("coerces a missing diagram source to empty string", () => {
    expect(elementToNode(el({ kind: "mermaid", payload: {} })))
      .toMatchObject({ type: "mermaid", source: "" });
  });

  it("coerces malformed geometry + payload to defaults instead of crashing", () => {
    const n = elementToNode(el({
      kind: "note",
      x: "nope" as never,
      w: NaN as never,
      z_index: undefined as never,
      payload: { font_size: "14" },
    }));
    expect(n).toMatchObject({ type: "note", x: 0, w: 100, zIndex: 0, fontSize: 14 });
  });
});

describe("elementsToNodes", () => {
  it("drops soft-deleted elements and sorts by z_index ascending", () => {
    const nodes = elementsToNodes([
      el({ id: "a", z_index: 2 }),
      el({ id: "b", z_index: 1 }),
      el({ id: "gone", deleted_at: 123 }),
    ]);
    expect(nodes.map((n) => n.id)).toEqual(["b", "a"]);
  });
});
