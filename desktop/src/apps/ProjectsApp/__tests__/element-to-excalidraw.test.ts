import { describe, it, expect } from "vitest";
import { elementToSkeleton, elementsToSkeletons } from "../canvas/element-to-excalidraw";
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

describe("elementToSkeleton", () => {
  it("maps a note to a rectangle with a coloured background + label", () => {
    const s = elementToSkeleton(el({ kind: "note", payload: { text: "hi", color: "blue", font_size: 18 } }));
    expect(s).toMatchObject({
      type: "rectangle",
      id: "el1",
      x: 10,
      y: 20,
      width: 100,
      height: 50,
      backgroundColor: "#a5d8ff",
      label: { text: "hi", fontSize: 18 },
    });
  });

  it("falls back to the yellow note background for an unknown colour", () => {
    const s = elementToSkeleton(el({ kind: "note", payload: { color: "chartreuse" } }));
    expect(s).toMatchObject({ type: "rectangle", backgroundColor: "#ffec99" });
  });

  it("maps a link to a rectangle labelled with its title", () => {
    const s = elementToSkeleton(el({ kind: "link", payload: { url: "https://x.test", title: "X" } }));
    expect(s).toMatchObject({ type: "rectangle", label: { text: "X" } });
  });

  it("labels a link with the url when it has no title", () => {
    const s = elementToSkeleton(el({ kind: "link", payload: { url: "https://x.test" } }));
    expect(s).toMatchObject({ type: "rectangle", label: { text: "https://x.test" } });
  });

  it("maps an image to an image skeleton carrying the file id", () => {
    const s = elementToSkeleton(el({ kind: "image", payload: { file_id: "f1", alt: "pic" } }));
    expect(s).toMatchObject({ type: "image", fileId: "f1" });
  });

  it("maps a text element with its font + colour", () => {
    const s = elementToSkeleton(el({ kind: "text", payload: { text: "idea", font_size: 22, color: "#0f172a" } }));
    expect(s).toMatchObject({ type: "text", text: "idea", fontSize: 22, strokeColor: "#0f172a" });
  });

  it("maps mermaid/flowchart to a rectangle labelled with the first source line", () => {
    const m = elementToSkeleton(el({ kind: "mermaid", payload: { source: "\ngraph TD\n  A-->B" } }));
    expect(m).toMatchObject({ type: "rectangle", label: { text: "graph TD" } });
    const f = elementToSkeleton(el({ kind: "flowchart", payload: { source: "flowchart LR" } }));
    expect(f).toMatchObject({ type: "rectangle", label: { text: "flowchart LR" } });
  });

  it("maps a mindmap_edge to an arrow bound to the from/to ids", () => {
    const s = elementToSkeleton(el({ kind: "mindmap_edge", payload: { from: "a", to: "b" } }));
    expect(s).toMatchObject({ type: "arrow", start: { id: "a" }, end: { id: "b" } });
  });

  it("omits an arrow binding whose id is missing instead of binding to an empty id", () => {
    const s = elementToSkeleton(el({ kind: "mindmap_edge", payload: { from: "a" } })) as {
      type: string;
      start?: unknown;
      end?: unknown;
    };
    expect(s.type).toBe("arrow");
    expect(s.start).toEqual({ id: "a" });
    expect(s.end).toBeUndefined();
  });

  it("maps an unknown kind to a generic rectangle", () => {
    const s = elementToSkeleton(el({ kind: "user_shape" as CanvasElement["kind"] }));
    expect(s.type).toBe("rectangle");
  });

  it("coerces malformed geometry to defaults instead of crashing", () => {
    const s = elementToSkeleton(
      el({ x: NaN as unknown as number, w: undefined as unknown as number, rotation: "x" as unknown as number }),
    );
    expect(s).toMatchObject({ x: 0, width: 100, angle: 0 });
  });
});

describe("elementsToSkeletons", () => {
  it("drops soft-deleted elements and sorts by z_index ascending", () => {
    const out = elementsToSkeletons([
      el({ id: "top", z_index: 5 }),
      el({ id: "gone", deleted_at: 123 }),
      el({ id: "bottom", z_index: 1 }),
    ]);
    expect(out.map((s) => s.id)).toEqual(["bottom", "top"]);
  });
});
