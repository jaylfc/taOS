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
export interface ServiceWorkerStatus {
  state: "unregistered" | "registered" | "failed";
  error: string | null;
}

let status: ServiceWorkerStatus = { state: "unregistered", error: null };

/** The outcome of the last registerServiceWorker() call. A failed registration
 *  means taOS has no service worker and no fast-boot cache, so it is recorded
 *  where a human or a test can see it: here, on `<html data-taos-sw>`, and as a
 *  `taos:sw-registration-failed` window event, besides console.error. */
export function getServiceWorkerStatus(): ServiceWorkerStatus {
  return status;
}

function recordStatus(next: ServiceWorkerStatus): void {
  status = next;
  if (typeof document !== "undefined") {
    document.documentElement.dataset.taosSw = next.state;
  }
}

export async function registerServiceWorker(): Promise<void> {
  if (typeof navigator === "undefined" || !navigator.serviceWorker) return;
  try {
    // Wire the controllerchange -> reload listener at most once per SW
    // container, even if registerServiceWorker is called again (React
    // StrictMode double-invokes effects in dev, HMR, remounts). The marker
    // lives on the container instance so it resets naturally per test.
    const swc = navigator.serviceWorker as ServiceWorkerContainer & {
      __taosReloadWired?: boolean;
    };
    // Only wire it for a session that ALREADY has a controller: a first-ever
    // visit claims control with no stale page to fix, so a reload there is just
    // a wasted refresh.
    if (
      !swc.__taosReloadWired &&
      swc.controller &&
      typeof swc.addEventListener === "function"
    ) {
      swc.__taosReloadWired = true;
      let reloading = false;
      // controllerchange fires whenever the controlling SW changes. This app
      // registers one SW at one scope and never unregisters, so in practice it
      // means a newly-activated version took control -> reload once onto the
      // fresh index. (A manual unregister would also trigger it, which we do
      // not do.)
      swc.addEventListener("controllerchange", () => {
        if (reloading) return;
        reloading = true;
        window.location.reload();
      });
    }

    const registration = await navigator.serviceWorker.register("/sw.js");
    recordStatus({ state: "registered", error: null });

    // Proactively check for a new SW now (browsers otherwise only check on
    // navigation / ~24h) so a fresh deploy is picked up without waiting.
    if (registration && typeof registration.update === "function") {
      registration.update().catch((err) => {
        // Surface update failures (SW 404 / scope mismatch / offline) rather
        // than swallowing them, so deploy issues are visible in the console.
        console.debug("[taos] service worker update check failed:", err);
      });
    }
  } catch (err) {
    // Not thrown into the boot path, but not a quiet warning either: a failed
    // registration (e.g. a worker script the browser cannot parse) leaves the
    // app with no service worker at all.
    const error = String(err);
    recordStatus({ state: "failed", error });
    console.error("[taos] service worker registration failed:", err);
    if (typeof window !== "undefined" && typeof CustomEvent === "function") {
      window.dispatchEvent(
        new CustomEvent("taos:sw-registration-failed", { detail: { error } }),
      );
    }
  }
}
