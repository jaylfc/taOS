/**
 * Bootstrap a push subscription if Notification.permission is 'granted'.
 * Idempotent — safe to call on every page load. Caches a stable device_id
 * in localStorage. No-ops on 'default' (caller decides when to prompt) or
 * 'denied'.
 */

import { getVapidPublicKey, subscribePush } from "./browser-push-api";
import { randomId } from "./uid";

const DEVICE_ID_KEY = "taos-browser:push-device-id";

function urlBase64ToUint8Array(b64url: string): Uint8Array {
  const padding = "=".repeat((4 - (b64url.length % 4)) % 4);
  const base64 = (b64url + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

function arrayBufferToBase64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]!);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

export async function bootstrapPushSubscription(): Promise<
  | { status: "subscribed"; device_id: string }
  | { status: "no-permission"; reason: "default" | "denied" }
  | { status: "unsupported" }
  | { status: "error"; error: Error }
> {
  try {
    // 1. Check browser support
    if (
      !("serviceWorker" in navigator) ||
      !navigator.serviceWorker ||
      !("PushManager" in window) ||
      !(window as unknown as Record<string, unknown>)["PushManager"]
    ) {
      return { status: "unsupported" };
    }

    // 2. Get or create stable device_id
    let device_id = localStorage.getItem(DEVICE_ID_KEY);
    if (!device_id) {
      device_id = randomId();
      localStorage.setItem(DEVICE_ID_KEY, device_id);
    }

    // 3. Check notification permission — do NOT prompt here
    if (typeof Notification === "undefined") {
      return { status: "unsupported" };
    }
    const permission = Notification.permission;
    if (permission === "default") {
      return { status: "no-permission", reason: "default" };
    }
    if (permission === "denied") {
      return { status: "no-permission", reason: "denied" };
    }

    // 4. Wait for service worker registration
    const registration = await navigator.serviceWorker.ready;

    // 5. Check for existing subscription
    const existing = await registration.pushManager.getSubscription();
    if (existing) {
      // Re-POST to keep last_seen_at fresh (idempotent upsert)
      const p256dhKey = existing.getKey("p256dh");
      const authKey = existing.getKey("auth");
      await subscribePush({
        device_id,
        endpoint: existing.endpoint,
        p256dh_key: p256dhKey ? arrayBufferToBase64Url(p256dhKey) : "",
        auth_key: authKey ? arrayBufferToBase64Url(authKey) : "",
        user_agent: navigator.userAgent,
      });
      return { status: "subscribed", device_id };
    }

    // 6. Subscribe — fetch VAPID key and call pushManager.subscribe
    const vapidPublicKey = await getVapidPublicKey();
    const applicationServerKey = urlBase64ToUint8Array(vapidPublicKey);
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: applicationServerKey as unknown as BufferSource,
    });

    // 7. Extract keys and POST subscription
    const p256dhBuffer = subscription.getKey("p256dh");
    const authBuffer = subscription.getKey("auth");
    await subscribePush({
      device_id,
      endpoint: subscription.endpoint,
      p256dh_key: p256dhBuffer ? arrayBufferToBase64Url(p256dhBuffer) : "",
      auth_key: authBuffer ? arrayBufferToBase64Url(authBuffer) : "",
      user_agent: navigator.userAgent,
    });

    // 8. Return success
    return { status: "subscribed", device_id };
  } catch (err) {
    return { status: "error", error: err instanceof Error ? err : new Error(String(err)) };
  }
}
