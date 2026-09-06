// Legacy self-unregistering service worker.
//
// An earlier taOS release (HTMX/Jinja2 era, removed in 95651bd) registered
// this service worker with a cache-first strategy for /static/*. The new
// React SPA does not register a service worker, but browsers that visited
// the old app still have this SW active — silently intercepting requests
// and serving pinned cache entries long after the code on disk has moved on.
//
// This replacement body tears itself down: it deletes every cache it owns
// and unregisters itself on next install/activate. After one PWA launch
// against this script, the browser is back to plain HTTP caching and picks
// up fresh content normally.
self.addEventListener('install', () => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
        await self.registration.unregister();
        const clients = await self.clients.matchAll({ includeUncontrolled: true });
        await Promise.all(clients.map((client) => client.navigate(client.url)));
    })());
});
