import { CanvasElement } from "./canvas-api";
import { legacyShapeToSkeleton, type LegacyContext } from "./tldraw-to-excalidraw";

// Engine-neutral mapping from a backend CanvasElement to an Excalidraw skeleton
// element (the input shape `convertToExcalidrawElements` accepts). CanvasElement
// stays canonical; this is the view-side projection that targets Excalidraw's
// schema so the board can render the scene.
//
// The skeleton types below are a faithful subset of Excalidraw's
// `ExcalidrawElementSkeleton`. Keeping them local means this pure mapping (and
// its tests) carry no dependency on the heavy @excalidraw/excalidraw runtime,
// which the board wires in a later slice; the output stays assignable to
// `ExcalidrawElementSkeleton[]` when that slice imports the real type.
//
// The backend only validates `kind`, so every payload field is coerced to its
// declared type with a sensible default: a malformed element still places on the
// board instead of crashing the renderer.

export interface SkeletonLabel {
  text: string;
  fontSize?: number;
  fontFamily?: number;
  strokeColor?: string;
  textAlign?: "left" | "center" | "right";
  verticalAlign?: "top" | "middle" | "bottom";
}

export type SkeletonCustomData = Record<string, unknown>;

export interface BaseSkeleton {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  angle?: number;
  opacity?: number;
  customData?: SkeletonCustomData;
}

// Shared stroke/fill styling; each variant only carries what Excalidraw
// honours for that element type.
export interface StrokeStyleFields {
  strokeColor?: string;
  strokeWidth?: number;
  strokeStyle?: "solid" | "dashed" | "dotted";
  roughness?: number;
}

export type SkeletonArrowhead =
  | "arrow"
  | "bar"
  | "dot"
  | "circle"
  | "circle_outline"
  | "triangle"
  | "triangle_outline"
  | "diamond"
  | "diamond_outline"
  | null;

export type ExcalidrawSkeleton =
  | (BaseSkeleton &
      StrokeStyleFields & {
        type: "rectangle" | "ellipse" | "diamond";
        backgroundColor?: string;
        fillStyle?: "hachure" | "cross-hatch" | "solid" | "zigzag";
        roundness?: { type: number } | null;
        label?: SkeletonLabel;
      })
  | (BaseSkeleton & {
      type: "text";
      text: string;
      fontSize?: number;
      fontFamily?: number;
      textAlign?: "left" | "center" | "right";
      strokeColor?: string;
    })
  | (BaseSkeleton & {
      type: "image";
      fileId: string;
    })
  | (BaseSkeleton &
      StrokeStyleFields & {
        type: "arrow" | "line";
        // Points relative to (x, y); omitted for a bound mindmap edge, which
        // Excalidraw routes between its two endpoints itself.
        points?: [number, number][];
        backgroundColor?: string;
        fillStyle?: "hachure" | "cross-hatch" | "solid" | "zigzag";
        roundness?: { type: number } | null;
        startArrowhead?: SkeletonArrowhead;
        endArrowhead?: SkeletonArrowhead;
        label?: SkeletonLabel;
        start?: { id: string };
        end?: { id: string };
      })
  | (BaseSkeleton & {
      type: "freedraw";
      points: [number, number][];
      pressures: number[];
      simulatePressure: boolean;
      strokeColor?: string;
      strokeWidth?: number;
    })
  | (BaseSkeleton & {
      type: "frame";
      name: string | null;
      children: string[];
    });

function num(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function str(v: unknown, fallback = ""): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return fallback;
}

// First non-empty line of a diagram source, used as the placeholder label for
// mermaid/flowchart until the diagram-render slice converts the real source.
function firstLine(source: string, fallback: string): string {
  const line = source.split("\n").map((s) => s.trim()).find((s) => s.length > 0);
  return line || fallback;
}

// Sticky-note colour names map onto Excalidraw's background palette; unknown
// names fall back to the yellow note default.
const NOTE_BG: Record<string, string> = {
  yellow: "#ffec99",
  blue: "#a5d8ff",
  green: "#b2f2bb",
  red: "#ffc9c9",
  pink: "#ffc9c9",
  purple: "#d0bfff",
  orange: "#ffd8a8",
  gray: "#e9ecef",
  grey: "#e9ecef",
};

const NOTE_BG_DEFAULT = "#ffec99"; // yellow

function noteBackground(color: string): string {
  return NOTE_BG[color.toLowerCase()] ?? NOTE_BG_DEFAULT;
}

