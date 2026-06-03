// taOS BrowserApp v2 — copilot.js
//
// Read-op implementation (PR 6).  Drive ops (scrollTo, click, type, navigate,
// focus, highlight, arrow, sticky, cursor, clear) land in PR 7.
//
// Injected into every proxied page by injector.py.  Runs same-origin with the
// parent shell (the proxy serves both).

(function () {
  'use strict';

  // Idempotent guard — re-injection (e.g. turbo / PJAX frame swap) is a no-op.
  if (window.__taosCopilot) return;
  window.__taosCopilot = true;

  // ─── Service Worker registration + priming ──────────────────────────────────
  // The proxied iframe now has a REAL, separate origin (the proxy-origin port)
  // and runs with allow-same-origin, so navigator.serviceWorker IS available
  // here. We register /__taos/sw.js (scope "/") from inside the iframe — the SW
  // belongs to the proxy origin, NOT the shell — and prime it with the page's
  // real base URL + profile id so it can rewrite relative SPA fetches back
  // through the proxy. Priming context comes from meta tags the backend
  // injector templates into this page (the sandbox blocks reaching the parent
  // for it).
  (function registerProxyServiceWorker() {
    if (!('serviceWorker' in navigator)) return;

    function readMeta(name) {
      var el = document.querySelector('meta[name="' + name + '"]');
      return el ? (el.getAttribute('content') || '') : '';
    }
    var pageBaseUrl = readMeta('taos-page-base') || location.href;
    var profileId = readMeta('taos-profile-id') || 'personal';

    function prime(sw) {
      if (!sw) return;
      try {
        sw.postMessage({
          type: 'taos-sw:prime',
          pageBaseUrl: pageBaseUrl,
          profileId: profileId,
        });
      } catch (_e) { /* SW gone — ignore */ }
    }

    navigator.serviceWorker.register('/__taos/sw.js', { scope: '/' }).then(
      function () {
        // Prime via .ready so the message is delivered whether or not the SW
        // has claimed this client yet. The active worker (or, on first install,
        // the one that just took control on next load) gets the page base.
        navigator.serviceWorker.ready.then(function (reg) {
          prime(reg.active);
        });
        if (navigator.serviceWorker.controller) {
          prime(navigator.serviceWorker.controller);
        }
      },
      function () { /* registration failed (e.g. http) — SPA fetch falls back */ }
    );
  })();

  // ─── WebSocket constructor patch ─────────────────────────────────────────────
  // PR 8 ships a no-op patch that logs cross-origin WS attempts. PR 9 (or
  // follow-up) will add real WS proxying through the server. The hook is in
  // place now so future code can extend the wrapper.
  (function patchWebSocket() {
    if (!window.WebSocket || window.WebSocket.__taosPatched) return;
    var OriginalWebSocket = window.WebSocket;

    function PatchedWebSocket(url, protocols) {
      var u;
      try { u = new URL(url, window.location.href); } catch (_e) {
        return new OriginalWebSocket(url, protocols);
      }
      if (u.origin !== window.location.origin) {
        // Cross-origin WS — would need server-side WS proxying (PR 9)
        if (typeof console !== 'undefined' && console.warn) {
          console.warn('[taos] WebSocket to', url, 'is not proxied — PR 9 will add support');
        }
      }
      return new OriginalWebSocket(url, protocols);
    }
    PatchedWebSocket.prototype = OriginalWebSocket.prototype;
    PatchedWebSocket.__taosPatched = true;
    window.WebSocket = PatchedWebSocket;
  })();

  var meta = document.querySelector('meta[name="taos-copilot-ws"]');
  if (!meta) return; // injector didn't run; bail silently

  // ---------------------------------------------------------------------------
  // Op table — read-only ops in PR 6
  // PR 7 will add: scrollTo, click, type, navigate, focus, highlight,
  //                arrow, sticky, cursor, clear
  // ---------------------------------------------------------------------------
  var ops = {
    extract: extractReadable,
    screenshot: function () {
      return { error: 'screenshot not implemented in PR 6' };
    },
    scrollPosition: function () {
      return {
        x: window.scrollX,
        y: window.scrollY,
        viewport: { w: window.innerWidth, h: window.innerHeight },
      };
    },
    findElement: findElement,

    // ─── Drive ops ─────────────────────────────────────────────────────────────
    // These require server-side capability check (server enforces in Task 11).
    // copilot.js runs them unconditionally — server is responsible for not
    // dispatching them without a grant.

    scrollTo: function (args) {
      if (args && args.selector) {
        var el = document.querySelector(args.selector);
        if (!el) return { error: 'not-found' };
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return { ok: true };
      }
      if (args && typeof args.y === 'number') {
        window.scrollTo({ top: args.y, behavior: 'smooth' });
        return { ok: true };
      }
      return { error: 'missing selector or y' };
    },

    click: function (args) {
      if (!args || !args.selector) return { error: 'missing selector' };
      var el = document.querySelector(args.selector);
      if (!el) return { error: 'not-found' };
      // Synthetic click works for buttons/links and bubbles like a user click.
      el.click();
      return { ok: true };
    },

    type: function (args) {
      if (!args || !args.selector) return { error: 'missing selector' };
      var el = document.querySelector(args.selector);
      if (!el) return { error: 'not-found' };
      if (!('value' in el)) return { error: 'not-input' };
      el.value = (args.value !== undefined && args.value !== null) ? String(args.value) : '';
      // Fire input + change so React/Vue/etc. controlled inputs notice
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      if (args.submit && el.form) {
        if (typeof el.form.requestSubmit === 'function') {
          el.form.requestSubmit();
        } else {
          el.form.submit();
        }
      }
      return { ok: true };
    },

    navigate: function (args) {
      if (!args || typeof args.url !== 'string' || !args.url) {
        return { error: 'missing url' };
      }
      var parsed;
      try {
        parsed = new URL(args.url, location.href);
      } catch (_e) {
        return { error: 'invalid url' };
      }
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
        return { error: 'unsupported scheme' };
      }
      // The proxied iframe is sandboxed; setting location.href triggers a
      // navigation through the proxy (the rewriter has already prefixed
      // anchor hrefs but a synthetic navigate uses the raw URL — the browser
      // shell picks this up via the navigation event).
      location.href = parsed.href;
      return { ok: true };
    },

    focus: function (args) {
      if (!args || !args.selector) return { error: 'missing selector' };
      var el = document.querySelector(args.selector);
      if (!el) return { error: 'not-found' };
      if (typeof el.focus !== 'function') return { error: 'not-focusable' };
      el.focus();
      return { ok: true };
    },

    // ─── Annotation ops ────────────────────────────────────────────────────────
    // In-iframe annotations drawn directly into the proxied DOM. Anchored
    // elements survive iframe scroll natively. Free-floating cursor + arrows
    // are drawn by the parent's AnnotationLayer.tsx (this op returns a
    // sentinel so the parent knows to draw them).
    //
    // Annotations are tagged with data-taos-annotation-id so 'clear' can
    // remove them by id.

    highlight: function (args) {
      if (!args || !args.selector) return { error: 'missing selector' };
      var el = document.querySelector(args.selector);
      if (!el) return { error: 'not-found' };
      var color = args.color || 'yellow';
      var id = 'h-' + Date.now() + '-' + Math.floor(Math.random() * 100000);
      // Stash original background so 'clear' can restore it
      el.dataset.taosAnnotationOrigBg = el.style.background || '';
      el.style.background = color;
      el.dataset.taosAnnotation = 'highlight';
      el.dataset.taosAnnotationId = id;
      return { ok: true, annotationId: id };
    },

    sticky: function (args) {
      if (!args || !args.anchorSelector) return { error: 'missing anchorSelector' };
      var anchor = document.querySelector(args.anchorSelector);
      if (!anchor) return { error: 'not-found' };
      var id = 'sticky-' + Date.now() + '-' + Math.floor(Math.random() * 100000);
      var note = document.createElement('div');
      note.textContent = (args.text !== undefined && args.text !== null) ? String(args.text) : '';
      note.dataset.taosAnnotation = 'sticky';
      note.dataset.taosAnnotationId = id;
      note.style.cssText = 'position:absolute;'
        + 'background:' + (args.color || '#fff8b0') + ';'
        + 'border:1px solid #999;'
        + 'padding:6px;'
        + 'font:13px sans-serif;'
        + 'border-radius:4px;'
        + 'z-index:2147483647;'
        + 'max-width:240px;'
        + 'box-shadow:0 2px 8px rgba(0,0,0,0.15);';
      // Position below the anchor in document coordinates
      var rect = anchor.getBoundingClientRect();
      note.style.top = (window.scrollY + rect.bottom + 4) + 'px';
      note.style.left = (window.scrollX + rect.left) + 'px';
      document.body.appendChild(note);
      return { ok: true, annotationId: id };
    },

    arrow: function (args) {
      // Parent-overlay path — see AnnotationLayer.tsx (PR 7 Task 8). The agent
      // sees this sentinel and re-issues via the parent's annotation channel.
      return { error: 'use-parent-overlay' };
    },

    cursor: function (args) {
      // Same: parent-overlay only. The cursor follows mouse coords from the
      // parent's perspective and the iframe doesn't have those.
      return { error: 'use-parent-overlay' };
    },

    clear: function (args) {
      if (args && args.annotationId) {
        var el = document.querySelector(
          '[data-taos-annotation-id="' + args.annotationId + '"]'
        );
        if (el) {
          if (el.dataset.taosAnnotation === 'highlight') {
            el.style.background = el.dataset.taosAnnotationOrigBg || '';
            delete el.dataset.taosAnnotation;
            delete el.dataset.taosAnnotationId;
            delete el.dataset.taosAnnotationOrigBg;
          } else {
            el.parentNode && el.parentNode.removeChild(el);
          }
        }
        return { ok: true };
      }
      // Clear all annotations
      var all = document.querySelectorAll('[data-taos-annotation]');
      for (var i = 0; i < all.length; i++) {
        var a = all[i];
        if (a.dataset.taosAnnotation === 'highlight') {
          a.style.background = a.dataset.taosAnnotationOrigBg || '';
          delete a.dataset.taosAnnotation;
          delete a.dataset.taosAnnotationId;
          delete a.dataset.taosAnnotationOrigBg;
        } else if (a.parentNode) {
          a.parentNode.removeChild(a);
        }
      }
      return { ok: true };
    },
  };

  function extractReadable(args) {
    var mode = (args && args.mode) || 'readable';
    if (mode === 'readable') {
      var main = document.querySelector('main, article, [role="main"]') || document.body;
      return { text: (main.innerText || '').slice(0, 8000) };
    }
    if (mode === 'dom') {
      return { html: document.documentElement.outerHTML.slice(0, 200000) };
    }
    if (mode === 'a11y') {
      var interactive = Array.from(document.querySelectorAll('a, button, input, [role]'));
      return {
        tree: interactive.slice(0, 200).map(function (el) {
          return {
            tag: el.tagName,
            role: el.getAttribute('role'),
            label: el.getAttribute('aria-label')
                   || (el.textContent ? el.textContent.trim().slice(0, 80) : ''),
          };
        }),
      };
    }
    return { error: 'unknown mode' };
  }

  function findElement(args) {
    if (args && args.selector) {
      var el = document.querySelector(args.selector);
      if (!el) return { error: 'not-found' };
      var r = el.getBoundingClientRect();
      return {
        box: { x: r.x, y: r.y, w: r.width, h: r.height },
        selector: args.selector,
        text: el.textContent ? el.textContent.slice(0, 200) : '',
      };
    }
    if (args && args.text) {
      var all = document.querySelectorAll('a, button, h1, h2, h3, p, span');
      for (var i = 0; i < all.length; i++) {
        var candidate = all[i];
        if (candidate.textContent && candidate.textContent.indexOf(args.text) !== -1) {
          var rect = candidate.getBoundingClientRect();
          return {
            box: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
            text: candidate.textContent.slice(0, 200),
          };
        }
      }
      return { error: 'not-found' };
    }
    return { error: 'missing selector or text' };
  }

  function cssPath(el) {
    if (!(el instanceof Element)) return '';
    var path = [];
    while (el && el.nodeType === 1) {
      var s = el.nodeName.toLowerCase();
      if (el.id) {
        s += '#' + (window.CSS && window.CSS.escape ? window.CSS.escape(el.id) : el.id);
        path.unshift(s);
        break;
      }
      var sib = el.parentNode
        ? Array.from(el.parentNode.children).filter(function (c) { return c.nodeName === el.nodeName; })
        : [];
      if (sib.length > 1) s += ':nth-of-type(' + (sib.indexOf(el) + 1) + ')';
      path.unshift(s);
      el = el.parentNode;
    }
    return path.join(' > ');
  }

  // ---------------------------------------------------------------------------
  // Connection management
  // One WebSocket per (tab, agent).  Multiple agents → multiple connections.
  // ---------------------------------------------------------------------------
  var _connections = {}; // agentId -> WebSocket

  // The parent shell mints a ticket per (tab, agent) and postMessages it into
  // the iframe.  We open one WS per agentId.
  // ─── Tab focus context ──────────────────────────────────────────────────────
  // window_id and tab_id are not available inside the sandboxed iframe directly.
  // The parent shell (TabRenderer.tsx or BrowserApp.tsx) sends them via
  // postMessage of type 'taos-copilot:tab-focus' whenever the active tab changes.
  // We cache them here and forward via WS when the iframe receives focus.
  var _focusWindowId = '';
  var _focusTabId = '';

  function _sendTabFocusToAll() {
    if (!_focusWindowId || !_focusTabId) return;
    var msg = JSON.stringify({
      event: 'tab-focus',
      window_id: _focusWindowId,
      tab_id: _focusTabId,
    });
    var ids = Object.keys(_connections);
    for (var i = 0; i < ids.length; i++) {
      var ws = _connections[ids[i]];
      if (ws && ws.readyState === 1) {
        ws.send(msg);
      }
    }
  }

  // When the iframe window itself gains focus (user clicks into it)
  window.addEventListener('focus', function () {
    _sendTabFocusToAll();
  });

  // When the iframe becomes visible (tab switch back to this tab)
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') {
      _sendTabFocusToAll();
    }
  });

  window.addEventListener('message', function (e) {
    // SECURITY: sandbox attribute is "allow-scripts allow-forms allow-popups
    // allow-downloads" (no allow-same-origin), so accessing
    // window.parent.location.origin would throw SecurityError.
    // Instead we verify the message came from the direct parent window, which
    // is equivalent and works in sandboxed contexts.
    if (e.source !== window.parent) return;

    var data = e.data || {};
    if (data.type === 'taos-copilot:open' && data.ticket && data.agentId) {
      openConnection(data.ticket, data.agentId);
    } else if (data.type === 'taos-copilot:close' && data.agentId) {
      closeConnection(data.agentId);
    } else if (data.type === 'taos-copilot:tab-focus' && data.window_id && data.tab_id) {
      // Parent shell tells us which tab is currently focused (and whether it's
      // this iframe's tab). We always update and forward — the server checks
      // whether the stored (window_id, tab_id) matches the agent's pinned tab.
      _focusWindowId = data.window_id;
      _focusTabId = data.tab_id;
      if (data.focused) {
        _sendTabFocusToAll();
      }
    }
  });

  function openConnection(ticket, agentId) {
    if (_connections[agentId]) return; // already open

    var proto = location.protocol === 'https:' ? 'wss' : 'ws';
    var url = proto + '://' + location.host
            + '/api/desktop/browser/copilot?ticket=' + encodeURIComponent(ticket);
    var ws = new WebSocket(url);
    _connections[agentId] = ws;

    ws.addEventListener('message', function (evt) {
      var msg;
      try { msg = JSON.parse(evt.data); } catch (_e) { return; }
      if (!msg) return;

      // Forward server-emitted events (page-changed, url-changed, etc.) up
      // to the parent shell via postMessage. The parent's agent-ws-bridge
      // listens for these, so we don't need a second WebSocket from the
      // parent (which would clobber this one in the server's hub registry).
      if (msg.event && window.parent && window.parent !== window) {
        try {
          // Target origin "*" — sandboxed iframe can't query parent's origin
          // without allow-same-origin. The parent's listener verifies
          // e.source === iframe.contentWindow, which is the equivalent guard.
          window.parent.postMessage({
            type: 'taos-copilot:server-event',
            agentId: agentId,
            message: msg,
          }, '*');
        } catch (_e) { /* parent gone or hostile — ignore */ }
      }

      // Op dispatch (read ops; drive ops land in PR 7).
      if (msg.op && Object.prototype.hasOwnProperty.call(ops, msg.op)) {
        var result;
        try {
          result = ops[msg.op](msg.args || {});
        } catch (err) {
          result = { error: String(err) };
        }
        // Drive ops flip the chrome to "driving" — tell the parent
        var DRIVE_OPS = { scrollTo: 1, click: 1, type: 1, navigate: 1, focus: 1 };
        if (DRIVE_OPS[msg.op] && window.parent && window.parent !== window) {
          try {
            window.parent.postMessage({
              type: 'taos-copilot:server-event',
              agentId: agentId,
              message: { event: 'driving-state', state: 'driving', timestamp: Date.now() / 1000 },
            }, '*');
          } catch (_e) { /* parent gone — ignore */ }
        }
        if (ws.readyState === 1) {
          ws.send(JSON.stringify({ event: 'ack', op_id: msg.op_id, result: result }));
        }
      }
    });

    // Page lifecycle events forwarded to the server.
    // Scroll is throttled to one event per animation frame.
    var scrollPending = false;
    function onScroll() {
      if (scrollPending) return;
      scrollPending = true;
      requestAnimationFrame(function () {
        scrollPending = false;
        if (ws.readyState === 1) {
          ws.send(JSON.stringify({ event: 'scroll', x: window.scrollX, y: window.scrollY }));
        }
      });
    }

    function onSubmit(e) {
      if (ws.readyState === 1) {
        ws.send(JSON.stringify({ event: 'form-submit', selector: cssPath(e.target) }));
      }
    }

    ws.addEventListener('close', function () {
      window.removeEventListener('scroll', onScroll);
      document.removeEventListener('submit', onSubmit, true);
      delete _connections[agentId];
    });

    window.addEventListener('scroll', onScroll, { passive: true });
    document.addEventListener('submit', onSubmit, true);
  }

  function closeConnection(agentId) {
    var ws = _connections[agentId];
    if (ws) {
      try { ws.close(); } catch (_e) {}
      delete _connections[agentId];
    }
  }
})();
