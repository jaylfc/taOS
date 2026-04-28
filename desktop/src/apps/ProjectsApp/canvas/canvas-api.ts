export type CanvasElementKind = "note" | "link" | "image" | "user_shape";

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
}

async function jsonOrThrow<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`canvas-api ${r.status}: ${body}`);
  }
  return r.json() as Promise<T>;
}

export const canvasApi = {
  async listElements(projectId: string): Promise<CanvasElement[]> {
    const r = await fetch(`/api/projects/${projectId}/canvas/elements`);
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

  async setPermission(
    projectId: string, agentId: string, canEdit: boolean,
  ): Promise<void> {
    const r = await fetch(
      `/api/projects/${projectId}/canvas/permissions/${agentId}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ can_edit_canvas: canEdit }),
      },
    );
    if (!r.ok) throw new Error(`setPermission failed: ${r.status}`);
  },

  snapshotPngUrl(projectId: string): string {
    return `/api/projects/${projectId}/canvas/snapshot.png`;
  },
};
