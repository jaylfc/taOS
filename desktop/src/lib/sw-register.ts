/**
 * Register the SPA's service worker once on boot.
 * Safe to call from a useEffect - does nothing on browsers without
 * service worker support and never throws.
 *
 * On a returning session (already controlled by a SW), reload once when a new
 * SW takes control. A redeploy ships a new hashed bundle; the old page keeps
 * referencing now-404 chunk URLs until it reloads, which otherwise surfaces as
 * a ChunkLoadError loop (e.g. opening an app after a deploy). skipWaiting +
 * clients.claim in sw.ts make the new SW activate promptly; this reload swaps
 * the page onto the fresh index so its chunk refs match the deployed assets.
 */
export async function registerServiceWorker(): Promise<void> {
  if (typeof navigator === "undefined" || !navigator.serviceWorker) return;
  try {
    // Only wire the auto-reload for a session that ALREADY has a controller.
    // On a first-ever visit the SW claims control with no stale page to fix,
    // so reloading there would just be a wasted refresh.
    if (
      navigator.serviceWorker.controller &&
      typeof navigator.serviceWorker.addEventListener === "function"
    ) {
      let reloading = false;
      navigator.serviceWorker.addEventListener("controllerchange", () => {
        if (reloading) return;
        reloading = true;
        window.location.reload();
      });
    }

    const registration = await navigator.serviceWorker.register("/sw.js");

    // Proactively check for a new SW now (browsers otherwise only check on
    // navigation / ~24h), so a fresh deploy is picked up without waiting.
    if (registration && typeof registration.update === "function") {
      registration.update().catch(() => {});
    }
  } catch (err) {
    console.warn("[taos] service worker registration failed:", err);
  }
}
