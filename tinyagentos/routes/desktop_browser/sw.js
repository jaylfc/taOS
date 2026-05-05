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
  event.waitUntil(self.clients.claim());
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
