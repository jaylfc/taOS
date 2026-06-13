import { withCsrf } from "./csrf";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface GitHubRepo {
  owner: string;
  name: string;
  description: string;
  stars: number;
  forks: number;
  language: string;
  license: string;
  updated_at: string;
  topics: string[];
  readme_content?: string;
}

export interface GitHubComment {
  author: string;
  body: string;
  created_at: string;
  reactions: Record<string, number>;
}

export interface GitHubIssue {
  number: number;
  title: string;
  state: string;
  author: string;
  body: string;
  labels: string[];
  comments: GitHubComment[];
  created_at: string;
  repo: string;
  is_pull_request: boolean;
}

export interface GitHubRelease {
  tag: string;
  name: string;
  body: string;
  author: string;
  published_at: string;
  assets: { name: string; size: number; download_count: number }[];
  prerelease: boolean;
}

export interface GitHubAuthStatus {
  authenticated: boolean;
  username?: string;
  method?: string;
}

export interface GitHubIdentity {
  id: string;
  login: string;
  avatar_url: string;
  created_at: number;
}

export interface DeviceStart {
  user_code: string;
  verification_uri: string;
  device_code: string;
  interval: number;
  expires_in: number;
}

export type DevicePoll =
  | { status: "pending"; slow_down?: boolean }
  | { status: "connected"; identity: GitHubIdentity }
  | { status: "error"; error: string };

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

async function postJson<T>(url: string, body: unknown, fallback: T): Promise<T> {
  return fetchJson(url, fallback, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/* ------------------------------------------------------------------ */
/*  Starred Repos                                                      */
/* ------------------------------------------------------------------ */

export async function fetchStarred(
  page?: number,
): Promise<{ repos: GitHubRepo[]; total: number }> {
  const qs = new URLSearchParams();
  if (page != null) qs.set("page", String(page));
  const query = qs.toString();
  const url = `/api/github/starred${query ? `?${query}` : ""}`;
  const data = await fetchJson<{ repos: GitHubRepo[]; total: number }>(url, { repos: [], total: 0 });
  return { repos: Array.isArray(data.repos) ? data.repos : [], total: data.total ?? 0 };
}

/* ------------------------------------------------------------------ */
/*  Notifications                                                      */
/* ------------------------------------------------------------------ */

export async function fetchNotifications(): Promise<{
  notifications: GitHubIssue[];
  unread_count: number;
}> {
  const data = await fetchJson<{ notifications: GitHubIssue[]; unread_count: number }>(
    "/api/github/notifications",
    { notifications: [], unread_count: 0 },
  );
  return {
    notifications: Array.isArray(data.notifications) ? data.notifications : [],
    unread_count: data.unread_count ?? 0,
  };
}

/* ------------------------------------------------------------------ */
/*  Repo                                                               */
/* ------------------------------------------------------------------ */

export async function fetchRepo(owner: string, repo: string): Promise<GitHubRepo | null> {
  try {
    const res = await fetch(`/api/github/repo/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`, {
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

/* ------------------------------------------------------------------ */
/*  Issues                                                             */
/* ------------------------------------------------------------------ */

export async function fetchIssues(
  owner: string,
  repo: string,
  state?: string,
  page?: number,
): Promise<{ issues: GitHubIssue[]; total: number }> {
  const qs = new URLSearchParams();
  if (state) qs.set("state", state);
  if (page != null) qs.set("page", String(page));
  const query = qs.toString();
  const url = `/api/github/repo/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/issues${query ? `?${query}` : ""}`;
  const data = await fetchJson<{ issues: GitHubIssue[]; total: number }>(url, { issues: [], total: 0 });
  return { issues: Array.isArray(data.issues) ? data.issues : [], total: data.total ?? 0 };
}

export async function fetchIssue(
  owner: string,
  repo: string,
  number: number,
): Promise<GitHubIssue | null> {
  try {
    const res = await fetch(
      `/api/github/repo/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/issues/${number}`,
      { headers: { Accept: "application/json" } },
    );
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/*  Releases                                                           */
/* ------------------------------------------------------------------ */

export async function fetchReleases(owner: string, repo: string): Promise<GitHubRelease[]> {
  const data = await fetchJson<{ releases: GitHubRelease[] }>(
    `/api/github/repo/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/releases`,
    { releases: [] },
  );
  return Array.isArray(data.releases) ? data.releases : [];
}

/* ------------------------------------------------------------------ */
/*  Auth                                                               */
/* ------------------------------------------------------------------ */

export async function getAuthStatus(): Promise<GitHubAuthStatus> {
  return fetchJson<GitHubAuthStatus>("/api/github/auth/status", { authenticated: false });
}

/* ------------------------------------------------------------------ */
/*  Save to Library                                                    */
/* ------------------------------------------------------------------ */

export async function saveToLibrary(url: string): Promise<{ id: string; status: string } | null> {
  return postJson<{ id: string; status: string } | null>("/api/knowledge/ingest", {
    url,
    title: "",
    text: "",
    categories: [],
    source: "github-browser",
  }, null);
}

/* ------------------------------------------------------------------ */
/*  OAuth Device Flow (Connect GitHub)                                 */
/* ------------------------------------------------------------------ */

export async function startDeviceFlow(): Promise<DeviceStart> {
  const res = await fetch(
    "/api/github/oauth/device/start",
    withCsrf({ method: "POST", headers: { Accept: "application/json" } }),
  );
  if (!res.ok) throw new Error("Failed to start GitHub connect");
  return res.json();
}

export async function pollDeviceFlow(deviceCode: string): Promise<DevicePoll> {
  const res = await fetch(
    "/api/github/oauth/device/poll",
    withCsrf({
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ device_code: deviceCode }),
    }),
  );
  if (!res.ok) return { status: "error", error: "poll_failed" };
  return res.json();
}

export async function listIdentities(): Promise<GitHubIdentity[]> {
  return fetchJson<GitHubIdentity[]>("/api/github/identities", []);
}

export async function deleteIdentity(id: string): Promise<boolean> {
  const res = await fetch(
    `/api/github/identities/${encodeURIComponent(id)}`,
    withCsrf({ method: "DELETE", headers: { Accept: "application/json" } }),
  );
  return res.ok;
}
