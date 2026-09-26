import type { CanvasElement } from "./canvas-api";
import type {
  BaseSkeleton,
  ExcalidrawSkeleton,
  SkeletonArrowhead,
  SkeletonLabel,
  StrokeStyleFields,
} from "./element-to-excalidraw";

// Read-time conversion of a legacy `user_shape` row (payload.tldraw_shape, the
// shape as the old tldraw board captured it at CREATION time) into an
// Excalidraw skeleton. Pure: no DOM, no React, and no import from the tldraw
// packages (a gate test greps this file for the package scope), so it keeps
// working after the tldraw dependency is removed.
//
// The load-bearing rule: nothing is ever silently lost. Every row yields
// exactly one skeleton. Anything that cannot be rendered (image/video/bookmark/
// embed whose bytes were never persisted, groups, unknown types, malformed or
// missing blobs, or a throw inside a converter branch) becomes a VISIBLE
// dashed placeholder at the row's bbox whose label and customData carry the
// original element id so the row can be recovered from the canvas backup.
//
// Geometry rule: the row's x/y/w/h/rotation are the current geometry (the old
// board PATCHed them on every move/resize) while the blob is a creation-time
// snapshot, so the row wins. The exceptions are draw, highlight, line and
// arrow: their row w/h stayed at the 100 default because tldraw keeps no w/h
// for them, so their bbox comes from the points.

export interface LegacyContext {
  // Live sibling rows, used to resolve tldraw frame/group parents: a child's
  // stored x/y are relative to its parent's origin.
  rows: CanvasElement[];
}

export interface DrawPoint {
  x: number;
  y: number;
  z: number;
}

type Dict = Record<string, unknown>;

const isDict = (v: unknown): v is Dict => typeof v === "object" && v !== null && !Array.isArray(v);
const fin = (v: unknown, fallback: number): number => (typeof v === "number" && Number.isFinite(v) ? v : fallback);
const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);

// ---------------------------------------------------------------------------
// tldraw 4.5 draw/highlight segment path codec (mirror of the tlschema package's
// b64Vecs.decodePoints): the first point is three little-endian Float32 values
// (12 bytes, 16 base64 chars), every further point three Float16 deltas from
// the previous point (6 bytes, 8 base64 chars). The real Pi rows confirmed this
// encoding ("AAAAAAAAAAAAAAA/" is the single point 0,0,0.5).
// ---------------------------------------------------------------------------

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const B64_LOOKUP: Record<string, number> = {};
for (let i = 0; i < B64.length; i++) B64_LOOKUP[B64.charAt(i)] = i;

function base64ToBytes(b64: string): Uint8Array | null {
  const clean = b64.replace(/=+$/, "");
  if (clean.length % 4 !== 0) return null;
  const bytes = new Uint8Array((clean.length / 4) * 3);
  let o = 0;
  for (let i = 0; i < clean.length; i += 4) {
    const c0 = B64_LOOKUP[clean.charAt(i)];
    const c1 = B64_LOOKUP[clean.charAt(i + 1)];
    const c2 = B64_LOOKUP[clean.charAt(i + 2)];
    const c3 = B64_LOOKUP[clean.charAt(i + 3)];
    if (c0 === undefined || c1 === undefined || c2 === undefined || c3 === undefined) return null;
    const bitmap = (c0 << 18) | (c1 << 12) | (c2 << 6) | c3;
    bytes[o++] = (bitmap >> 16) & 255;
    bytes[o++] = (bitmap >> 8) & 255;
    bytes[o++] = bitmap & 255;
  }
  return bytes;
}

function float16BitsToNumber(bits: number): number {
  const sign = bits >> 15 ? -1 : 1;
  const exp = (bits >> 10) & 31;
  const frac = bits & 1023;
  if (exp === 0) return sign * frac * Math.pow(2, -24);
  if (exp === 31) return frac ? NaN : sign * Infinity;
  return sign * Math.pow(2, exp - 15) * (1 + frac / 1024);
}

export function decodeDrawPath(path: string): DrawPoint[] {
  if (typeof path !== "string" || path.length < 16) return [];
  const bytes = base64ToBytes(path);
  if (!bytes || bytes.length < 12) return [];
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let x = dv.getFloat32(0, true);
  let y = dv.getFloat32(4, true);
  let z = dv.getFloat32(8, true);
  const out: DrawPoint[] = [{ x, y, z }];
  for (let off = 12; off + 6 <= bytes.length; off += 6) {
    x += float16BitsToNumber(dv.getUint16(off, true));
    y += float16BitsToNumber(dv.getUint16(off + 2, true));
    z += float16BitsToNumber(dv.getUint16(off + 4, true));
    out.push({ x, y, z });
  }
  return out.filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
}

