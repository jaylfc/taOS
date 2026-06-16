import { CanvasElement } from "./canvas-api";

// Maps a backend CanvasElement to a tldraw shape descriptor.
// Kept free of tldraw runtime imports so it can be unit tested in isolation.
//
// note/link/image use custom shape utils whose props schema is STRICT: tldraw
// validates every field's type at editor.createShape. The backend only
// validates `kind`, so an agent can store a note/link/image whose payload is
// missing a field or has the wrong type (e.g. font_size as the string "14", or
// an empty payload). Feeding that raw payload into props makes tldraw throw a
// ValidationError, so the element vanishes from the canvas (and, before the
// per-element guard in CanvasBoard, took the whole board down). To keep
// imperfect agent-authored content renderable, every payload field is coerced
// to its declared type with a sensible default before it reaches props.
//
// Any other kind falls back to taos-generic, which only declares geometry
// props; its taos_* data goes in shape.meta (tldraw allows arbitrary meta) to
// avoid a ValidationError. Built-in tldraw shapes are never used here, so they
// never receive taos_* props.

export function shapeType(kind: string): string {
  if (kind === "note") return "taos-note";
  if (kind === "link") return "taos-link";
  if (kind === "image") return "taos-image";
  return "taos-generic";
}

// --- Coercion helpers (pure, no tldraw imports so this stays unit-testable) ---

function num(v: unknown, fallback: number): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

function str(v: unknown, fallback = ""): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return fallback;
}

function authorKind(v: unknown): "user" | "agent" {
  return v === "agent" ? "agent" : "user";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function notePayload(p: any) {
  return {
    text: str(p?.text, ""),
    color: str(p?.color, "yellow"),
    font_size: num(p?.font_size, 14),
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function linkPayload(p: any) {
  return {
    url: str(p?.url),
    title: str(p?.title),
    description: str(p?.description),
    preview_image_url: str(p?.preview_image_url),
    favicon_url: str(p?.favicon_url),
    fetched_at: num(p?.fetched_at, 0),
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function imagePayload(p: any) {
  return {
    file_id: str(p?.file_id),
    alt: str(p?.alt),
    mime: str(p?.mime, "image/png"),
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function elementToShape(el: CanvasElement, projectSlug: string): any {
  // Geometry is validated too (T.number), so coerce it as well: a malformed
  // element still places on the board instead of throwing.
  const x = num(el.x, 0);
  const y = num(el.y, 0);
  const w = num(el.w, 100);
  const h = num(el.h, 100);
  const rotation = num(el.rotation, 0);

  if (el.kind === "note" || el.kind === "link" || el.kind === "image") {
    const taosFields = {
      taos_kind: el.kind,
      taos_payload:
        el.kind === "note"
          ? notePayload(el.payload)
          : el.kind === "link"
            ? linkPayload(el.payload)
            : imagePayload(el.payload),
      taos_author_id: str(el.author_id, "user"),
      taos_author_kind: authorKind(el.author_kind),
    };
    const baseProps = { w, h, ...taosFields };
    return {
      id: `shape:${el.id}`,
      type: shapeType(el.kind),
      x, y, rotation,
      props: el.kind === "image"
        ? { ...baseProps, project_slug: projectSlug }
        : baseProps,
    };
  }

  return {
    id: `shape:${el.id}`,
    type: "taos-generic",
    x, y, rotation,
    props: { w, h },
    // Generic props are unvalidated, so the raw payload round-trips untouched.
    meta: {
      taos_kind: el.kind,
      taos_payload: el.payload,
      taos_author_id: el.author_id,
      taos_author_kind: el.author_kind,
    },
  };
}
