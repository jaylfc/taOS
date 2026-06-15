import { CanvasElement } from "./canvas-api";

// Maps a backend CanvasElement to a tldraw shape descriptor.
// Kept free of tldraw runtime imports so it can be unit tested in isolation.
//
// note/link/image use custom shape utils that declare the taos_* fields in
// their own props schema, so those fields go in props. Any other kind falls
// back to taos-generic, which only declares geometry props; its taos_* data
// goes in shape.meta (tldraw allows arbitrary meta) to avoid a ValidationError.
// Built-in tldraw shapes are never used here, so they never receive taos_* props.

export function shapeType(kind: string): string {
  if (kind === "note") return "taos-note";
  if (kind === "link") return "taos-link";
  if (kind === "image") return "taos-image";
  return "taos-generic";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function elementToShape(el: CanvasElement, projectSlug: string): any {
  const taosMeta = {
    taos_kind: el.kind,
    taos_payload: el.payload,
    taos_author_id: el.author_id,
    taos_author_kind: el.author_kind,
  };

  if (el.kind === "note" || el.kind === "link" || el.kind === "image") {
    const baseProps = { w: el.w, h: el.h, ...taosMeta };
    return {
      id: `shape:${el.id}`,
      type: shapeType(el.kind),
      x: el.x, y: el.y, rotation: el.rotation,
      props: el.kind === "image"
        ? { ...baseProps, project_slug: projectSlug }
        : baseProps,
    };
  }

  return {
    id: `shape:${el.id}`,
    type: "taos-generic",
    x: el.x, y: el.y, rotation: el.rotation,
    props: { w: el.w, h: el.h },
    meta: taosMeta,
  };
}