// ---------------------------------------------------------------------------
// Style tables (tldraw 4.5.12 light-theme "solid" palette; tldraw stroke sizes
// map onto Excalidraw's 1/2/4 stroke widths; tldraw font sizes by size token).
// ---------------------------------------------------------------------------

const PALETTE: Record<string, string> = {
  black: "#1d1d1d",
  grey: "#9fa8b2",
  "light-violet": "#e085f4",
  violet: "#ae3ec9",
  blue: "#4465e9",
  "light-blue": "#4ba1f1",
  yellow: "#f1ac4b",
  orange: "#e16919",
  green: "#099268",
  "light-green": "#4cb05e",
  "light-red": "#f87777",
  red: "#e03131",
  white: "#FFFFFF",
};

// tldraw sticky notes are pastel; use the same pastel set the note kind uses.
const NOTE_BG: Record<string, string> = {
  black: "#fff9db",
  grey: "#e9ecef",
  "light-violet": "#e5dbff",
  violet: "#d0bfff",
  blue: "#a5d8ff",
  "light-blue": "#c5f6fa",
  yellow: "#ffec99",
  orange: "#ffd8a8",
  green: "#b2f2bb",
  "light-green": "#d3f9d8",
  "light-red": "#ffe3e3",
  red: "#ffc9c9",
  white: "#ffffff",
};

const STROKE_WIDTH: Record<string, number> = { s: 1, m: 2, l: 4, xl: 4 };
const FONT_SIZE: Record<string, number> = { s: 18, m: 24, l: 36, xl: 44 };
const LABEL_FONT_SIZE: Record<string, number> = { s: 18, m: 22, l: 26, xl: 32 };
// Excalidraw FONT_FAMILY ids: Excalifont 5, Nunito 6, Lilita One 7, Cascadia 3.
const FONT_FAMILY: Record<string, number> = { draw: 5, sans: 6, serif: 7, mono: 3 };
const ARROWHEAD: Record<string, SkeletonArrowhead> = {
  arrow: "arrow",
  triangle: "triangle",
  square: "bar",
  dot: "dot",
  pipe: "bar",
  diamond: "diamond",
  inverted: "triangle_outline",
  bar: "bar",
  none: null,
};

const BLACK = "#1d1d1d";
const paletteColor = (name: unknown, fallback: string = BLACK): string => PALETTE[str(name)] ?? fallback;

function strokeStyle(props: Dict): StrokeStyleFields {
  const dash = str(props.dash, "draw");
  return {
    strokeColor: paletteColor(props.color),
    strokeWidth: STROKE_WIDTH[str(props.size)] ?? 2,
    strokeStyle: dash === "dashed" ? "dashed" : dash === "dotted" ? "dotted" : "solid",
    roughness: dash === "draw" ? 1 : 0,
  };
}

function fillStyle(props: Dict): { backgroundColor: string; fillStyle: "hachure" | "solid" } {
  const fill = str(props.fill, "none");
  if (fill === "none") return { backgroundColor: "transparent", fillStyle: "solid" };
  return { backgroundColor: paletteColor(props.color), fillStyle: fill === "pattern" ? "hachure" : "solid" };
}

// Flatten tldraw richText (a ProseMirror/TipTap doc) to plain text: text nodes
// concatenated, paragraphs joined by newlines. Bold/italic/links are dropped.
export function richTextToPlain(rich: unknown): string {
  if (typeof rich === "string") return rich;
  if (!isDict(rich)) return "";
  const blocks: string[] = [];
  const walk = (node: unknown, into: string[]): void => {
    if (!isDict(node)) return;
    if (typeof node.text === "string") into.push(node.text);
    if (Array.isArray(node.content)) for (const c of node.content) walk(c, into);
  };
  const content = Array.isArray(rich.content) ? rich.content : [];
  for (const block of content) {
    const parts: string[] = [];
    walk(block, parts);
    blocks.push(parts.join(""));
  }
  return blocks.join("\n").replace(/\n+$/, "");
}

