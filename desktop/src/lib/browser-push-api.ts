/**
 * Fetch wrappers for /api/desktop/browser/push/*.
 * Read operations return empty/false on error; write operations throw.
 */

export interface PushSubscriptionInfo {
  device_id: string;
  endpoint: string;
  user_agent: string | null;
  created_at: number;
  last_seen_at: number;
}

export interface PushMute {
  agent_id: string;
  kind: "chat" | "drive-started" | "download-finished";
  muted_at: number;
}

export async function getVapidPublicKey(): Promise<string> {
  const resp = await fetch("/api/desktop/browser/push/vapid-public-key", {
    credentials: "include",
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const body = await resp.json();
  if (typeof body?.public_key !== "string") throw new Error("Missing public_key in response");
  return body.public_key;
}

export async function subscribePush(args: {
  device_id: string;
  endpoint: string;
  p256dh_key: string;
  auth_key: string;
  user_agent?: string | null;
}): Promise<{ ok: true }> {
  const resp = await fetch("/api/desktop/browser/push/subscribe", {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return { ok: true };
}

export async function listPushSubscriptions(): Promise<PushSubscriptionInfo[]> {
  try {
    const resp = await fetch("/api/desktop/browser/push/subscriptions", {
      credentials: "include",
    });
    if (!resp.ok) return [];
    const body = await resp.json();
    return Array.isArray(body?.subscriptions) ? body.subscriptions : [];
  } catch {
    return [];
  }
}

export async function unsubscribePush(device_id: string): Promise<{ ok: boolean }> {
  try {
    const resp = await fetch(
      `/api/desktop/browser/push/subscriptions/${encodeURIComponent(device_id)}`,
      { method: "DELETE", credentials: "include" },
    );
    if (!resp.ok) return { ok: false };
    const body = await resp.json();
    return { ok: typeof body?.ok === "boolean" ? body.ok : resp.ok };
  } catch {
    return { ok: false };
  }
}

export async function listPushMutes(): Promise<PushMute[]> {
  try {
    const resp = await fetch("/api/desktop/browser/push/mutes", {
      credentials: "include",
    });
    if (!resp.ok) return [];
    const body = await resp.json();
    return Array.isArray(body?.mutes) ? body.mutes : [];
  } catch {
    return [];
  }
}

export async function setPushMute(args: {
  agent_id: string;
  kind: "chat" | "drive-started" | "download-finished";
  muted: boolean;
}): Promise<{ ok: true }> {
  const resp = await fetch("/api/desktop/browser/push/mutes", {
    method: "PUT",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return { ok: true };
}
