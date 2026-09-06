/* ------------------------------------------------------------------ */
/*  Memory API client                                                  */
/* ------------------------------------------------------------------ */

import { withCsrf } from "./csrf";

const API = '/api';

async function fetchJson<T>(url: string, fallback: T, init?: RequestInit): Promise<T> {
  try {
    const headers = new Headers(init?.headers);
    headers.set("Accept", "application/json");
    const res = await fetch(url, { ...init, headers });
    if (!res.ok) return fallback;
    const ct = res.headers.get('content-type') ?? '';
    if (!ct.includes('application/json')) return fallback;
    return (await res.json()) as T;
  } catch {
    return fallback;
  }
}

/** Like fetchJson but throws on HTTP errors, network failures, and non-JSON
 *  responses so callers can surface errors instead of fabricating state. */
async function fetchJsonOrThrow<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    let detail = '';
    try { const body = await res.json(); detail = body?.detail || body?.error || ''; } catch { /* ignore */ }
    throw new Error(detail || `HTTP ${res.status}: ${res.statusText}`);
  }
  const ct = res.headers.get('content-type') ?? '';
  if (!ct.includes('application/json')) {
    throw new Error(`Expected JSON response, got ${ct || 'unknown content type'}`);
  }
  return (await res.json()) as T;
}

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface BackendCapabilities {
  name: string;
  version: string;
  capabilities: string[];
}

export interface TaOSmdEndpoint {
  url: string;
  is_local: boolean;
  reachable: boolean;
  tier?: string;
}

export interface CatalogSession {
  id: number;
  date: string;
  topic: string;
  description?: string;
  start_time?: string;
  end_time?: string;
  category?: string;
  sub_sessions?: CatalogSession[];
  crystal_narrative?: string;
  raw_lines?: string[];
}

/* ------------------------------------------------------------------ */
/*  Stats / capabilities / endpoint                                   */
/* ------------------------------------------------------------------ */

export async function fetchMemoryStats(): Promise<Record<string, any>> {
  return fetchJson(`${API}/memory/stats`, {});
}

export async function fetchBackendCapabilities(): Promise<BackendCapabilities> {
  return fetchJson(`${API}/memory/backend/capabilities`, { name: 'unknown', version: '0', capabilities: [] });
}

export async function fetchSettingsSchema(): Promise<Record<string, any>> {
  return fetchJson(`${API}/memory/backend/settings-schema`, {});
}

export async function fetchMemorySettings(): Promise<Record<string, any>> {
  return fetchJson(`${API}/memory/settings`, {});
}

export async function updateMemorySettings(settings: Record<string, any>): Promise<Record<string, any>> {
  return fetchJson(`${API}/memory/settings`, {}, withCsrf({
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  }));
}

export async function fetchMemoryEndpoint(): Promise<TaOSmdEndpoint> {
  return fetchJsonOrThrow<TaOSmdEndpoint>(`${API}/settings/memory-url`);
}

export async function updateMemoryEndpoint(url: string): Promise<TaOSmdEndpoint> {
  return fetchJsonOrThrow<TaOSmdEndpoint>(`${API}/settings/memory-url`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
}

/* ------------------------------------------------------------------ */
/*  Catalog                                                            */
/* ------------------------------------------------------------------ */

export async function fetchCatalogDate(date: string): Promise<any[]> {
  return fetchJson(`${API}/memory/catalog/date/${date}`, []);
}

export async function fetchCatalogSession(id: number): Promise<any> {
  return fetchJson(`${API}/memory/catalog/session/${id}`, null);
}

export async function fetchCatalogSessionContext(id: number): Promise<any> {
  return fetchJson(`${API}/memory/catalog/session/${id}/context`, null);
}

export async function triggerCatalogIndex(body: {
  date?: string;
  start_date?: string;
  end_date?: string;
  force?: boolean;
}): Promise<any> {
  return fetchJson(`${API}/memory/catalog/index`, {}, withCsrf({
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

export async function fetchCatalogSearch(query: string): Promise<any[]> {
  return fetchJson(`${API}/memory/catalog/search?q=${encodeURIComponent(query)}`, []);
}

export async function fetchCatalogStats(): Promise<Record<string, any>> {
  return fetchJson(`${API}/memory/catalog/stats`, {});
}

/* ------------------------------------------------------------------ */
/*  Agent memory config                                                */
/* ------------------------------------------------------------------ */

export async function fetchAgentMemoryConfig(name: string): Promise<Record<string, any>> {
  return fetchJson(`${API}/agents/${encodeURIComponent(name)}/memory-config`, {});
}

export async function updateAgentMemoryConfig(name: string, config: Record<string, any>): Promise<Record<string, any>> {
  return fetchJson(`${API}/agents/${encodeURIComponent(name)}/memory-config`, {}, withCsrf({
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  }));
}
