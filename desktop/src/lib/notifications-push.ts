/**
 * OS-level PWA web-push enable/disable for taOS notifications.
 *
 * Wires the shell service worker (/sw.js, whose push + notificationclick
 * handlers already live in sw.ts) to the /api/notifications/push/* endpoints
 * so notifications reach an installed PWA even when taOS is closed.
 *
 * iOS caveat: Safari only exposes PushManager when the site has been added to
 * the Home Screen (running standalone). Before that, getPushState() reports
 * "needs-install" so the UI can tell the user to install first.
 */

import { getVapidPublicKey, subscribePush, unsubscribePush } from "./notifications-push-api";

export type PushState =
  | "enabled"
  | "disabled"
  | "denied"
  | "unsupported"
  | "needs-install";

export function urlBase64ToUint8Array(b64url: string): Uint8Array {
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

/** True when running as an installed / standalone PWA. */
function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  const mm = typeof window.matchMedia === "function"
    && window.matchMedia("(display-mode: standalone)").matches;
  // iOS Safari exposes the legacy navigator.standalone flag.
  const iosStandalone = (navigator as unknown as { standalone?: boolean }).standalone === true;
  return Boolean(mm || iosStandalone);
}

/** True when Notification + PushManager + serviceWorker are all present. */
export function isPushSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    "serviceWorker" in navigator &&
    typeof window !== "undefined" &&
    "PushManager" in window &&
    typeof Notification !== "undefined"
  );
}

/**
 * Report the current push state for this device without prompting or
 * subscribing. On iOS, an uninstalled PWA reports "needs-install".
 */
export async function getPushState(): Promise<PushState> {
  if (!isPushSupported()) {
    // On iOS the PushManager API is hidden until the PWA is installed. Detect
    // that case so the UI can prompt to add-to-Home-Screen rather than saying
    // "unsupported".
    const isIOS = typeof navigator !== "undefined"
      && /iP(hone|ad|od)/.test(navigator.userAgent)
      && !isStandalone();
    return isIOS ? "needs-install" : "unsupported";
  }
  if (Notification.permission === "denied") return "denied";
  try {
    const registration = await navigator.serviceWorker.ready;
    const existing = await registration.pushManager.getSubscription();
    return existing ? "enabled" : "disabled";
  } catch {
    return "disabled";
  }
}

/**
 * Enable OS notifications on this device: request permission, subscribe the
 * shell SW to the server VAPID key, and POST the subscription. Returns the
 * resulting state.
 */
export async function enableNotificationsPush(): Promise<PushState> {
  if (!isPushSupported()) return getPushState();
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    return permission === "denied" ? "denied" : "disabled";
  }
  const registration = await navigator.serviceWorker.ready;
  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    const vapidPublicKey = await getVapidPublicKey();
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(vapidPublicKey) as unknown as BufferSource,
    });
  }
  const p256dh = subscription.getKey("p256dh");
  const auth = subscription.getKey("auth");
  await subscribePush({
    endpoint: subscription.endpoint,
    keys: {
      p256dh: p256dh ? arrayBufferToBase64Url(p256dh) : "",
      auth: auth ? arrayBufferToBase64Url(auth) : "",
    },
  });
  return "enabled";
}

/**
 * Disable OS notifications on this device: unsubscribe the SW and tell the
 * server to drop the subscription. Idempotent.
 */
export async function disableNotificationsPush(): Promise<PushState> {
  if (!isPushSupported()) return getPushState();
  try {
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    if (subscription) {
      const endpoint = subscription.endpoint;
      await subscription.unsubscribe();
      await unsubscribePush(endpoint);
    }
  } catch {
    // Best-effort: even if the SW/network hiccups, report the intended state.
  }
  return "disabled";
}
