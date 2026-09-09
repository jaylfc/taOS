import type { CanvasState } from "./canvas-store";
import type { StoreApi } from "zustand";
import type { CanvasElement } from "./canvas-api";
import { createSseConnection } from "@/lib/sse";

export interface CanvasEvent {
  type:
    | "canvas.element_added"
    | "canvas.element_updated"
    | "canvas.element_deleted"
    | "canvas.permission_changed";
  project_id: string;
  payload: Record<string, unknown>;
  ts: number;
}

export function subscribeCanvasStream(
  projectId: string,
  store: StoreApi<CanvasState>,
  onPermissionChanged?: (agentId: string, canEdit: boolean) => void,
): () => void {
  return createSseConnection({
    url: `/api/projects/${projectId}/canvas/stream`,
    onMessage: (msg) => {
      let evt: CanvasEvent;
      try {
        evt = JSON.parse(msg.data) as CanvasEvent;
      } catch {
        return;
      }
      if (evt.type === "canvas.element_added" || evt.type === "canvas.element_updated") {
        const el = evt.payload.element as CanvasElement | undefined;
        if (el) store.getState().upsert(el);
      } else if (evt.type === "canvas.element_deleted") {
        const id = evt.payload.element_id as string | undefined;
        if (id) store.getState().remove(id);
      } else if (evt.type === "canvas.permission_changed") {
        const agentId = evt.payload.agent_id as string | undefined;
        const canEdit = !!evt.payload.can_edit_canvas;
        if (agentId && onPermissionChanged) onPermissionChanged(agentId, canEdit);
      }
    },
  });
}
