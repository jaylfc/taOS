import { useEffect, useMemo, useRef } from "react";
import { Tldraw, Editor, createTLStore, defaultShapeUtils, TLShape } from "@tldraw/tldraw";
import "@tldraw/tldraw/tldraw.css";

import { canvasApi, CanvasElement } from "./canvas-api";
import { createCanvasStore } from "./canvas-store";
import { subscribeCanvasStream } from "./canvas-sse";
import { TaosNoteShapeUtil } from "./shapes/NoteShape";
import { TaosLinkShapeUtil } from "./shapes/LinkShape";
import { TaosImageShapeUtil } from "./shapes/ImageShape";
import { useIsMobile } from "../../../hooks/use-is-mobile";

interface CanvasBoardProps {
  projectId: string;
  projectSlug: string;
}

const CUSTOM_SHAPE_UTILS = [TaosNoteShapeUtil, TaosLinkShapeUtil, TaosImageShapeUtil];

export function CanvasBoard({ projectId, projectSlug }: CanvasBoardProps) {
  const isMobile = useIsMobile();
  const cacheRef = useRef(createCanvasStore());
  const editorRef = useRef<Editor | null>(null);

  // In tldraw v4, shapeUtils are passed to <Tldraw>, not createTLStore
  const store = useMemo(
    () => createTLStore({ defaultName: `canvas-${projectId}` }),
    [projectId],
  );

  // Initial load + SSE subscription
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const elements = await canvasApi.listElements(projectId);
      if (cancelled) return;
      cacheRef.current.getState().seed(elements);
      hydrateEditor(editorRef.current, elements, projectSlug);
    })();
    const unsub = subscribeCanvasStream(projectId, cacheRef.current);
    return () => {
      cancelled = true;
      unsub();
    };
  }, [projectId, projectSlug]);

  // Re-hydrate on local cache changes (SSE-driven updates from agents)
  useEffect(() => {
    const unsub = cacheRef.current.subscribe((s) => {
      hydrateEditor(editorRef.current, Object.values(s.elements), projectSlug);
    });
    return unsub;
  }, [projectSlug]);

  // Keep readonly state in sync if isMobile changes after mount (e.g. window resize)
  useEffect(() => {
    editorRef.current?.updateInstanceState({ isReadonly: isMobile });
  }, [isMobile]);

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <Tldraw
        store={store}
        shapeUtils={[...defaultShapeUtils, ...CUSTOM_SHAPE_UTILS] as any}
        onMount={(editor) => {
          editorRef.current = editor;
          editor.updateInstanceState({ isReadonly: isMobile });
          // Send local user edits to backend
          editor.store.listen(
            (entry) => {
              if (entry.source !== "user") return;
              const added = entry.changes.added as Record<string, TLShape | undefined>;
              for (const shape of Object.values(added)) {
                if (!shape || !shape.id.startsWith("shape:")) continue;
                pushAdd(projectId, shape).catch(console.warn);
              }
              const updated = entry.changes.updated as Record<string, [TLShape, TLShape] | undefined>;
              for (const pair of Object.values(updated)) {
                if (!pair) continue;
                const next = pair[1];
                if (!next.id.startsWith("shape:")) continue;
                pushUpdate(projectId, next).catch(console.warn);
              }
              const removed = entry.changes.removed as Record<string, TLShape | undefined>;
              for (const shape of Object.values(removed)) {
                if (!shape || !shape.id.startsWith("shape:")) continue;
                const elementId = shape.id.replace(/^shape:/, "");
                canvasApi.deleteElement(projectId, elementId).catch(console.warn);
              }
            },
            { source: "user", scope: "document" },
          );
        }}
      />
    </div>
  );
}

function hydrateEditor(
  editor: Editor | null,
  elements: CanvasElement[],
  projectSlug: string,
) {
  if (!editor) return;
  editor.run(() => {
    const wantedIds = new Set(elements.map((e) => `shape:${e.id}`));
    const existing = editor.getCurrentPageShapes();
    const toRemove = existing.filter((s) => !wantedIds.has(s.id) && s.id.toString().startsWith("shape:"));
    if (toRemove.length) editor.deleteShapes(toRemove.map((s) => s.id) as any);

    for (const el of elements) {
      const id = `shape:${el.id}` as TLShape["id"];
      const existingShape = editor.getShape(id);
      const newShape = elementToShape(el, projectSlug);
      if (existingShape) {
        editor.updateShape(newShape);
      } else {
        editor.createShape(newShape);
      }
    }
  });
}

function elementToShape(el: CanvasElement, projectSlug: string): any {
  const baseProps = {
    w: el.w, h: el.h,
    taos_kind: el.kind,
    taos_payload: el.payload,
    taos_author_id: el.author_id,
    taos_author_kind: el.author_kind,
  };
  return {
    id: `shape:${el.id}`,
    type: shapeType(el.kind),
    x: el.x, y: el.y, rotation: el.rotation,
    props: el.kind === "image"
      ? { ...baseProps, project_slug: projectSlug }
      : baseProps,
  };
}

function shapeType(kind: string): string {
  if (kind === "note") return "taos-note";
  if (kind === "link") return "taos-link";
  if (kind === "image") return "taos-image";
  return "geo";
}

async function pushAdd(projectId: string, shape: TLShape) {
  if (!shape.id.toString().startsWith("shape:")) return;
  const props: any = shape.props;
  await canvasApi.addElement(projectId, {
    id: shape.id.toString().replace(/^shape:/, ""),
    kind: (props.taos_kind ?? "user_shape") as any,
    x: shape.x, y: shape.y,
    w: props.w ?? 100, h: props.h ?? 100,
    rotation: shape.rotation,
    payload: props.taos_payload ?? { tldraw_shape: shape },
  });
}

async function pushUpdate(projectId: string, shape: TLShape) {
  const elementId = shape.id.toString().replace(/^shape:/, "");
  const props: any = shape.props;
  await canvasApi.updateElement(projectId, elementId, {
    x: shape.x, y: shape.y,
    w: props.w, h: props.h,
    rotation: shape.rotation,
    payload: props.taos_payload,
  });
}
