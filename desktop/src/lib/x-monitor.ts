/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

import { withCsrf } from "./csrf";

export interface TweetMedia {
  type: string;
  url: string;
}

export interface Tweet {
  id: string;
  author: string;
  handle: string;
  text: string;
  likes: number;
  reposts: number;
  views: number;
  created_at: number;
  media: TweetMedia[];
}

export interface XThread {
  tweets: Tweet[];
  text: string;
}

export interface WatchFilters {
  all_posts: boolean;
  min_likes: number;
  threads_only: boolean;
  media_only: boolean;
}

export interface AuthorWatch {
  handle: string;
  filters: WatchFilters;
  frequency: number;
  enabled: boolean;
  last_check: number;
  created_at: number;
}

export interface XAuthStatus {
  authenticated: boolean;
  handle?: string;
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

async function postJson<T>(url: string, body: unknown, fallback: T): Promise<T> {
  return fetchJson(url, fallback, withCsrf({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

async function putJson<T>(url: string, body: unknown, fallback: T): Promise<T> {
  return fetchJson(url, fallback, withCsrf({
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

/* ------------------------------------------------------------------ */
/*  API functions                                                      */
/* ------------------------------------------------------------------ */

/**
 * Fetch a single tweet by ID via yt-dlp fallback.
 */
export async function fetchTweet(tweetId: string): Promise<Tweet | null> {
  return fetchJson<Tweet | null>(`/api/x/tweet/${encodeURIComponent(tweetId)}`, null);
}

/**
 * Fetch a tweet thread by tweet ID.
 * v1 returns a single-tweet thread; future versions reconstruct the full chain.
 */
export async function fetchThread(tweetId: string): Promise<XThread | null> {
  return fetchJson<XThread | null>(`/api/x/thread/${encodeURIComponent(tweetId)}`, null);
}

/**
 * Get X authentication status.
 * v1 always returns {authenticated: false}.
 */
export async function getAuthStatus(): Promise<XAuthStatus> {
  return fetchJson<XAuthStatus>("/api/x/auth/status", { authenticated: false });
}

/**
 * List all author watches.
 */
export async function listWatches(): Promise<AuthorWatch[]> {
  const data = await fetchJson<{ watches: AuthorWatch[] }>("/api/x/watches", { watches: [] });
  return Array.isArray(data.watches) ? data.watches : [];
}

/**
 * Create a new author watch.
 */
export async function createWatch(
  handle: string,
  filters?: Partial<WatchFilters>,
  frequency?: number,
): Promise<AuthorWatch | null> {
  return postJson<AuthorWatch | null>(
    "/api/x/watch",
    { handle, filters: filters ?? null, frequency: frequency ?? 1800 },
    null,
  );
}

/**
 * Update an existing author watch.
 */
export async function updateWatch(
  handle: string,
  updates: { filters?: Partial<WatchFilters>; frequency?: number; enabled?: boolean },
): Promise<AuthorWatch | null> {
  return putJson<AuthorWatch | null>(
    `/api/x/watch/${encodeURIComponent(handle)}`,
    updates,
    null,
  );
}

/**
 * Delete an author watch.
 */
export async function deleteWatch(handle: string): Promise<boolean> {
  const result = await fetchJson<{ deleted?: boolean } | null>(
    `/api/x/watch/${encodeURIComponent(handle)}`,
    null,
    withCsrf({ method: "DELETE" }),
  );
  return result?.deleted === true;
}

/**
 * Save a tweet URL to the Knowledge Library.
 */
export async function saveToLibrary(url: string): Promise<{ id: string; status: string } | null> {
  return postJson<{ id: string; status: string } | null>(
    "/api/knowledge/ingest",
    {
      url,
      title: "",
      text: "",
      categories: [],
      source: "x-monitor",
    },
    null,
  );
}
