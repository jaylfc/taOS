import { describe, it, expect } from "vitest";
import {
  DEFAULT_ARTBOARD,
  MAGIC_STYLE_CHIPS,
  ZOOM_STEPS,
  hasFill,
  hasStroke,
  isValidDesignContent,
  type CanvasElement,
  type DesignContent,
} from "./types";

function textEl(over: Partial<CanvasElement> = {}): CanvasElement {
  return {
    id: "t1",
    type: "text",
    x: 0,
    y: 0,
    width: 100,
    height: 40,
    rotation: 0,
    zIndex: 0,
    visible: true,
    text: "Hi",
    fontSize: 32,
    fontFamily: "Inter",
    fill: "#fff",
    align: "left",
    ...over,
  } as CanvasElement;
}

function rectEl(): CanvasElement {
  return {
    id: "r1",
    type: "rect",
    x: 0,
    y: 0,
    width: 50,
    height: 50,
    rotation: 0,
    zIndex: 1,
    visible: true,
    fill: "#000",
    stroke: "#fff",
    strokeWidth: 1,
  } as CanvasElement;
}

function lineEl(): CanvasElement {
  return {
    id: "l1",
    type: "line",
    x: 0,
    y: 0,
    width: 80,
    height: 2,
    rotation: 0,
    zIndex: 2,
    visible: true,
    stroke: "#fff",
    strokeWidth: 3,
  } as CanvasElement;
}

function imageEl(): CanvasElement {
  return {
    id: "i1",
    type: "image",
    x: 0,
    y: 0,
    width: 100,
    height: 100,
    rotation: 0,
    zIndex: 3,
    visible: true,
    src: "data:image/png;base64,AAAA",
  } as CanvasElement;
}

describe("hasFill", () => {
  it("is true for text, rect and ellipse", () => {
    expect(hasFill(textEl())).toBe(true);
    expect(hasFill(rectEl())).toBe(true);
    expect(hasFill({ ...rectEl(), type: "ellipse" } as CanvasElement)).toBe(true);
  });

  it("is false for line and image", () => {
    expect(hasFill(lineEl())).toBe(false);
    expect(hasFill(imageEl())).toBe(false);
  });
});

describe("hasStroke", () => {
  it("is true for rect, ellipse and line", () => {
    expect(hasStroke(rectEl())).toBe(true);
    expect(hasStroke({ ...rectEl(), type: "ellipse" } as CanvasElement)).toBe(true);
    expect(hasStroke(lineEl())).toBe(true);
  });

  it("is false for text and image", () => {
    expect(hasStroke(textEl())).toBe(false);
    expect(hasStroke(imageEl())).toBe(false);
  });
});

describe("isValidDesignContent", () => {
  const valid: DesignContent = {
    artboard: { name: "Poster", width: 1080, height: 1350 },
    elements: [textEl(), rectEl()],
  };

  it("accepts a well-formed design with elements", () => {
    expect(isValidDesignContent(valid)).toBe(true);
  });

  it("accepts an empty elements array", () => {
    expect(isValidDesignContent({ ...valid, elements: [] })).toBe(true);
  });

  it.each([
    ["null", null],
    ["a string", "nope"],
    ["a number", 5],
    ["undefined", undefined],
  ])("rejects %s", (_label, value) => {
    expect(isValidDesignContent(value)).toBe(false);
  });

  it("rejects a missing artboard", () => {
    expect(isValidDesignContent({ elements: [] })).toBe(false);
  });

  it("rejects an artboard with non-numeric dimensions", () => {
    expect(
      isValidDesignContent({ artboard: { name: "x", width: "1080", height: 1350 }, elements: [] }),
    ).toBe(false);
  });

  it("rejects an artboard without a name", () => {
    expect(
      isValidDesignContent({ artboard: { width: 1080, height: 1350 }, elements: [] }),
    ).toBe(false);
  });

  it("rejects when elements is not an array", () => {
    expect(isValidDesignContent({ artboard: valid.artboard, elements: {} })).toBe(false);
  });

  it("rejects an element missing an id or type", () => {
    expect(
      isValidDesignContent({ artboard: valid.artboard, elements: [{ type: "text" }] }),
    ).toBe(false);
    expect(
      isValidDesignContent({ artboard: valid.artboard, elements: [{ id: "x" }] }),
    ).toBe(false);
  });

  it("rejects a non-object element (e.g. null in the array)", () => {
    expect(isValidDesignContent({ artboard: valid.artboard, elements: [null] })).toBe(false);
  });
});

describe("constants", () => {
  it("DEFAULT_ARTBOARD is a valid design's artboard", () => {
    expect(isValidDesignContent({ artboard: DEFAULT_ARTBOARD, elements: [] })).toBe(true);
    expect(DEFAULT_ARTBOARD.width).toBeGreaterThan(0);
    expect(DEFAULT_ARTBOARD.height).toBeGreaterThan(0);
  });

  it("ZOOM_STEPS are ascending and include 100%", () => {
    const arr = [...ZOOM_STEPS];
    expect(arr).toContain(1);
    expect([...arr].sort((a, b) => a - b)).toEqual(arr);
  });

  it("MAGIC_STYLE_CHIPS is a non-empty unique list", () => {
    expect(MAGIC_STYLE_CHIPS.length).toBeGreaterThan(0);
    expect(new Set(MAGIC_STYLE_CHIPS).size).toBe(MAGIC_STYLE_CHIPS.length);
  });
});