function labelFor(props: Dict, fontSizes: Record<string, number>): SkeletonLabel | undefined {
  const text = richTextToPlain(props.richText ?? props.text);
  if (!text.trim()) return undefined;
  const align = str(props.align ?? props.textAlign, "middle");
  return {
    text,
    fontSize: fontSizes[str(props.size)] ?? 22,
    fontFamily: FONT_FAMILY[str(props.font)] ?? 5,
    strokeColor: paletteColor(props.labelColor ?? props.color),
    textAlign: align.startsWith("start") ? "left" : align.startsWith("end") ? "right" : "center",
  };
}

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

// tldraw rotates about the shape's top-left corner; Excalidraw rotates `angle`
// about the centre. Same visual result requires moving the origin so the
// rotated centre matches.
function rowBox(el: CanvasElement, parentOrigin: { x: number; y: number }): BaseSkeleton {
  const w = Math.max(1, fin(el.w, 100));
  const h = Math.max(1, fin(el.h, 100));
  const r = fin(el.rotation, 0);
  const x = fin(el.x, 0) + parentOrigin.x;
  const y = fin(el.y, 0) + parentOrigin.y;
  if (r === 0) return { id: el.id, x, y, width: w, height: h, angle: 0 };
  const cx = x + (w / 2) * Math.cos(r) - (h / 2) * Math.sin(r);
  const cy = y + (w / 2) * Math.sin(r) + (h / 2) * Math.cos(r);
  return { id: el.id, x: cx - w / 2, y: cy - h / 2, width: w, height: h, angle: r };
}

// Excalidraw linear/freedraw elements keep points relative to (x, y) with the
// first point normally at the origin; a stroke that wanders left/above its
// tldraw origin is shifted so no point is negative and the bbox is tight.
function fromPoints(
  el: CanvasElement,
  pts: { x: number; y: number }[],
  parentOrigin: { x: number; y: number },
): { base: BaseSkeleton; points: [number, number][] } {
  const minX = Math.min(...pts.map((p) => p.x));
  const minY = Math.min(...pts.map((p) => p.y));
  const maxX = Math.max(...pts.map((p) => p.x));
  const maxY = Math.max(...pts.map((p) => p.y));
  const ox = fin(el.x, 0) + parentOrigin.x + minX;
  const oy = fin(el.y, 0) + parentOrigin.y + minY;
  return {
    base: { id: el.id, x: ox, y: oy, width: maxX - minX, height: maxY - minY, angle: fin(el.rotation, 0) },
    points: pts.map((p) => [p.x - minX, p.y - minY]),
  };
}

