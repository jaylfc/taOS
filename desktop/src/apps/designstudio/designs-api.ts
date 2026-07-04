/* ------------------------------------------------------------------ */
/*  Design Studio -- persistence API client (/api/designs)             */
/*                                                                     */
/*  Thin fetch wrapper mirroring the office/web studio persistence      */
/*  pattern: an opaque JSON `content` blob keyed by id, listed          */
/*  without content for the library sidebar, fetched in full on open.   */
/* ------------------------------------------------------------------ */

/** A design's saved content is capped to match the backend's limit so an
 *  oversized save (usually too many/too-large inlined images) can be
 *  caught here with a clear message rather than failing only at PUT time
 *  with a raw 413. */
export const MAX_CONTENT_BYTES = 5 * 1024 * 1024; // 5 MB

export interface SavedDesign {
  id: string;
  name: string;
  created_at?: number;
  updated_at?: number;
}

export interface DesignDoc extends SavedDesign {
  content: string;
}

async function parseErrorMessage(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return (body as { error?: string }).error || fallback;
}

export async function listDesigns(): Promise<SavedDesign[]> {
  const res = await fetch("/api/designs", { credentials: "include" });
  if (!res.ok) throw new Error("Could not load designs");
  return (await res.json()) as SavedDesign[];
}

export async function getDesign(id: string): Promise<DesignDoc> {
  const res = await fetch(`/api/designs/${encodeURIComponent(id)}`, { credentials: "include" });
  if (!res.ok) throw new Error("Could not open design");
  return (await res.json()) as DesignDoc;
}

export async function createDesign(name: string, content: string): Promise<DesignDoc> {
  const res = await fetch("/api/designs", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, content }),
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res, "Save failed"));
  return (await res.json()) as DesignDoc;
}

export async function updateDesign(
  id: string,
  name?: string,
  content?: string,
): Promise<DesignDoc> {
  const payload: { name?: string; content?: string } = {};
  if (name !== undefined) payload.name = name;
  if (content !== undefined) payload.content = content;
  const res = await fetch(`/api/designs/${encodeURIComponent(id)}`, {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await parseErrorMessage(res, "Save failed"));
  return (await res.json()) as DesignDoc;
}

export async function renameDesign(id: string, name: string): Promise<DesignDoc> {
  return updateDesign(id, name);
}

export async function deleteDesign(id: string): Promise<void> {
  const res = await fetch(`/api/designs/${encodeURIComponent(id)}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw new Error("Delete failed");
}
