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
    const s = elementToSkeleton(el({ kind: "bogus_kind" as CanvasElement["kind"] }));
    expect(s.type).toBe("rectangle");
  });

  it("passes a user_shape's excalidraw_element through untouched (new rows drawn in Excalidraw)", () => {
    const native = {
      id: "el1",
      type: "ellipse",
      x: 1,
      y: 2,
      width: 30,
      height: 40,
      angle: 0.25,
      strokeColor: "#123456",
      customData: { taos_id: "el1" },
    };
    const s = elementToSkeleton(el({ kind: "user_shape", payload: { excalidraw_element: native } }));
    expect(s).toMatchObject({ type: "ellipse", x: 1, y: 2, width: 30, height: 40, angle: 0.25, strokeColor: "#123456" });
    expect(s.customData).toMatchObject({ taos_id: "el1", taos_kind: "user_shape" });
  });

  it("converts a legacy user_shape (tldraw_shape only) through the tldraw converter", () => {
    const s = elementToSkeleton(
      el({
        kind: "user_shape",
        x: 300,
        y: 400,
        w: 250,
        h: 80,
        payload: {
          tldraw_shape: {
            type: "geo",
            x: 10,
            y: 20,
            props: { geo: "ellipse", w: 100, h: 100, color: "blue", fill: "none", dash: "draw", size: "m" },
          },
        },
      }),
    );
    expect(s).toMatchObject({ type: "ellipse", x: 300, y: 400, width: 250, height: 80, strokeColor: "#4465e9" });
  });

  it("prefers excalidraw_element over tldraw_shape when a legacy row has been edited in Excalidraw", () => {
    const s = elementToSkeleton(
      el({
        kind: "user_shape",
        payload: {
          tldraw_shape: { type: "geo", props: { geo: "rectangle" } },
          excalidraw_element: { id: "el1", type: "diamond", x: 0, y: 0, width: 9, height: 9 },
        },
      }),
    );
    expect(s.type).toBe("diamond");
  });

  it("turns a user_shape with neither blob into a visible placeholder carrying the row id", () => {
    const s = elementToSkeleton(el({ id: "u9", kind: "user_shape", payload: {} }));
    expect(s.type).toBe("rectangle");
    expect(s.customData).toMatchObject({ taos_placeholder: true, taos_original_element_id: "u9", taos_id: "u9" });
  });

  it("stamps customData {taos_id, taos_kind, taos_author_id, taos_author_kind} on every skeleton", () => {
    const kinds: CanvasElement["kind"][] = ["note", "link", "image", "text", "mermaid", "flowchart", "mindmap_edge", "user_shape"];
    for (const kind of kinds) {
      const s = elementToSkeleton(el({ id: `id-${kind}`, kind, author_id: "agent-7", author_kind: "agent" }));
      expect(s.customData, kind).toMatchObject({
        taos_id: `id-${kind}`,
        taos_kind: kind,
        taos_author_id: "agent-7",
        taos_author_kind: "agent",
      });
    }
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
