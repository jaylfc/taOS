import { describe, it, expect } from "vitest";
import { elementToShape, shapeType } from "../canvas/element-to-shape";
import { CanvasElement } from "../canvas/canvas-api";

function makeElement(over: Partial<CanvasElement>): CanvasElement {
  return {
    id: "cve-1",
    project_id: "p",
    kind: "note",
    author_kind: "user",
    author_id: "u",
    x: 10, y: 20, w: 100, h: 50, rotation: 0, z_index: 0,
    payload: {},
    created_at: 0, updated_at: 0, deleted_at: null,
    ...over,
  };
}

describe("shapeType", () => {
  it("maps known kinds to custom shape types", () => {
    expect(shapeType("note")).toBe("taos-note");
    expect(shapeType("link")).toBe("taos-link");
    expect(shapeType("image")).toBe("taos-image");
  });
  it("maps unknown kinds to the taos-generic fallback, never built-in geo", () => {
    expect(shapeType("user_shape")).toBe("taos-generic");
    expect(shapeType("whatever")).toBe("taos-generic");
  });
});

describe("elementToShape", () => {
  it("note: taos_* live in props, geometry preserved", () => {
    const s = elementToShape(makeElement({ kind: "note", payload: { text: "hi", color: "yellow", font_size: 14 } }), "slug");
    expect(s.type).toBe("taos-note");
    expect(s.x).toBe(10);
    expect(s.props.w).toBe(100);
    expect(s.props.taos_kind).toBe("note");
    expect(s.props.taos_payload).toEqual({ text: "hi", color: "yellow", font_size: 14 });
    expect(s.meta).toBeUndefined();
  });

  it("image: includes project_slug in props", () => {
    const s = elementToShape(makeElement({ kind: "image" }), "my-slug");
    expect(s.type).toBe("taos-image");
    expect(s.props.project_slug).toBe("my-slug");
    expect(s.props.taos_kind).toBe("image");
  });

  it("link: taos_* live in props", () => {
    const s = elementToShape(makeElement({ kind: "link" }), "slug");
    expect(s.type).toBe("taos-link");
    expect(s.props.taos_kind).toBe("link");
  });

  it("fallback: taos_* live in meta, props only carry geometry", () => {
    const s = elementToShape(makeElement({ kind: "user_shape", author_kind: "agent", author_id: "bot", payload: { foo: 1 } }), "slug");
    expect(s.type).toBe("taos-generic");
    // props must NOT carry taos_* (built-in geo would reject; generic only declares w/h)
    expect(s.props).toEqual({ w: 100, h: 50 });
    expect(s.props.taos_kind).toBeUndefined();
    // taos_* round-trip via meta
    expect(s.meta.taos_kind).toBe("user_shape");
    expect(s.meta.taos_payload).toEqual({ foo: 1 });
    expect(s.meta.taos_author_id).toBe("bot");
    expect(s.meta.taos_author_kind).toBe("agent");
  });
});
