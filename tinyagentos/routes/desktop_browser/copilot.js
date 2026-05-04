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
        s += '#' + el.id;
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
      if (msg && msg.op && ops[msg.op]) {
        var result;
        try {
          result = ops[msg.op](msg.args || {});
        } catch (err) {
          result = { error: String(err) };
        }
        if (ws.readyState === 1) {
          ws.send(JSON.stringify({ event: 'ack', op_id: msg.op_id, result: result }));
        }
      }
    });

    ws.addEventListener('close', function () {
      delete _connections[agentId];
    });

    // Page lifecycle events forwarded to the server.
    // Scroll is throttled to one event per animation frame.
    var scrollPending = false;
    window.addEventListener('scroll', function () {
      if (scrollPending) return;
      scrollPending = true;
      requestAnimationFrame(function () {
        scrollPending = false;
        if (ws.readyState === 1) {
          ws.send(JSON.stringify({ event: 'scroll', x: window.scrollX, y: window.scrollY }));
        }
      });
    }, { passive: true });

    document.addEventListener('submit', function (e) {
      var form = e.target;
      if (ws.readyState === 1) {
        ws.send(JSON.stringify({
          event: 'form-submit',
          selector: cssPath(form),
        }));
      }
    }, true);
  }

  function closeConnection(agentId) {
    var ws = _connections[agentId];
    if (ws) {
      try { ws.close(); } catch (_e) {}
      delete _connections[agentId];
    }
  }
})();
