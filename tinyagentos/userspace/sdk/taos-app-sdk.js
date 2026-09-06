// tinyagentos/userspace/sdk/taos-app-sdk.js
(() => {
  const APP_ID = new URLSearchParams(location.search).get("app")
    || (document.currentScript && document.currentScript.dataset.appId) || "";
  let seq = 0;
  const pending = new Map();

  // --- Theme API state ---
  let _themeTokens = {};
  const _themeSubscribers = [];

  window.addEventListener("message", (e) => {
    const m = e.data;
    // Broker replies
    if (m && m.taosAppReply != null && pending.has(m.taosAppReply)) {
      const { resolve } = pending.get(m.taosAppReply);
      pending.delete(m.taosAppReply);
      resolve(m);
    }
    // Theme push from the shell (only first-party apps receive this)
    if (m && m.taosTheme && typeof m.taosTheme === "object" && !Array.isArray(m.taosTheme)) {
      _themeTokens = m.taosTheme;
      for (const cb of _themeSubscribers) {
        try { cb(_themeTokens); } catch (_) {}
      }
    }
  });

  function call(capability, args) {
    const id = ++seq;
    return new Promise((resolve) => {
      pending.set(id, { resolve });
      parent.postMessage({ taosApp: APP_ID, id, capability, args: args || {} }, "*");
    });
  }

  window.taos = {
    appId: APP_ID,
    kv: {
      get: (k) => call("app.kv.get", { key: k }).then((r) => r.result),
      set: (k, v) => call("app.kv.set", { key: k, value: v }),
      delete: (k) => call("app.kv.delete", { key: k }),
      keys: () => call("app.kv.keys", {}).then((r) => r.result),
    },
    table: {
      insert: (t, row) => call("app.table.insert", { table: t, row }).then((r) => r.result),
      query: (t, where) => call("app.table.query", { table: t, where }).then((r) => r.result),
      delete: (t, id) => call("app.table.delete", { table: t, id }),
    },
    files: {
      read: (p) => call("app.files.read", { path: p }).then((r) => r.result),
      write: (p, content) => call("app.files.write", { path: p, content }),
    },
    notify: (title, body) => call("app.notify", { title, body }),
    // gated -- resolve to {error:"permission_denied"} if not granted
    net: { fetch: (url, opts) => call("app.net", { path: url, method: (opts && opts.method) || "GET", body: opts && opts.body, headers: opts && opts.headers }) },
    backend: {
      fetch: (path, opts) => call("app.net", {
        path,
        method: (opts && opts.method) || "GET",
        body: opts && opts.body,
        headers: opts && opts.headers,
      }).then((r) => r.result),
    },
    agent: { ask: (name, message) => call("app.agent", { name, message }).then((r) => r.result) },
    memory: { search: (q) => call("app.memory.search", { q }).then((r) => r.result) },
    // Theme API -- populated only for first-party apps that receive taosTheme
    // messages from the shell. Community apps never receive these messages.
    theme: {
      /** Returns the last set of CSS variable tokens received from the shell. */
      get: () => ({ ..._themeTokens }),
      /**
       * Register a callback to be called whenever the shell posts new theme
       * tokens. Returns an unsubscribe function.
       */
      subscribe: (cb) => {
        _themeSubscribers.push(cb);
        return () => {
          const i = _themeSubscribers.indexOf(cb);
          if (i !== -1) _themeSubscribers.splice(i, 1);
        };
      },
    },
  };
})();
