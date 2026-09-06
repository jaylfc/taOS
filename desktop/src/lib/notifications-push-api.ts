/**
 * Fetch wrappers for /api/notifications/push/* (OS-level PWA web-push).
 * Separate key + subscription store from the Browser copilot push.
 * Read operations throw on error (the caller surfaces state); write
 * operations throw so the caller can reflect failure.
 */

export async function getVapidPublicKey(): Promise<string> {
  const resp = await fetch("/api/notifications/push/vapid-public-key", {
    credentials: "include",
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const body = await resp.json();
  if (typeof body?.public_key !== "string") throw new Error("Missing public_key in response");
  return body.public_key;
}

export async function subscribePush(subscription: {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}): Promise<{ ok: true }> {
  const resp = await fetch("/api/notifications/push/subscribe", {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ subscription }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return { ok: true };
}

export async function unsubscribePush(endpoint: string): Promise<{ ok: boolean }> {
  const resp = await fetch("/api/notifications/push/unsubscribe", {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ endpoint }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const body = await resp.json();
  return { ok: typeof body?.ok === "boolean" ? body.ok : true };
}
