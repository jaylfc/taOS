/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export type LibraryItemStatus = "pending" | "processing" | "ready" | "error";

export interface LibraryItem {
  id: string;
  kind: string;
  source_url: string;
  title: string;
  status: LibraryItemStatus;
  storage_path: string;
  bytes: number;
  meta_json: string;
  created_at: number;
  updated_at: number;
}

export interface LibraryArtifact {
  id: string;
  item_id: string;
  kind: string;
  path: string;
  meta_json: string;
  created_at: number;
}

export interface LibraryJob {
  id: string;
  item_id: string;
  stage: string;
  state: string;
  error: string;
  created_at: number;
  updated_at: number;
}

export interface LibraryItemDetail {
  item: LibraryItem;
  artifacts: LibraryArtifact[];
  jobs: LibraryJob[];
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function fetchJson<T>(url: string, fallback: T, init?: RequestInit): Promise<T> {
  try {
    const res = await fetch(url, { ...init, headers: { Accept: "application/json", ...init?.headers } });
    if (!res.ok) return fallback;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return fallback;
    return await res.json();
  } catch {
    return fallback;
  }
}

/* ------------------------------------------------------------------ */
/*  Items                                                              */
/* ------------------------------------------------------------------ */

export interface ListLibraryItemsParams {
  kind?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export async function listLibraryItems(
  params?: ListLibraryItemsParams,
): Promise<{ items: LibraryItem[]; count: number }> {
  const qs = new URLSearchParams();
  if (params?.kind) qs.set("kind", params.kind);
  if (params?.status) qs.set("status", params.status);
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));
  const query = qs.toString();
  const url = `/api/library/items${query ? `?${query}` : ""}`;
  const data = await fetchJson<{ items: LibraryItem[]; count: number }>(url, { items: [], count: 0 });
  return { items: Array.isArray(data.items) ? data.items : [], count: data.count ?? 0 };
}

export async function getLibraryItem(itemId: string): Promise<LibraryItemDetail | null> {
  try {
    const res = await fetch(`/api/library/items/${encodeURIComponent(itemId)}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function deleteLibraryItem(itemId: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/library/items/${encodeURIComponent(itemId)}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function reprocessLibraryItem(itemId: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/library/items/${encodeURIComponent(itemId)}/reprocess`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
    return res.ok;
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/*  Ingest                                                             */
/* ------------------------------------------------------------------ */

export interface IngestLibraryOptions {
  title?: string;
}

export async function ingestLibraryUrl(
  url: string,
  opts?: IngestLibraryOptions,
): Promise<{ item_id: string; status: string } | null> {
  try {
    const res = await fetch("/api/library/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ url, title: opts?.title ?? "" }),
    });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}
