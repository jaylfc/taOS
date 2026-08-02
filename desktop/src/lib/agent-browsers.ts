/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface BrowserProfile {
  id: string;
  agent_name: string | null;
  profile_name: string;
  node: string;
  status: "stopped" | "running" | "error";
  container_id: string | null;
  created_at: number;
  updated_at: number;
}

export interface LoginStatus {
  x: boolean;
  github: boolean;
  youtube: boolean;
  reddit: boolean;
}

export interface CookieEntry {
  name: string;
  value: string;
  domain: string;
  path: string;
  expires: number;
  httpOnly: boolean;
  secure: boolean;
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
  return fetchJson(url, fallback, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function putJson<T>(url: string, body: unknown, fallback: T): Promise<T> {
  return fetchJson(url, fallback, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/* ------------------------------------------------------------------ */
/*  Profiles                                                           */
/* ------------------------------------------------------------------ */

export async function listProfiles(agentName?: string): Promise<BrowserProfile[]> {
  const qs = agentName ? `?agent_name=${encodeURIComponent(agentName)}` : "";
  const data = await fetchJson<{ profiles: BrowserProfile[] }>(
    `/api/agent-browsers/profiles${qs}`,
    { profiles: [] },
  );
  return Array.isArray(data.profiles) ? data.profiles : [];
}

export async function createProfile(
  name: string,
  agentName?: string,
  node?: string,
): Promise<{ id: string; status: string } | null> {
  try {
    const res = await fetch("/api/agent-browsers/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ profile_name: name, agent_name: agentName ?? null, node: node ?? "local" }),
    });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function deleteProfile(id: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/agent-browsers/profiles/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function deleteProfileData(id: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/agent-browsers/profiles/${encodeURIComponent(id)}/data`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
    return res.ok;
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------------ */
/*  Browser lifecycle                                                  */
/* ------------------------------------------------------------------ */

export async function startBrowser(id: string): Promise<BrowserProfile | null> {
  const data = await postJson<BrowserProfile | null>(
    `/api/agent-browsers/profiles/${encodeURIComponent(id)}/start`,
    {},
    null,
  );
  return data;
}

export async function stopBrowser(id: string): Promise<BrowserProfile | null> {
  const data = await postJson<BrowserProfile | null>(
    `/api/agent-browsers/profiles/${encodeURIComponent(id)}/stop`,
    {},
    null,
  );
  return data;
}

/* ------------------------------------------------------------------ */
/*  Screenshot                                                         */
/* ------------------------------------------------------------------ */

export async function getScreenshot(id: string): Promise<string | null> {
  const data = await fetchJson<{ data?: string }>(
    `/api/agent-browsers/profiles/${encodeURIComponent(id)}/screenshot`,
    {},
  );
  return data.data ?? null;
}

/* ------------------------------------------------------------------ */
/*  Cookies                                                            */
/* ------------------------------------------------------------------ */

export async function getCookies(
  agent: string,
  profile: string,
  domain: string,
): Promise<CookieEntry[]> {
  const qs = `?domain=${encodeURIComponent(domain)}`;
  const data = await fetchJson<{ cookies: CookieEntry[] }>(
    `/api/agent-browsers/${encodeURIComponent(agent)}/${encodeURIComponent(profile)}/cookies${qs}`,
    { cookies: [] },
  );
  return Array.isArray(data.cookies) ? data.cookies : [];
}

/* ------------------------------------------------------------------ */
/*  Login status                                                       */
/* ------------------------------------------------------------------ */

export async function getLoginStatus(id: string): Promise<LoginStatus | null> {
  try {
    const res = await fetch(
      `/api/agent-browsers/profiles/${encodeURIComponent(id)}/login-status`,
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
/*  Agent assignment / node migration                                  */
/* ------------------------------------------------------------------ */

export async function assignAgent(id: string, agentName: string): Promise<BrowserProfile | null> {
  const data = await putJson<BrowserProfile | null>(
    `/api/agent-browsers/profiles/${encodeURIComponent(id)}/assign`,
    { agent_name: agentName },
    null,
  );
  return data;
}

export async function moveToNode(id: string, node: string): Promise<BrowserProfile | null> {
  const data = await putJson<BrowserProfile | null>(
    `/api/agent-browsers/profiles/${encodeURIComponent(id)}/move`,
    { node },
    null,
  );
  return data;
}
