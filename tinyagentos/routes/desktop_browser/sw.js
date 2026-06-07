// taOS BrowserApp v2 — Service Worker for SPA fetch interception.
//
// Registered by copilot.js. Intercepts fetch events from the proxied
// iframe and routes them through /api/desktop/browser/proxy, preserving
// the original URL via the ?url= query param.
//
// Safe paths (NOT intercepted): /api/desktop/browser/*, /__taos/*.

self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  // No claim() call here — taking control of all same-origin clients (including
  // the parent shell) would expose /api/ fetches to this SW's interception
  // logic. The SW naturally controls iframes that load after it activates,
  // which is sufficient for the proxy use-case.
  event.waitUntil(Promise.resolve());
});

function shouldIntercept(url) {
  if (url.origin !== self.location.origin) return false;
  if (url.pathname.indexOf('/api/desktop/browser/') === 0) return false;
  if (url.pathname.indexOf('/__taos/') === 0) return false;
  if (url.pathname === '/favicon.ico') return false;
  return true;
}

self.addEventListener('fetch', function (event) {
  var req = event.request;
  // PR 8 limitation: the proxy endpoint is GET-only. Non-GET requests
  // (POST/PUT/DELETE/PATCH) would return 405. Skip interception for those
  // and let the request hit its native target — most SPA mutations will
  // CORS-fail until a follow-up PR extends the proxy to support all methods.
  if (req.method !== 'GET' && req.method !== 'HEAD') return;

  var url;
  try { url = new URL(req.url); } catch (_e) { return; }
  if (!shouldIntercept(url)) return;

  var pageBaseUrl = self.__taosPageBaseUrl;
  if (!pageBaseUrl) {
    // SW not yet primed with the page base — let the request through.
    return;
  }

  var absoluteOriginal;
  try {
    absoluteOriginal = new URL(url.pathname + url.search + url.hash, pageBaseUrl).href;
  } catch (_e) {
    return;
  }

  var profileId = self.__taosProfileId || 'personal';
  var proxiedUrl = '/api/desktop/browser/proxy?profile_id=' +
    encodeURIComponent(profileId) + '&url=' + encodeURIComponent(absoluteOriginal);

  event.respondWith(fetch(proxiedUrl, {
    method: req.method,
    headers: req.headers,
    body: (req.method === 'GET' || req.method === 'HEAD') ? undefined : req.clone().body,
    credentials: 'include',
  }));
});

// Receive page base URL + profile ID from copilot.js.
// Hardened: only trusted clients can prime; profileId and pageBaseUrl are
// validated so a malicious proxied page cannot re-prime to a different origin
// or inject path-traversal characters into the profile ID.
self.addEventListener('message', function (event) {
  // Ignore messages with no source (not from a SW client).
  if (!event.source) return;

  var data = event.data || {};
  if (data.type !== 'taos-sw:prime') return;

  // profileId must be a safe alphanumeric slug (no path separators / odd chars).
  var profileId = data.profileId;
  if (typeof profileId !== 'string' || !/^[a-zA-Z0-9_-]+$/.test(profileId)) return;

  // pageBaseUrl must resolve to this origin (or be a relative path).
  var pageBaseUrl = data.pageBaseUrl;
  if (typeof pageBaseUrl !== 'string') return;
  try {
    var resolved = new URL(pageBaseUrl, self.location.origin);
    if (resolved.origin !== self.location.origin) return;
  } catch (_e) {
    return;
  }

  self.__taosPageBaseUrl = pageBaseUrl;
  self.__taosProfileId = profileId;
});

self.addEventListener('push', function (event) {
  // Server pushes a JSON payload {title, body, tag?, icon?, data?}.
  // We forward to showNotification, falling back to a generic message
  // if parsing fails or the push has no payload.
  var payload;
  try {
    payload = event.data ? event.data.json() : null;
  } catch (_e) {
    payload = null;
  }
  if (!payload || typeof payload !== 'object') {
    payload = { title: 'taOS', body: 'New activity' };
  }
  var title = (typeof payload.title === 'string') ? payload.title : 'taOS';
  var options = {
    body: (typeof payload.body === 'string') ? payload.body : '',
    tag: (typeof payload.tag === 'string') ? payload.tag : undefined,
    icon: (typeof payload.icon === 'string') ? payload.icon : undefined,
    data: (payload.data && typeof payload.data === 'object') ? payload.data : {},
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function (event) {
  // Close the notification, then either focus an existing same-origin
  // taOS window (postMessage so the shell can route to the right tab)
  // or open a new one.
  event.notification.close();
  var data = event.notification.data || {};
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (clientsList) {
      for (var i = 0; i < clientsList.length; i++) {
        var client = clientsList[i];
        if (client.url && new URL(client.url).origin === self.location.origin) {
          // Found a same-origin client — focus it and forward the click data.
          client.postMessage({ type: 'taos-push:click', data: data });
          return client.focus();
        }
      }
      // No matching window — open a new one at root.
      if (self.clients.openWindow) {
        return self.clients.openWindow('/');
      }
      return null;
    })
  );
});
