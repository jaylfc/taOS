import { describe, it, expect } from "vitest";
import {
  createImageElement,
  createLineElement,
  createShapeElement,
  createTextElement,
  duplicateElement,
  nextZIndex,
} from "./elementFactory";
import type { CanvasElement } from "./types";

const AW = 1080;
const AH = 1350;

describe("nextZIndex", () => {
  it("returns 0 for an empty canvas", () => {
    expect(nextZIndex([])).toBe(0);
  });

  it("returns one above the current max zIndex", () => {
    const els = [
      { zIndex: 0 } as CanvasElement,
      { zIndex: 4 } as CanvasElement,
      { zIndex: 2 } as CanvasElement,
    ];
    expect(nextZIndex(els)).toBe(5);
  });
});

describe("createTextElement", () => {
  it("creates a centered text element with sane defaults", () => {
    const el = createTextElement([], AW, AH);
    expect(el.type).toBe("text");
    expect(el.visible).toBe(true);
    expect(el.rotation).toBe(0);
    expect(el.zIndex).toBe(0);
    if (el.type === "text") {
      expect(el.fontSize).toBeGreaterThan(0);
      expect(el.text.length).toBeGreaterThan(0);
    }
    // Centered horizontally: left margin + width + right margin == artboard.
    expect(el.x).toBe(Math.round((AW - el.width) / 2));
    expect(el.y).toBe(Math.round((AH - el.height) / 2));
  });

  it("caps text width at 320 for a wide artboard", () => {
    const el = createTextElement([], 4000, 4000);
    expect(el.width).toBe(320);
  });

  it("stacks zIndex above existing elements", () => {
    const first = createTextElement([], AW, AH);
    const second = createTextElement([first], AW, AH);
    expect(second.zIndex).toBe(1);
  });
});

describe("createShapeElement", () => {
  it("creates a square rect centered on the artboard", () => {
    const el = createShapeElement([], "rect", AW, AH);
    expect(el.type).toBe("rect");
    expect(el.width).toBe(el.height);
    expect(el.x).toBe(Math.round((AW - el.width) / 2));
  });

  it("honors the requested kind (ellipse)", () => {
    const el = createShapeElement([], "ellipse", AW, AH);
    expect(el.type).toBe("ellipse");
  });

  it("caps size at 220 for a wide artboard", () => {
    const el = createShapeElement([], "rect", 4000, 4000);
    expect(el.width).toBe(220);
  });
});

describe("createLineElement", () => {
  it("creates a thin horizontal line", () => {
    const el = createLineElement([], AW, AH);
    expect(el.type).toBe("line");
    expect(el.height).toBe(2);
    expect(el.width).toBeGreaterThan(el.height);
  });
});

describe("createImageElement", () => {
  it("creates a square image carrying its src and prompt", () => {
    const el = createImageElement([], "data:image/png;base64,AA", AW, AH, "a cat");
    expect(el.type).toBe("image");
    expect(el.src).toBe("data:image/png;base64,AA");
    expect(el.prompt).toBe("a cat");
    expect(el.width).toBe(el.height);
  });

  it("leaves the prompt undefined when not supplied", () => {
    const el = createImageElement([], "data:x", AW, AH);
    expect(el.prompt).toBeUndefined();
  });
});

describe("id uniqueness", () => {
  it("every factory produces a distinct id even in the same tick", () => {
    const els = [
      createTextElement([], AW, AH),
      createShapeElement([], "rect", AW, AH),
      createLineElement([], AW, AH),
      createImageElement([], "data:x", AW, AH),
    ];
    const ids = els.map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("ids are prefixed by their type family", () => {
    expect(createTextElement([], AW, AH).id).toMatch(/^text-/);
    expect(createShapeElement([], "ellipse", AW, AH).id).toMatch(/^ellipse-/);
    expect(createLineElement([], AW, AH).id).toMatch(/^line-/);
    expect(createImageElement([], "data:x", AW, AH).id).toMatch(/^image-/);
  });
});

describe("duplicateElement", () => {
  it("offsets the copy by 16px, gives it a fresh id and the top zIndex", () => {
    const original = createShapeElement([], "rect", AW, AH);
    const existing = [original];
    const copy = duplicateElement(original, existing);
    expect(copy.id).not.toBe(original.id);
    expect(copy.x).toBe(original.x + 16);
    expect(copy.y).toBe(original.y + 16);
    expect(copy.zIndex).toBe(nextZIndex(existing));
    // Everything else (fill, size, type) is preserved.
    expect(copy.type).toBe(original.type);
    expect(copy.width).toBe(original.width);
  });

  it("does not mutate the source element", () => {
    const original = createShapeElement([], "rect", AW, AH);
    const snapshot = { ...original };
    duplicateElement(original, [original]);
    expect(original).toEqual(snapshot);
  });
});