// Identity every skeleton carries back to its row, so the interactive board
// (and anything reading the scene) can map an Excalidraw element to the taOS
// element it came from without a side table.
function taosCustomData(el: CanvasElement): SkeletonCustomData {
  return {
    taos_id: el.id,
    taos_kind: el.kind,
    taos_author_id: el.author_id,
    taos_author_kind: el.author_kind,
  };
}

function withCustomData(s: ExcalidrawSkeleton, el: CanvasElement): ExcalidrawSkeleton {
  // Row identity wins over whatever a stored element claims about itself; a
  // converter-set flag such as taos_placeholder is kept.
  return { ...s, customData: { ...(s.customData ?? {}), ...taosCustomData(el) } };
}

// A user_shape row written by the Excalidraw board stores the element itself.
// It is used as-is (an Excalidraw element is a superset of its skeleton) with
// the id and geometry pinned to the row so a stale or hand-edited blob cannot
// move the element away from where the row says it is.
function nativeUserShape(el: CanvasElement, native: Record<string, unknown>): ExcalidrawSkeleton {
  const out = { ...native, id: el.id } as unknown as ExcalidrawSkeleton;
  if (!Number.isFinite(out.x)) out.x = num(el.x, 0);
  if (!Number.isFinite(out.y)) out.y = num(el.y, 0);
  if (!Number.isFinite(out.width)) out.width = num(el.w, 100);
  if (!Number.isFinite(out.height)) out.height = num(el.h, 100);
  return out;
}

export function elementToSkeleton(el: CanvasElement, ctx?: LegacyContext): ExcalidrawSkeleton {
  return withCustomData(elementToSkeletonInner(el, ctx), el);
}

function elementToSkeletonInner(el: CanvasElement, ctx?: LegacyContext): ExcalidrawSkeleton {
  const base: BaseSkeleton = {
    id: el.id,
    x: num(el.x, 0),
    y: num(el.y, 0),
    width: num(el.w, 100),
    height: num(el.h, 100),
    angle: num(el.rotation, 0),
  };
  const p = (el.payload ?? {}) as Record<string, unknown>;

  switch (el.kind) {
    case "note":
      return {
        ...base,
        type: "rectangle",
        backgroundColor: noteBackground(str(p.color, "yellow")),
        label: { text: str(p.text, ""), fontSize: num(p.font_size, 14) },
      };
    case "link":
      return {
        ...base,
        type: "rectangle",
        label: { text: str(p.title) || str(p.url) },
      };
    case "image":
      return { ...base, type: "image", fileId: str(p.file_id) };
    case "text":
      return {
        ...base,
        type: "text",
        text: str(p.text, ""),
        fontSize: num(p.font_size, 16),
        strokeColor: str(p.color, "#1e293b"),
      };
    case "mermaid":
      return { ...base, type: "rectangle", label: { text: firstLine(str(p.source), "mermaid") } };
    case "flowchart":
      return { ...base, type: "rectangle", label: { text: firstLine(str(p.source), "flowchart") } };
    case "mindmap_edge": {
      // Only bind an endpoint when its id is present. An empty-string id is
      // never a valid binding target, and an arrow with no bindings still
      // renders as a free-floating line rather than misbehaving.
      const from = str(p.from);
      const to = str(p.to);
      return {
        ...base,
        type: "arrow",
        ...(from ? { start: { id: from } } : {}),
        ...(to ? { end: { id: to } } : {}),
      };
    }
    case "user_shape": {
      // New rows drawn in Excalidraw carry the element itself; legacy rows
      // carry the tldraw snapshot and are converted at read time. A row with
      // neither still renders (as a placeholder), never nothing.
      const native = p.excalidraw_element;
      if (native && typeof native === "object" && !Array.isArray(native)) {
        return nativeUserShape(el, native as Record<string, unknown>);
      }
      return legacyShapeToSkeleton(el, ctx);
    }
    default:
      // Any unknown kind renders as a generic rectangle.
      return { ...base, type: "rectangle" };
  }
}

// Render order: skip soft-deleted elements, lowest z_index first so higher
// z_index sits on top (Excalidraw draws in array order).
export function elementsToSkeletons(elements: CanvasElement[]): ExcalidrawSkeleton[] {
  const live = elements.filter((el) => el.deleted_at == null);
  // Legacy tldraw frame/group children store parent-relative coordinates; the
  // full live set lets the converter resolve the parent origin.
  const ctx: LegacyContext = { rows: live };
  return live
    .slice()
    .sort((a, b) => num(a.z_index, 0) - num(b.z_index, 0))
    .map((el) => elementToSkeleton(el, ctx));
}
