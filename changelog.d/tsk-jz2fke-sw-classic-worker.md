### Fixed

- The service worker now builds as a self-contained classic script (`/sw.js`
  with no `import`/`export` and no external chunk), so browsers can register
  it again; it was emitted as an ES module and every browser rejected it,
  leaving taOS with no service worker and an empty fast-boot cache.
- A failed service worker registration is now observable: it is logged with
  `console.error`, recorded on `<html data-taos-sw="failed">` and via
  `getServiceWorkerStatus()`, and announced as a `taos:sw-registration-failed`
  window event, instead of a single console warning.
