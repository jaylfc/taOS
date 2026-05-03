// taOS BrowserApp v2 — copilot.js (stub)
//
// Full implementation (RPC bridge to copilot_ws, DOM extraction,
// annotation rendering, drive ops) lands in PR 6.
//
// PR 3 only needs this file to exist as a static asset so the
// injector's <script src="/__taos/copilot.js"> reference resolves.

(function () {
  'use strict';

  if (window.__taos_copilot_loaded__) {
    return;
  }
  window.__taos_copilot_loaded__ = true;

  const meta = document.querySelector('meta[name="taos-copilot-ws"]');
  const wsUrl = meta ? meta.getAttribute('content') : null;

  console.info('[taos-copilot] stub loaded', { wsUrl });

  // PR 6 will:
  //   - open ws connection to wsUrl
  //   - register message handlers for {extract, drive.*, highlight, …}
  //   - send page-loaded event
  //   - render annotations via parent postMessage
})();
