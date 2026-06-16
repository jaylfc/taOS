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

  // The backend only validates `kind`, not the payload shape, so an agent can
  // store note/link/image elements whose payload would fail tldraw's strict
  // props schema. elementToShape must coerce those to valid, renderable props
  // instead of producing a shape that throws a ValidationError and vanishes.
  describe("payload coercion (malformed agent writes still render)", () => {
    it("note: fills a missing field with its default", () => {
      const s = elementToShape(makeElement({ kind: "note", payload: { text: "hi", color: "blue" } }), "slug");
      expect(s.props.taos_payload).toEqual({ text: "hi", color: "blue", font_size: 14 });
    });

    it("note: coerces a numeric string font_size to a number", () => {
      const s = elementToShape(makeElement({ kind: "note", payload: { text: "x", color: "blue", font_size: "18" } }), "slug");
      expect(s.props.taos_payload.font_size).toBe(18);
    });

    it("note: empty payload falls back to all defaults", () => {
      const s = elementToShape(makeElement({ kind: "note", payload: {} }), "slug");
      expect(s.props.taos_payload).toEqual({ text: "", color: "yellow", font_size: 14 });
    });

    it("link: missing/empty fields coerce to typed defaults", () => {
      const s = elementToShape(makeElement({ kind: "link", payload: { url: "https://x.test" } }), "slug");
      expect(s.props.taos_payload).toEqual({
        url: "https://x.test",
        title: "",
        description: "",
        preview_image_url: "",
        favicon_url: "",
        fetched_at: 0,
      });
    });

    it("image: missing mime falls back to image/png", () => {
      const s = elementToShape(makeElement({ kind: "image", payload: { file_id: "f1" } }), "slug");
      expect(s.props.taos_payload).toEqual({ file_id: "f1", alt: "", mime: "image/png" });
    });

    it("coerces non-numeric geometry to numeric defaults", () => {
      const s = elementToShape(
        makeElement({ kind: "note", x: "5" as unknown as number, w: null as unknown as number, payload: {} }),
        "slug",
      );
      expect(s.x).toBe(5);
      expect(s.props.w).toBe(100);
    });

    it("coerces a non-enum author_kind to 'user'", () => {
      const s = elementToShape(makeElement({ kind: "note", author_kind: "system" as unknown as "user", payload: {} }), "slug");
      expect(s.props.taos_author_kind).toBe("user");
    });
  });
});
