/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

import { withCsrf } from "./csrf";

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
    const headers = new Headers(init?.headers);
    headers.set("Accept", "application/json");
    const res = await fetch(url, { ...init, headers });
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
    const res = await fetch(`/api/library/items/${encodeURIComponent(itemId)}`, withCsrf({
      method: "DELETE",
      headers: { Accept: "application/json" },
    }));
    return res.ok;
  } catch {
    return false;
  }
}

export async function reprocessLibraryItem(itemId: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/library/items/${encodeURIComponent(itemId)}/reprocess`, withCsrf({
      method: "POST",
      headers: { Accept: "application/json" },
    }));
    return res.ok;
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/*  Jobs                                                               */
/* ------------------------------------------------------------------ */

export interface ListLibraryJobsParams {
  state?: string;
  limit?: number;
}

/** List ingest pipeline jobs (queued, processing, error, done, ...).
 *  Throws when the endpoint is unreachable so the caller can distinguish
 *  "no jobs" from "route missing".
 */
export async function listLibraryJobs(
  params?: ListLibraryJobsParams,
): Promise<{ jobs: LibraryJob[] }> {
  const qs = new URLSearchParams();
  if (params?.state) qs.set("state", params.state);
  if (params?.limit != null) qs.set("limit", String(params.limit));
  const query = qs.toString();
  const url = `/api/library/jobs${query ? `?${query}` : ""}`;
  const headers = new Headers();
  headers.set("Accept", "application/json");
  const res = await fetch(url, { headers });
  if (!res.ok) throw new Error(`Library jobs endpoint returned ${res.status}`);
  const ct = res.headers.get("content-type") ?? "";
  if (!ct.includes("application/json")) throw new Error("Library jobs endpoint returned non-JSON");
  const data = await res.json();
  return { jobs: Array.isArray(data.jobs) ? data.jobs : [] };
}

/** Retry a failed job. */
export async function retryLibraryJob(jobId: string): Promise<boolean> {
  try {
    const res = await fetch(
      `/api/library/jobs/${encodeURIComponent(jobId)}/retry`,
      withCsrf({ method: "POST", headers: { Accept: "application/json" } }),
    );
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
    const res = await fetch("/api/library/ingest", withCsrf({
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ url, title: opts?.title ?? "" }),
    }));
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/*  Settings (#2060)                                                   */
/* ------------------------------------------------------------------ */

/** Quality values in the backend vocabulary (yt-dlp format heights). */
export const LIBRARY_QUALITIES: string[] = ["360", "480", "720", "1080", "best"];

export interface LibrarySettings {
  preferred_quality: string;
  storage_cap_bytes: number;
}

export const DEFAULT_LIBRARY_SETTINGS: LibrarySettings = {
  preferred_quality: "720",
  storage_cap_bytes: 50 * 1024 * 1024 * 1024,
};

export async function getLibrarySettings(): Promise<LibrarySettings> {
  const data = await fetchJson<Partial<LibrarySettings>>(
    "/api/library/settings",
    {},
  );
  return {
    preferred_quality:
      typeof data.preferred_quality === "string"
        ? data.preferred_quality
        : DEFAULT_LIBRARY_SETTINGS.preferred_quality,
    storage_cap_bytes:
      typeof data.storage_cap_bytes === "number"
        ? data.storage_cap_bytes
        : DEFAULT_LIBRARY_SETTINGS.storage_cap_bytes,
  };
}

/** Persist settings; returns the saved settings, or null on failure. */
export async function updateLibrarySettings(
  updates: Partial<LibrarySettings>,
): Promise<LibrarySettings | null> {
  try {
    const res = await fetch("/api/library/settings", withCsrf({
      method: "PUT",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(updates),
    }));
    if (!res.ok) return null;
    const data = (await res.json()) as Partial<LibrarySettings>;
    return {
      preferred_quality:
        typeof data.preferred_quality === "string"
          ? data.preferred_quality
          : DEFAULT_LIBRARY_SETTINGS.preferred_quality,
      storage_cap_bytes:
        typeof data.storage_cap_bytes === "number"
          ? data.storage_cap_bytes
          : DEFAULT_LIBRARY_SETTINGS.storage_cap_bytes,
    };
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/*  Source rules (#2060)                                               */
/* ------------------------------------------------------------------ */

export interface LibrarySourceRule {
  id: string;
  source_pattern: string;
  quality: string;
  auto_download: number | boolean;
  enabled: number | boolean;
  created_at: number;
}

export async function listLibrarySourceRules(): Promise<{
  rules: LibrarySourceRule[];
  count: number;
}> {
  const data = await fetchJson<{ rules: LibrarySourceRule[]; count: number }>(
    "/api/library/rules",
    { rules: [], count: 0 },
  );
  return {
    rules: Array.isArray(data.rules) ? data.rules : [],
    count: data.count ?? 0,
  };
}

export type CreateRuleResult =
  | { ok: true; rule_id: string }
  | { ok: false; status: number };

export async function createLibrarySourceRule(input: {
  source_pattern: string;
  quality: string;
  auto_download: boolean;
}): Promise<CreateRuleResult> {
  try {
    const form = new FormData();
    form.set("source_pattern", input.source_pattern);
    form.set("quality", input.quality);
    form.set("auto_download", input.auto_download ? "true" : "false");
    const res = await fetch("/api/library/rules", withCsrf({
      method: "POST",
      headers: { Accept: "application/json" },
      body: form,
    }));
    if (!res.ok) return { ok: false, status: res.status };
    const data = (await res.json()) as { rule_id?: string };
    return { ok: true, rule_id: data.rule_id ?? "" };
  } catch {
    return { ok: false, status: 0 };
  }
}

export async function deleteLibrarySourceRule(ruleId: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/library/rules/${encodeURIComponent(ruleId)}`, withCsrf({
      method: "DELETE",
      headers: { Accept: "application/json" },
    }));
    return res.ok;
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/*  Storage accounting (#2060)                                         */
/* ------------------------------------------------------------------ */

export interface LibraryUsageSource {
  source_type: string;
  bytes: number;
  item_count: number;
}

export interface LibraryUsageItem {
  id: string;
  title: string;
  source_type: string;
  bytes: number;
}

export interface LibraryUsage {
  total_count: number;
  total_bytes: number;
  by_kind: Record<string, { count: number; bytes: number }>;
  by_source: Record<string, { count: number; bytes: number }>;
  sources: LibraryUsageSource[];
  items: LibraryUsageItem[];
  cap_bytes: number;
  paused_at_cap: boolean;
}

/** Fetch storage accounting; null when the endpoint is unavailable. */
export async function getLibraryUsage(): Promise<LibraryUsage | null> {
  try {
    const res = await fetch("/api/library/usage", {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    const data = (await res.json()) as Partial<LibraryUsage>;
    return {
      total_count: data.total_count ?? 0,
      total_bytes: data.total_bytes ?? 0,
      by_kind: data.by_kind ?? {},
      by_source: data.by_source ?? {},
      sources: Array.isArray(data.sources) ? data.sources : [],
      items: Array.isArray(data.items) ? data.items : [],
      cap_bytes: data.cap_bytes ?? 0,
      paused_at_cap: data.paused_at_cap ?? false,
    };
  } catch {
    return null;
  }
}
