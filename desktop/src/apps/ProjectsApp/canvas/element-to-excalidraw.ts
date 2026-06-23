import { CanvasElement } from "./canvas-api";

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
  strokeColor?: string;
}

export interface BaseSkeleton {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  angle?: number;
}

export type ExcalidrawSkeleton =
  | (BaseSkeleton & {
      type: "rectangle" | "ellipse" | "diamond";
      backgroundColor?: string;
      strokeColor?: string;
      label?: SkeletonLabel;
    })
  | (BaseSkeleton & {
      type: "text";
      text: string;
      fontSize?: number;
      strokeColor?: string;
    })
  | (BaseSkeleton & {
      type: "image";
      fileId: string;
    })
  | (BaseSkeleton & {
      type: "arrow" | "line";
      strokeColor?: string;
      start?: { id: string };
      end?: { id: string };
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

export function elementToSkeleton(el: CanvasElement): ExcalidrawSkeleton {
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
    default:
      // user_shape and any unknown kind render as a generic rectangle.
      return { ...base, type: "rectangle" };
  }
}

// Render order: skip soft-deleted elements, lowest z_index first so higher
// z_index sits on top (Excalidraw draws in array order).
export function elementsToSkeletons(elements: CanvasElement[]): ExcalidrawSkeleton[] {
  return elements
    .filter((el) => el.deleted_at == null)
    .slice()
    .sort((a, b) => num(a.z_index, 0) - num(b.z_index, 0))
    .map(elementToSkeleton);
}
