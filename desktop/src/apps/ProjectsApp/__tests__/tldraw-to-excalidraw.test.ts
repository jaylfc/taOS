import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import {
  legacyShapeToSkeleton,
  decodeDrawPath,
  isPlaceholder,
} from "../canvas/tldraw-to-excalidraw";
import type { ExcalidrawSkeleton } from "../canvas/element-to-excalidraw";
import type { CanvasElement } from "../canvas/canvas-api";
import fixtures from "./fixtures/tldraw-shapes.json" with { type: "json" };

// The converter source, read raw so the "no tldraw import" gate checks the
// text of the module rather than whatever the bundler resolved.
const converterSources = import.meta.glob("../canvas/tldraw-to-excalidraw.ts", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const here = dirname(fileURLToPath(import.meta.url));
// The lead's raw dump from the dev Pi (5 rows, types geo and draw). Read from
// the repo-level fixture so the property test runs on the file as committed,
// not on a copy that could drift.
const piDumpPath = resolve(here, "../../../../../tests/fixtures/canvas/pi_tldraw_shapes_20260926.json");

function el(over: Partial<CanvasElement>): CanvasElement {
  return {
    id: "row-1",
    project_id: "prj",
    kind: "user_shape",
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

function shape(type: string, props: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  return {
    x: 0,
    y: 0,
    rotation: 0,
    isLocked: false,
    opacity: 1,
    meta: {},
    id: `shape:${type}`,
    type,
    props,
    parentId: "page:page",
    index: "a1",
    typeName: "shape",
    ...extra,
  };
}

function richText(...paragraphs: string[]) {
  return {
    type: "doc",
    content: paragraphs.map((p) =>
      p ? { type: "paragraph", content: [{ type: "text", text: p }] } : { type: "paragraph" },
    ),
  };
}

const geoProps = (geo: string, over: Record<string, unknown> = {}) => ({
  w: 100,
  h: 100,
  geo,
  dash: "draw",
  growY: 0,
  url: "",
  scale: 1,
  color: "black",
  labelColor: "black",
  fill: "none",
  size: "m",
  font: "draw",
  align: "middle",
  verticalAlign: "middle",
  richText: richText(""),
  ...over,
});

const fixtureRows = [...fixtures.pi_rows, ...fixtures.synthetic_rows] as unknown as CanvasElement[];
const byId = (id: string) => {
  const r = fixtureRows.find((x) => x.id === id);
  if (!r) throw new Error(`fixture row ${id} missing`);
  return r;
};

describe("legacyShapeToSkeleton", () => {
  it("converts a geo rectangle using row geometry, not blob geometry", () => {
    const row = el({
      id: "r1",
      x: 300,
      y: 400,
      w: 250,
      h: 80,
      rotation: 0,
      payload: { tldraw_shape: shape("geo", geoProps("rectangle", { w: 100, h: 100 }), { x: 10, y: 20 }) },
    });
    const s = legacyShapeToSkeleton(row);
    expect(s).toMatchObject({ type: "rectangle", id: "r1", x: 300, y: 400, width: 250, height: 80 });
    expect(isPlaceholder(s)).toBe(false);
  });

  it("converts a rotated rectangle from top-left rotation to a centre angle", () => {
    const r = Math.PI / 2; // 90 degrees about the tldraw top-left corner
    const row = el({
      id: "rot",
      x: 100,
      y: 100,
      w: 200,
      h: 50,
      rotation: r,
      payload: { tldraw_shape: shape("geo", geoProps("rectangle")) },
    });
    const s = legacyShapeToSkeleton(row);
    // centre = (x + (w/2)cos r - (h/2)sin r, y + (w/2)sin r + (h/2)cos r) = (75, 200)
    expect(s.angle).toBeCloseTo(r, 10);
    expect(s.x).toBeCloseTo(75 - 100, 6);
    expect(s.y).toBeCloseTo(200 - 25, 6);
  });

  it("maps geo styles: palette colour, fill, dash and size", () => {
    const row = el({
      payload: {
        tldraw_shape: shape("geo", geoProps("ellipse", { color: "red", fill: "solid", dash: "dashed", size: "l" })),
      },
    });
    const s = legacyShapeToSkeleton(row);
    expect(s).toMatchObject({
      type: "ellipse",
      strokeColor: "#e03131",
      backgroundColor: "#e03131",
      strokeStyle: "dashed",
      strokeWidth: 4,
    });
  });

  it("flattens a geo richText label to plain bound text", () => {
    const row = el({
      payload: { tldraw_shape: shape("geo", geoProps("diamond", { richText: richText("Hello", "world") })) },
    });
    const s = legacyShapeToSkeleton(row);
    expect(s.type).toBe("diamond");
    expect((s as { label?: { text: string } }).label?.text).toBe("Hello\nworld");
  });

  it("converts a non-primitive geo (star) to a closed line polygon at the row bbox", () => {
    const row = el({ x: 50, y: 50, w: 100, h: 100, payload: { tldraw_shape: shape("geo", geoProps("star")) } });
    const s = legacyShapeToSkeleton(row);
    expect(s.type).toBe("line");
    const pts = (s as { points: [number, number][] }).points;
    expect(pts.length).toBeGreaterThan(4);
    expect(pts[0]).toEqual(pts[pts.length - 1]);
    for (const [px, py] of pts) {
      expect(px).toBeGreaterThanOrEqual(-1e-6);
      expect(px).toBeLessThanOrEqual(100 + 1e-6);
      expect(py).toBeGreaterThanOrEqual(-1e-6);
      expect(py).toBeLessThanOrEqual(100 + 1e-6);
    }
    expect(s).toMatchObject({ x: 50, y: 50, width: 100, height: 100 });
  });

  it("converts a cloud geo to an ellipse", () => {
    const s = legacyShapeToSkeleton(el({ payload: { tldraw_shape: shape("geo", geoProps("cloud")) } }));
    expect(s.type).toBe("ellipse");
  });

  it("converts a draw shape to freedraw with points relative to origin", () => {
    // tldraw 4.5.12 b64Vecs.encodePoints of (0,0,.5) (10,5,.6) (20,15,.7) (30,10,.4)
    const row = el({
      id: "d1",
      x: 100,
      y: 100,
      w: 100,
      h: 100,
      payload: {
        tldraw_shape: shape("draw", {
          segments: [{ type: "free", path: "AAAAAAAAAAAAAAA/AEkARWYuAEkASWYuAEkAxc20" }],
          color: "black",
          fill: "none",
          dash: "draw",
          size: "m",
          isComplete: true,
          isClosed: false,
          isPen: false,
          scale: 1,
          scaleX: 1,
          scaleY: 1,
        }),
      },
    });
    const s = legacyShapeToSkeleton(row);
    expect(s.type).toBe("freedraw");
    const fd = s as Extract<ExcalidrawSkeleton, { type: "freedraw" }>;
    expect(fd.x).toBe(100);
    expect(fd.y).toBe(100);
    expect(fd.points.length).toBe(4);
    expect(fd.points[0]).toEqual([0, 0]);
    expect(fd.points[1][0]).toBeCloseTo(10, 2);
    expect(fd.points[1][1]).toBeCloseTo(5, 2);
    expect(fd.points[3][0]).toBeCloseTo(30, 2);
    expect(fd.points[3][1]).toBeCloseTo(10, 2);
    expect(fd.pressures.length).toBe(4);
    expect(fd.pressures[1]).toBeCloseTo(0.6, 2);
    // bbox from the points, not the row's default 100x100
    expect(fd.width).toBeCloseTo(30, 2);
    expect(fd.height).toBeCloseTo(15, 2);
    expect(isPlaceholder(s)).toBe(false);
  });

  it("shifts a draw stroke whose points sit left/above the origin so points stay relative", () => {
    // encodes (-5.5,-2.25,.5) (-10,8,.5)
    const row = el({
      x: 50,
      y: 50,
      payload: {
        tldraw_shape: shape("draw", {
          segments: [{ type: "free", path: "AACwwAAAEMAAAAA/gMQgSQAA" }],
          color: "red",
          fill: "none",
          dash: "draw",
          size: "l",
          isComplete: true,
          isClosed: false,
          isPen: false,
          scale: 1,
          scaleX: 1,
          scaleY: 1,
        }),
      },
    });
    const fd = legacyShapeToSkeleton(row) as Extract<ExcalidrawSkeleton, { type: "freedraw" }>;
    expect(fd.type).toBe("freedraw");
    expect(fd.x).toBeCloseTo(40, 4);
    expect(fd.y).toBeCloseTo(47.75, 4);
    expect(fd.points[0][0]).toBeCloseTo(4.5, 4);
    expect(fd.points[0][1]).toBeCloseTo(0, 4);
    expect(fd.points[1][0]).toBeCloseTo(0, 4);
    expect(fd.points[1][1]).toBeCloseTo(10.25, 4);
  });

  it("converts the truncated single-point Pi draw rows to a one-point freedraw, not a placeholder", () => {
    for (const row of fixtures.pi_rows as unknown as CanvasElement[]) {
      const blob = (row.payload as { tldraw_shape: { type: string } }).tldraw_shape;
      if (blob.type !== "draw") continue;
      const s = legacyShapeToSkeleton(row);
      expect(s.type).toBe("freedraw");
      const fd = s as Extract<ExcalidrawSkeleton, { type: "freedraw" }>;
      expect(fd.points).toEqual([[0, 0]]);
      expect(fd.pressures).toEqual([0.5]);
      expect(fd.x).toBe(row.x);
      expect(fd.y).toBe(row.y);
      expect(isPlaceholder(s)).toBe(false);
    }
  });

  it("converts a highlight to a translucent wide freedraw", () => {
    const s = legacyShapeToSkeleton(byId("syn-highlight"));
    expect(s.type).toBe("freedraw");
    expect(s.opacity).toBe(40);
    expect((s as { points: unknown[] }).points.length).toBe(3);
  });

  it("converts a text shape to a text element with the plain text", () => {
    const s = legacyShapeToSkeleton(byId("syn-text"));
    expect(s).toMatchObject({ type: "text", text: "Some text\nsecond line", x: 200, y: -50, fontSize: 24 });
  });

  it("converts a tldraw sticky note to a coloured rectangle with a label", () => {
    const s = legacyShapeToSkeleton(byId("syn-note"));
    expect(s).toMatchObject({ type: "rectangle", x: 600, y: 100, width: 200, height: 200, label: { text: "sticky" } });
    expect((s as { backgroundColor: string }).backgroundColor).toMatch(/^#/);
  });

  it("converts a line from its index-keyed point dict in index order, bbox from points", () => {
    const s = legacyShapeToSkeleton(byId("syn-line"));
    expect(s.type).toBe("line");
    const pts = (s as { points: [number, number][] }).points;
    // points (0,0) (60,10) (120,-30) relative to row origin (20,20); minY is -30
    expect(s.x).toBe(20);
    expect(s.y).toBe(-10);
    expect(pts).toEqual([
      [0, 30],
      [60, 40],
      [120, 0],
    ]);
    expect(s.width).toBe(120);
    expect(s.height).toBe(40);
  });

  it("gives a cubic-spline line a roundness", () => {
    const s = legacyShapeToSkeleton(byId("syn-line-cubic"));
    expect((s as { roundness?: unknown }).roundness).toBeTruthy();
  });

  it("converts an arrow, unbound, with the bend as a mid control point and the label as text", () => {
    const s = legacyShapeToSkeleton(byId("syn-arrow"));
    expect(s.type).toBe("arrow");
    const a = s as Extract<ExcalidrawSkeleton, { type: "arrow" | "line" }>;
    expect(a.start).toBeUndefined();
    expect(a.end).toBeUndefined();
    expect(a.points?.length).toBe(3);
    // tldraw: mid = med(a,b) + per(unit(b-a)) * -bend, per(v) = (v.y, -v.x); here a=(0,0) b=(100,0), bend 20 -> (50, 20)
    expect(a.points?.[1][0]).toBeCloseTo(50, 6);
    expect(a.points?.[1][1]).toBeCloseTo(20, 6);
    expect(a.endArrowhead).toBe("arrow");
    expect(a.startArrowhead).toBeNull();
    expect(a.label?.text).toBe("to B");
  });

  it("converts a frame keeping its name", () => {
    const s = legacyShapeToSkeleton(byId("syn-frame"));
    expect(s).toMatchObject({ type: "frame", name: "Sprint 3", x: -200, y: -200, width: 400, height: 300 });
  });

  it("adds the parent origin to a frame child when the sibling rows are supplied", () => {
    const parent = byId("syn-frame");
    const child = byId("syn-frame-child");
    const alone = legacyShapeToSkeleton(child);
    expect(alone).toMatchObject({ x: 10, y: 10 });
    const withParent = legacyShapeToSkeleton(child, { rows: [parent, child] });
    expect(withParent).toMatchObject({ x: -190, y: -190 });
    const frame = legacyShapeToSkeleton(parent, { rows: [parent, child] });
    expect((frame as { children: string[] }).children).toEqual(["syn-frame-child"]);
  });

  it("an image shape becomes a visible placeholder carrying the original element id", () => {
    const row = el({
      id: "img-42",
      x: 5,
      y: 6,
      w: 200,
      h: 150,
      payload: { tldraw_shape: shape("image", { w: 200, h: 150, assetId: "asset:abc", url: "" }) },
    });
    const s = legacyShapeToSkeleton(row);
    expect(isPlaceholder(s)).toBe(true);
    expect(s).toMatchObject({
      id: "img-42",
      type: "rectangle",
      x: 5,
      y: 6,
      width: 200,
      height: 150,
      strokeStyle: "dashed",
      customData: {
        taos_placeholder: true,
        taos_id: "img-42",
        taos_original_element_id: "img-42",
        taos_original_type: "image",
      },
    });
    const label = (s as { label?: { text: string } }).label?.text ?? "";
    expect(label).toContain("tldraw image: not convertible");
    expect(label).toContain("img-42");
  });

  it("a malformed blob {type:'draw',props:{nonsense:1}} becomes a placeholder, does not throw", () => {
    const row = el({ id: "bad-draw", payload: { tldraw_shape: { type: "draw", props: { nonsense: 1 } } } });
    let s: ExcalidrawSkeleton | undefined;
    expect(() => {
      s = legacyShapeToSkeleton(row);
    }).not.toThrow();
    expect(s && isPlaceholder(s)).toBe(true);
    expect(s?.customData).toMatchObject({
      taos_placeholder: true,
      taos_original_element_id: "bad-draw",
      taos_original_type: "draw",
    });
  });

  it("a missing tldraw_shape becomes a placeholder carrying the original element id", () => {
    const s = legacyShapeToSkeleton(el({ id: "empty-7", payload: {} }));
    expect(isPlaceholder(s)).toBe(true);
    expect(s.customData).toMatchObject({
      taos_placeholder: true,
      taos_id: "empty-7",
      taos_original_element_id: "empty-7",
      taos_original_type: "unknown",
    });
    expect((s as { label?: { text: string } }).label?.text).toContain("empty-7");
  });

  it("video, bookmark, embed, group and unknown types all become id-carrying placeholders", () => {
    for (const id of ["syn-video", "syn-bookmark", "syn-embed", "syn-group", "syn-unknown-type", "syn-blob-not-object"]) {
      const s = legacyShapeToSkeleton(byId(id));
      expect(isPlaceholder(s), id).toBe(true);
      expect(s.customData?.taos_original_element_id, id).toBe(id);
      expect(s.id, id).toBe(id);
    }
  });

  it("N user_shape rows in -> N skeletons out (no silent drop)", () => {
    const piDump = JSON.parse(readFileSync(piDumpPath, "utf8")) as { rows: CanvasElement[] };
    expect(piDump.rows.length).toBe(5);
    const inputs: CanvasElement[] = [...piDump.rows.map((r) => el(r)), ...fixtureRows];
    const out = inputs.map((r) => legacyShapeToSkeleton(r));
    expect(out.length).toBe(inputs.length);
    const outIds = new Set(out.map((s) => s.id));
    for (const r of inputs) {
      expect(outIds.has(r.id), `input id ${r.id} missing from output`).toBe(true);
    }
    for (const s of out) {
      expect(s, s.id).toBeTruthy();
      expect(typeof s.type, s.id).toBe("string");
      expect(Number.isFinite(s.x), `${s.id} x`).toBe(true);
      expect(Number.isFinite(s.y), `${s.id} y`).toBe(true);
      expect(s.customData?.taos_id, s.id).toBe(s.id);
      expect(s.customData?.taos_original_element_id, s.id).toBe(s.id);
    }
  });

  it("module source contains no '@tldraw' import", () => {
    const sources = Object.values(converterSources);
    expect(sources.length).toBe(1);
    expect(sources[0]).not.toMatch(/@tldraw/);
    expect(sources[0]).not.toMatch(/from ["']tldraw["']/);
  });
});

describe("decodeDrawPath", () => {
  it("decodes the tldraw 4.5 delta-encoded base64 path (Float32 first point, Float16 deltas)", () => {
    expect(decodeDrawPath("AAAAAAAAAAAAAAA/")).toEqual([{ x: 0, y: 0, z: 0.5 }]);
    const pts = decodeDrawPath("AAAAAAAAAAAAAAA/AEkARWYuAEkASWYuAEkAxc20");
    expect(pts.length).toBe(4);
    expect(pts[2].x).toBeCloseTo(20, 2);
    expect(pts[2].y).toBeCloseTo(15, 2);
    expect(pts[2].z).toBeCloseTo(0.7, 2);
  });

  it("returns an empty list for an empty or too-short path", () => {
    expect(decodeDrawPath("")).toEqual([]);
    expect(decodeDrawPath("AAAA")).toEqual([]);
  });
});
