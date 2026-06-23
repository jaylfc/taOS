import { CanvasElement } from "./canvas-api";

// Engine-neutral mapping from a backend CanvasElement to a flat render node the
// Konva board draws directly. This is the MIT-engine analog of element-to-shape
// (which targets tldraw). The backend only validates `kind`, so every field is
// coerced to its declared type with a sensible default: a malformed element
// still places on the board instead of crashing the renderer.

export interface BaseNode {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  rotation: number;
  zIndex: number;
}

export type CanvasNode =
  | (BaseNode & { type: "note"; text: string; color: string; fontSize: number })
  | (BaseNode & { type: "link"; url: string; title: string; description: string })
  | (BaseNode & { type: "image"; fileId: string; alt: string; mime: string })
  | (BaseNode & { type: "shape" });

function num(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function str(v: unknown, fallback = ""): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return fallback;
}

export function elementToNode(el: CanvasElement): CanvasNode {
  const base: BaseNode = {
    id: el.id,
    x: num(el.x, 0),
    y: num(el.y, 0),
    w: num(el.w, 100),
    h: num(el.h, 100),
    rotation: num(el.rotation, 0),
    zIndex: num(el.z_index, 0),
  };
  const p = (el.payload ?? {}) as Record<string, unknown>;

  switch (el.kind) {
    case "note":
      return {
        ...base,
        type: "note",
        text: str(p.text, ""),
        color: str(p.color, "yellow"),
        fontSize: num(p.font_size, 14),
      };
    case "link":
      return {
        ...base,
        type: "link",
        url: str(p.url),
        title: str(p.title),
        description: str(p.description),
      };
    case "image":
      return {
        ...base,
        type: "image",
        fileId: str(p.file_id),
        alt: str(p.alt),
        mime: str(p.mime, "image/png"),
      };
    default:
      // user_shape and any unknown kind render as a generic box.
      return { ...base, type: "shape" };
  }
}

// Render order: skip soft-deleted elements, lowest z_index first (drawn first,
// so higher z_index sits on top).
export function elementsToNodes(elements: CanvasElement[]): CanvasNode[] {
  return elements
    .filter((el) => el.deleted_at == null)
    .map(elementToNode)
    .sort((a, b) => a.zIndex - b.zIndex);
}
