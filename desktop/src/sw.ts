/// <reference lib="WebWorker" />
/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * taOS service worker.
 *
 * Caches the SPA shell so the UI loads when the backend is unreachable
 * (e.g. mid-restart after Install Update). Scope: '/' — covers both
 * /desktop and /chat-pwa. Strategy:
 *  - cache-first for /desktop/assets/* (immutable hashed URLs)
 *  - stale-while-revalidate for /desktop/index.html, /chat-pwa,
 *    static manifests and icons
 *  - passes everything else through (/api/*, /ws/*, ...)
 *
 * No app logic, no postMessage, no polling. The reconnect / version
 * UX lives entirely in app code.
 */
declare const self: ServiceWorkerGlobalScope;
declare const __TAOS_VERSION__: string;
export {};

const VERSION = __TAOS_VERSION__;
const STATIC_CACHE = `taos-static-${VERSION}`;

const PRECACHE_URLS = [
  "/desktop/",
  "/desktop/index.html",
  "/chat-pwa",
  "/static/manifest-desktop.json",
  "/static/manifest-chat.json",
  "/static/favicon.ico",
  "/static/icon-16.png",
  "/static/icon-32.png",
  "/static/icon-180.png",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

self.addEventListener("install", (event: ExtendableEvent) => {
  event.waitUntil((async () => {
    const cache = await caches.open(STATIC_CACHE);
    // Per-URL add so a single missing asset doesn't abort the whole install.
    // Optional precache misses (e.g. an icon that wasn't shipped this build)
    // shouldn't break the SW — log and continue.
    const results = await Promise.allSettled(PRECACHE_URLS.map((url) => cache.add(url)));
    results.forEach((r, i) => {
      if (r.status === "rejected") {
        console.warn("[sw] precache failed for", PRECACHE_URLS[i], r.reason);
      }
    });
  })());
  self.skipWaiting();
});

self.addEventListener("activate", (event: ExtendableEvent) => {
  event.waitUntil(
    (async () => {
      // Drop old taos-static-* caches from previous SW versions.
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((k) => k.startsWith("taos-static-") && k !== STATIC_CACHE)
            .map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

function isImmutableAsset(url: URL): boolean {
  return url.pathname.startsWith("/desktop/assets/");
}

function isShellHTML(url: URL): boolean {
  if (url.pathname === "/desktop/" || url.pathname === "/desktop/index.html") return true;
  if (url.pathname === "/chat-pwa" || url.pathname.startsWith("/chat-pwa/")) return true;
  return false;
}

function isPrecachedStatic(url: URL): boolean {
  return PRECACHE_URLS.includes(url.pathname);
}

self.addEventListener("fetch", (event: FetchEvent) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  // Same-origin only; never intercept API or WebSocket traffic.
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;
  if (url.pathname.startsWith("/ws/")) return;

  if (isImmutableAsset(url)) {
    // Cache-first: hashed asset filenames are by definition immutable.
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const hit = await cache.match(req);
        if (hit) return hit;
        const fresh = await fetch(req);
        if (fresh.ok) cache.put(req, fresh.clone());
        return fresh;
      })
    );
    return;
  }

  if (isShellHTML(url) || isPrecachedStatic(url)) {
    // Stale-while-revalidate: serve cache instantly, refresh in background.
    // For chat-pwa subpaths (e.g. /chat-pwa/foo), serve cached /chat-pwa.
    const cacheKey = isShellHTML(url) && url.pathname.startsWith("/chat-pwa")
      ? new Request("/chat-pwa")
      : (isShellHTML(url) && url.pathname !== "/desktop/index.html"
          ? new Request("/desktop/")
          : req);
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const hit = await cache.match(cacheKey);
        const network = fetch(req).then((r) => {
          if (r.ok) cache.put(cacheKey, r.clone());
          return r;
        }).catch((err) => {
          // If we have a cached copy, fall back to it. Otherwise propagate
          // the network error so the browser shows a normal failure rather
          // than crashing the SW handler with an undefined Response.
          if (hit) return hit;
          throw err;
        });
        if (hit) return hit;
        return network;
      })
    );
    return;
  }

  // Everything else: pass through.
});

// ---------------------------------------------------------------------------
// Web push
// ---------------------------------------------------------------------------

self.addEventListener("push", (event: PushEvent) => {
  // Server pushes a JSON payload {title, body, tag?, icon?, data?}.
  // Fallback to a generic message if parsing fails or the push has no payload.
  let payload: Record<string, unknown> | null = null;
  try {
    payload = event.data ? (event.data.json() as Record<string, unknown>) : null;
  } catch {
    payload = null;
  }
  if (!payload || typeof payload !== "object") {
    payload = { title: "taOS", body: "New activity" };
  }
  const title = typeof payload["title"] === "string" ? payload["title"] : "taOS";
  const options: NotificationOptions = {
    body: typeof payload["body"] === "string" ? payload["body"] : "",
    tag: typeof payload["tag"] === "string" ? payload["tag"] : undefined,
    icon: typeof payload["icon"] === "string" ? payload["icon"] : undefined,
    data: payload["data"] && typeof payload["data"] === "object" ? payload["data"] : {},
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event: NotificationEvent) => {
  // Close the notification, then focus an existing same-origin window
  // (posting the click data so the shell can route to the right tab)
  // or open a new one at root.
  event.notification.close();
  const data = (event.notification.data as Record<string, unknown>) || {};
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clientsList) => {
        for (const client of clientsList) {
          if (client.url && new URL(client.url).origin === self.location.origin) {
            (client as WindowClient).postMessage({ type: "taos-push:click", data });
            return (client as WindowClient).focus();
          }
        }
        if (self.clients.openWindow) {
          return self.clients.openWindow("/");
        }
        return null;
      })
  );
});