// Outline vertices for the geo kinds Excalidraw has no primitive for, in a
// unit box (0..1) that is scaled to the row bbox. Closed by repeating the
// first vertex, which is how Excalidraw draws a polygon with a line element.
function unitPolygon(geo: string): [number, number][] | null {
  const regular = (n: number, rot = -Math.PI / 2): [number, number][] =>
    Array.from({ length: n }, (_, i) => {
      const a = rot + (i * 2 * Math.PI) / n;
      return [0.5 + 0.5 * Math.cos(a), 0.5 + 0.5 * Math.sin(a)];
    });
  switch (geo) {
    case "triangle":
      return [
        [0.5, 0],
        [1, 1],
        [0, 1],
      ];
    case "pentagon":
      return regular(5);
    case "hexagon":
      return regular(6);
    case "octagon":
      return regular(8, -Math.PI / 8);
    case "star":
      return Array.from({ length: 10 }, (_, i) => {
        const a = -Math.PI / 2 + (i * Math.PI) / 5;
        const r = i % 2 === 0 ? 0.5 : 0.2;
        return [0.5 + r * Math.cos(a), 0.5 + r * Math.sin(a)];
      });
    case "rhombus":
      return [
        [0.25, 0],
        [1, 0],
        [0.75, 1],
        [0, 1],
      ];
    case "rhombus-2":
      return [
        [0, 0],
        [0.75, 0],
        [1, 1],
        [0.25, 1],
      ];
    case "trapezoid":
      return [
        [0.2, 0],
        [0.8, 0],
        [1, 1],
        [0, 1],
      ];
    case "arrow-right":
      return [
        [0, 0.25],
        [0.6, 0.25],
        [0.6, 0],
        [1, 0.5],
        [0.6, 1],
        [0.6, 0.75],
        [0, 0.75],
      ];
    case "arrow-left":
      return [
        [1, 0.25],
        [0.4, 0.25],
        [0.4, 0],
        [0, 0.5],
        [0.4, 1],
        [0.4, 0.75],
        [1, 0.75],
      ];
    case "arrow-up":
      return [
        [0.25, 1],
        [0.25, 0.4],
        [0, 0.4],
        [0.5, 0],
        [1, 0.4],
        [0.75, 0.4],
        [0.75, 1],
      ];
    case "arrow-down":
      return [
        [0.25, 0],
        [0.25, 0.6],
        [0, 0.6],
        [0.5, 1],
        [1, 0.6],
        [0.75, 0.6],
        [0.75, 0],
      ];
    case "heart": {
      // Parametric heart sampled and normalised into the unit box.
      const raw: [number, number][] = Array.from({ length: 40 }, (_, i) => {
        const t = (i * 2 * Math.PI) / 40;
        return [16 * Math.pow(Math.sin(t), 3), -(13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t))];
      });
      const xs = raw.map((p) => p[0]);
      const ys = raw.map((p) => p[1]);
      const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
      return raw.map(([px, py]) => [(px - x0) / (x1 - x0), (py - y0) / (y1 - y0)]);
    }
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Placeholder
// ---------------------------------------------------------------------------

export function isPlaceholder(s: ExcalidrawSkeleton): boolean {
  return s.customData?.taos_placeholder === true;
}

export function placeholderSkeleton(el: CanvasElement, originalType: string, reason?: string): ExcalidrawSkeleton {
  const box = rowBox(el, { x: 0, y: 0 });
  return {
    ...box,
    type: "rectangle",
    strokeColor: "#868e96",
    strokeStyle: "dashed",
    strokeWidth: 2,
    roughness: 0,
    backgroundColor: "#f1f3f5",
    fillStyle: "solid",
    label: {
      text: `tldraw ${originalType}: not convertible (element ${el.id}). Recover: canvas backup menu or ask your agent`,
      fontSize: 14,
      fontFamily: 6,
      strokeColor: "#495057",
      textAlign: "center",
    },
    customData: {
      taos_placeholder: true,
      taos_id: el.id,
      taos_original_element_id: el.id,
      taos_original_type: originalType,
      ...(reason ? { taos_placeholder_reason: reason } : {}),
    },
  };
}

// ---------------------------------------------------------------------------
// Per-type converters. Each throws on anything it cannot make sense of; the
// entry point turns a throw into a placeholder.
// ---------------------------------------------------------------------------

type Origin = { x: number; y: number };

function convertGeo(el: CanvasElement, props: Dict, origin: Origin): ExcalidrawSkeleton {
  const geo = str(props.geo, "rectangle");
  const box = rowBox(el, origin);
  const style = { ...strokeStyle(props), ...fillStyle(props) };
  const label = labelFor(props, LABEL_FONT_SIZE);
  const common = { ...box, ...style, ...(label ? { label } : {}) };
  if (geo === "rectangle" || geo === "x-box" || geo === "check-box") return { ...common, type: "rectangle" };
  if (geo === "ellipse" || geo === "oval" || geo === "cloud") return { ...common, type: "ellipse" };
  if (geo === "diamond") return { ...common, type: "diamond" };
  const unit = unitPolygon(geo);
  if (!unit) throw new Error(`unknown geo ${geo}`);
  const points: [number, number][] = unit.map(([ux, uy]) => [ux * box.width, uy * box.height]);
  const first = points[0];
  if (!first) throw new Error(`empty polygon for geo ${geo}`);
  points.push([first[0], first[1]]);
  return { ...common, type: "line", points };
}

function convertText(el: CanvasElement, props: Dict, origin: Origin): ExcalidrawSkeleton {
  const box = rowBox(el, origin);
  const text = richTextToPlain(props.richText ?? props.text);
  const align = str(props.textAlign, "start");
  return {
    ...box,
    type: "text",
    text,
    fontSize: FONT_SIZE[str(props.size)] ?? 24,
    fontFamily: FONT_FAMILY[str(props.font)] ?? 5,
    strokeColor: paletteColor(props.color),
    textAlign: align === "middle" ? "center" : align === "end" ? "right" : "left",
  };
}

function convertNote(el: CanvasElement, props: Dict, origin: Origin): ExcalidrawSkeleton {
  const box = rowBox(el, origin);
  const label = labelFor(props, LABEL_FONT_SIZE);
  return {
    ...box,
    type: "rectangle",
    backgroundColor: NOTE_BG[str(props.color)] ?? NOTE_BG.yellow,
    fillStyle: "solid",
    strokeColor: "transparent",
    strokeWidth: 1,
    roughness: 0,
    ...(label ? { label } : {}),
  };
}

function decodeSegments(props: Dict): DrawPoint[] {
  if (!Array.isArray(props.segments)) throw new Error("segments missing");
  const pts: DrawPoint[] = [];
  for (const seg of props.segments) {
    if (!isDict(seg)) throw new Error("segment not an object");
    if (typeof seg.path === "string") {
      pts.push(...decodeDrawPath(seg.path));
    } else if (Array.isArray(seg.points)) {
      // pre-4.x plain point arrays, kept for completeness
      for (const p of seg.points) {
        if (isDict(p)) pts.push({ x: fin(p.x, 0), y: fin(p.y, 0), z: fin(p.z, 0.5) });
      }
    } else {
      throw new Error("segment has neither path nor points");
    }
  }
  if (pts.length === 0) throw new Error("no points");
  const sx = fin(props.scale, 1) * fin(props.scaleX, 1);
  const sy = fin(props.scale, 1) * fin(props.scaleY, 1);
  return pts.map((p) => ({ x: p.x * sx, y: p.y * sy, z: p.z }));
}

function convertFreedraw(el: CanvasElement, props: Dict, origin: Origin, highlight: boolean): ExcalidrawSkeleton {
  // The stroke may be truncated at capture (the old board stored the shape on
  // its "added" event, often a single point); convert what is there.
  const pts = decodeSegments(props);
  const { base, points } = fromPoints(el, pts, origin);
  return {
    ...base,
    type: "freedraw",
    points,
    pressures: pts.map((p) => Math.min(1, Math.max(0, fin(p.z, 0.5)))),
    simulatePressure: false,
    strokeColor: paletteColor(props.color, highlight ? "#f1ac4b" : BLACK),
    strokeWidth: highlight ? 4 : (STROKE_WIDTH[str(props.size)] ?? 2),
    ...(highlight ? { opacity: 40 } : {}),
  };
}

function convertLine(el: CanvasElement, props: Dict, origin: Origin): ExcalidrawSkeleton {
  // 4.x stores points as an index-keyed dict; tldraw fractional indexes sort
  // lexicographically as strings.
  const dict = props.points;
  let raw: Dict[];
  if (isDict(dict)) {
    raw = Object.values(dict).filter(isDict);
    raw.sort((a, b) => (str(a.index) < str(b.index) ? -1 : str(a.index) > str(b.index) ? 1 : 0));
  } else if (Array.isArray(dict)) {
    raw = dict.filter(isDict);
  } else {
    throw new Error("points missing");
  }
  const scale = fin(props.scale, 1);
  const pts = raw.map((p) => ({ x: fin(p.x, NaN) * scale, y: fin(p.y, NaN) * scale }));
  if (pts.length < 2 || pts.some((p) => !Number.isFinite(p.x) || !Number.isFinite(p.y))) {
    throw new Error("line needs two finite points");
  }
  const { base, points } = fromPoints(el, pts, origin);
  return {
    ...base,
    type: "line",
    points,
    ...strokeStyle(props),
    ...(str(props.spline) === "cubic" ? { roundness: { type: 2 } } : { roundness: null }),
  };
}

function convertArrow(el: CanvasElement, props: Dict, origin: Origin): ExcalidrawSkeleton {
  // Bindings lived in separate tldraw records the old board never persisted,
  // so the arrow comes back unbound at its stored start/end.
  const s = props.start;
  const e = props.end;
  if (!isDict(s) || !isDict(e)) throw new Error("arrow needs start and end");
  const scale = fin(props.scale, 1);
  const a = { x: fin(s.x, NaN) * scale, y: fin(s.y, NaN) * scale };
  const b = { x: fin(e.x, NaN) * scale, y: fin(e.y, NaN) * scale };
  if (![a.x, a.y, b.x, b.y].every(Number.isFinite)) throw new Error("arrow endpoints not finite");
  const pts = [a];
  const bend = fin(props.bend, 0);
  if (bend !== 0) {
    // tldraw: mid = med(a,b) + per(unit(b - a)) * -bend, per(v) = (v.y, -v.x)
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len;
    const uy = dy / len;
    pts.push({ x: (a.x + b.x) / 2 + uy * -bend, y: (a.y + b.y) / 2 + -ux * -bend });
  }
  pts.push(b);
  const { base, points } = fromPoints(el, pts, origin);
  const label = labelFor({ ...props, align: "middle" }, { s: 18, m: 20, l: 24, xl: 28 });
  return {
    ...base,
    type: "arrow",
    points,
    ...strokeStyle(props),
    startArrowhead: ARROWHEAD[str(props.arrowheadStart, "none")] ?? null,
    endArrowhead: ARROWHEAD[str(props.arrowheadEnd, "arrow")] ?? "arrow",
    ...(bend !== 0 ? { roundness: { type: 2 } } : { roundness: null }),
    ...(label ? { label } : {}),
  };
}

function convertFrame(el: CanvasElement, props: Dict, origin: Origin, ctx?: LegacyContext): ExcalidrawSkeleton {
  const box = rowBox(el, origin);
  const mine = shapeIdOf(el);
  const children = (ctx?.rows ?? [])
    .filter((r) => r.id !== el.id && r.deleted_at == null && parentIdOf(r) === mine)
    .map((r) => r.id);
  return { ...box, type: "frame", name: str(props.name) || null, children };
}

// ---------------------------------------------------------------------------
// Parent resolution: a tldraw child's x/y are relative to its parent shape.
// ---------------------------------------------------------------------------

function blobOf(el: CanvasElement): Dict | null {
  const p = el.payload;
  if (!isDict(p)) return null;
  const blob = p.tldraw_shape;
  return isDict(blob) ? blob : null;
}

function shapeIdOf(el: CanvasElement): string {
  const blob = blobOf(el);
  const id = blob ? str(blob.id) : "";
  return id || `shape:${el.id}`;
}

function parentIdOf(el: CanvasElement): string | null {
  const blob = blobOf(el);
  const pid = blob ? str(blob.parentId) : "";
  return pid && pid.startsWith("shape:") ? pid : null;
}

function parentOrigin(el: CanvasElement, ctx?: LegacyContext): Origin {
  const origin = { x: 0, y: 0 };
  if (!ctx) return origin;
  const seen = new Set<string>([el.id]);
  let cur: CanvasElement | undefined = el;
  for (let depth = 0; cur && depth < 16; depth++) {
    const pid = parentIdOf(cur);
    if (!pid) break;
    const parent = ctx.rows.find((r) => shapeIdOf(r) === pid || `shape:${r.id}` === pid);
    if (!parent || seen.has(parent.id)) break;
    seen.add(parent.id);
    origin.x += fin(parent.x, 0);
    origin.y += fin(parent.y, 0);
    cur = parent;
  }
  return origin;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

const UNRECOVERABLE = new Set(["image", "video", "bookmark", "embed", "group"]);

export function legacyShapeToSkeleton(el: CanvasElement, ctx?: LegacyContext): ExcalidrawSkeleton {
  let type = "unknown";
  try {
    const blob = blobOf(el);
    if (!blob) return placeholderSkeleton(el, "unknown", "missing tldraw_shape");
    type = str(blob.type) || "unknown";
    if (UNRECOVERABLE.has(type)) return placeholderSkeleton(el, type, "not persisted by the old board");
    const props: Dict = isDict(blob.props) ? blob.props : {};
    const origin = parentOrigin(el, ctx);
    let out: ExcalidrawSkeleton;
    switch (type) {
      case "geo":
        out = convertGeo(el, props, origin);
        break;
      case "text":
        out = convertText(el, props, origin);
        break;
      case "note":
        out = convertNote(el, props, origin);
        break;
      case "draw":
        out = convertFreedraw(el, props, origin, false);
        break;
      case "highlight":
        out = convertFreedraw(el, props, origin, true);
        break;
      case "line":
        out = convertLine(el, props, origin);
        break;
      case "arrow":
        out = convertArrow(el, props, origin);
        break;
      case "frame":
        out = convertFrame(el, props, origin, ctx);
        break;
      default:
        return placeholderSkeleton(el, type, "unknown tldraw type");
    }
    if (![out.x, out.y, out.width, out.height].every(Number.isFinite)) throw new Error("non-finite geometry");
    const opacity = fin(blob.opacity, 1);
    return {
      ...out,
      ...(out.opacity === undefined && opacity < 1 ? { opacity: Math.round(opacity * 100) } : {}),
      customData: {
        ...(out.customData ?? {}),
        taos_id: el.id,
        taos_original_element_id: el.id,
        taos_original_type: type,
      },
    };
  } catch (err) {
    return placeholderSkeleton(el, type, err instanceof Error ? err.message : String(err));
  }
}
