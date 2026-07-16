/// <reference lib="WebWorker" />
/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * taOS service worker.
 *
 * Caches the SPA shell so the UI loads when the backend is unreachable
 * (e.g. mid-restart after Install Update). Scope: '/' — covers both
 * /desktop and /chat-pwa. Strategy:
 *  - cache-first for /desktop/assets/* (immutable hashed URLs)
 *  - network-first for the SPA shell HTML (/desktop/index.html, /chat-pwa):
 *    a stale cached index references old hashed chunk URLs that 404 after a
 *    redeploy, which crashes lazy imports (ChunkLoadError) and forces a reload
 *    loop. Always fetch the current index when online; fall back to cache only
 *    when the network fails (offline / mid-restart).
 *  - stale-while-revalidate for static manifests and icons
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

  // isShellHTML is checked BEFORE isPrecachedStatic on purpose: /desktop/,
  // /desktop/index.html and /chat-pwa are in PRECACHE_URLS but must use
  // network-first, not the SWR branch below -- a stale cached index
  // reintroduces the ChunkLoadError loop. Keep this branch first.
  if (isShellHTML(url)) {
    // Network-first (bounded) for the SPA shell: fetch the current index when
    // the backend is healthy so its chunk refs match the deployed assets. Fall
    // back to the cached shell on a non-OK response (503 mid-restart), a
    // network failure (offline), or a stall (aborted after the timeout),
    // preserving the Install-Update reconnect UX. cache:"no-store" bypasses the
    // browser HTTP cache so a 304-with-empty-body cannot slip through.
    const cacheKey = url.pathname.startsWith("/chat-pwa")
      ? new Request("/chat-pwa")
      : (url.pathname !== "/desktop/index.html" ? new Request("/desktop/") : req);
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 4000);
        try {
          const fresh = await fetch(req, { cache: "no-store", signal: controller.signal });
          if (fresh.ok) {
            cache.put(cacheKey, fresh.clone());
            return fresh;
          }
          // Non-OK (e.g. 503 mid-restart after Install Update): prefer the
          // cached shell so the UI still loads and the reconnect banner shows.
          const hit = await cache.match(cacheKey);
          return hit || fresh;
        } catch (err) {
          // Offline / stall (aborted) / network failure: fall back to cache.
          const hit = await cache.match(cacheKey);
          if (hit) return hit;
          throw err;
        } finally {
          clearTimeout(timer);
        }
      })
    );
    return;
  }

  if (isPrecachedStatic(url)) {
    // Stale-while-revalidate for icons/manifests (no hashed-chunk coupling):
    // serve cache instantly, refresh in background.
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const hit = await cache.match(req);
        const network = fetch(req).then((r) => {
          if (r.ok) cache.put(req, r.clone());
          return r;
        }).catch((err) => {
          if (hit) return hit;
          throw err;
        });
        return hit || network;
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
  // (posting the click data so the shell can route to the right tab) or
  // open a new one at the notification's deep link (data.url), falling back
  // to root.
  event.notification.close();
  const data = (event.notification.data as Record<string, unknown>) || {};
  // Resolve the deep link against our own origin and refuse anything
  // cross-origin (open-redirect / phishing guard): a hostile push cannot make
  // openWindow() navigate to an external site.
  let target = typeof data["url"] === "string" && data["url"] ? (data["url"] as string) : "/";
  try {
    const u = new URL(target, self.location.origin);
    target = u.origin === self.location.origin && u.pathname.startsWith("/")
      ? u.pathname + u.search
      : "/";
  } catch {
    target = "/";
  }
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
          return self.clients.openWindow(target);
        }
        return null;
      })
  );
});
