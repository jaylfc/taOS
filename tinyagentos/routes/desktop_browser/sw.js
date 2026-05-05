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

// Receive page base URL + profile ID from copilot.js
self.addEventListener('message', function (event) {
  var data = event.data || {};
  if (data.type === 'taos-sw:prime') {
    self.__taosPageBaseUrl = data.pageBaseUrl;
    self.__taosProfileId = data.profileId;
  }
});
