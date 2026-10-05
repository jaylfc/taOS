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

/** Convert a data URL (as returned by FileReader.readAsDataURL) into a Blob.
 *
 * Fetching the data URL (`await (await fetch(data)).blob()`) works in browsers
 * but does extra work and fails in non-browser test environments (jsdom has no
 * fetch support for data URLs). Parse the `data:` prefix for the MIME type and
 * decode the base64 body straight into a Uint8Array.
 */
function dataUrlToBlob(dataUrl: string): Blob {
  const commaIndex = dataUrl.indexOf(",");
  const meta = dataUrl.slice(0, commaIndex);
  const encoded = dataUrl.slice(commaIndex + 1);
  const binary = atob(encoded);
  const u8 = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    u8[i] = binary.charCodeAt(i);
  }
  const type = meta.split(";")[0];
  const mimeType = type && type.indexOf(":") >= 0 ? type.slice(type.indexOf(":") + 1) : "application/octet-stream";
  return new Blob([u8], { type: mimeType });
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

  // Manual-recovery downloads. The .tldr opens in stock tldraw; the raw JSON is
  // every element row (soft-deleted ones included and flagged) with the
  // untouched payloads, so no drawing data is ever out of the user's reach.
  snapshotTldrUrl(projectId: string): string {
    return `/api/projects/${encodeURIComponent(projectId)}/canvas/snapshot.tldr`;
  },

  elementsJsonUrl(projectId: string): string {
    return `/api/projects/${encodeURIComponent(projectId)}/canvas/elements?include_deleted=true`;
  },

  // Upload one or more canvas image files and return the entries Excalidraw
  // needs to render them (id + dataURL). Each file is fetched from the
  // project's same-origin canvas file store and POSTed back as a multipart
  // upload into files/canvas so it is persisted alongside the element rows.
  async addFiles(
    projectSlug: string,
    files: { file_id: string; data: string }[],
  ): Promise<{ id: string; data: string }[]> {
    await Promise.all(
      files.map(async ({ file_id, data }) => {
        const blob = dataUrlToBlob(data);
        const form = new FormData();
        form.set("file", blob, file_id);
        form.set("path", "canvas");
        const r = await fetch(`/api/projects/${projectSlug}/files/upload?path=canvas`, {
          method: "POST",
          body: form,
        });
        if (!r.ok) {
          const body = await r.text();
          throw new Error(`addFiles upload ${file_id}: ${r.status} ${body}`);
        }
        // Response body is ignored; the caller rebuilds entries from the inputs below.
        return { id: file_id, data };
      }),
    );
    return files.map(({ file_id, data }) => ({ id: file_id, data }));
  },
};
