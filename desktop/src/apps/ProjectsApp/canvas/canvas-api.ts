export type CanvasElementKind =
  | "note"
  | "link"
  | "image"
  | "user_shape"
  // Ideas-board kinds (#68), matching the backend store + REST.
  | "text"
  | "mermaid"
  | "flowchart"
  | "mindmap_edge";

export interface CanvasElement {
  id: string;
  project_id: string;
  kind: CanvasElementKind;
  author_kind: "user" | "agent";
  author_id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  rotation: number;
  z_index: number;
  payload: Record<string, unknown>;
  element_id: string | null;
  created_at: number;
  updated_at: number;
  deleted_at: number | null;
}

export interface CanvasElementInput {
  id?: string;
  kind: CanvasElementKind;
  x: number;
  y: number;
  w: number;
  h: number;
  rotation?: number;
  z_index?: number;
  payload: Record<string, unknown>;
  element_id?: string | null;
}

async function jsonOrThrow<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`canvas-api ${r.status}: ${body}`);
  }
  return r.json() as Promise<T>;
}

export const canvasApi = {
  async listElements(projectId: string, elementId?: string | null): Promise<CanvasElement[]> {
    const qs = elementId != null ? `?element_id=${encodeURIComponent(elementId)}` : "";
    const r = await fetch(`/api/projects/${projectId}/canvas/elements${qs}`);
    const body = await jsonOrThrow<{ elements: CanvasElement[] }>(r);
    return body.elements;
  },

  async addElement(
    projectId: string, input: CanvasElementInput,
  ): Promise<CanvasElement> {
    const r = await fetch(`/api/projects/${projectId}/canvas/elements`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
    const body = await jsonOrThrow<{ element: CanvasElement }>(r);
    return body.element;
  },

  async updateElement(
    projectId: string, elementId: string, patch: Partial<CanvasElementInput>,
  ): Promise<CanvasElement> {
    const r = await fetch(
      `/api/projects/${projectId}/canvas/elements/${elementId}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      },
    );
    const body = await jsonOrThrow<{ element: CanvasElement }>(r);
    return body.element;
  },

  async deleteElement(projectId: string, elementId: string): Promise<boolean> {
    const r = await fetch(
      `/api/projects/${projectId}/canvas/elements/${elementId}`,
      { method: "DELETE" },
    );
    return r.ok;
  },

  // Set a single canvas capability checkbox for an agent member. `flag` selects
  // which capability to flip: "read" (can_read_canvas) or "edit" (can_edit_canvas).
  // Both default OFF in the store, so the human ticks exactly what each agent
  // may do. The backend permission PATCH accepts either field independently.
  async setPermission(
    projectId: string, agentId: string, flag: "read" | "edit", allowed: boolean,
  ): Promise<void> {
    const body = flag === "read"
      ? { can_read_canvas: allowed }
      : { can_edit_canvas: allowed };
    const r = await fetch(
      `/api/projects/${projectId}/canvas/permissions/${agentId}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (!r.ok) throw new Error(`setPermission failed: ${r.status}`);
  },

  snapshotPngUrl(projectId: string): string {
    return `/api/projects/${projectId}/canvas/snapshot.png`;
  },
};
